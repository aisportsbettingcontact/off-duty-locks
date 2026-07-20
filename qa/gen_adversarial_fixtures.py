#!/usr/bin/env python3
"""Generate the adversarial challenge fixtures under ``fixtures/adversarial/``.

Each fixture is derived by a single, well-understood mutation of the known-good
sanitized envelope (``fixtures/sanitized/leaguedashteamstats_2026_lastn7.json``)
so the challenge is obvious and the rest of the payload stays internally
consistent. Every emitted fixture carries ``_provenance.synthetic = true`` and a
``challenge`` tag naming the scenario. Idempotent: re-running overwrites.

Run: ``python3 qa/gen_adversarial_fixtures.py`` (offline; no network).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASE = REPO / "fixtures" / "sanitized" / "leaguedashteamstats_2026_lastn7.json"
OUT = REPO / "fixtures" / "adversarial"

UNKNOWN_TEAM_ID = 9999999999  # deliberately not in the 2026 reference set


def _load_base() -> dict:
    return json.loads(BASE.read_text(encoding="utf-8"))


def _rs(env: dict) -> dict:
    return env["resultSets"][0]


def _col(env: dict, name: str) -> int:
    return _rs(env)["headers"].index(name)


def _emit(name: str, env: dict, challenge: str, note: str = "") -> None:
    env = copy.deepcopy(env)
    env["_provenance"] = {
        "synthetic": True,
        "capturedAtUtc": None,
        "describedBy": "docs/source-contract.md",
        "challenge": challenge,
        "notes": note or f"Adversarial fixture for the '{challenge}' scenario, "
        "derived by a single mutation of the known-good sanitized envelope.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(env, indent=2) + "\n", encoding="utf-8")


def generate() -> list[str]:
    base = _load_base()
    written: list[str] = []

    def emit(name, env, challenge, note=""):
        _emit(name, env, challenge, note)
        written.append(name)

    # ---- PASS scenarios (must still produce a valid snapshot) --------------

    # changed_header_order: reverse header order and permute every row to match.
    env = copy.deepcopy(base)
    rs = _rs(env)
    order = list(range(len(rs["headers"])))[::-1]
    rs["headers"] = [rs["headers"][i] for i in order]
    rs["rowSet"] = [[row[i] for i in order] for row in rs["rowSet"]]
    emit("changed_header_order.json", env, "changed_header_order",
         "Valid data with headers reversed; lookup-by-name must still pass.")

    # added_column: an extra official column the contract doesn't map.
    env = copy.deepcopy(base)
    rs = _rs(env)
    rs["headers"].append("EXTRA_OFFICIAL_STAT")
    for i, row in enumerate(rs["rowSet"]):
        row.append(round(1.0 + i, 3))
    emit("added_column.json", env, "added_column",
         "Extra official column must be preserved in extras, not rejected.")

    # null_percentage: FG_PCT null with FGA > 0 -> stays null, no recompute.
    env = copy.deepcopy(base)
    rs = _rs(env)
    rs["rowSet"][0][_col(env, "FG_PCT")] = None
    emit("null_percentage.json", env, "null_percentage",
         "Null percentage with attempts>0 must stay null (never 0), and pass.")

    # short_season: fewer than seven completed games is legal.
    env = copy.deepcopy(base)
    rs = _rs(env)
    gp, w, l, wp = (_col(env, c) for c in ("GP", "W", "L", "W_PCT"))
    for row in rs["rowSet"]:
        row[gp], row[w], row[l], row[wp] = 3, 2, 1, 0.667
    emit("short_season.json", env, "short_season",
         "All teams GP<=3 with W+L=GP; must pass (early season).")

    # ---- FAIL scenarios (each isolates a validation code) ------------------

    # empty_response / offseason_empty: headers intact, zero rows.
    env = copy.deepcopy(base)
    _rs(env)["rowSet"] = []
    emit("empty_response.json", env, "empty_response",
         "In-season empty dataset -> EMPTY_DATASET (never fresh zeros).")
    env = copy.deepcopy(base)
    _rs(env)["rowSet"] = []
    emit("offseason_empty.json", env, "offseason_empty",
         "Offseason empty payload; runner month-gates so this shouldn't alert.")

    # partial_response: only some expected teams present.
    env = copy.deepcopy(base)
    _rs(env)["rowSet"] = _rs(env)["rowSet"][:8]
    emit("partial_response.json", env, "partial_response",
         "Half the teams -> MISSING_EXPECTED_TEAM.")

    # missing_result_set: the LeagueDashTeamStats set renamed away.
    env = copy.deepcopy(base)
    _rs(env)["name"] = "SomethingElse"
    emit("missing_result_set.json", env, "missing_result_set",
         "No LeagueDashTeamStats result set -> MISSING_RESULT_SET.")

    # removed_required_column: drop PTS from headers and every row.
    env = copy.deepcopy(base)
    rs = _rs(env)
    idx = rs["headers"].index("PTS")
    rs["headers"].pop(idx)
    for row in rs["rowSet"]:
        row.pop(idx)
    emit("removed_required_column.json", env, "removed_required_column",
         "PTS column removed -> MISSING_REQUIRED_COLUMN.")

    # row_width_mismatch: one row shorter than the header list.
    env = copy.deepcopy(base)
    _rs(env)["rowSet"][0].pop()
    emit("row_width_mismatch.json", env, "row_width_mismatch",
         "Row width != header count -> HEADER_ROW_WIDTH_MISMATCH.")

    # duplicate_team: two rows for the same TEAM_ID.
    env = copy.deepcopy(base)
    _rs(env)["rowSet"].append(copy.deepcopy(_rs(env)["rowSet"][0]))
    emit("duplicate_team.json", env, "duplicate_team",
         "Repeated TEAM_ID -> DUPLICATE_TEAM_ID (and DUPLICATE_RECORD).")

    # duplicate_record: same idempotency key twice (same team id).
    env = copy.deepcopy(base)
    _rs(env)["rowSet"].append(copy.deepcopy(_rs(env)["rowSet"][1]))
    emit("duplicate_record.json", env, "duplicate_record",
         "Same natural key twice -> DUPLICATE_RECORD.")

    # unknown_team: a TEAM_ID not in the expected set (16th row).
    env = copy.deepcopy(base)
    rs = _rs(env)
    extra = copy.deepcopy(rs["rowSet"][0])
    extra[_col(env, "TEAM_ID")] = UNKNOWN_TEAM_ID
    extra[_col(env, "TEAM_NAME")] = "Atlantis Krakens"
    rs["rowSet"].append(extra)
    emit("unknown_team.json", env, "unknown_team",
         "TEAM_ID not in expected set -> UNEXPECTED_TEAM.")

    # string_instead_of_number: PTS as text.
    env = copy.deepcopy(base)
    _rs(env)["rowSet"][0][_col(env, "PTS")] = "many"
    emit("string_instead_of_number.json", env, "string_instead_of_number",
         "Non-numeric stat cell -> NON_NUMERIC_VALUE (no coercion).")

    # negative_stat: AST negative (counting stats can't be < 0).
    env = copy.deepcopy(base)
    _rs(env)["rowSet"][0][_col(env, "AST")] = -5.0
    emit("negative_stat.json", env, "negative_stat",
         "Negative counting stat -> NEGATIVE_COUNTING_STAT.")

    # pct_wrong_scale: percentage on a 0-100 scale.
    env = copy.deepcopy(base)
    _rs(env)["rowSet"][0][_col(env, "FG_PCT")] = 47.2
    emit("pct_wrong_scale.json", env, "pct_wrong_scale",
         "Percentage outside [0,1] -> PCT_SCALE_VIOLATION.")

    # wl_gp_mismatch: wins + losses != games played.
    env = copy.deepcopy(base)
    rs = _rs(env)
    rs["rowSet"][0][_col(env, "W")] = 5
    rs["rowSet"][0][_col(env, "L")] = 1        # 5 + 1 = 6 != GP(7)
    rs["rowSet"][0][_col(env, "W_PCT")] = 0.714  # keep W_PCT consistent w/ W/GP
    emit("wl_gp_mismatch.json", env, "wl_gp_mismatch",
         "W + L != GP -> WL_GP_MISMATCH.")

    # makes_exceed_attempts: FGM > FGA.
    env = copy.deepcopy(base)
    rs = _rs(env)
    rs["rowSet"][0][_col(env, "FGM")] = 80.0
    rs["rowSet"][0][_col(env, "FGA")] = 71.2
    emit("makes_exceed_attempts.json", env, "makes_exceed_attempts",
         "FGM > FGA -> MAKES_EXCEED_ATTEMPTS.")

    # reb_mismatch: REB far from OREB + DREB.
    env = copy.deepcopy(base)
    _rs(env)["rowSet"][0][_col(env, "REB")] = 99.0
    emit("reb_mismatch.json", env, "reb_mismatch",
         "OREB + DREB != REB beyond tolerance -> REB_RECONCILE_FAIL.")

    # lastn_exceeded: GP greater than the requested window.
    env = copy.deepcopy(base)
    rs = _rs(env)
    rs["rowSet"][0][_col(env, "GP")] = 9
    rs["rowSet"][0][_col(env, "W")] = 6
    rs["rowSet"][0][_col(env, "L")] = 3
    rs["rowSet"][0][_col(env, "W_PCT")] = 0.667
    emit("lastn_exceeded.json", env, "lastn_exceeded",
         "GP > last_n_games -> LASTN_EXCEEDED.")

    # stale_upstream: valid data (freshness is a runner concern, tested there).
    env = copy.deepcopy(base)
    env.setdefault("_meta", {})["sourceObservedAtUtc"] = "2019-01-01T00:00:00Z"
    emit("stale_upstream.json", env, "stale_upstream",
         "Valid payload; staleness is judged by the runner against LKG age.")

    # ---- non-JSON payloads (for the HTTP/extractor layer) ------------------
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "truncated.json").write_text(
        json.dumps(base)[: len(json.dumps(base)) // 2], encoding="utf-8")
    written.append("truncated.json")
    (OUT / "malformed.json").write_text(
        '{"resultSets": [ this is not valid json ', encoding="utf-8")
    written.append("malformed.json")

    return written


if __name__ == "__main__":
    names = generate()
    print(f"wrote {len(names)} adversarial fixtures to {OUT}")
    for n in sorted(names):
        print(f"  - {n}")
