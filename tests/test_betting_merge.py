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
    circa = vsin.parse_splits((fixtures_dir / "betting" / "vsin_circa_wnba.html").read_text())
    return an, circa


def test_merge_attaches_circa_and_computes(fixtures_dir):
    an, circa = _fixtures(fixtures_dir)
    merged = {m.game_key: m for m in merge_games(an, circa, fetched_at_utc="2026-07-22T12:00:00Z")}

    phx = merged["2026-07-22:PHX@LA"]
    assert phx.sharp_book == "Circa"
    assert phx.sharp_spread == 1.0 and phx.sharp_total == 176.0
    assert phx.public_book == "DraftKings"
    assert phx.total_line_move == -2.0
    assert phx.total_rlm is True          # public 79% over, total dropped

    minsea = merged["2026-07-22:MIN@SEA"]
    assert minsea.spread_line_move == -1.0
    assert minsea.spread_rlm is True      # public on home, line moved toward away


def test_merge_without_circa_leaves_sharp_null(fixtures_dir):
    an, _ = _fixtures(fixtures_dir)
    merged = merge_games(an, [], fetched_at_utc="t")
    assert merged
    assert all(m.sharp_spread is None and m.sharp_book is None for m in merged)
    # AN-derived fields are still populated without VSIN.
    assert all(m.current_spread is not None for m in merged)
