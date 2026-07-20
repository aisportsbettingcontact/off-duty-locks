"""Tests for wnba_pipeline.validation.validate_and_normalize.

Self-contained: payloads are built by mutating a locally constructed valid
envelope (no dependency on fixtures/sanitized/*, which are owned by another
workstream). The team universe comes from the pinned reference set
fixtures/teams-2026-reference.json — team COUNT is never hardcoded.
"""

from __future__ import annotations

import copy
import json
import pathlib
import random

import pytest

from wnba_pipeline import contract, validation
from wnba_pipeline.contract import (
    ExpectedTeamSet,
    ExtractionParams,
    RawFetchResult,
    ValidationState,
)
from wnba_pipeline.validation import validate_and_normalize

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
REFERENCE = json.loads((FIXTURES_DIR / "teams-2026-reference.json").read_text())
REFERENCE_TEAMS: dict[str, str] = REFERENCE["teams"]

EXTRA_HEADERS = ["GP_RANK", "PTS_RANK", "CFID", "CFPARAMS"]
ALL_HEADERS = list(contract.SOURCE_HEADER_MAP) + EXTRA_HEADERS


# ---------------------------------------------------------------------------
# Envelope builders (valid by construction; tests mutate copies)
# ---------------------------------------------------------------------------

def _per_game_cells(i: int, team_id: str, name: str) -> dict[str, object]:
    gp = 7
    w = i % 8
    fga = round(70.0 + 0.5 * i, 1)
    fgm = round(fga * 0.45, 1)
    fg3a = round(24.0 + 0.3 * i, 1)
    fg3m = round(fg3a * 0.35, 1)
    fta = round(16.0 + 0.2 * i, 1)
    ftm = round(fta * 0.8, 1)
    oreb = round(7.0 + 0.1 * i, 1)
    dreb = round(25.0 + 0.2 * i, 1)
    return {
        "TEAM_ID": int(team_id),
        "TEAM_NAME": name,
        "GP": gp,
        "W": w,
        "L": gp - w,
        "W_PCT": round(w / gp, 3),
        "MIN": 40.2,
        "FGM": fgm,
        "FGA": fga,
        "FG_PCT": round(fgm / fga, 3),
        "FG3M": fg3m,
        "FG3A": fg3a,
        "FG3_PCT": round(fg3m / fg3a, 3),
        "FTM": ftm,
        "FTA": fta,
        "FT_PCT": round(ftm / fta, 3),
        "OREB": oreb,
        "DREB": dreb,
        "REB": round(oreb + dreb, 1),
        "AST": round(19.0 + 0.2 * i, 1),
        "TOV": round(13.5 - 0.1 * i, 1),
        "STL": 7.5,
        "BLK": 4.1,
        "BLKA": 3.6,
        "PF": 16.2,
        "PFD": 15.8,
        "PTS": round(2 * fgm + fg3m + ftm, 1),
        "PLUS_MINUS": round(3.5 - 0.5 * i, 1),  # negative for later teams
        "GP_RANK": i + 1,
        "PTS_RANK": i + 1,
        "CFID": 10,
        "CFPARAMS": f"{team_id},2026",
    }


def _totals_cells(i: int, team_id: str, name: str) -> dict[str, object]:
    gp = 7
    w = i % 8
    fga = 490 + 3 * i
    fgm = int(fga * 0.45)
    fg3a = 170 + i
    fg3m = int(fg3a * 0.35)
    fta = 112 + i
    ftm = int(fta * 0.8)
    oreb = 49 + i
    dreb = 175 + i
    return {
        "TEAM_ID": int(team_id),
        "TEAM_NAME": name,
        "GP": gp,
        "W": w,
        "L": gp - w,
        "W_PCT": round(w / gp, 3),
        "MIN": 1410,
        "FGM": fgm,
        "FGA": fga,
        "FG_PCT": round(fgm / fga, 3),
        "FG3M": fg3m,
        "FG3A": fg3a,
        "FG3_PCT": round(fg3m / fg3a, 3),
        "FTM": ftm,
        "FTA": fta,
        "FT_PCT": round(ftm / fta, 3),
        "OREB": oreb,
        "DREB": dreb,
        "REB": oreb + dreb,  # exact in Totals mode
        "AST": 135 + i,
        "TOV": 92,
        "STL": 52,
        "BLK": 29,
        "BLKA": 25,
        "PF": 113,
        "PFD": 111,
        "PTS": 2 * fgm + fg3m + ftm,
        "PLUS_MINUS": 20 - 4 * i,
        "GP_RANK": i + 1,
        "PTS_RANK": i + 1,
        "CFID": 10,
        "CFPARAMS": f"{team_id},2026",
    }


