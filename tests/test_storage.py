"""Storage-layer tests: atomicity, immutability, LKG protection, retention."""

from __future__ import annotations

import json

import pytest

from wnba_pipeline import contract, storage
from wnba_pipeline.contract import ExtractionParams, StorageError, ValidationFailure
from wnba_pipeline.storage import Store, decode_key, encode_key

from tests._builders import make_raw, make_snapshot

KEY = ExtractionParams().extraction_key()


def test_key_encoding_roundtrip():
    enc = encode_key(KEY)
    assert ":" not in enc
    assert decode_key(enc) == KEY


def test_save_raw_writes_sanitized_and_is_immutable(tmp_path):
    store = Store(tmp_path)
    raw = make_raw()
    path = store.save_raw("run-1", KEY, raw)
    doc = json.loads(path.read_text())
    assert doc["runId"] == "run-1"
    assert doc["sourceChecksum"] == raw.source_checksum
    assert doc["payload"] == raw.payload
    # No secrets in the raw record: sanitized URL + params only, no headers.
    text = path.read_text().lower()
    assert "cookie" not in text
    assert "authorization" not in text
    # Immutable: a second save for the same run id must refuse to overwrite.
    with pytest.raises(StorageError):
        store.save_raw("run-1", KEY, make_raw(source_checksum="different"))


def test_accept_snapshot_promotes_to_lkg(tmp_path):
    store = Store(tmp_path)
    snap = make_snapshot(source_checksum="abc")
    snap_path = store.accept_snapshot("run-1", KEY, snap)
    assert snap_path.exists()
    lkg, lkg_path = store.load_last_known_good(KEY)
    assert lkg is not None
    assert lkg_path == store.current_path(KEY)
    assert lkg["sourceChecksum"] == "abc"
    assert lkg["teamCount"] == snap.team_count


def test_accept_snapshot_refuses_duplicate_run(tmp_path):
    store = Store(tmp_path)
    store.accept_snapshot("run-1", KEY, make_snapshot(source_checksum="a"))
    with pytest.raises(StorageError):
        store.accept_snapshot("run-1", KEY, make_snapshot(source_checksum="b"))


def test_second_snapshot_updates_lkg(tmp_path):
    store = Store(tmp_path)
    store.accept_snapshot("run-1", KEY, make_snapshot(source_checksum="v1"))
    store.accept_snapshot("run-2", KEY, make_snapshot(source_checksum="v2"))
    lkg, _ = store.load_last_known_good(KEY)
    assert lkg["sourceChecksum"] == "v2"


def test_missing_lkg_is_none_never_empty_dataset(tmp_path):
    store = Store(tmp_path)
    lkg, path = store.load_last_known_good(KEY)
    assert lkg is None and path is None


def test_corrupt_lkg_raises_not_silently_absent(tmp_path):
    store = Store(tmp_path)
    p = store.current_path(KEY)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ this is not json")
    with pytest.raises(StorageError):
        store.load_last_known_good(KEY)


def test_interrupted_lkg_write_leaves_previous_intact(tmp_path, monkeypatch):
    """Crash between writing the snapshot file and replacing current/ must
    leave the previous LKG byte-identical and loadable."""
    store = Store(tmp_path)
    store.accept_snapshot("run-1", KEY, make_snapshot(source_checksum="good"))
    before = store.current_path(KEY).read_bytes()

    real_replace = storage.os.replace
    current_target = str(store.current_path(KEY))

    def failing_replace(src, dst, *a, **k):
        if str(dst) == current_target:
            raise OSError("simulated crash before LKG swap")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(storage.os, "replace", failing_replace)
    with pytest.raises(StorageError):
        store.accept_snapshot("run-2", KEY, make_snapshot(source_checksum="bad"))
    monkeypatch.undo()

    after = store.current_path(KEY).read_bytes()
    assert after == before  # previous LKG untouched
    lkg, _ = store.load_last_known_good(KEY)
    assert lkg["sourceChecksum"] == "good"
    # No orphaned temp files linger where readers would see them.
    assert not list(store.current_dir.glob("*.tmp"))


