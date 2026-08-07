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
    z                 = hypot(edge_spread/SPREAD_MIN, edge_total/TOTAL_MIN)
    edge_score        = 10 * z**1.5 / (z**1.5 + 1)   # approaches 10, never pins

Null-safety: missing team stats -> None (no model at all); missing market
values -> that edge (and the score, if no edge remains) is None. Never a
fabricated number.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

HOME_COURT_POINTS = 2.5      # WNBA home advantage (documented constant)

# Weight on the last-7 window; the remainder falls on the season split.
#
# Why blend at all: model_total is an UNANCHORED SUM of two offensive ratings,
# with no defensive term and nothing that normalises or regresses it. Whatever
# scoring level the trailing window happens to hold is passed straight into
# every projected total, twice. On 2026-08-07 the league's last-7 mean ORtg was
# 110.56 against a season mean of 106.07 — a +4.49 drift measured across all 15
# teams (7 games each vs 28-32 each), which at ~82.5 possessions per side
# injects roughly +7.4 points into every total the model publishes.
#
# 0.60 is a JUDGEMENT, not an optimum. On a 9-game slate every weight in
# 0.45-0.65 is observationally equivalent, so this value is chosen for being a
# round number inside that range and for keeping recent form dominant. Do not
# describe it as fitted, and do not re-fit it on a single slate.
FORM_WEIGHT = 0.60

# Edge thresholds. The previous values (1.5 / 2.0) sat below the model's own
# dispersion, so the model-edge signal fired on 9 of 9 games on a live slate —
# a signal that fires on everything ranks nothing. These sit near one standard
# deviation of the measured residual distribution. They are round numbers by
# intent: the underlying sample cannot resolve a boundary more finely.
MODEL_EDGE_SPREAD_MIN = 4.0
MODEL_EDGE_TOTAL_MIN = 7.0

_SCORE_CAP = 10.0


def _num(row: Mapping[str, Any] | None, key: str) -> float | None:
    if row is None:
        return None
    value = row.get(key)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def blended_rating(recent: Mapping[str, Any] | None,
                   season: Mapping[str, Any] | None) -> float | None:
    """``FORM_WEIGHT`` on the recent window, the remainder on the season.

    Returns None unless BOTH windows are present. A one-sided fallback would
    silently reintroduce the unanchored trailing window this blend exists to
    correct — and it would do so invisibly, which is worse than no projection.
    """
    recent_ortg = _num(recent, "offensive_rating")
    season_ortg = _num(season, "offensive_rating")
    if recent_ortg is None or season_ortg is None:
        return None
    return FORM_WEIGHT * recent_ortg + (1.0 - FORM_WEIGHT) * season_ortg


def edge_score_for(edge_spread: float | None, edge_total: float | None) -> float | None:
    """0-10 disagreement score that does not pin the middle of a slate at its cap.

    The previous form was ``min(10, 2*|spread| + |total|)``, which saturated on
    5 of 9 live games — the headline number could not rank the majority of the
    board against itself. This scales each market by its own threshold, then
    maps the combined magnitude through a saturating curve that approaches 10
    without ever reaching it, so ordering is preserved all the way up.
    """
    if edge_spread is None and edge_total is None:
        return None
    spread_z = abs(edge_spread or 0.0) / MODEL_EDGE_SPREAD_MIN
    total_z = abs(edge_total or 0.0) / MODEL_EDGE_TOTAL_MIN
    magnitude = math.hypot(spread_z, total_z)
    if magnitude <= 0:
        return 0.0
    shaped = magnitude ** 1.5
    return _SCORE_CAP * shaped / (shaped + 1.0)


def project_game(
    away: Mapping[str, Any] | None,
    home: Mapping[str, Any] | None,
    current_spread: float | None,
    current_total: float | None,
    away_season: Mapping[str, Any] | None = None,
    home_season: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Project one game.

    ``away``/``home`` carry the recent (last-7) window; ``*_season`` carry the
    season split. When both are supplied the offensive ratings are blended (see
    ``FORM_WEIGHT``); when the season split is absent the recent window is used
    alone, which is the pre-blend behaviour and is still correct — just more
    exposed to whatever scoring level the trailing window holds.
    """
    ortg_away = _num(away, "offensive_rating")
    ortg_home = _num(home, "offensive_rating")
    if away_season is not None or home_season is not None:
        blended_away = blended_rating(away, away_season)
        blended_home = blended_rating(home, home_season)
        if blended_away is None or blended_home is None:
            return None
        ortg_away, ortg_home = blended_away, blended_home
    poss_away, poss_home = _num(away, "possessions"), _num(home, "possessions")
    if None in (ortg_away, ortg_home, poss_away, poss_home):
        return None

    poss_avg = (poss_away + poss_home) / 2
    model_spread = (ortg_home - ortg_away) * poss_avg / 100 + HOME_COURT_POINTS
    model_total = (ortg_away + ortg_home) * poss_avg / 100

    edge_spread = None if current_spread is None else current_spread - model_spread
    edge_total = None if current_total is None else model_total - current_total

    edge_score = edge_score_for(edge_spread, edge_total)

    return {
        "spread": model_spread,
        "total": model_total,
        "edge_spread": edge_spread,
        "edge_total": edge_total,
        "edge_score": edge_score,
    }