def build_rows(per_mode: str = "PerGame") -> list[dict[str, object]]:
    cells = _per_game_cells if per_mode == "PerGame" else _totals_cells
    return [
        cells(i, team_id, name)
        for i, (team_id, name) in enumerate(sorted(REFERENCE_TEAMS.items()))
    ]


def build_payload(
    rows: list[dict[str, object]] | None = None,
    headers: list[str] | None = None,
    per_mode: str = "PerGame",
) -> dict[str, object]:
    if rows is None:
        rows = build_rows(per_mode)
    if headers is None:
        headers = list(ALL_HEADERS)
    return {
        "resource": "leaguedashteamstats",
        "parameters": {"LeagueID": "10", "Season": "2026"},
        "resultSets": [
            {
                "name": "LeagueDashTeamStats",
                "headers": list(headers),
                "rowSet": [[row[h] for h in headers] for row in rows],
            }
        ],
    }


def make_raw(payload: dict[str, object], per_mode: str = "PerGame") -> RawFetchResult:
    params = ExtractionParams(per_mode=per_mode)
    blob = json.dumps(payload).encode("utf-8")
    return RawFetchResult(
        endpoint=contract.SOURCE_ENDPOINT,
        url=f"{contract.SOURCE_ENDPOINT}?LeagueID=10&Season=2026",
        params=params,
        payload=payload,  # type: ignore[arg-type]
        raw_bytes=blob,
        source_checksum=contract.sha256_hex(blob),
        fetched_at_utc="2026-07-17T12:00:00+00:00",
        http_status=200,
        request_count=1,
        retry_count=0,
        source_observed_at_utc="2026-07-17T11:59:58+00:00",
    )


def expected_set(teams: dict[str, str] | None = None) -> ExpectedTeamSet:
    teams = dict(REFERENCE_TEAMS if teams is None else teams)
    return ExpectedTeamSet(
        season="2026",
        source="test",
        source_url="test://expected",
        resolved_at_utc="2026-07-17T12:00:00+00:00",
        teams=teams,
        version_checksum=contract.canonical_json_checksum(
            {"season": "2026", "teams": teams}
        ),
    )


def codes(outcome) -> list[str]:
    return [f.code for f in outcome.failures]


def validate(payload, per_mode: str = "PerGame"):
    return validate_and_normalize(make_raw(payload, per_mode), expected_set())


def row_index_of(rows: list[dict[str, object]], team_name: str) -> int:
    return next(i for i, r in enumerate(rows) if r["TEAM_NAME"] == team_name)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

