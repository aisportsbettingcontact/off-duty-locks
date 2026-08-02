"""Model v0 — transparent, deterministic projections (serving layer).

Not a trained model: a documented formula over the pipeline's verified
team stats (same spirit as the owner's offensive-rating formula in
``derived.py``), labeled "MODEL v0" in the UI. Upgrading to a trained model
later only changes this module.

Formulas (away-side spread convention matches betting_games.current_spread):

    poss_avg          = (poss_away + poss_home) / 2
    proj_margin_home  = (ORtg_home - ORtg_away) * poss_avg / 100 + HOME_COURT_POINTS
    model_spread_away = proj_margin_home        # negative = away favored
    model_total       = (ORtg_away + ORtg_home) * poss_avg / 100
    edge_spread       = current_spread - model_spread   # > 0 value on AWAY
    edge_total        = model_total - current_total     # > 0 value on OVER
    edge_score        = min(10, 2.0*|edge_spread| + 1.0*|edge_total|)

Null-safety: missing team stats -> None (no model at all); missing market
values -> that edge (and the score, if no edge remains) is None. Never a
fabricated number.
"""

from __future__ import annotations

from typing import Any, Mapping

HOME_COURT_POINTS = 2.5      # WNBA home advantage (documented constant)
MODEL_EDGE_SPREAD_MIN = 1.5  # spread edge (pts) to fire the model-edge signal
MODEL_EDGE_TOTAL_MIN = 2.0   # total edge (pts) to fire the model-edge signal

_SPREAD_WEIGHT = 2.0
_TOTAL_WEIGHT = 1.0
_SCORE_CAP = 10.0


def _num(row: Mapping[str, Any] | None, key: str) -> float | None:
    if row is None:
        return None
    value = row.get(key)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def project_game(
    away: Mapping[str, Any] | None,
    home: Mapping[str, Any] | None,
    current_spread: float | None,
    current_total: float | None,
) -> dict[str, Any] | None:
    ortg_away, ortg_home = _num(away, "offensive_rating"), _num(home, "offensive_rating")
    poss_away, poss_home = _num(away, "possessions"), _num(home, "possessions")
    if None in (ortg_away, ortg_home, poss_away, poss_home):
        return None

    poss_avg = (poss_away + poss_home) / 2
    model_spread = (ortg_home - ortg_away) * poss_avg / 100 + HOME_COURT_POINTS
    model_total = (ortg_away + ortg_home) * poss_avg / 100

    edge_spread = None if current_spread is None else current_spread - model_spread
    edge_total = None if current_total is None else model_total - current_total

    if edge_spread is None and edge_total is None:
        edge_score = None
    else:
        edge_score = min(
            _SCORE_CAP,
            _SPREAD_WEIGHT * abs(edge_spread or 0.0) + _TOTAL_WEIGHT * abs(edge_total or 0.0),
        )

    return {
        "spread": model_spread,
        "total": model_total,
        "edge_spread": edge_spread,
        "edge_total": edge_total,
        "edge_score": edge_score,
    }
