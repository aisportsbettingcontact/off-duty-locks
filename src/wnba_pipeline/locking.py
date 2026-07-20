"""Single-active-run file lock for the pipeline data root.

Overlapping runs are REJECTED, never interleaved: exactly one run may hold
``<data_root>/.lock`` at a time. The lock file contains a small JSON document
(``{"pid": ..., "acquiredAtUtc": ..., "runId": ...}`` — no secrets, ever) so
operators can see who holds it (see docs/runbook.md, "Lock takeover").

Acquisition strategy:
  * primary path: ``os.open(O_CREAT | O_EXCL)`` — atomic create-if-absent on
    every POSIX filesystem this pipeline targets;
  * a *fresh* existing lock raises :class:`~wnba_pipeline.contract.LockHeld`;
  * a *stale* existing lock (older than ``max_age_seconds``, default 2 hours)
    is taken over by atomically replacing it (temp file + ``os.replace``) —
    a crashed run must never wedge the pipeline forever.

Stale-takeover caveat (documented, accepted): two processes that both find
the same stale lock at the same instant could both replace it. The production
scheduler (GitHub Actions ``concurrency: wnba-extract``) already serializes
runs, so takeover races only matter for concurrent *manual* runs after a
crash, where the 2-hour staleness window makes collision overwhelmingly
unlikely. This is the smallest mechanism that satisfies the contract.

Release only ever removes a lock we still own (``runId`` match) — releasing
after our lock was taken over must not delete the new owner's lock.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from wnba_pipeline.contract import LockHeld, StorageError

logger = logging.getLogger("wnba_pipeline.locking")

DEFAULT_LOCK_MAX_AGE_SECONDS = 2 * 60 * 60  # 2 hours
LOCK_FILENAME = ".lock"

_ISO_Z = "%Y-%m-%dT%H:%M:%SZ"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_ISO_Z)


def _parse_iso_z(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, _ISO_Z).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None


class RunLock:
    """Context-manager file lock at ``<data_root>/.lock``.

    Usage::

        with RunLock(data_root, run_id) as lock:
            ...  # exclusive section; released in finally even on exceptions
    """

    def __init__(
        self,
        data_root: str | os.PathLike[str],
        run_id: str,
        *,
        max_age_seconds: float = DEFAULT_LOCK_MAX_AGE_SECONDS,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.run_id = run_id
        self.max_age_seconds = float(max_age_seconds)
        self._now_fn = now_fn or _utcnow
        self.lock_path = self.data_root / LOCK_FILENAME
        self._held = False

    # -- introspection ------------------------------------------------------

    @property
    def held(self) -> bool:
        return self._held

    def _payload(self) -> dict[str, Any]:
        # No secrets: pid, timestamp, and run id only.
        return {
            "pid": os.getpid(),
            "acquiredAtUtc": _to_iso_z(self._now_fn()),
            "runId": self.run_id,
        }

    def read_holder(self) -> dict[str, Any] | None:
        """Best-effort read of the current lock-holder document."""
        try:
            with open(self.lock_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return None

    def _existing_lock_age_seconds(self) -> float | None:
        """Age of the existing lock, or None if it does not exist.

        Prefers the ``acquiredAtUtc`` recorded inside the lock; falls back to
        filesystem mtime when the lock file is unreadable or corrupt (a
        corrupt lock must still age out rather than wedge forever).
        """
        holder = self.read_holder()
        now = self._now_fn()
        if holder is not None:
            acquired = _parse_iso_z(str(holder.get("acquiredAtUtc", "")))
            if acquired is not None:
                return (now - acquired).total_seconds()
        try:
            mtime = os.stat(self.lock_path).st_mtime
        except FileNotFoundError:
            return None
        except OSError:
            return None
        return now.timestamp() - mtime

    # -- acquire / release --------------------------------------------------

    def acquire(self) -> "RunLock":
        if self._held:
            return self
        self.data_root.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(self._payload(), sort_keys=True).encode("utf-8")
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            age = self._existing_lock_age_seconds()
            if age is None:
                # Lock vanished between O_EXCL failure and stat: retry once.
                return self.acquire()
            if age <= self.max_age_seconds:
                holder = self.read_holder() or {}
                raise LockHeld(
                    "fresh lock present at "
                    f"{self.lock_path} (age {age:.0f}s <= max "
                    f"{self.max_age_seconds:.0f}s, holder runId="
                    f"{holder.get('runId')!r} pid={holder.get('pid')!r})"
                )
            # Stale lock: take over by atomic replace.
            logger.warning(
                json.dumps(
                    {
                        "event": "lock_stale_takeover",
                        "lockPath": str(self.lock_path),
                        "staleAgeSeconds": round(age, 1),
                        "newRunId": self.run_id,
                    }
                )
            )
            self._atomic_replace(blob)
            self._held = True
            return self
        try:
            os.write(fd, blob)
            os.fsync(fd)
        finally:
            os.close(fd)
        self._held = True
        return self

    def _atomic_replace(self, blob: bytes) -> None:
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=self.data_root, prefix=".lock.", suffix=".tmp"
            )
            try:
                os.write(fd, blob)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp_path, self.lock_path)
        except OSError as exc:  # pragma: no cover - environment-specific
            raise StorageError(f"could not take over stale lock: {exc}") from exc

    def release(self) -> None:
        """Remove the lock file if (and only if) this run still owns it."""
        if not self._held:
            return
        self._held = False
        holder = self.read_holder()
        if holder is not None and holder.get("runId") not in (None, self.run_id):
            # Our lock was taken over (stale takeover by a later run) — do not
            # delete the new owner's lock.
            logger.warning(
                json.dumps(
                    {
                        "event": "lock_release_skipped_not_owner",
                        "lockPath": str(self.lock_path),
                        "ourRunId": self.run_id,
                        "currentRunId": holder.get("runId"),
                    }
                )
            )
            return
        try:
            os.unlink(self.lock_path)
        except FileNotFoundError:
            pass

    def __enter__(self) -> "RunLock":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        # Always release, including on exceptions (the runner's finally).
        self.release()