class TestSuccess:
    def test_valid_envelope_passes(self):
        outcome = validate(build_payload())
        assert outcome.state is ValidationState.PASSED
        assert outcome.failures == []
        assert outcome.snapshot is not None
        n = len(REFERENCE_TEAMS)  # never hardcoded
        assert outcome.expected_team_count == n
        assert outcome.actual_team_count == n
        assert outcome.valid_row_count == n
        assert outcome.rejected_row_count == 0
        snap = outcome.snapshot
        assert snap.row_count == n
        assert snap.team_count == n
        assert snap.validation_state is ValidationState.PASSED
        assert snap.freshness_state.value == "FRESH"
        assert snap.expected_teams_version == expected_set().version_checksum
        assert snap.per_mode == "PerGame"
        assert snap.extraction_key == ExtractionParams().extraction_key()

    def test_records_sorted_case_insensitive_by_name(self):
        rows = build_rows()
        random.Random(42).shuffle(rows)
        outcome = validate(build_payload(rows=rows))
        assert outcome.state is ValidationState.PASSED
        names = [r.team_name for r in outcome.snapshot.records]
        assert names == sorted(names, key=str.casefold)
        assert set(names) == set(REFERENCE_TEAMS.values())

    def test_row_order_does_not_change_normalized_checksum(self):
        baseline = validate(build_payload()).snapshot
        rows = build_rows()
        random.Random(7).shuffle(rows)
        shuffled = validate(build_payload(rows=rows)).snapshot
        assert shuffled.normalized_checksum() == baseline.normalized_checksum()

    def test_header_reorder_passes_and_matches_baseline(self):
        baseline = validate(build_payload()).snapshot
        reordered = list(reversed(ALL_HEADERS))
        outcome = validate(build_payload(headers=reordered))
        assert outcome.state is ValidationState.PASSED
        assert outcome.snapshot.normalized_checksum() == baseline.normalized_checksum()

    def test_added_unknown_column_goes_to_extras(self):
        rows = build_rows()
        for row in rows:
            row["NEW_OFFICIAL_METRIC"] = 1.23
        headers = ALL_HEADERS + ["NEW_OFFICIAL_METRIC"]
        outcome = validate(build_payload(rows=rows, headers=headers))
        assert outcome.state is ValidationState.PASSED
        for record in outcome.snapshot.records:
            assert record.extras["NEW_OFFICIAL_METRIC"] == 1.23

    def test_standard_extras_preserved_verbatim(self):
        outcome = validate(build_payload())
        record = outcome.snapshot.records[0]
        assert set(EXTRA_HEADERS) <= set(record.extras)
        assert isinstance(record.extras["CFPARAMS"], str)  # strings fine in extras
        assert record.extras["CFID"] == 10

    def test_stat_keys_are_canonical_fields(self):
        outcome = validate(build_payload())
        for record in outcome.snapshot.records:
            assert tuple(record.stats) == contract.CANONICAL_STAT_FIELDS

    def test_negative_plus_minus_is_allowed(self):
        rows = build_rows()
        assert any(row["PLUS_MINUS"] < 0 for row in rows)  # baseline has some
        assert validate(build_payload(rows=rows)).state is ValidationState.PASSED

    def test_integral_float_counts_normalized_to_int(self):
        rows = build_rows()
        rows[0]["GP"], rows[0]["W"], rows[0]["L"] = 7.0, 7.0, 0.0
        rows[0]["W_PCT"] = 1.0
        outcome = validate(build_payload(rows=rows))
        assert outcome.state is ValidationState.PASSED
        record = next(
            r for r in outcome.snapshot.records if r.team_id == str(rows[0]["TEAM_ID"])
        )
        for field_name in ("games_played", "wins", "losses"):
            assert isinstance(record.stats[field_name], int)
        assert record.stats["games_played"] == 7

    def test_result_set_found_at_any_index(self):
        payload = build_payload()
        payload["resultSets"].insert(
            0, {"name": "SomeOtherResultSet", "headers": ["X"], "rowSet": [[1]]}
        )
        assert validate(payload).state is ValidationState.PASSED

    def test_determinism_validate_twice_identical(self):
        payload = build_payload()
        first = validate(payload)
        second = validate(copy.deepcopy(payload))
        assert first.snapshot.normalized_checksum() == second.snapshot.normalized_checksum()
        assert first.snapshot.to_json_dict() == second.snapshot.to_json_dict()

    def test_snapshot_provenance_from_raw(self):
        raw = make_raw(build_payload())
        snap = validate_and_normalize(raw, expected_set()).snapshot
        assert snap.source_checksum == raw.source_checksum
        assert snap.source_url == raw.url
        assert snap.source_endpoint == raw.endpoint
        assert snap.fetched_at_utc == raw.fetched_at_utc
        assert snap.source_observed_at_utc == raw.source_observed_at_utc


# ---------------------------------------------------------------------------
# Null semantics (None is never zero)
# ---------------------------------------------------------------------------

class TestNullSemantics:
    def test_null_stat_stays_none_never_zero(self):
        rows = build_rows()
        rows[2]["BLKA"] = None
        outcome = validate(build_payload(rows=rows))
        assert outcome.state is ValidationState.PASSED
        record = next(
            r for r in outcome.snapshot.records if r.team_id == str(rows[2]["TEAM_ID"])
        )
        assert record.stats["blocked_attempts"] is None
        assert record.stats["blocked_attempts"] != 0

    def test_null_pct_skips_recompute(self):
        rows = build_rows()
        rows[1]["FG3_PCT"] = None  # attempts/makes still present
        outcome = validate(build_payload(rows=rows))
        assert outcome.state is ValidationState.PASSED
        record = next(
            r for r in outcome.snapshot.records if r.team_id == str(rows[1]["TEAM_ID"])
        )
        assert record.stats["three_point_pct"] is None

    @pytest.mark.parametrize("pct", [0.0, None])
    def test_zero_attempts_edge_never_crashes(self, pct):
        rows = build_rows()
        rows[0]["FG3A"] = 0.0
        rows[0]["FG3M"] = 0.0
        rows[0]["FG3_PCT"] = pct
        outcome = validate(build_payload(rows=rows))
        assert outcome.state is ValidationState.PASSED


