"""Betting scrapers: Action Network + VSIN parsing against recorded fixtures."""

from __future__ import annotations

import json

from wnba_pipeline.betting import actionnetwork, vsin
from wnba_pipeline.betting.contract import parse_american_odds, parse_line, parse_percent


def _an(fixtures_dir):
    payload = json.loads((fixtures_dir / "betting" / "an_scoreboard_wnba.json").read_text())
    return actionnetwork.parse_scoreboard(payload, "2026-07-22")


def test_an_parse_open_and_current_lines(fixtures_dir):
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
    # AN no longer carries the % splits — those come from VSIN now
    assert not hasattr(phx, "spread_pct_bets_away")


def test_an_skips_games_missing_team_data():
    payload = {"games": [{"id": 1, "away_team_id": 9, "home_team_id": 8,
                          "teams": [], "markets": {}}]}
    assert actionnetwork.parse_scoreboard(payload, "2026-07-22") == []


def test_vsin_dk_parse_lines_and_splits(fixtures_dir):
    games = {g.away_slug: g for g in
             vsin.parse_splits((fixtures_dir / "betting" / "vsin_dk_wnba.html").read_text())}
    assert len(games) == 6
    phx = games["phoenix-mercury"]
    assert phx.home_slug == "los-angeles-sparks"
    assert phx.game_date == "2026-07-22"          # parsed from gamecode YYYYMMDD
    assert phx.spread_away == 1.5
    assert phx.total == 176.5
    # VSIN splits: away side (spread/ML) and over side (total)
    assert phx.spread_pct_bets_away == 17
    assert phx.spread_pct_money_away == 75
    assert phx.total_pct_bets_over == 73
    assert phx.total_pct_money_over == 21
    assert phx.ml_pct_bets_away == 25


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
    assert parse_percent("75%") == 75
    assert parse_percent("▲ 17%") == 17
    assert parse_percent("") is None
    assert parse_percent(0) == 0 and parse_percent(100) == 100  # inclusive bounds


def test_parse_percent_rejects_values_outside_zero_hundred():
    """A VSIN layout change can put an unrelated number where the badge was
    (a gamecode, a moneyline price). Anything outside 0..100 is not a
    percentage — return None rather than publish garbage splits."""
    assert parse_percent("20260722") is None    # gamecode picked up whole
    assert parse_percent("-110") is None        # a price, not a split
    assert parse_percent(101) is None
    assert parse_percent(-1) is None


# --------------------------------------------------------------------------- #
# Parser hardening — every case below published a WRONG number, not a missing
# one. `-1,650` reached production as `-1` and was served beside the correct
# `-1650` from the other feed.
# --------------------------------------------------------------------------- #

import pytest as _pytest
from wnba_pipeline.betting.contract import (
    MAX_LINE_MAGNITUDE, MIN_AMERICAN_ODDS, normalize_numeric_text)


@_pytest.mark.parametrize("cell,expected", [
    ("-1650", -1650),
    ("-1,650", -1650),       # PRODUCTION DEFECT: regex stopped at the comma
    ("+1,200", 1200),
    ("1,650", 1650),
    ("−1650", -1650),   # MINUS SIGN — used to invert to +1650
    ("–1650", -1650),   # EN DASH
    ("—1650", -1650),   # EM DASH
    ("－1650", -1650),   # FULLWIDTH
    ("- 110", -110),         # sign split across two nodes
    ("- 110", -110),    # non-breaking space between sign and digits
    ("+110", 110),
    ("-105", -105),
])
def test_moneyline_parses_every_upstream_sign_and_separator_form(cell, expected):
    assert parse_american_odds(cell) == expected


@_pytest.mark.parametrize("impossible", ["0", "5", "-99", "99", "-1", "1"])
def test_moneyline_rejects_magnitudes_american_odds_cannot_have(impossible):
    """No American price lies strictly between -100 and +100, or is 0.

    This guard alone would have caught the live `-1` while the truncation bug
    was still latent.
    """
    assert parse_american_odds(impossible) is None


