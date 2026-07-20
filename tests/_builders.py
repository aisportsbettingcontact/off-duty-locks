"""Shared factory helpers for building frozen-contract objects in tests.

Kept separate from ``conftest.py`` (which is orchestrator-owned) so the ops and
QA suites can construct ``Snapshot`` / ``RawFetchResult`` / ``ExpectedTeamSet``
/ ``ValidationOutcome`` values without depending on any single implementation
module's internal helpers.
"""

from __future__ import annotations

import json
from typing import Any

from wnba_pipeline import contract
from wnba_pipeline.contract import (
    CANONICAL_STAT_FIELDS,
    ExpectedTeamSet,
    ExtractionParams,
    FreshnessState,
    RawFetchResult,
    Snapshot,
    TeamRecord,
    ValidationFailure,
    ValidationOutcome,
    ValidationState,
    canonical_json_checksum,
    sha256_hex,
)

EXAMPLE_TEAMS: dict[str, str] = {
    "1611661319": "Las Vegas Aces",
    "1611661324": "Minnesota Lynx",
    "1611661313": "New York Liberty",
}


def make_expected_team_set(teams: dict[str, str] | None = None,
                           season: str = "2026") -> ExpectedTeamSet:
    teams = dict(teams if teams is not None else EXAMPLE_TEAMS)
    return ExpectedTeamSet(
        season=season,
        source="test-fixture",
        source_url="file:///test",
        resolved_at_utc="2026-07-20T00:00:00Z",
        teams=teams,
        version_checksum=canonical_json_checksum({"season": season, "teams": teams}),
    )


def make_team_record(team_id: str, name: str, *,
                     stats: dict[str, Any] | None = None,
                     extras: dict[str, Any] | None = None) -> TeamRecord:
    base = {f: 0 for f in CANONICAL_STAT_FIELDS}
    base.update({
        "games_played": 5, "wins": 3, "losses": 2, "win_pct": 0.6,
        "minutes": 40.0, "points": 82.4, "plus_minus": 1.5,
        "field_goals_made": 30.0, "field_goals_attempted": 68.0, "field_goal_pct": 0.441,
    })
    if stats:
        base.update(stats)
    return TeamRecord(team_id=team_id, team_name=name, stats=base,
                      extras=dict(extras or {}))


def make_snapshot(*, teams: dict[str, str] | None = None,
                  source_checksum: str = "checksum-a",
                  season: str = "2026") -> Snapshot:
    teams = dict(teams if teams is not None else EXAMPLE_TEAMS)
    records = [make_team_record(tid, name) for tid, name in sorted(
        teams.items(), key=lambda kv: (kv[1].lower(), kv[0]))]
    params = ExtractionParams(season=season)
    return Snapshot(
        source=contract.SOURCE,
        source_url=contract.SOURCE_PAGE_URL,
        source_endpoint=contract.SOURCE_ENDPOINT,
        season=season,
        season_type="Regular Season",
        last_n_games=7,
        sort_field="TEAM_NAME",
        sort_direction="asc",
        fetched_at_utc="2026-07-20T12:00:00Z",
        source_observed_at_utc=None,
        schema_version=contract.SCHEMA_VERSION,
        source_checksum=source_checksum,
        row_count=len(records),
        team_count=len(records),
        freshness_state=FreshnessState.FRESH,
        validation_state=ValidationState.PASSED,
        records=records,
        extraction_key=params.extraction_key(),
        per_mode="PerGame",
        expected_teams_version=canonical_json_checksum(
            {"season": season, "teams": teams}),
    )


def make_raw(payload: dict[str, Any] | None = None, *,
             params: ExtractionParams | None = None,
             source_checksum: str | None = None,
             http_status: int = 200) -> RawFetchResult:
    params = params or ExtractionParams()
    payload = payload if payload is not None else {"resultSets": [
        {"name": "LeagueDashTeamStats", "headers": [], "rowSet": []}]}
    raw_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    return RawFetchResult(
        endpoint=contract.SOURCE_ENDPOINT,
        url=contract.SOURCE_ENDPOINT + "?Season=2026",
        params=params,
        payload=payload,
        raw_bytes=raw_bytes,
        source_checksum=source_checksum or sha256_hex(raw_bytes),
        fetched_at_utc="2026-07-20T12:00:00Z",
        http_status=http_status,
        request_count=1,
        retry_count=0,
        source_observed_at_utc=None,
    )


def make_outcome_passed(snapshot: Snapshot,
                        expected: ExpectedTeamSet) -> ValidationOutcome:
    return ValidationOutcome(
        state=ValidationState.PASSED,
        failures=[],
        snapshot=snapshot,
        expected_team_count=expected.team_count,
        actual_team_count=snapshot.team_count,
        valid_row_count=snapshot.row_count,
        rejected_row_count=0,
    )


def make_outcome_failed(expected: ExpectedTeamSet, *,
                        codes: list[str] | None = None) -> ValidationOutcome:
    codes = codes or ["MAKES_EXCEED_ATTEMPTS"]
    failures = [ValidationFailure(code=c, message=f"synthetic {c}") for c in codes]
    return ValidationOutcome(
        state=ValidationState.FAILED,
        failures=failures,
        snapshot=None,
        expected_team_count=expected.team_count,
        actual_team_count=0,
        valid_row_count=0,
        rejected_row_count=1,
    )