# ---------------------------------------------------------------------------
# Structural failures
# ---------------------------------------------------------------------------

class TestStructuralFailures:
    def test_missing_result_set(self):
        payload = build_payload()
        payload["resultSets"][0]["name"] = "SomethingElse"
        outcome = validate(payload)
        assert outcome.state is ValidationState.FAILED
        assert codes(outcome) == [validation.MISSING_RESULT_SET]
        assert outcome.snapshot is None

    def test_header_row_width_mismatch(self):
        payload = build_payload()
        payload["resultSets"][0]["rowSet"][3] = payload["resultSets"][0]["rowSet"][3][:-1]
        outcome = validate(payload)
        assert outcome.state is ValidationState.FAILED
        assert validation.HEADER_ROW_WIDTH_MISMATCH in codes(outcome)
        assert outcome.rejected_row_count == 1
        assert outcome.valid_row_count == len(REFERENCE_TEAMS) - 1

    def test_missing_required_column(self):
        headers = [h for h in ALL_HEADERS if h != "FG_PCT"]
        outcome = validate(build_payload(headers=headers))
        assert outcome.state is ValidationState.FAILED
        assert codes(outcome) == [validation.MISSING_REQUIRED_COLUMN]
        assert "FG_PCT" in outcome.failures[0].message

    def test_empty_dataset_is_failure_not_fresh_zeros(self):
        payload = build_payload(rows=[])
        outcome = validate(payload)
        assert outcome.state is ValidationState.FAILED
        assert validation.EMPTY_DATASET in codes(outcome)
        # every expected team is also reported absent
        missing = [f for f in outcome.failures if f.code == validation.MISSING_EXPECTED_TEAM]
        assert len(missing) == len(REFERENCE_TEAMS)
        assert outcome.snapshot is None
        assert outcome.valid_row_count == 0
        assert outcome.rejected_row_count == 0


# ---------------------------------------------------------------------------
# Value-level failures
# ---------------------------------------------------------------------------

class TestValueFailures:
    def test_non_numeric_string_number(self):
        rows = build_rows()
        rows[0]["PTS"] = "12"  # numeric string is still a failure — no coercion
        outcome = validate(build_payload(rows=rows))
        assert outcome.state is ValidationState.FAILED
        assert validation.NON_NUMERIC_VALUE in codes(outcome)
        assert outcome.rejected_row_count == 1
        assert outcome.valid_row_count == len(REFERENCE_TEAMS) - 1

    def test_non_numeric_text(self):
        rows = build_rows()
        rows[4]["MIN"] = "abc"
        outcome = validate(build_payload(rows=rows))
        assert validation.NON_NUMERIC_VALUE in codes(outcome)

    def test_boolean_is_non_numeric(self):
        rows = build_rows()
        rows[0]["BLK"] = True
        outcome = validate(build_payload(rows=rows))
        assert validation.NON_NUMERIC_VALUE in codes(outcome)

    def test_pct_scale_violation(self):
        rows = build_rows()
        rows[0]["FG_PCT"] = 47.2  # percentage on 0-100 scale => wrong
        outcome = validate(build_payload(rows=rows))
        assert outcome.state is ValidationState.FAILED
        assert validation.PCT_SCALE_VIOLATION in codes(outcome)

    def test_w_pct_scale_violation(self):
        rows = build_rows()
        rows[0]["W_PCT"] = 85.7
        outcome = validate(build_payload(rows=rows))
        assert validation.PCT_SCALE_VIOLATION in codes(outcome)

    def test_negative_counting_stat(self):
        rows = build_rows()
        rows[5]["STL"] = -1.2
        outcome = validate(build_payload(rows=rows))
        assert outcome.state is ValidationState.FAILED
        assert codes(outcome) == [validation.NEGATIVE_COUNTING_STAT]
        assert outcome.failures[0].team_id == str(rows[5]["TEAM_ID"])


