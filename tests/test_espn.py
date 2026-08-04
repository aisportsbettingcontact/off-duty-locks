"""ESPN team-stats source: parsers, split builders, crosswalk, and envelope.

All tests run offline against ``fixtures/espn/`` — real responses captured
2026-08-04 and trimmed to the consumed fields (provenance in each file). The
golden numbers below are transcribed from those captures, so a change in any
parser that would alter published values fails here first.

Definitional coverage (see the wnba_pipeline.espn module docstring):
  - TOV: the ``turnovers`` counter, never ``totalTurnovers`` (whose season
    value 663 = 22.9/game fails WNBA believability and does not reconcile
    with ESPN's own game boxes);
  - percentages normalized to fraction 0-1 (core-API values are 0-100);
  - minutes derived from game length (40 + 5*OT via status.period), because
    ESPN's avgMinutes is 0.0 at every level;
  - regular-season filtering by each event's own seasonType (the fixture
    schedule deliberately contains two real completed PRESEASON games).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wnba_pipeline import contract, espn
from wnba_pipeline.contract import UpstreamUnavailable
from wnba_pipeline.validation import validate_and_normalize

from tests._builders import make_expected_team_set

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "espn"

ATL_ESPN_ID = "20"
ATL_CANONICAL_ID = "1611661330"   # fixtures/expected_teams/2026.json


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def teams_payload() -> dict:
    return load("teams.json")


@pytest.fixture(scope="module")
def season_values() -> dict:
    return espn.stat_values(load("season_stats_atl.json"))


@pytest.fixture(scope="module")
def record() -> dict:
    return espn.parse_record(load("team_record_atl.json"))


@pytest.fixture(scope="module")
def completed_events() -> list:
    return espn.parse_completed_regular_season(load("schedule_atl.json"), ATL_ESPN_ID)


# --------------------------------------------------------------------------- #
# parsers
# --------------------------------------------------------------------------- #

def test_team_directory_lists_all_fifteen_teams(teams_payload):
    directory = espn.parse_team_directory(teams_payload)
    assert len(directory) == 15
    atl = next(t for t in directory if t.espn_id == ATL_ESPN_ID)
    assert atl.display_name == "Atlanta Dream"
    assert atl.abbreviation == "ATL"


def test_team_directory_malformed_raises_upstream_unavailable():
    with pytest.raises(UpstreamUnavailable):
        espn.parse_team_directory({"sports": []})


def test_stat_values_are_raw_espn_scales(season_values):
    # Totals are plain counts; pct *values* are 0-100 on the core API — the
    # scale normalization is the split builders' job, so it must NOT have
    # happened yet here.
    assert season_values["gamesPlayed"] == 29.0
    assert season_values["fieldGoalsMade"] == 898.0
    assert season_values["fieldGoalsAttempted"] == 2053.0
    assert season_values["fieldGoalPct"] == pytest.approx(43.741, abs=1e-3)


def test_record_totals(record):
    assert record["wins"] == 18.0
    assert record["losses"] == 11.0
    assert record["gamesPlayed"] == 29.0
    # winPercent arrives ALREADY as a fraction (unlike the core-API pcts).
    assert record["winPercent"] == pytest.approx(18.0 / 29.0, abs=1e-6)


def test_schedule_filters_to_completed_regular_season(completed_events):
    # The fixture holds 44 regular-season events (29 completed) plus two real
    # completed PRESEASON games; only the 29 may survive — this is the count
    # that must equal the record's gamesPlayed.
    assert len(completed_events) == 29
    assert all(e.periods >= 4 for e in completed_events)
    # Newest event last (the Last-N window slices from the tail).
    assert completed_events[-1].event_id == "401857111"
    assert completed_events[-1].won is False           # ATL 87-109 LVA
    assert completed_events[-1].points_for == 87.0
    assert completed_events[-1].points_against == 109.0


def test_schedule_excludes_the_real_preseason_events():
    payload = load("schedule_atl.json")
    completed_ids = {e.event_id for e in
                     espn.parse_completed_regular_season(payload, ATL_ESPN_ID)}
    preseason = {e["id"] for e in payload["events"]
                 if e["seasonType"]["type"] == 1}
    assert preseason == {"401866517", "401867795"}   # both really completed
    assert not (completed_ids & preseason)


def test_game_length_minutes_regulation_and_overtime():
    assert espn.game_length_minutes(4) == 40.0
    assert espn.game_length_minutes(5) == 45.0
    assert espn.game_length_minutes(7) == 55.0


def test_summary_box_parses_both_teams():
    payload = load("summary_401857111.json")
    atl = espn.parse_summary_box(payload, ATL_ESPN_ID)
    lva = espn.parse_summary_box(payload, "17")
    assert atl == {
        "fgm": 33.0, "fga": 75.0, "fg3m": 4.0, "fg3a": 24.0,
        "ftm": 17.0, "fta": 20.0, "oreb": 12.0, "dreb": 22.0, "reb": 34.0,
        "ast": 19.0, "tov": 11.0, "stl": 5.0, "blk": 2.0, "pf": 14.0,
    }
    assert lva["fgm"] == 44.0 and lva["fga"] == 71.0 and lva["tov"] == 9.0


def test_summary_box_matches_core_competitor_statistics():
    """Parity witness: the two ESPN box surfaces agree for the same event/team,
    so preferring the summary (both teams per request) loses nothing."""
    summary = espn.parse_summary_box(load("summary_401857111.json"), ATL_ESPN_ID)
    core = espn.stat_values(load("core_box_401857111_t20.json"))
    for summary_key, core_name in (
        ("fgm", "fieldGoalsMade"), ("fga", "fieldGoalsAttempted"),
        ("fg3m", "threePointFieldGoalsMade"),
        ("fg3a", "threePointFieldGoalsAttempted"),
        ("ftm", "freeThrowsMade"), ("fta", "freeThrowsAttempted"),
        ("oreb", "offensiveRebounds"), ("dreb", "defensiveRebounds"),
        ("reb", "totalRebounds"), ("ast", "assists"), ("tov", "turnovers"),
        ("stl", "steals"), ("blk", "blocks"), ("pf", "fouls"),
    ):
        assert summary[summary_key] == core[core_name], (summary_key, core_name)


def test_summary_box_missing_team_raises():
    with pytest.raises(UpstreamUnavailable):
        espn.parse_summary_box(load("summary_401857111.json"), "9999")


# --------------------------------------------------------------------------- #
# TOV definitional evidence (the reason `turnovers` is the counter we publish)
# --------------------------------------------------------------------------- #

def test_turnover_counter_choice_is_the_believable_box_consistent_one(season_values):
    gp = season_values["gamesPlayed"]
    per_game = season_values["turnovers"] / gp
    total_per_game = season_values["totalTurnovers"] / gp
    # The chosen counter sits inside the believable WNBA band...
    assert 8.0 <= per_game <= 22.0
    assert per_game == pytest.approx(13.0, abs=0.01)
    # ...while season totalTurnovers (= turnovers + a 286-game "teamTurnovers"
    # accumulator that cannot be a sum of 0-1 per-game values) does not.
    assert total_per_game > 22.0
    assert season_values["totalTurnovers"] == pytest.approx(
        season_values["turnovers"] + season_values["teamTurnovers"])
    # Game boxes tell the same story: teamTurnovers there is a tiny single
    # digit (0-2 across the captured games), so 29 of them can never sum to
    # the season accumulator's 286.
    for eid in ("401857097", "401857102", "401857111"):
        box = load(f"summary_{eid}.json")
        for team in box["boxscore"]["teams"]:
            stats = {s["name"]: s["displayValue"] for s in team["statistics"]}
            assert int(stats["teamTurnovers"]) <= 3
            assert int(stats["totalTurnovers"]) == (
                int(stats["turnovers"]) + int(stats["teamTurnovers"]))


# --------------------------------------------------------------------------- #
# split builders — golden numbers from the 2026-08-04 captures
# --------------------------------------------------------------------------- #

def test_ytd_stats_golden_atlanta(season_values, record, completed_events):
    stats = espn.ytd_stats(season_values, record, completed_events, "ATL")
    assert stats["games_played"] == 29.0
    assert stats["wins"] == 18.0 and stats["losses"] == 11.0
    assert stats["win_pct"] == pytest.approx(18 / 29, abs=1e-6)
    # All 29 completed games ended in regulation -> exactly 40.0 (and the
    # record agrees: OTWins == OTLosses == 0).
    assert stats["minutes"] == 40.0
    assert record["OTWins"] == 0.0 and record["OTLosses"] == 0.0
    assert stats["field_goals_made"] == pytest.approx(898 / 29)
    assert stats["field_goals_attempted"] == pytest.approx(2053 / 29)
    # Percentages are fractions (0-1), full precision from makes/attempts —
    # and equal ESPN's own 0-100 value divided by 100.
    assert stats["field_goal_pct"] == pytest.approx(898 / 2053)
    assert stats["field_goal_pct"] == pytest.approx(43.741 / 100, abs=1e-4)
    assert stats["three_point_pct"] == pytest.approx(232 / 763)
    assert stats["free_throw_pct"] == pytest.approx(562 / 725)
    assert stats["offensive_rebounds"] == pytest.approx(324 / 29)
    assert stats["defensive_rebounds"] == pytest.approx(679 / 29)
    assert stats["total_rebounds"] == pytest.approx(1003 / 29)
    assert stats["assists"] == pytest.approx(594 / 29)
    assert stats["turnovers"] == pytest.approx(377 / 29)   # 13.0 — NOT 663/29
    assert stats["steals"] == pytest.approx(275 / 29)
    assert stats["blocks"] == pytest.approx(85 / 29)
    assert stats["personal_fouls"] == pytest.approx(561 / 29)
    assert stats["points"] == pytest.approx(2590 / 29)
    # plus/minus per game = avg points for - avg points allowed.
    assert stats["plus_minus"] == pytest.approx(2590 / 29 - 86.06896, abs=1e-3)
    # ESPN has no BLKA/PFD equivalents: absent stays None, never zero.
    assert stats["blocked_attempts"] is None
    assert stats["personal_fouls_drawn"] is None


def test_ytd_gp_divergence_beyond_tolerance_fails_the_run(
        season_values, record, completed_events):
    truncated = completed_events[:-2]   # schedule now says 27 games, record 29
    with pytest.raises(UpstreamUnavailable) as excinfo:
        espn.ytd_stats(season_values, record, truncated, "ATL")
    assert "espn_gp_inconsistent" in excinfo.value.reason


def test_lastn_stats_golden_three_game_window(completed_events):
    window = completed_events[-3:]
    assert [e.event_id for e in window] == ["401857097", "401857102", "401857111"]
    boxes = {
        e.event_id: espn.parse_summary_box(load(f"summary_{e.event_id}.json"),
                                           ATL_ESPN_ID)
        for e in window
    }
    stats = espn.lastn_stats(window, boxes)
    assert stats["games_played"] == 3.0
    # Schedule winner flags decide W-L inside the window.
    wins = sum(1 for e in window if e.won)
    assert stats["wins"] == float(wins)
    assert stats["losses"] == 3.0 - wins
    assert stats["win_pct"] == pytest.approx(wins / 3)
    assert stats["minutes"] == 40.0            # all three ended in regulation
    assert stats["field_goals_made"] == pytest.approx((31 + 27 + 33) / 3)
    assert stats["field_goals_attempted"] == pytest.approx((70 + 64 + 75) / 3)
    # Window pct = summed makes / summed attempts (not a mean of games' pcts).
    assert stats["field_goal_pct"] == pytest.approx((31 + 27 + 33) / (70 + 64 + 75))
    assert stats["turnovers"] == pytest.approx((15 + 17 + 11) / 3)
    assert stats["points"] == pytest.approx(
        sum(e.points_for for e in window) / 3)
    assert stats["plus_minus"] == pytest.approx(
        sum(e.points_for - e.points_against for e in window) / 3)


def test_lastn_missing_box_is_never_a_partial_window(completed_events):
    window = completed_events[-3:]
    boxes = {window[0].event_id: espn.parse_summary_box(
        load(f"summary_{window[0].event_id}.json"), ATL_ESPN_ID)}
    with pytest.raises(UpstreamUnavailable):
        espn.lastn_stats(window, boxes)


# --------------------------------------------------------------------------- #
# crosswalk
# --------------------------------------------------------------------------- #

def test_crosswalk_maps_espn_display_name_to_canonical_id():
    expected = make_expected_team_set({ATL_CANONICAL_ID: "Atlanta Dream"})
    assert espn.crosswalk_team_id("Atlanta Dream", expected,
                                  ATL_ESPN_ID) == ATL_CANONICAL_ID
    # Normalized matching: case + punctuation differences still resolve.
    assert espn.crosswalk_team_id("ATLANTA  DREAM", expected,
                                  ATL_ESPN_ID) == ATL_CANONICAL_ID


def test_crosswalk_miss_returns_loud_sentinel_that_fails_validation():
    expected = make_expected_team_set({ATL_CANONICAL_ID: "Atlanta Dream"})
    sentinel = espn.crosswalk_team_id("Someplace Newteam", expected, "99")
    assert sentinel == "espn-unmatched-99"
    assert sentinel not in expected.teams


# --------------------------------------------------------------------------- #
# envelope -> the existing validator (full reuse of validation.py rules)
# --------------------------------------------------------------------------- #

def _one_team_raw(last_n_games: int):
    """RawFetchResult for a real single-team extraction (ATL fixtures)."""
    values = espn.stat_values(load("season_stats_atl.json"))
    record = espn.parse_record(load("team_record_atl.json"))
    events = espn.parse_completed_regular_season(load("schedule_atl.json"),
                                                 ATL_ESPN_ID)
    params = espn.EspnExtractionParams(last_n_games=last_n_games)
    if last_n_games == 0:
        stats = espn.ytd_stats(values, record, events, "ATL")
        event_ids = ()
    else:
        window = events[-last_n_games:]
        boxes = {e.event_id: espn.parse_summary_box(
            load(f"summary_{e.event_id}.json"), ATL_ESPN_ID) for e in window}
        stats = espn.lastn_stats(window, boxes)
        event_ids = tuple(e.event_id for e in window)
    expected = make_expected_team_set({ATL_CANONICAL_ID: "Atlanta Dream"})
    rows = [{
        "team_id": espn.crosswalk_team_id("Atlanta Dream", expected, ATL_ESPN_ID),
        "team_name": "Atlanta Dream",
        "espn_team_id": ATL_ESPN_ID,
        "event_ids": event_ids,
        "stats": stats,
    }]
    payload = espn.build_envelope(rows, params, {"https://example/x": "ab" * 32})
    raw_bytes = json.dumps(payload, sort_keys=True).encode()
    raw = contract.RawFetchResult(
        endpoint=espn.CORE_API,
        url=f"{espn.SITE_API}/teams",
        params=params,
        payload=payload,
        raw_bytes=raw_bytes,
        source_checksum=contract.sha256_hex(raw_bytes),
        fetched_at_utc="2026-08-04T12:00:00Z",
        http_status=200,
        request_count=4,
        retry_count=0,
        source=espn.ESPN_SOURCE,
    )
    return raw, expected


def test_ytd_envelope_passes_the_existing_validator_end_to_end():
    raw, expected = _one_team_raw(last_n_games=0)
    outcome = validate_and_normalize(raw, expected)
    assert outcome.state.value == "PASSED", [f.message for f in outcome.failures]
    snapshot = outcome.snapshot
    # Provenance: the snapshot must say ESPN, and the key must carry source=espn.
    assert snapshot.source == "espn"
    assert snapshot.extraction_key.startswith("wnba-teamstats:v2:source=espn:")
    record = snapshot.records[0]
    assert record.team_id == ATL_CANONICAL_ID
    # Integer normalization + golden spot checks survive validation untouched.
    assert record.stats["games_played"] == 29
    assert record.stats["turnovers"] == pytest.approx(377 / 29)
    assert record.stats["blocked_attempts"] is None
    # ESPN provenance rides along in extras for the file snapshots.
    assert record.extras["ESPN_TEAM_ID"] == ATL_ESPN_ID


def test_lastn_envelope_passes_the_existing_validator_end_to_end():
    raw, expected = _one_team_raw(last_n_games=3)
    outcome = validate_and_normalize(raw, expected)
    assert outcome.state.value == "PASSED", [f.message for f in outcome.failures]
    record = outcome.snapshot.records[0]
    assert record.stats["games_played"] == 3
    assert record.extras["ESPN_EVENT_IDS"] == "401857097,401857102,401857111"


def test_unmatched_team_fails_validation_loudly():
    raw, expected = _one_team_raw(last_n_games=0)
    # Rewrite the TEAM_ID cell to the sentinel an unmatched name produces.
    result_set = raw.payload["resultSets"][0]
    id_index = result_set["headers"].index("TEAM_ID")
    result_set["rowSet"][0][id_index] = "espn-unmatched-20"
    outcome = validate_and_normalize(raw, expected)
    assert outcome.state.value == "FAILED"
    codes = {f.code for f in outcome.failures}
    assert "UNEXPECTED_TEAM" in codes and "MISSING_EXPECTED_TEAM" in codes


# --------------------------------------------------------------------------- #
# possessions cross-check (WARN-only tripwire)
# --------------------------------------------------------------------------- #

def test_possessions_check_warns_beyond_tolerance_never_raises(caplog):
    stats = {"field_goals_attempted": 70.0, "offensive_rebounds": 11.0,
             "turnovers": 13.0, "free_throws_attempted": 25.0}
    with caplog.at_level("WARNING", logger="wnba_pipeline.espn"):
        espn._check_possessions("ATL", stats, espn_avg_possessions=120.0)
    assert any("possessions divergence" in r.message for r in caplog.records)


def test_possessions_check_quiet_within_tolerance(caplog):
    stats = {"field_goals_attempted": 70.0, "offensive_rebounds": 11.0,
             "turnovers": 13.0, "free_throws_attempted": 25.0}
    ours = 70.0 - 11.0 + 13.0 + 0.44 * 25.0
    with caplog.at_level("WARNING", logger="wnba_pipeline.espn"):
        espn._check_possessions("ATL", stats, espn_avg_possessions=ours * 1.05)
    assert not caplog.records


# --------------------------------------------------------------------------- #
# client memoization + spacing
# --------------------------------------------------------------------------- #

def test_client_memoizes_and_spaces_requests():
    calls: list[str] = []
    sleeps: list[float] = []

    def fake_fetch(url, params):
        calls.append(EspnClient_key := espn.EspnClient._key(url, params))
        return {"ok": EspnClient_key}, b"{}", 1, 0

    client = espn.EspnClient(fetch_raw=fake_fetch, sleep=sleeps.append,
                             spacing_s=0.5)
    a1, checksum1 = client.get("https://x/teams")
    a2, checksum2 = client.get("https://x/teams")          # memo hit
    client.get("https://x/summary", {"event": "1"})
    assert a1 is a2 and checksum1 == checksum2
    assert len(calls) == 2                                  # not 3
    assert client.request_count == 2
    # Spacing before the second LIVE request only (never before the first,
    # never for cache hits).
    assert sleeps == [0.5]


def test_extraction_key_identifies_espn_source():
    params = espn.EspnExtractionParams(season="2026", last_n_games=7)
    assert params.extraction_key() == (
        "wnba-teamstats:v2:source=espn:season=2026:type=regular-season"
        ":lastn=7:measure=base:permode=pergame"
    )
    # And can never collide with the historical v1 stats.wnba.com keys.
    assert contract.ExtractionParams().extraction_key().startswith(
        "wnba-teamstats:v1:")
