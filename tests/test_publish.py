"""Serving-layer publish hook wiring (no live database).

Verifies run_once fires the injected publish callable exactly on accepted
snapshots (SUCCESS and SUCCESS_UNCHANGED), never on failure paths, and records
the outcome on the manifest without changing the exit code. Also verifies the
run-team-stats CLI drives BOTH the Last-7 and Year-to-Date splits.
"""

from __future__ import annotations

import json

from wnba_pipeline.contract import (
    EXIT_OK,
    EXIT_UPSTREAM_UNAVAILABLE,
    ExtractionParams,
    RunStatus,
    UpstreamUnavailable,
)
from wnba_pipeline.runner import run_once

from tests._builders import (
    make_expected_team_set,
    make_outcome_passed,
    make_raw,
    make_snapshot,
)

TEAMS = {"1611661319": "Las Vegas Aces", "1611661313": "New York Liberty"}


def _fakes(*, checksum="src-1"):
    expected = make_expected_team_set(TEAMS)
    snap = make_snapshot(teams=TEAMS, source_checksum=checksum)

    def fetch_fn(params):
        return make_raw(params=params, source_checksum=checksum)

    def resolve_fn(season):
        return expected

    def validate_fn(raw, exp):
        return make_outcome_passed(snap, exp)

    return fetch_fn, resolve_fn, validate_fn


def test_publish_called_on_success(tmp_path):
    fetch_fn, resolve_fn, validate_fn = _fakes()
    published = []
    manifest, code = run_once(
        ExtractionParams(), tmp_path,
        fetch_fn=fetch_fn, resolve_teams_fn=resolve_fn, validate_fn=validate_fn,
        publish_fn=lambda snap: published.append(snap) or len(snap.records),
    )
    assert code == EXIT_OK
    assert len(published) == 1
    assert manifest.publish_result == f"PUBLISHED:{len(TEAMS)}"


def test_publish_called_on_unchanged(tmp_path):
    fetch_fn, resolve_fn, validate_fn = _fakes(checksum="stable")
    calls = []
    run_once(ExtractionParams(), tmp_path, fetch_fn=fetch_fn,
             resolve_teams_fn=resolve_fn, validate_fn=validate_fn,
             publish_fn=lambda snap: calls.append(1))
    m2, _ = run_once(ExtractionParams(), tmp_path, fetch_fn=fetch_fn,
                     resolve_teams_fn=resolve_fn, validate_fn=validate_fn,
                     publish_fn=lambda snap: calls.append(1))
    assert m2.status is RunStatus.SUCCESS_UNCHANGED
    assert len(calls) == 2  # published on the initial AND the unchanged run


def test_publish_not_called_on_upstream_unavailable(tmp_path):
    _, resolve_fn, validate_fn = _fakes()
    calls = []

    def failing_fetch(params):
        raise UpstreamUnavailable("http_403_forbidden", http_status=403)

    manifest, code = run_once(
        ExtractionParams(), tmp_path, fetch_fn=failing_fetch,
        resolve_teams_fn=resolve_fn, validate_fn=validate_fn,
        publish_fn=lambda snap: calls.append(1))
    assert code == EXIT_UPSTREAM_UNAVAILABLE
    assert calls == []
    assert manifest.publish_result is None


def test_publish_failure_does_not_fail_the_run(tmp_path):
    fetch_fn, resolve_fn, validate_fn = _fakes()

    def boom(snap):
        raise RuntimeError("db down")

    manifest, code = run_once(
        ExtractionParams(), tmp_path, fetch_fn=fetch_fn,
        resolve_teams_fn=resolve_fn, validate_fn=validate_fn, publish_fn=boom)
    assert code == EXIT_OK  # file store is the source of truth; run still succeeds
    assert manifest.status is RunStatus.SUCCESS
    assert manifest.publish_result == "FAILED:RuntimeError"


def test_run_team_stats_cli_runs_both_splits(tmp_path, capsys):
    from wnba_pipeline.__main__ import main

    fixture = "fixtures/sanitized/leaguedashteamstats_2026_lastn7.json"
    rc = main(["run-team-stats", "--fixture", fixture,
               "--data-root", str(tmp_path), "--no-publish"])
    assert rc == EXIT_OK
    lines = capsys.readouterr().out.strip().splitlines()
    keys = {json.loads(line)["extractionKey"] for line in lines}
    assert any("lastn=7" in k for k in keys)   # Last 7 Games split
    assert any("lastn=0" in k for k in keys)   # Year-to-Date split
