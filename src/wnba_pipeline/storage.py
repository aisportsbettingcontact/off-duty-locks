"""File-based storage implementing the layout in docs/data-contract.md.

Layout under ``Store(root)``::

    raw/<encoded_key>/<run_id>.json         immutable raw upstream payloads
    snapshots/<encoded_key>/<run_id>.json   accepted normalized snapshots
    quarantine/<encoded_key>/<run_id>.json  rejected candidates + reasons
    current/<encoded_key>.json              last-known-good (LKG) snapshot
    manifests/<run_id>.json                 one manifest per run
    teams/<season>.json                     latest expected-team set
    teams/<season>.<checksum12>.json        versioned team-set history

Extraction-key filename encoding
--------------------------------
Extraction keys contain ``:`` (e.g. ``wnba-teamstats:v1:season=2026:...``),
which is not filesystem-safe everywhere (and illegal on Windows). Directory
and file names therefore encode ``':' -> '__'`` (two underscores) via
:func:`encode_key`; :func:`decode_key` reverses it. Keys themselves never
contain ``__`` (they are built from slugs, digits, ``=`` and ``-``), so the
encoding is unambiguous.

Atomicity
---------
Every write goes through a ``NamedTemporaryFile`` created *in the destination
directory* followed by ``os.replace`` — readers never observe a partial file,
and a crash mid-write leaves at most an orphaned ``*.tmp`` file which is
ignored by all readers and by pruning-order logic.

Guarantees enforced here:
  * ``save_raw`` is immutable — an existing ``<run_id>.json`` is never
    silently overwritten (raises :class:`StorageError`);
  * ``accept_snapshot`` writes the snapshot file first and only then
    atomically replaces the LKG pointer (``current/<key>.json``);
  * ``prune`` never touches ``current/`` and never deletes the newest N;
  * missing data stays missing — ``load_last_known_good`` returns
    ``(None, None)`` when there is no LKG, never an empty dataset.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wnba_pipeline.contract import (
    ExpectedTeamSet,
    RawFetchResult,
    RunManifest,
    Snapshot,
    StorageError,
    ValidationFailure,
    canonical_json_checksum,
)

logger = logging.getLogger("wnba_pipeline.storage")

_KEY_SEP = ":"
_KEY_SEP_ENCODED = "__"


def encode_key(extraction_key: str) -> str:
    """Filesystem-safe form of an extraction key (``':' -> '__'``)."""
    return extraction_key.replace(_KEY_SEP, _KEY_SEP_ENCODED)


def decode_key(encoded: str) -> str:
    """Inverse of :func:`encode_key`."""
    return encoded.replace(_KEY_SEP_ENCODED, _KEY_SEP)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path: Path, obj: Any) -> Path:
    """Write JSON atomically: temp file in destination dir + ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_name = tmp.name
            json.dump(obj, tmp, indent=2, sort_keys=False)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError as exc:
        raise StorageError(f"atomic write to {path} failed: {exc}") from exc
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    return path


