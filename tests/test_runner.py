"""Runner tests: every outcome path, manifest completeness, exit codes.

Collaborators are injected (fake fetch / resolve / validate) so these tests
exercise the orchestration contract without network or the real validator —
except the explicit offline fixture-mode integration test, which drives the
real teams+validation modules end to end.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from wnba_pipeline import contract
from wnba_pipeline.contract import (
    EXIT_CONFIG_ERROR,
    EXIT_LOCK_HELD,
    EXIT_OK,
    EXIT_UPSTREAM_UNAVAILABLE,
    EXIT_VALIDATION_FAILED,
    ConfigError,
    ExtractionParams,
    FreshnessState,
    RunStatus,
    UpstreamUnavailable,
    ValidationState,
)
from wnba_pipeline.locking import RunLock
from wnba_pipeline.runner import run_once
from wnba_pipeline.storage import Store

from tests._builders import (
    make_expected_team_set,
    make_outcome_failed,
    make_outcome_passed,
    make_raw,
    make_snapshot,
)

TEAMS = {"1611661319": "Las Vegas Aces", "1611661324": "Minnesota Lynx",
         "1611661313": "New York Liberty"}


def _payload_with_rows(n: int) -> dict:
    return {"resultSets": [{"name": "LeagueDashTeamStats", "headers": ["TEAM_ID"],
                            "rowSet": [[i] for i in range(n)]}]}


def _fakes(*, checksum="src-1", outcome_kind="pass", fail_codes=None):
    expected = make_expected_team_set(TEAMS)
    snap = make_snapshot(teams=TEAMS, source_checksum=checksum)

    def fetch_fn(params):
        return make_raw(_payload_with_rows(3), params=params, source_checksum=checksum)

    def resolve_fn(season):
        return expected

    def validate_fn(raw, exp):
        if outcome_kind == "pass":
            return make_outcome_passed(snap, exp)
        return make_outcome_failed(exp, codes=fail_codes)

    return fetch_fn, resolve_fn, validate_fn, expected, snap


def test_success_path_manifest_is_complete(tmp_path):
    fetch_fn, resolve_fn, validate_fn, expected, snap = _fakes()
    manifest, code = run_once(
        ExtractionParams(), tmp_path,
        fetch_fn=fetch_fn, resolve_teams_fn=resolve_fn, validate_fn=validate_fn)
    assert code == EXIT_OK
    assert manifest.status is RunStatus.SUCCESS
    # Every observability field populated.
    assert manifest.run_id
    assert manifest.request_count == 1
    assert manifest.response_status == 200
    assert manifest.retry_count == 0
    assert manifest.raw_row_count == 3
    assert manifest.valid_row_count == 3
    assert manifest.rejected_row_count == 0
    assert manifest.expected_team_count == 3
    assert manifest.actual_team_count == 3
    assert manifest.source_checksum == "src-1"
    assert manifest.normalized_checksum
    assert manifest.freshness_state is FreshnessState.FRESH
    assert manifest.validation_state is ValidationState.PASSED
    assert manifest.storage_result == "SNAPSHOT_ACCEPTED"
    assert manifest.last_known_good_preserved is True
    assert manifest.failure_reason is None
    # Persisted: manifest file + snapshot + LKG all on disk.
    store = Store(tmp_path)
    assert store.manifest_path(manifest.run_id).exists()
    lkg, _ = store.load_last_known_good(ExtractionParams().extraction_key())
    assert lkg["sourceChecksum"] == "src-1"


def test_manifest_emitted_as_single_json_line(tmp_path, capsys):
    fetch_fn, resolve_fn, validate_fn, *_ = _fakes()
    run_once(ExtractionParams(), tmp_path,
             fetch_fn=fetch_fn, resolve_teams_fn=resolve_fn, validate_fn=validate_fn)
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1  # exactly one line on stdout
    doc = json.loads(out[0])
    assert doc["status"] == "SUCCESS"


def test_idempotent_rerun_is_unchanged(tmp_path):
    fetch_fn, resolve_fn, validate_fn, *_ = _fakes(checksum="stable")
    m1, c1 = run_once(ExtractionParams(), tmp_path, fetch_fn=fetch_fn,
                      resolve_teams_fn=resolve_fn, validate_fn=validate_fn)
    m2, c2 = run_once(ExtractionParams(), tmp_path, fetch_fn=fetch_fn,
                      resolve_teams_fn=resolve_fn, validate_fn=validate_fn)
    assert c1 == EXIT_OK and c2 == EXIT_OK
    assert m1.status is RunStatus.SUCCESS
    assert m2.status is RunStatus.SUCCESS_UNCHANGED
    assert m2.storage_result == "UNCHANGED_LKG_KEPT"
    # Idempotent: exactly one snapshot file was written.
    store = Store(tmp_path)
    from wnba_pipeline.storage import encode_key
    snaps = list((store.snapshots_dir / encode_key(ExtractionParams().extraction_key())).glob("*.json"))
    assert len(snaps) == 1


def test_upstream_unavailable_preserves_lkg(tmp_path):
    # First a good run to establish LKG.
    fetch_fn, resolve_fn, validate_fn, *_ = _fakes(checksum="good")
    run_once(ExtractionParams(), tmp_path, fetch_fn=fetch_fn,
             resolve_teams_fn=resolve_fn, validate_fn=validate_fn)
    store = Store(tmp_path)
    key = ExtractionParams().extraction_key()
    before = store.current_path(key).read_bytes()

    def failing_fetch(params):
        raise UpstreamUnavailable("http_429_rate_limited", http_status=429,
                                  request_count=5, retry_count=4)

    # Pin "now" near the LKG's fetch time so freshness is deterministic
    # regardless of the real wall clock: the LKG is recent, hence FRESH.
    manifest, code = run_once(ExtractionParams(), tmp_path, fetch_fn=failing_fetch,
                              resolve_teams_fn=resolve_fn, validate_fn=validate_fn,
                              now_fn=lambda: datetime(2026, 7, 20, 12, 30, tzinfo=timezone.utc))
    assert code == EXIT_UPSTREAM_UNAVAILABLE
    assert manifest.status is RunStatus.UPSTREAM_UNAVAILABLE
    assert manifest.failure_reason == "http_429_rate_limited"
    assert manifest.request_count == 5 and manifest.retry_count == 4
    assert manifest.last_known_good_preserved is True
    # LKG present and byte-identical; freshness reflects the LKG, not a fake zero.
    assert store.current_path(key).read_bytes() == before
    assert manifest.freshness_state is FreshnessState.FRESH


def test_upstream_unavailable_without_lkg_is_missing(tmp_path):
    _, resolve_fn, validate_fn, *_ = _fakes()

    def failing_fetch(params):
        raise UpstreamUnavailable("http_403_forbidden", http_status=403,
                                  request_count=1, retry_count=0)

    manifest, code = run_once(ExtractionParams(), tmp_path, fetch_fn=failing_fetch,
                              resolve_teams_fn=resolve_fn, validate_fn=validate_fn)
    assert code == EXIT_UPSTREAM_UNAVAILABLE
    assert manifest.freshness_state is FreshnessState.MISSING  # no LKG, not zero


def test_validation_failure_quarantines_and_preserves_lkg(tmp_path):
    # Establish an LKG first.
    fetch_fn, resolve_fn, validate_fn, *_ = _fakes(checksum="good")
    run_once(ExtractionParams(), tmp_path, fetch_fn=fetch_fn,
             resolve_teams_fn=resolve_fn, validate_fn=validate_fn)
    store = Store(tmp_path)
    key = ExtractionParams().extraction_key()
    before = store.current_path(key).read_bytes()

    # Now a candidate that fails validation.
    bad_fetch, _, bad_validate, _, _ = _fakes(
        checksum="bad", outcome_kind="fail", fail_codes=["MAKES_EXCEED_ATTEMPTS", "DUPLICATE_TEAM_ID"])
    manifest, code = run_once(ExtractionParams(), tmp_path, fetch_fn=bad_fetch,
                              resolve_teams_fn=resolve_fn, validate_fn=bad_validate)
    assert code == EXIT_VALIDATION_FAILED
    assert manifest.status is RunStatus.VALIDATION_FAILED
    assert manifest.storage_result == "QUARANTINED"
    assert manifest.last_known_good_preserved is True
    assert "MAKES_EXCEED_ATTEMPTS" in manifest.failure_reason
    # LKG untouched; candidate raw was still preserved; quarantine written.
    assert store.current_path(key).read_bytes() == before
    from wnba_pipeline.storage import encode_key
    enc = encode_key(key)
    assert list((store.quarantine_dir / enc).glob("*.json"))
    assert len(list((store.raw_dir / enc).glob("*.json"))) == 2  # both runs' raw kept


def test_lock_held_rejects_second_run(tmp_path):
    key = ExtractionParams().extraction_key()
    fetch_fn, resolve_fn, validate_fn, *_ = _fakes()
    # Hold the lock with a separate, fresh owner for the duration of the run.
    holder = RunLock(tmp_path, "other-run", max_age_seconds=3600)
    holder.acquire()
    try:
        manifest, code = run_once(ExtractionParams(), tmp_path, fetch_fn=fetch_fn,
                                  resolve_teams_fn=resolve_fn, validate_fn=validate_fn)
        assert code == EXIT_LOCK_HELD
        assert manifest.status is RunStatus.LOCK_HELD
        # A manifest is still written for the rejected run.
        assert Store(tmp_path).manifest_path(manifest.run_id).exists()
    finally:
        holder.release()


def test_config_error_when_teams_unresolvable(tmp_path):
    fetch_fn, _, validate_fn, *_ = _fakes()

    def bad_resolve(season):
        raise ConfigError("expected-team set unavailable from all sources")

    manifest, code = run_once(ExtractionParams(), tmp_path, fetch_fn=fetch_fn,
                              resolve_teams_fn=bad_resolve, validate_fn=validate_fn)
    assert code == EXIT_CONFIG_ERROR
    assert manifest.status is RunStatus.CONFIG_ERROR


def test_internal_error_releases_lock(tmp_path):
    _, resolve_fn, _, *_ = _fakes()

    def exploding_fetch(params):
        raise RuntimeError("unexpected boom")

    manifest, code = run_once(ExtractionParams(), tmp_path, fetch_fn=exploding_fetch,
                              resolve_teams_fn=resolve_fn,
                              validate_fn=lambda r, e: None)
    assert code == contract.EXIT_INTERNAL_ERROR
    assert manifest.status is RunStatus.INTERNAL_ERROR
    assert "RuntimeError" in manifest.failure_reason
    # Lock was released despite the crash: a fresh run can acquire immediately.
    follow = RunLock(tmp_path, "next", max_age_seconds=3600)
    follow.acquire()
    follow.release()


# --- offline fixture-mode integration (real teams + validation modules) -------

FIXTURE = "fixtures/sanitized/leaguedashteamstats_2026_lastn7.json"


def test_offline_fixture_mode_end_to_end(tmp_path):
    manifest, code = run_once(ExtractionParams(), tmp_path, fixture_path=FIXTURE)
    assert code == EXIT_OK
    assert manifest.status is RunStatus.SUCCESS
    assert manifest.validation_state is ValidationState.PASSED
    assert manifest.actual_team_count == manifest.expected_team_count
    assert manifest.actual_team_count > 0  # real team universe, not hardcoded


def test_offline_empty_fixture_quarantines(tmp_path):
    manifest, code = run_once(
        ExtractionParams(), tmp_path,
        fixture_path="fixtures/sanitized/leaguedashteamstats_offseason_empty.json")
    assert code == EXIT_VALIDATION_FAILED
    assert manifest.status is RunStatus.VALIDATION_FAILED
    # Empty in-season response is a failure, never a fresh all-zero dataset.
    assert any("EMPTY" in c or "MISSING_EXPECTED_TEAM" in c
               for c in manifest.failure_reason.split(": ")[-1].split(", "))


# --- CLI passthrough ----------------------------------------------------------

def test_cli_run_and_status_exit_codes(tmp_path, capsys):
    from wnba_pipeline.__main__ import main
    rc = main(["run", "--fixture", FIXTURE, "--data-root", str(tmp_path)])
    assert rc == EXIT_OK
    capsys.readouterr()
    rc_status = main(["status", "--data-root", str(tmp_path)])
    assert rc_status == EXIT_OK
    status_doc = json.loads(capsys.readouterr().out)
    assert status_doc["freshnessState"] == "FRESH"
    assert status_doc["lastKnownGood"]["teamCount"] > 0


def test_cli_status_missing_is_not_error(tmp_path, capsys):
    from wnba_pipeline.__main__ import main
    rc = main(["status", "--data-root", str(tmp_path)])
    assert rc == EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    assert doc["freshnessState"] == "MISSING"
    assert doc["lastKnownGood"] is None
