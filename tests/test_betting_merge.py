"""Betting merge: line movement, reverse line movement, and Circa attachment."""

from __future__ import annotations

import json

from wnba_pipeline.betting import actionnetwork, vsin
from wnba_pipeline.betting.merge import (
    line_move,
    merge_games,
    rlm_moneyline,
    rlm_spread,
    rlm_total,
)


def test_line_move():
    assert line_move(176.5, 178.5) == -2.0
    assert line_move(None, 1.0) is None
    assert line_move(1.0, None) is None


def test_rlm_spread():
    # public on away (70%) but away line moved up (toward home) -> RLM
    assert rlm_spread(70, 0.5) is True
    # public on away and line moved toward away -> follows public, not RLM
    assert rlm_spread(70, -0.5) is False
    # public on home (30%) but line moved toward away -> RLM
    assert rlm_spread(30, -0.5) is True
    assert rlm_spread(50, -0.5) is None   # no ticket majority
    assert rlm_spread(70, 0) is None      # no movement
    assert rlm_spread(None, -0.5) is None


def test_rlm_total():
    assert rlm_total(79, -2.0) is True    # public over, total dropped -> RLM
    assert rlm_total(79, 2.0) is False    # public over, total rose -> follows
    assert rlm_total(30, 2.0) is True     # public under, total rose -> RLM
    assert rlm_total(50, -2.0) is None


def test_rlm_moneyline():
    # public on away, away price drifted weaker (-150 -> -130) -> RLM
    assert rlm_moneyline(70, -150, -130) is True
    # public on away, away price strengthened -> follows public
    assert rlm_moneyline(70, -150, -170) is False
    assert rlm_moneyline(50, -150, -130) is None
    assert rlm_moneyline(70, None, -130) is None


def _fixtures(fixtures_dir):
    an = actionnetwork.parse_scoreboard(
        json.loads((fixtures_dir / "betting" / "an_scoreboard_wnba.json").read_text()),
        "2026-07-22",
    )
    dk = vsin.parse_splits((fixtures_dir / "betting" / "vsin_dk_wnba.html").read_text())
    circa = vsin.parse_splits((fixtures_dir / "betting" / "vsin_circa_wnba.html").read_text())
    return an, dk, circa


def test_merge_sources_splits_from_vsin_and_sharp_from_circa(fixtures_dir):
    an, dk, circa = _fixtures(fixtures_dir)
    merged = {m.game_key: m for m in
              merge_games(an, dk, circa, fetched_at_utc="2026-07-22T12:00:00Z")}

    phx = merged["2026-07-22:PHX@LA"]
    # splits come from VSIN (17/75/73), not Action Network
    assert phx.spread_pct_bets_away == 17
    assert phx.spread_pct_money_away == 75
    assert phx.total_pct_bets_over == 73
    # sharp line from VSIN Circa
    assert phx.sharp_book == "Circa"
    assert phx.sharp_spread == 1.0 and phx.sharp_total == 176.0
    assert phx.public_book == "DraftKings"
    assert phx.total_line_move == -2.0
    assert phx.total_rlm is True          # VSIN 73% over, total dropped -> RLM

    minsea = merged["2026-07-22:MIN@SEA"]
    assert minsea.spread_line_move == -1.0
    assert minsea.spread_rlm is True      # VSIN bets lean home, line moved to away


def test_merge_without_vsin_leaves_splits_and_sharp_null(fixtures_dir):
    an, _, _ = _fixtures(fixtures_dir)
    merged = merge_games(an, [], [], fetched_at_utc="t")
    assert merged
    assert all(m.sharp_spread is None and m.sharp_book is None for m in merged)
    assert all(m.spread_pct_bets_away is None for m in merged)
    # AN-derived line fields are still populated without VSIN.
    assert all(m.current_spread is not None for m in merged)


# --------------------------------------------------------------------------- #
# VSIN confirmation stamp — the age of the preserved splits must be knowable
# --------------------------------------------------------------------------- #

def _an_game():
    from wnba_pipeline.betting.contract import AnGame
    return AnGame(
        game_id=1, game_date="2026-08-07", start_time=None, status="scheduled",
        away_team_id=1, home_team_id=2,
        away_name="Phoenix Mercury", away_abbr="PHX",
        home_name="Los Angeles Sparks", home_abbr="LA",
        open_spread_away=1.5, open_total=170.0, open_ml_away=110, open_ml_home=-130,
        dk_spread_away=2.0, dk_total=171.0, dk_ml_away=120, dk_ml_home=-140)


def _vsin_game():
    from wnba_pipeline.betting.contract import VsinGame
    return VsinGame(
        game_id="20260807WNBA1", game_date="2026-08-07",
        away_slug="phoenix-mercury", home_slug="los-angeles-sparks",
        away_name="Phoenix Mercury", home_name="Los Angeles Sparks",
        spread_away=2.0, total=171.0, ml_away=120, ml_home=-140,
        spread_pct_bets_away=60, spread_pct_money_away=80)


def test_vsin_stamp_advances_only_when_vsin_carried_the_game():
    stamp = "2026-08-07T12:00:00+00:00"
    hit = merge_games([_an_game()], [_vsin_game()], [], fetched_at_utc=stamp)[0]
    assert hit.vsin_fetched_at_utc == stamp


def test_vsin_stamp_stays_none_on_a_miss_so_coalesce_preserves_the_old_one():
    """A VSIN miss must not restamp the values it did not confirm.

    The VSIN-derived columns COALESCE-preserve, so without this a sharp price
    and an RLM badge would keep Action Network's fresh timestamp and read as
    current forever.
    """
    miss = merge_games([_an_game()], [], [],
                       fetched_at_utc="2026-08-07T12:00:00+00:00")[0]
    assert miss.vsin_fetched_at_utc is None
    # Action Network's own stamp is unaffected — its values ARE fresh.
    assert miss.fetched_at_utc == "2026-08-07T12:00:00+00:00"


def test_a_circa_only_hit_still_stamps():
    stamp = "2026-08-07T12:00:00+00:00"
    only_circa = merge_games([_an_game()], [], [_vsin_game()], fetched_at_utc=stamp)[0]
    assert only_circa.vsin_fetched_at_utc == stamp
    assert only_circa.sharp_book is not None