# ---------------------------------------------------------------------------
# Cross-field failures
# ---------------------------------------------------------------------------

class TestCrossFieldFailures:
    def test_wl_gp_mismatch(self):
        rows = build_rows()
        rows[0]["W"], rows[0]["L"], rows[0]["GP"] = 3, 3, 7
        rows[0]["W_PCT"] = round(3 / 7, 3)
        outcome = validate(build_payload(rows=rows))
        assert codes(outcome) == [validation.WL_GP_MISMATCH]

    def test_lastn_exceeded(self):
        rows = build_rows()
        rows[0]["GP"], rows[0]["W"], rows[0]["L"] = 8, 4, 4
        rows[0]["W_PCT"] = 0.5
        outcome = validate(build_payload(rows=rows))
        assert codes(outcome) == [validation.LASTN_EXCEEDED]

    def test_makes_exceed_attempts(self):
        rows = build_rows()
        rows[0]["FGM"] = rows[0]["FGA"] + 0.5
        rows[0]["FG_PCT"] = None  # isolate: no recompute/scale noise
        outcome = validate(build_payload(rows=rows))
        assert codes(outcome) == [validation.MAKES_EXCEED_ATTEMPTS]

    def test_makes_equal_attempts_ok(self):
        rows = build_rows()
        rows[0]["FTM"] = rows[0]["FTA"]
        rows[0]["FT_PCT"] = 1.0
        assert validate(build_payload(rows=rows)).state is ValidationState.PASSED

    def test_reb_reconcile_fail_per_game(self):
        rows = build_rows()
        rows[0]["REB"] = round(rows[0]["OREB"] + rows[0]["DREB"] + 0.2, 1)
        outcome = validate(build_payload(rows=rows))
        assert codes(outcome) == [validation.REB_RECONCILE_FAIL]

    def test_reb_within_per_game_tolerance_ok(self):
        rows = build_rows()
        rows[0]["REB"] = round(rows[0]["OREB"] + rows[0]["DREB"] + 0.1, 1)
        assert validate(build_payload(rows=rows)).state is ValidationState.PASSED

    def test_pct_recompute_fail_per_game(self):
        rows = build_rows()
        rows[0]["FG_PCT"] = round(rows[0]["FGM"] / rows[0]["FGA"] + 0.05, 3)
        outcome = validate(build_payload(rows=rows))
        assert codes(outcome) == [validation.PCT_RECOMPUTE_FAIL]

    def test_pct_within_per_game_tolerance_ok(self):
        rows = build_rows()
        rows[0]["FG_PCT"] = round(rows[0]["FGM"] / rows[0]["FGA"] + 0.015, 3)
        assert validate(build_payload(rows=rows)).state is ValidationState.PASSED

    def test_w_pct_recompute_fail(self):
        rows = build_rows()
        target = row_index_of(rows, "Chicago Sky")
        w, gp = rows[target]["W"], rows[target]["GP"]
        rows[target]["W_PCT"] = round(w / gp + 0.005, 3)  # beyond ±0.001
        outcome = validate(build_payload(rows=rows))
        assert codes(outcome) == [validation.PCT_RECOMPUTE_FAIL]


# ---------------------------------------------------------------------------
# Totals-mode strict tolerances
# ---------------------------------------------------------------------------

class TestTotalsMode:
    def test_valid_totals_passes(self):
        outcome = validate(build_payload(per_mode="Totals"), per_mode="Totals")
        assert outcome.state is ValidationState.PASSED
        assert outcome.snapshot.per_mode == "Totals"

    def test_totals_reb_must_be_exact(self):
        rows = build_rows("Totals")
        rows[0]["REB"] = rows[0]["OREB"] + rows[0]["DREB"] + 1  # ok in PerGame? no: Totals exact
        outcome = validate(build_payload(rows=rows, per_mode="Totals"), per_mode="Totals")
        assert codes(outcome) == [validation.REB_RECONCILE_FAIL]

    def test_totals_pct_tolerance_is_strict(self):
        rows = build_rows("Totals")
        true_pct = rows[0]["FGM"] / rows[0]["FGA"]
        rows[0]["FG_PCT"] = round(true_pct, 3) + 0.002  # beyond ±0.001
        outcome = validate(build_payload(rows=rows, per_mode="Totals"), per_mode="Totals")
        assert codes(outcome) == [validation.PCT_RECOMPUTE_FAIL]

    def test_totals_pct_within_tolerance_ok(self):
        rows = build_rows("Totals")
        rows[0]["FG_PCT"] = rows[0]["FGM"] / rows[0]["FGA"] + 0.0008  # within ±0.001
        outcome = validate(build_payload(rows=rows, per_mode="Totals"), per_mode="Totals")
        assert outcome.state is ValidationState.PASSED


