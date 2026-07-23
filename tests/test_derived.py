"""Derived-metric calculations: possessions and offensive rating.

Verifies the exact formulas, null-safety (missing operands never become zero),
and the no-division-by-zero guard.
"""

from __future__ import annotations

import pytest

from wnba_pipeline.derived import derived_metrics, offensive_rating, possessions


def test_possessions_formula():
    # FGA - OREB + TOV + 0.44*FTA = 68 - 10 + 12 + 0.44*20 = 78.8
    stats = {
        "field_goals_attempted": 68.0,
        "offensive_rebounds": 10.0,
        "turnovers": 12.0,
        "free_throws_attempted": 20.0,
    }
    assert possessions(stats) == pytest.approx(78.8)


def test_offensive_rating_points_per_100():
    stats = {
        "points": 82.0,
        "field_goals_attempted": 68.0,
        "offensive_rebounds": 10.0,
        "turnovers": 12.0,
        "free_throws_attempted": 20.0,
    }
    assert offensive_rating(stats) == pytest.approx(82.0 / 78.8 * 100.0)


def test_missing_operand_yields_none_never_zero():
    stats = {
        "field_goals_attempted": 68.0,
        "offensive_rebounds": None,  # missing operand
        "turnovers": 12.0,
        "free_throws_attempted": 20.0,
        "points": 80.0,
    }
    assert possessions(stats) is None
    assert offensive_rating(stats) is None


def test_zero_possessions_no_division_error():
    # All-zero inputs -> possessions 0 -> rating None (never ZeroDivisionError).
    stats = {
        k: 0.0
        for k in (
            "field_goals_attempted",
            "offensive_rebounds",
            "turnovers",
            "free_throws_attempted",
            "points",
        )
    }
    assert possessions(stats) == 0.0
    assert offensive_rating(stats) is None


def test_bool_is_not_a_number():
    # bool is an int subclass but must never be treated as a stat value.
    stats = {
        "field_goals_attempted": True,
        "offensive_rebounds": 0.0,
        "turnovers": 0.0,
        "free_throws_attempted": 0.0,
    }
    assert possessions(stats) is None


def test_derived_metrics_keys_and_values():
    stats = {
        "points": 80.0,
        "field_goals_attempted": 60.0,
        "offensive_rebounds": 8.0,
        "turnovers": 10.0,
        "free_throws_attempted": 15.0,
    }
    d = derived_metrics(stats)
    assert set(d) == {"possessions", "offensive_rating"}
    assert d["possessions"] == pytest.approx(60 - 8 + 10 + 0.44 * 15)
    assert d["offensive_rating"] == pytest.approx(80.0 / d["possessions"] * 100.0)
