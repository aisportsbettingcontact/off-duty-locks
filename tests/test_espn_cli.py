"""End-to-end `espn-team-stats` CLI runs — offline, no network, no database.

The fake transport serves the REAL trimmed captures for Atlanta and derives
the other 14 teams from them (ids swapped, event ids suffixed) so the full
15-team path — teams list -> crosswalk -> both splits -> validation ->
storage -> manifests — executes exactly as in production. The derivation is
purely mechanical; every shape and stat value is the real capture's.

Verified here:
  - both windows run (Last-N first, then YTD) with DISTINCT extraction keys,
    each emitting one manifest line, both PASSING the full validator with all
    15 expected teams;
  - the two splits carry genuinely different numbers (ytd GP=29 vs window GP);
  - a second identical invocation is SUCCESS_UNCHANGED for both splits (the
    envelope checksum is stable when upstream bytes are stable);
  - stats.wnba.com is never contacted (the fake transport would raise).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from wnba_pipeline import espn
from wnba_pipeline.__main__ import main

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "espn"
EVENT_IDS = ("401857097", "401857102", "401857111")


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _swap_ids(obj, mapping: dict[str, str]):
    """Recursively swap competitor/team id values (key 'id') per ``mapping``."""
    if isinstance(obj, dict):
        return {
            key: (mapping.get(value, value)
                  if key == "id" and isinstance(value, str) else
                  _swap_ids(value, mapping))
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [_swap_ids(item, mapping) for item in obj]
    return obj


def build_routes() -> dict[str, dict]:
    """URL-key -> payload for all 15 teams, derived from the ATL captures."""
    teams_payload = load("teams.json")
    season_stats = load("season_stats_atl.json")
    record = load("team_record_atl.json")
    schedule = load("schedule_atl.json")
    summaries = {eid: load(f"summary_{eid}.json") for eid in EVENT_IDS}

    routes: dict[str, dict] = {
        espn.EspnClient._key(f"{espn.SITE_API}/teams", {}): teams_payload,
    }
    directory = espn.parse_team_directory(teams_payload)
    for team in directory:
        tid = team.espn_id
        swap = {"20": tid, tid: "20"}   # involution: works for ATL itself too
        team_schedule = _swap_ids(copy.deepcopy(schedule), swap)
        # Give every team its own event ids so no fake summary is shared
        # between teams (real shared events are covered by the client
        # memoization test in test_espn.py).
        for event in team_schedule["events"]:
            event["id"] = f"{event['id']}{tid}"
        routes[espn.EspnClient._key(
            f"{espn.CORE_API}/seasons/2026/types/2/teams/{tid}/statistics", {}
        )] = season_stats
        routes[espn.EspnClient._key(f"{espn.SITE_API}/teams/{tid}", {})] = record
        routes[espn.EspnClient._key(
            f"{espn.SITE_API}/teams/{tid}/schedule",
            {"season": "2026", "seasontype": "2"})] = team_schedule
        for eid, summary in summaries.items():
            routes[espn.EspnClient._key(
                f"{espn.SITE_API}/summary", {"event": f"{eid}{tid}"}
            )] = _swap_ids(copy.deepcopy(summary), swap)
    return routes


@pytest.fixture()
def fake_transport(monkeypatch):
    routes = build_routes()
    calls: list[str] = []

    def fake_fetch_raw(self, url, params):
        key = espn.EspnClient._key(url, params)
        assert "wnba.com" not in url, "the ESPN path must NEVER touch stats.wnba.com"
        if key not in routes:
            raise AssertionError(f"unexpected ESPN request: {key}")
        calls.append(key)
        payload = routes[key]
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        return payload, raw, 1, 0

    monkeypatch.setattr(espn.EspnClient, "_live_fetch_raw", fake_fetch_raw)
    # No politeness pauses in tests.
    monkeypatch.setattr(espn, "DEFAULT_SPACING_S", 0.0)
    return calls


def _run(tmp_path, capsys) -> list[dict]:
    code = main([
        "espn-team-stats", "--last-n-games", "3", "--no-publish",
        "--data-root", str(tmp_path / "data"),
    ])
    assert code == 0
    lines = [json.loads(line)
             for line in capsys.readouterr().out.strip().splitlines()]
    assert len(lines) == 2
    return lines


def test_both_splits_run_validate_and_store(tmp_path, capsys, fake_transport):
    lastn, ytd = _run(tmp_path, capsys)

    assert lastn["extractionKey"] == (
        "wnba-teamstats:v2:source=espn:season=2026:type=regular-season"
        ":lastn=3:measure=base:permode=pergame")
    assert ytd["extractionKey"].endswith(":lastn=0:measure=base:permode=pergame")
    for manifest in (lastn, ytd):
        assert manifest["status"] == "SUCCESS"
        assert manifest["validationState"] == "PASSED"
        assert manifest["expectedTeamCount"] == 15
        assert manifest["actualTeamCount"] == 15
        assert manifest["publishResult"] is None   # --no-publish

    # The two splits are genuinely different datasets, not one relabeled.
    assert lastn["normalizedChecksum"] != ytd["normalizedChecksum"]

    # Snapshot spot-check: ytd GP=29 (record) vs window GP=3, distinct TOV.
    snapshots = sorted((tmp_path / "data" / "snapshots").rglob("*.json"))
    by_key = {}
    for path in snapshots:
        doc = json.loads(path.read_text())
        by_key[doc["extractionKey"]] = doc
    ytd_doc = by_key[ytd["extractionKey"]]
    lastn_doc = by_key[lastn["extractionKey"]]
    assert ytd_doc["source"] == "espn" and lastn_doc["source"] == "espn"
    assert all(r["stats"]["games_played"] == 29 for r in ytd_doc["records"])
    assert all(r["stats"]["games_played"] == 3 for r in lastn_doc["records"])
    assert ytd_doc["records"][0]["stats"]["turnovers"] == pytest.approx(377 / 29)
    assert lastn_doc["records"][0]["stats"]["turnovers"] == pytest.approx(
        (15 + 17 + 11) / 3)

    # Shared fetches are shared: the teams list went out exactly once and each
    # team's schedule exactly once, across BOTH splits.
    assert sum(1 for key in fake_transport if key.endswith("/teams")) == 1
    schedule_calls = [key for key in fake_transport if "/schedule" in key]
    assert len(schedule_calls) == len(set(schedule_calls)) == 15


def test_second_run_is_success_unchanged(tmp_path, capsys, fake_transport):
    _run(tmp_path, capsys)
    second = _run(tmp_path, capsys)
    assert [m["status"] for m in second] == [
        "SUCCESS_UNCHANGED", "SUCCESS_UNCHANGED"]