# ---------------------------------------------------------------------------
# Duplicates and team coverage
# ---------------------------------------------------------------------------

class TestDuplicatesAndCoverage:
    def test_duplicate_team_id_and_record(self):
        rows = build_rows()
        rows.append(dict(rows[0]))  # exact duplicate row
        outcome = validate(build_payload(rows=rows))
        assert outcome.state is ValidationState.FAILED
        found = set(codes(outcome))
        assert validation.DUPLICATE_TEAM_ID in found
        assert validation.DUPLICATE_RECORD in found
        assert outcome.rejected_row_count == 2  # both involved rows implicated

    def test_duplicate_team_name_case_insensitive(self):
        rows = build_rows()
        a = row_index_of(rows, "Atlanta Dream")
        b = row_index_of(rows, "Chicago Sky")
        rows[b]["TEAM_NAME"] = "ATLANTA dream"  # different case, same name
        outcome = validate(build_payload(rows=rows))
        assert codes(outcome) == [validation.DUPLICATE_TEAM_NAME]
        assert outcome.rejected_row_count == 2
        assert a != b

    def test_unexpected_team(self):
        rows = build_rows()
        extra = _per_game_cells(len(rows), "9999999999", "Albuquerque Isotopes")
        rows.append(extra)
        outcome = validate(build_payload(rows=rows))
        assert codes(outcome) == [validation.UNEXPECTED_TEAM]
        assert outcome.failures[0].team_id == "9999999999"
        assert outcome.rejected_row_count == 1
        assert outcome.actual_team_count == len(REFERENCE_TEAMS) + 1

    def test_missing_expected_team(self):
        rows = build_rows()[:-1]  # drop one active team
        outcome = validate(build_payload(rows=rows))
        assert codes(outcome) == [validation.MISSING_EXPECTED_TEAM]
        assert outcome.rejected_row_count == 0  # dataset-scoped, no row implicated
        assert outcome.valid_row_count == len(REFERENCE_TEAMS) - 1
        assert outcome.actual_team_count == len(REFERENCE_TEAMS) - 1


# ---------------------------------------------------------------------------
# Failure aggregation
# ---------------------------------------------------------------------------

class TestCollectAll:
    def test_all_failures_collected_not_just_first(self):
        rows = build_rows()
        rows[0]["STL"] = -3.0                       # NEGATIVE_COUNTING_STAT
        rows[1]["W"] = rows[1]["W"] + 1             # WL_GP_MISMATCH (+ w_pct drift)
        rows[2]["PTS"] = "n/a"                      # NON_NUMERIC_VALUE
        rows.append(_per_game_cells(99, "8888888888", "Ghost Team"))  # UNEXPECTED_TEAM
        outcome = validate(build_payload(rows=rows))
        assert outcome.state is ValidationState.FAILED
        found = set(codes(outcome))
        assert {
            validation.NEGATIVE_COUNTING_STAT,
            validation.WL_GP_MISMATCH,
            validation.NON_NUMERIC_VALUE,
            validation.UNEXPECTED_TEAM,
        } <= found
        assert outcome.snapshot is None
        assert outcome.rejected_row_count == 4
        assert outcome.valid_row_count == len(rows) - 4

    def test_failure_serialization_shape(self):
        rows = build_rows()
        rows[0]["STL"] = -1.0
        outcome = validate(build_payload(rows=rows))
        blob = outcome.failures[0].to_json_dict()
        assert blob["code"] == validation.NEGATIVE_COUNTING_STAT
        assert blob["teamId"] == str(rows[0]["TEAM_ID"])
        assert "message" in blob