def test_quarantine_preserves_lkg_byte_identical(tmp_path):
    store = Store(tmp_path)
    store.accept_snapshot("run-1", KEY, make_snapshot(source_checksum="good"))
    before = store.current_path(KEY).read_bytes()
    qpath = store.quarantine(
        "run-2", KEY, {"resultSets": []},
        [ValidationFailure("EMPTY_DATASET", "no rows")],
        {"season": "2026"},
    )
    assert qpath.exists()
    qdoc = json.loads(qpath.read_text())
    assert qdoc["failures"][0]["code"] == "EMPTY_DATASET"
    assert store.current_path(KEY).read_bytes() == before  # LKG untouched


def test_prune_keeps_current_and_newest_n(tmp_path):
    store = Store(tmp_path)
    # 5 snapshots with sortable run ids; current/ tracks the last.
    for i in range(5):
        rid = f"2026070{i}T000000Z-{i:08x}"
        store.accept_snapshot(rid, KEY, make_snapshot(source_checksum=f"c{i}"))
    deleted = store.prune(KEY, keep_snapshots=2, keep_raw=2,
                          keep_quarantine=2, keep_manifests=2)
    remaining = sorted((store.snapshots_dir / encode_key(KEY)).glob("*.json"))
    assert len(remaining) == 2
    assert deleted["snapshots"] == 3
    # current/ (LKG) is never pruned.
    assert store.current_path(KEY).exists()
    lkg, _ = store.load_last_known_good(KEY)
    assert lkg["sourceChecksum"] == "c4"


def test_prune_removes_orphan_tmp_files(tmp_path):
    store = Store(tmp_path)
    d = store.snapshots_dir / encode_key(KEY)
    d.mkdir(parents=True, exist_ok=True)
    (d / "orphan.json.tmp").write_text("partial")
    (d / "20260701T000000Z-1.json").write_text("{}")
    store.prune(KEY, keep_snapshots=50)
    assert not list(d.glob("*.tmp"))


def test_team_set_versioning_keeps_history(tmp_path):
    store = Store(tmp_path)
    from tests._builders import make_expected_team_set
    ts1 = make_expected_team_set({"1": "Aces", "2": "Lynx"})
    ts2 = make_expected_team_set({"1": "Aces", "2": "Lynx", "3": "Liberty"})
    store.save_team_set(ts1)
    store.save_team_set(ts2)
    latest = store.load_latest_team_set("2026")
    assert latest is not None
    assert latest.team_count == 3
    assert len(store.list_team_set_versions("2026")) == 2  # both versions kept
    # Idempotent re-save of an existing version does not add a new history file.
    store.save_team_set(ts2)
    assert len(store.list_team_set_versions("2026")) == 2


def test_write_manifest_roundtrip(tmp_path):
    from wnba_pipeline.contract import (
        FreshnessState,
        RunManifest,
        RunStatus,
        ValidationState,
    )
    store = Store(tmp_path)
    manifest = RunManifest(
        run_id="run-xyz", status=RunStatus.SUCCESS, extraction_key=KEY,
        params={"season": "2026"}, started_at_utc="2026-07-20T00:00:00Z",
        ended_at_utc="2026-07-20T00:00:01Z", duration_seconds=1.0,
        request_count=1, response_status=200, retry_count=0,
        raw_row_count=15, valid_row_count=15, rejected_row_count=0,
        expected_team_count=15, actual_team_count=15,
        source_checksum="c", normalized_checksum="n",
        freshness_state=FreshnessState.FRESH, validation_state=ValidationState.PASSED,
    )
    p = store.write_manifest(manifest)
    doc = json.loads(p.read_text())
    assert doc["runId"] == "run-xyz"
    assert doc["status"] == "SUCCESS"
