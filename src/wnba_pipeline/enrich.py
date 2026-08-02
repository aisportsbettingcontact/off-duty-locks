"""Game enrichment for the serving layer: both-side splits, Model v0, signals.

Pure functions over plain dicts (the web layer's row shape). Null-safety law:
a missing percentage stays None on BOTH sides — never a fabricated 50/50.
"""

from __future__ import annotations

from typing import Any, Mapping

from wnba_pipeline.model import project_game
from wnba_pipeline.signals import detect_signals

_COMPLEMENTS = {
    "spread_pct_bets_away": "spread_pct_bets_home",
    "spread_pct_money_away": "spread_pct_money_home",
    "total_pct_bets_over": "total_pct_bets_under",
    "total_pct_money_over": "total_pct_money_under",
    "ml_pct_bets_away": "ml_pct_bets_home",
    "ml_pct_money_away": "ml_pct_money_home",
}


def with_both_sides(game: dict[str, Any]) -> dict[str, Any]:
    out = dict(game)
    for src, dst in _COMPLEMENTS.items():
        value = out.get(src)
        out[dst] = None if value is None else 100 - value
    return out


def enrich_games(
    games: list[dict[str, Any]],
    stats_by_team_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    out = []
    for game in games:
        g = with_both_sides(game)
        model = project_game(
            stats_by_team_id.get(str(g.get("away_team_id"))),
            stats_by_team_id.get(str(g.get("home_team_id"))),
            g.get("current_spread"),
            g.get("current_total"),
        )
        g["model"] = model
        g["signals"] = detect_signals(g, model)
        out.append(g)
    return out
