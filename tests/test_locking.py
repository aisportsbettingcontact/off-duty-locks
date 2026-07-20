"""Overlap-lock tests: single active run, stale takeover, owner-safe release."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from wnba_pipeline.contract import LockHeld
from wnba_pipeline.locking import RunLock


def _clock(start: datetime):
    state = {"now": start}

    def now_fn() -> datetime:
        return state["now"]

    return state, now_fn


BASE = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


def test_acquire_writes_holder_without_secrets(tmp_path):
    lock = RunLock(tmp_path, "run-1")
    lock.acquire()
    try:
        holder = lock.read_holder()
        assert holder["runId"] == "run-1"
        assert set(holder) == {"pid", "acquiredAtUtc", "runId"}  # no secrets
    finally:
        lock.release()
    assert not lock.lock_path.exists()


def test_second_acquire_is_rejected(tmp_path):
    a = RunLock(tmp_path, "run-a")
    b = RunLock(tmp_path, "run-b")
    a.acquire()
    try:
        with pytest.raises(LockHeld):
            b.acquire()
    finally:
        a.release()
    # After release, a fresh run can acquire.
    b.acquire()
    b.release()


def test_context_manager_releases_on_exception(tmp_path):
    lock = RunLock(tmp_path, "run-1")
    with pytest.raises(ValueError):
        with lock:
            assert lock.lock_path.exists()
            raise ValueError("boom")
    assert not lock.lock_path.exists()  # released in __exit__


def test_stale_lock_is_taken_over(tmp_path):
    state, now_fn = _clock(BASE)
    old = RunLock(tmp_path, "run-old", max_age_seconds=3600, now_fn=now_fn)
    old.acquire()  # writes acquiredAtUtc = BASE
    # Advance clock past the staleness window; a new run takes over.
    state["now"] = BASE + timedelta(seconds=3601)
    new = RunLock(tmp_path, "run-new", max_age_seconds=3600, now_fn=now_fn)
    new.acquire()
    holder = new.read_holder()
    assert holder["runId"] == "run-new"
    new.release()


def test_fresh_lock_not_taken_over(tmp_path):
    state, now_fn = _clock(BASE)
    old = RunLock(tmp_path, "run-old", max_age_seconds=3600, now_fn=now_fn)
    old.acquire()
    state["now"] = BASE + timedelta(seconds=100)  # still fresh
    new = RunLock(tmp_path, "run-new", max_age_seconds=3600, now_fn=now_fn)
    with pytest.raises(LockHeld):
        new.acquire()
    old.release()


def test_release_does_not_delete_new_owners_lock(tmp_path):
    """After a stale takeover, the original owner's release must not remove the
    new owner's lock."""
    state, now_fn = _clock(BASE)
    old = RunLock(tmp_path, "run-old", max_age_seconds=3600, now_fn=now_fn)
    old.acquire()
    state["now"] = BASE + timedelta(seconds=3601)
    new = RunLock(tmp_path, "run-new", max_age_seconds=3600, now_fn=now_fn)
    new.acquire()
    # Old owner tries to release; its lock is gone, replaced by run-new's.
    old.release()
    assert new.lock_path.exists()
    assert new.read_holder()["runId"] == "run-new"
    new.release()


def test_corrupt_lock_ages_out_via_mtime(tmp_path):
    lock_path = tmp_path / ".lock"
    tmp_path.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("garbage-not-json")
    # A corrupt lock must still be takeover-able (falls back to mtime), never
    # wedge the pipeline forever. Use a zero max age so it is immediately stale.
    new = RunLock(tmp_path, "run-new", max_age_seconds=0)
    new.acquire()
    assert new.read_holder()["runId"] == "run-new"
    new.release()