def test_moneyline_guard_boundary_is_inclusive_at_100():
    assert parse_american_odds("-100") == -100
    assert parse_american_odds("+100") == 100
    assert MIN_AMERICAN_ODDS == 100


@_pytest.mark.parametrize("cell,expected", [
    ("−1.5", -1.5),      # MINUS SIGN on a RENDERED spread field
    ("–10.5", -10.5),    # EN DASH
    ("-  10.5", -10.5),
    ("+1.5", 1.5),
    ("188.5", 188.5),
])
def test_line_parses_every_sign_form(cell, expected):
    assert parse_line(cell) == expected


def test_pick_em_cell_reports_a_zero_line_not_its_juice():
    """"PK -110" is a zero line priced at -110, not a -110 line."""
    assert parse_line("PK -110") == 0.0
    assert parse_line("EV -105") == 0.0
    assert parse_line("PK") == 0.0


@_pytest.mark.parametrize("not_a_line", ["1,650", "-1650", "2500"])
def test_line_rejects_a_moneyline_that_landed_in_a_line_cell(not_a_line):
    assert parse_line(not_a_line) is None
    assert MAX_LINE_MAGNITUDE == 500.0


@_pytest.mark.parametrize("cell,expected", [
    ("48%", 48), ("▲ 75%", 75), ("12 %", 12), ("75", 75), ("100%", 100),
])
def test_percent_accepts_real_badge_shapes(cell, expected):
    assert parse_percent(cell) == expected


@_pytest.mark.parametrize("not_a_percent", ["1,650", "1650", "-", "—", "abc"])
def test_percent_requires_a_percent_shape_not_just_a_number_in_range(not_a_percent):
    """A price landing in the badge cell used to publish as a 1% split."""
    assert parse_percent(not_a_percent) is None


def test_normalizer_is_shared_and_idempotent():
    assert normalize_numeric_text("−1,650") == "-1650"
    assert normalize_numeric_text(normalize_numeric_text("- 1,650")) == "-1650"
    # A decimal comma is not a thousands separator and must survive untouched.
    assert normalize_numeric_text("1,65") == "1,65"


def _wnba_html(fixtures_dir):
    return (fixtures_dir / "betting" / "vsin_dk_wnba.html").read_text(encoding="utf-8")


def test_vsin_rows_pair_by_gamecode_not_position(fixtures_dir):
    """A stray row must not desynchronise every matchup after it.

    Positional pairing (rows 0/1, 2/3, ...) paired one game's away row with the
    next game's home row, publishing a matchup that does not exist and serving
    one team's own price as its opponent's.
    """
    from wnba_pipeline.betting.vsin import parse_splits

    html = _wnba_html(fixtures_dir)
    clean = parse_splits(html)
    assert len(clean) == 6

    marker = '<tr class="sp-row"'
    idx = html.find(marker, html.find(marker) + 1)
    mutated = html[:idx] + '<tr class="sp-row"><td>promo</td></tr>' + html[idx:]

    after = parse_splits(mutated)
    shape = lambda gs: [(g.game_id, g.away_name, g.home_name, g.ml_away, g.ml_home) for g in gs]
    assert shape(after) == shape(clean), "a stray row changed the published matchups"


def test_vsin_skips_a_gamecode_that_does_not_have_exactly_two_rows(fixtures_dir):
    from wnba_pipeline.betting.vsin import parse_splits

    html = _wnba_html(fixtures_dir)
    clean = parse_splits(html)
    orphan_code = clean[0].game_id

    # Duplicate one row of the first game: that gamecode now has three rows and
    # cannot be resolved into an away/home pair.
    row_start = html.find(f'data-gamecode="{orphan_code}"')
    tr_start = html.rfind("<tr", 0, row_start)
    tr_end = html.find("</tr>", tr_start) + len("</tr>")
    mutated = html[:tr_end] + html[tr_start:tr_end] + html[tr_end:]

    after = parse_splits(mutated)
    assert orphan_code not in [g.game_id for g in after], "an unresolvable pair was published"
    assert len(after) == len(clean) - 1
