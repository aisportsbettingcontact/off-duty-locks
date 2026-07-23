"""Derived team metrics, computed at publish time (serving layer).

These are intentionally NOT part of the validated ``Snapshot`` contract: they
are computed from the already-normalized per-team stats immediately before the
database upsert, so the site reads them as plain columns and never recomputes.

Formulas (as specified by the product owner):

    possessions      = FGA - OREB + TOV + 0.44 * FTA
    offensive_rating = (points / possessions) * 100        # points per 100 poss

Both operate on whatever PerMode the snapshot carries (PerGame here): the
possessions estimate scales with the inputs, and offensive rating is a ratio,
so per-game inputs yield the same rating as season totals.

Every function is null-safe: a missing/unparseable operand yields ``None``
(never a fabricated zero — consistent with the pipeline's core rule), and a
non-positive possessions estimate yields ``None`` for the rating rather than
dividing by zero.
"""

from __future__ import annotations

from typing import Any, Mapping

# Free-throw trips per possession (Oliver's coefficient); part of the formula.
FT_POSSESSION_FACTOR = 0.44


def _num(value: Any) -> float | None:
    """Coerce a stat cell to float, or None. ``bool`` is never a stat value."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def possessions(stats: Mapping[str, Any]) -> float | None:
    """``FGA - OREB + TOV + 0.44 * FTA`` from canonical stat fields.

    Returns ``None`` if any operand is missing/unparseable.
    """
    fga = _num(stats.get("field_goals_attempted"))
    oreb = _num(stats.get("offensive_rebounds"))
    tov = _num(stats.get("turnovers"))
    fta = _num(stats.get("free_throws_attempted"))
    if fga is None or oreb is None or tov is None or fta is None:
        return None
    return fga - oreb + tov + FT_POSSESSION_FACTOR * fta


def offensive_rating(stats: Mapping[str, Any]) -> float | None:
    """``(points / possessions) * 100`` — points per 100 possessions.

    Returns ``None`` when points is missing, possessions can't be computed, or
    possessions is non-positive (no division by zero, no fabricated value).
    """
    pts = _num(stats.get("points"))
    poss = possessions(stats)
    if pts is None or poss is None or poss <= 0:
        return None
    return (pts / poss) * 100.0


def derived_metrics(stats: Mapping[str, Any]) -> dict[str, float | None]:
    """Both derived metrics for one team's stats, keyed by DB column name."""
    return {
        "possessions": possessions(stats),
        "offensive_rating": offensive_rating(stats),
    }
