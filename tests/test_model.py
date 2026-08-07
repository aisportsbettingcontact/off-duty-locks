"""Model v0: exact formulas, null-safety as a unit, edge score bounds."""

import pytest

from wnba_pipeline.model import (
    FORM_WEIGHT, HOME_COURT_POINTS, MODEL_EDGE_SPREAD_MIN, MODEL_EDGE_TOTAL_MIN,
    blended_rating, edge_score_for, project_game)


AWAY = {"offensive_rating": 104.0, "possessions": 80.0}
HOME = {"offensive_rating": 110.0, "possessions": 84.0}
# poss_avg = 82; margin_home = (110-104)*82/100 + 2.5 = 4.92 + 2.5 = 7.42
# model_spread_away = 7.42 ; model_total = (104+110)*82/100 = 175.48


def test_projection_formulas_exact():
    m = project_game(AWAY, HOME, current_spread=6.0, current_total=170.0)
    assert m["spread"] == pytest.approx(7.42)
    assert m["total"] == pytest.approx(175.48)
    # edge_spread = current - model = 6.0 - 7.42 = -1.42 (value on HOME)
    assert m["edge_spread"] == pytest.approx(-1.42)
    # edge_total = model - current = 5.48 (value on OVER)
    assert m["edge_total"] == pytest.approx(5.48)
    # edge_score scales each market by its own threshold, then saturates:
    #   z = hypot(1.42/4.0, 5.48/7.0) = hypot(0.355, 0.78286) = 0.86971
    #   score = 10 * z**1.5 / (z**1.5 + 1) = 4.4350
    assert m["edge_score"] == pytest.approx(4.4350, abs=0.001)


def test_home_court_constant_is_in_formula():
    no_hca = project_game(AWAY, {**HOME, "offensive_rating": 104.0},
                          current_spread=None, current_total=None)
    assert no_hca["spread"] == pytest.approx(HOME_COURT_POINTS)


def test_missing_stats_yields_none_as_a_unit():
    assert project_game(None, HOME, 6.0, 170.0) is None
    assert project_game(AWAY, {"offensive_rating": None, "possessions": 80.0},
                        6.0, 170.0) is None


def test_missing_market_leaves_edges_null_but_projects():
    m = project_game(AWAY, HOME, current_spread=None, current_total=None)
    assert m["spread"] == pytest.approx(7.42)
    assert m["edge_spread"] is None
    assert m["edge_total"] is None
    assert m["edge_score"] is None


def test_edge_score_stays_inside_its_range_without_ever_pinning():
    """The old form was min(10, ...), which pinned 5 of 9 live games at exactly
    10.0 — the headline number could not rank the majority of a board against
    itself. The replacement approaches 10 asymptotically, so ordering survives
    all the way up."""
    huge = project_game(AWAY, HOME, current_spread=-200.0, current_total=10.0)
    assert 9.0 < huge["edge_score"] < 10.0

    bigger = project_game(AWAY, HOME, current_spread=-400.0, current_total=10.0)
    assert bigger["edge_score"] > huge["edge_score"], "ordering must survive at the top"


def test_edge_score_is_monotonic_in_each_market():
    base = edge_score_for(2.0, 3.0)
    assert edge_score_for(3.0, 3.0) > base
    assert edge_score_for(2.0, 5.0) > base
    assert edge_score_for(-3.0, 3.0) == pytest.approx(edge_score_for(3.0, 3.0))


def test_edge_score_with_one_edge_only():
    m = project_game(AWAY, HOME, current_spread=6.0, current_total=None)
    assert m["edge_total"] is None
    # z = 1.42/4.0 = 0.355 -> 10 * 0.355**1.5 / (0.355**1.5 + 1)
    assert m["edge_score"] == pytest.approx(edge_score_for(-1.42, None), abs=1e-9)
    assert 0.0 < m["edge_score"] < 10.0


def test_a_zero_edge_scores_zero_not_none():
    assert edge_score_for(0.0, 0.0) == 0.0
    assert edge_score_for(None, None) is None


# --------------------------------------------------------------------------- #
# Blended offensive rating
# --------------------------------------------------------------------------- #

def test_blend_weights_recent_form_against_the_season():
    blended = blended_rating({"offensive_rating": 120.0}, {"offensive_rating": 100.0})
    assert blended == pytest.approx(FORM_WEIGHT * 120.0 + (1 - FORM_WEIGHT) * 100.0)


def test_blend_requires_both_windows_and_never_falls_back_to_one():
    """A one-sided fallback would silently reinstate the unanchored trailing
    window the blend exists to correct."""
    assert blended_rating({"offensive_rating": 120.0}, None) is None
    assert blended_rating(None, {"offensive_rating": 100.0}) is None
    assert blended_rating({"offensive_rating": 120.0}, {"offensive_rating": None}) is None


def test_projection_uses_the_blend_when_a_season_split_is_supplied():
    away_season = {"offensive_rating": 94.0}
    home_season = {"offensive_rating": 100.0}
    blended = project_game(AWAY, HOME, 6.0, 170.0,
                           away_season=away_season, home_season=home_season)
    recent_only = project_game(AWAY, HOME, 6.0, 170.0)
    assert blended["total"] < recent_only["total"], (
        "blending toward a cooler season must pull the projected total down")
    expected_away = FORM_WEIGHT * 104.0 + (1 - FORM_WEIGHT) * 94.0
    expected_home = FORM_WEIGHT * 110.0 + (1 - FORM_WEIGHT) * 100.0
    assert blended["total"] == pytest.approx((expected_away + expected_home) * 82 / 100)


def test_projection_is_none_when_a_season_split_is_partially_missing():
    assert project_game(AWAY, HOME, 6.0, 170.0,
                        away_season={"offensive_rating": 94.0},
                        home_season=None) is None


def test_thresholds_sit_above_the_noise_they_are_meant_to_clear():
    """1.5/2.0 fired on 9 of 9 live games. Whatever the exact values, they must
    stay well clear of a fraction of a point."""
    assert MODEL_EDGE_SPREAD_MIN >= 3.0
    assert MODEL_EDGE_TOTAL_MIN >= 5.0