def _load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class Store:
    """File-based store rooted at ``root`` (usually ``./data``)."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.snapshots_dir = self.root / "snapshots"
        self.quarantine_dir = self.root / "quarantine"
        self.current_dir = self.root / "current"
        self.manifests_dir = self.root / "manifests"
        self.teams_dir = self.root / "teams"

    def ensure_layout(self) -> None:
        for d in (
            self.raw_dir,
            self.snapshots_dir,
            self.quarantine_dir,
            self.current_dir,
            self.manifests_dir,
            self.teams_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    # -- path helpers --------------------------------------------------------

    def raw_path(self, run_id: str, key: str) -> Path:
        return self.raw_dir / encode_key(key) / f"{run_id}.json"

    def snapshot_path(self, run_id: str, key: str) -> Path:
        return self.snapshots_dir / encode_key(key) / f"{run_id}.json"

    def quarantine_path(self, run_id: str, key: str) -> Path:
        return self.quarantine_dir / encode_key(key) / f"{run_id}.json"

    def current_path(self, key: str) -> Path:
        return self.current_dir / f"{encode_key(key)}.json"

    def manifest_path(self, run_id: str) -> Path:
        return self.manifests_dir / f"{run_id}.json"

    # -- raw -----------------------------------------------------------------

    def save_raw(self, run_id: str, key: str, raw: RawFetchResult) -> Path:
        """Persist the raw upstream payload immutably (never overwritten).

        Stored before validation so every candidate — including ones that
        later fail — is preserved with full provenance. No headers, no
        cookies, no secrets: sanitized URL + query params only.
        """
        path = self.raw_path(run_id, key)
        if path.exists():
            raise StorageError(
                f"refusing to overwrite existing immutable raw file: {path}"
            )
        doc = {
            "runId": run_id,
            "extractionKey": key,
            "endpoint": raw.endpoint,
            "url": raw.url,
            "httpStatus": raw.http_status,
            "requestCount": raw.request_count,
            "retryCount": raw.retry_count,
            "fetchedAtUtc": raw.fetched_at_utc,
            "sourceObservedAtUtc": raw.source_observed_at_utc,
            "sourceChecksum": raw.source_checksum,
            "params": _params_dict(raw.params),
            "payload": raw.payload,
        }
        return _atomic_write_json(path, doc)

    # -- snapshots / LKG -----------------------------------------------------

    def accept_snapshot(self, run_id: str, key: str, snapshot: Snapshot) -> Path:
        """Write the accepted snapshot, then atomically promote it to LKG.

        Ordering matters: the immutable snapshot file must be fully on disk
        before ``current/<key>.json`` is replaced, so a crash between the two
        steps leaves the previous LKG intact and loadable.
        """
        doc = snapshot.to_json_dict()
        snap_path = self.snapshot_path(run_id, key)
        if snap_path.exists():
            raise StorageError(
                f"refusing to overwrite existing snapshot file: {snap_path}"
            )
        _atomic_write_json(snap_path, doc)
        _atomic_write_json(self.current_path(key), doc)
        return snap_path

    def load_last_known_good(self, key: str) -> tuple[dict[str, Any] | None, Path | None]:
        """Return ``(snapshot_dict, path)`` for the LKG, or ``(None, None)``.

        A missing LKG is reported as missing — never as an empty dataset.
        A corrupt LKG raises :class:`StorageError` (it must never be silently
        treated as absent, which could let bad data replace good history).
        """
        path = self.current_path(key)
        if not path.exists():
            return None, None
        try:
            return _load_json(path), path
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(f"last-known-good at {path} is unreadable: {exc}") from exc

    # -- quarantine ----------------------------------------------------------

    def quarantine(
        self,
        run_id: str,
        key: str,
        raw_payload: Any,
        failures: list[ValidationFailure] | list[dict[str, Any]],
        params_dict: dict[str, Any],
        *,
        quarantined_at_utc: str | None = None,
    ) -> Path:
        """Persist a rejected candidate with its failure reasons.

        The LKG is never touched by quarantining — rejected data is stored
        alongside, not over, verified data.
        """
        failure_dicts: list[dict[str, Any]] = []
        for f in failures:
            if isinstance(f, ValidationFailure):
                failure_dicts.append(f.to_json_dict())
            else:
                failure_dicts.append(dict(f))
        doc = {
            "runId": run_id,
            "extractionKey": key,
            "quarantinedAtUtc": quarantined_at_utc or _utcnow_iso(),
            "params": params_dict,
            "failures": failure_dicts,
            "rawPayload": raw_payload,
        }
        return _atomic_write_json(self.quarantine_path(run_id, key), doc)

    # -- expected team sets ----------------------------------------------------

    def save_team_set(self, team_set: ExpectedTeamSet) -> Path:
        """Persist an expected-team set, versioned by checksum, keeping history.

        ``teams/<season>.json`` is the latest set; each distinct version is
        also kept as ``teams/<season>.<checksum12>.json``. Saving a set whose
        checksum already exists is idempotent (the latest pointer is still
        refreshed atomically).
        """
        doc = {
            "season": team_set.season,
            "source": team_set.source,
            "sourceUrl": team_set.source_url,
            "resolvedAtUtc": team_set.resolved_at_utc,
            "versionChecksum": team_set.version_checksum,
            "teams": team_set.teams,
        }
        version_path = (
            self.teams_dir / f"{team_set.season}.{team_set.version_checksum[:12]}.json"
        )
        if not version_path.exists():
            _atomic_write_json(version_path, doc)
        latest_path = self.teams_dir / f"{team_set.season}.json"
        _atomic_write_json(latest_path, doc)
        return latest_path

    def load_latest_team_set(self, season: str) -> ExpectedTeamSet | None:
        """Load ``teams/<season>.json`` or ``None`` if never persisted."""
        path = self.teams_dir / f"{season}.json"
        if not path.exists():
            return None
        try:
            doc = _load_json(path)
            teams = dict(doc["teams"])
            checksum = doc.get("versionChecksum") or canonical_json_checksum(
                {"season": doc["season"], "teams": teams}
            )
            return ExpectedTeamSet(
                season=str(doc["season"]),
                source=str(doc.get("source", "stored-team-set")),
                source_url=str(doc.get("sourceUrl", str(path))),
                resolved_at_utc=str(doc.get("resolvedAtUtc", "")),
                teams=teams,
                version_checksum=str(checksum),
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise StorageError(f"stored team set at {path} is unreadable: {exc}") from exc

    def list_team_set_versions(self, season: str) -> list[Path]:
        """All persisted version files for a season (history), sorted by name."""
        if not self.teams_dir.exists():
            return []
        return sorted(
            p
            for p in self.teams_dir.glob(f"{season}.*.json")
            if not p.name.endswith(".tmp")
        )

    # -- manifests -------------------------------------------------------------

    def write_manifest(self, manifest: RunManifest) -> Path:
        return _atomic_write_json(
            self.manifest_path(manifest.run_id), manifest.to_json_dict()
        )

    # -- retention ---------------------------------------------------------------

    def prune(
        self,
        key: str,
        keep_raw: int = 50,
        keep_snapshots: int = 50,
        keep_quarantine: int = 50,
        keep_manifests: int = 200,
    ) -> dict[str, int]:
        """Prune per-key history to the newest N files. NEVER touches current/.

        run_ids start with a UTC ``YYYYMMDDTHHMMSSZ`` timestamp, so plain
        name sort is chronological. Orphaned ``*.tmp`` files from interrupted
        writes are removed too. Returns counts of deleted files per area.
        """
        enc = encode_key(key)
        deleted = {
            "raw": self._prune_dir(self.raw_dir / enc, keep_raw),
            "snapshots": self._prune_dir(self.snapshots_dir / enc, keep_snapshots),
            "quarantine": self._prune_dir(self.quarantine_dir / enc, keep_quarantine),
            "manifests": self._prune_dir(self.manifests_dir, keep_manifests),
        }
        if any(deleted.values()):
            logger.info(json.dumps({"event": "pruned", "extractionKey": key, **deleted}))
        return deleted

    @staticmethod
    def _prune_dir(directory: Path, keep: int) -> int:
        if keep < 0:
            raise StorageError(f"keep count must be >= 0, got {keep}")
        if not directory.exists():
            return 0
        removed = 0
        for tmp in directory.glob("*.tmp"):
            try:
                tmp.unlink()
                removed += 1
            except OSError:
                pass
        files = sorted(p for p in directory.glob("*.json") if p.is_file())
        for stale in files[: max(0, len(files) - keep)]:
            try:
                stale.unlink()
                removed += 1
            except OSError:
                pass
        return removed


def _params_dict(params: Any) -> dict[str, Any]:
    """ExtractionParams -> plain dict (tolerates duck-typed fakes in tests)."""
    import dataclasses

    if dataclasses.is_dataclass(params) and not isinstance(params, type):
        return dataclasses.asdict(params)
    if isinstance(params, dict):
        return dict(params)
    return {"repr": repr(params)}
