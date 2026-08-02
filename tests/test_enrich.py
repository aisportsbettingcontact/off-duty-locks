"""Enrichment: both-side math, null passthrough, model+signals attachment."""

from wnba_pipeline.enrich import enrich_games, with_both_sides


def test_both_sides_complement():
    g = with_both_sides({
        "spread_pct_bets_away": 72, "spread_pct_money_away": 81,
        "total_pct_bets_over": 47, "total_pct_money_over": 53,
        "ml_pct_bets_away": 30, "ml_pct_money_away": 25,
    })
    assert g["spread_pct_bets_home"] == 28
    assert g["spread_pct_money_home"] == 19
    assert g["total_pct_bets_under"] == 53
    assert g["total_pct_money_under"] == 47
    assert g["ml_pct_bets_home"] == 70
    assert g["ml_pct_money_home"] == 75


def test_null_stays_null_never_fifty():
    g = with_both_sides({"spread_pct_bets_away": None})
    assert g["spread_pct_bets_home"] is None


def test_enrich_attaches_model_and_signals():
    games = [{
        "game_key": "2026-08-02:PHX@LAS",
        "away_team_id": "1611661317", "home_team_id": "1611661319",
        "current_spread": 6.0, "current_total": 170.0,
        "spread_pct_bets_away": 72, "spread_pct_money_away": 81,
        "total_pct_bets_over": None, "total_pct_money_over": None,
        "ml_pct_bets_away": None, "ml_pct_money_away": None,
        "spread_rlm": None, "total_rlm": None, "ml_rlm": None,
        "spread_line_move": None, "total_line_move": None,
    }]
    stats = {
        "1611661317": {"offensive_rating": 104.0, "possessions": 80.0},
        "1611661319": {"offensive_rating": 110.0, "possessions": 84.0},
    }
    out = enrich_games(games, stats)[0]
    assert out["model"]["spread"] is not None
    assert isinstance(out["signals"], list)
    assert {"market": "spread", "type": "public-heavy", "side": "away"} in out["signals"]


def test_enrich_without_stats_has_null_model():
    games = [{"game_key": "k", "away_team_id": "x", "home_team_id": "y",
              "current_spread": None, "current_total": None}]
    out = enrich_games(games, {})[0]
    assert out["model"] is None
    assert out["signals"] == []
