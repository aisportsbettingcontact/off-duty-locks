"""Model v0: exact formulas, null-safety as a unit, edge score bounds."""

import pytest

from wnba_pipeline.model import HOME_COURT_POINTS, project_game


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
    # edge_score = min(10, 2*1.42 + 5.48) = 8.32
    assert m["edge_score"] == pytest.approx(8.32)


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


def test_edge_score_caps_at_10():
    m = project_game(AWAY, HOME, current_spread=-20.0, current_total=100.0)
    assert m["edge_score"] == 10


def test_edge_score_with_one_edge_only():
    m = project_game(AWAY, HOME, current_spread=6.0, current_total=None)
    # only spread edge (1.42): score = 2*1.42 = 2.84
    assert m["edge_total"] is None
    assert m["edge_score"] == pytest.approx(2.84)
