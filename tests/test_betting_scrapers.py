"""Betting scrapers: Action Network + VSIN parsing against recorded fixtures."""

from __future__ import annotations

import json

from wnba_pipeline.betting import actionnetwork, vsin
from wnba_pipeline.betting.contract import parse_american_odds, parse_line


def _an(fixtures_dir):
    payload = json.loads((fixtures_dir / "betting" / "an_scoreboard_wnba.json").read_text())
    return actionnetwork.parse_scoreboard(payload, "2026-07-22")


def test_an_parse_open_current_and_splits(fixtures_dir):
    games = {g.away_abbr: g for g in _an(fixtures_dir)}
    assert set(games) == {"PHX", "MIN"}
    phx = games["PHX"]
    assert phx.home_abbr == "LA"
    assert phx.game_date == "2026-07-22"
    # open (book 30) vs current DK (book 68)
    assert phx.open_spread_away == 1.5
    assert phx.open_total == 178.5
    assert phx.dk_total == 176.5
    # American odds parsed to ints (100 == +100)
    assert phx.open_ml_away == -102
    assert phx.dk_ml_away == 100
    # DK splits from bet_info
    assert phx.spread_pct_bets_away == 32
    assert phx.spread_pct_money_away == 29
    assert phx.total_pct_bets_over == 79


def test_an_skips_games_missing_team_data():
    payload = {"games": [{"id": 1, "away_team_id": 9, "home_team_id": 8,
                          "teams": [], "markets": {}}]}
    assert actionnetwork.parse_scoreboard(payload, "2026-07-22") == []


def test_vsin_dk_parse(fixtures_dir):
    games = {g.away_slug: g for g in
             vsin.parse_splits((fixtures_dir / "betting" / "vsin_dk_wnba.html").read_text())}
    assert len(games) == 6
    phx = games["phoenix-mercury"]
    assert phx.home_slug == "los-angeles-sparks"
    assert phx.game_date == "2026-07-22"          # parsed from gamecode YYYYMMDD
    assert phx.spread_away == 1.5
    assert phx.total == 176.5


def test_vsin_circa_parse_gives_sharp_line(fixtures_dir):
    games = {g.away_slug: g for g in
             vsin.parse_splits((fixtures_dir / "betting" / "vsin_circa_wnba.html").read_text())}
    phx = games["phoenix-mercury"]
    assert phx.spread_away == 1.0
    assert phx.total == 176.0
    assert phx.ml_away == -105


def test_parse_helpers_tolerate_signs_arrows_and_pickem():
    assert parse_line("+1.5") == 1.5
    assert parse_line("-10.5") == -10.5
    assert parse_line("▲ 176") == 176.0     # leading arrow glyph
    assert parse_line("PK") == 0.0               # pick'em
    assert parse_line("") is None
    assert parse_american_odds("+100") == 100
    assert parse_american_odds("-121") == -121
    assert parse_american_odds("") is None
