"""Enrichment: both-side math, null passthrough, model+signals attachment."""

from wnba_pipeline.enrich import (
    enrich_games,
    find_team_stats,
    stats_by_team_name,
    with_both_sides,
)


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


def test_enrich_joins_by_name_when_id_namespaces_differ():
    # PRODUCTION regression: betting rows carry Action Network team ids
    # ("1341") while team_stats carries stats.wnba.com ids ("1611661319"),
    # so the id join never matches — the full-name fallback must carry
    # Model v0 (and therefore EDGE + signals) instead.
    games = [{
        "game_key": "2026-08-02:PHX@LVA",
        "away_team_id": "1340", "home_team_id": "1341",
        "away_name": "Phoenix Mercury", "home_name": "Las Vegas Aces",
        "current_spread": 6.0, "current_total": 170.0,
        "spread_pct_bets_away": 72, "spread_pct_money_away": 81,
    }]
    stats = {
        "1611661317": {"team_name": "Phoenix Mercury",
                       "offensive_rating": 104.0, "possessions": 80.0},
        "1611661319": {"team_name": "Las Vegas Aces",
                       "offensive_rating": 110.0, "possessions": 84.0},
    }
    out = enrich_games(games, stats)[0]
    assert out["model"] is not None
    assert out["model"]["spread"] is not None
    assert {"market": "spread", "type": "public-heavy", "side": "away"} in out["signals"]


def test_find_team_stats_id_join_wins_then_normalized_name():
    aces = {"team_name": "Las Vegas Aces", "offensive_rating": 110.0}
    sun = {"team_name": "Connecticut Sun", "offensive_rating": 99.0}
    stats = {"1611661319": aces, "1611661323": sun}
    by_name = stats_by_team_name(stats)
    # Same-namespace id keeps winning even when the name points elsewhere.
    assert find_team_stats(stats, by_name, "1611661319", "Connecticut Sun") is aces
    # Foreign id falls back to the name, normalized (case/punctuation drift).
    assert find_team_stats(stats, by_name, "1341", "  LAS  VEGAS//Aces ") is aces
    # No id match and no name match stays None — never a fabricated row.
    assert find_team_stats(stats, by_name, "1341", "Toronto Tempo") is None
    assert find_team_stats(stats, by_name, None, None) is None


def test_enrich_without_stats_has_null_model():
    games = [{"game_key": "k", "away_team_id": "x", "home_team_id": "y",
              "current_spread": None, "current_total": None}]
    out = enrich_games(games, {})[0]
    assert out["model"] is None
    assert out["signals"] == []
