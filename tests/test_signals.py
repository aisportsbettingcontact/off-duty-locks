"""Signal engine: thresholds, null-safety, conflict semantics, contract shape."""

from wnba_pipeline.signals import detect_signals


def g(**kw):
    base = {
        "spread_pct_bets_away": None, "spread_pct_money_away": None,
        "total_pct_bets_over": None, "total_pct_money_over": None,
        "ml_pct_bets_away": None, "ml_pct_money_away": None,
        "spread_rlm": None, "total_rlm": None, "ml_rlm": None,
        "spread_line_move": None, "total_line_move": None,
    }
    base.update(kw)
    return base


def types_for(signals, market):
    return {s["type"] for s in signals if s["market"] == market}


def test_all_null_yields_no_signals():
    assert detect_signals(g()) == []


def test_sharp_money_fires_at_exact_threshold():
    s = detect_signals(g(spread_pct_bets_away=40, spread_pct_money_away=55))
    assert {"market": "spread", "type": "sharp-money", "side": "away"} in s


def test_sharp_money_below_threshold_is_silent():
    s = detect_signals(g(spread_pct_bets_away=40, spread_pct_money_away=54))
    assert types_for(s, "spread") == set()


def test_sharp_money_side_follows_money_lean_home():
    # money on away 30 vs tickets 45 -> divergence 15 toward HOME side
    s = detect_signals(g(spread_pct_bets_away=45, spread_pct_money_away=30))
    assert {"market": "spread", "type": "sharp-money", "side": "home"} in s


def test_public_heavy_fires_at_70_on_either_side():
    s = detect_signals(g(spread_pct_bets_away=70, spread_pct_money_away=70))
    assert {"market": "spread", "type": "public-heavy", "side": "away"} in s
    s2 = detect_signals(g(spread_pct_bets_away=30, spread_pct_money_away=30))
    assert {"market": "spread", "type": "public-heavy", "side": "home"} in s2


def test_public_heavy_69_is_silent():
    s = detect_signals(g(spread_pct_bets_away=69, spread_pct_money_away=69))
    assert types_for(s, "spread") == set()


def test_rlm_comes_from_stored_booleans_only():
    s = detect_signals(g(spread_rlm=True, total_rlm=False, ml_rlm=None,
                         spread_line_move=1.0))
    assert {"market": "spread", "type": "rlm", "side": "home"} in s
    assert types_for(s, "total") == set()
    assert types_for(s, "moneyline") == set()


def test_rlm_side_is_direction_of_move():
    # away line moved DOWN (toward away) -> RLM side away
    s = detect_signals(g(spread_rlm=True, spread_line_move=-1.0))
    assert {"market": "spread", "type": "rlm", "side": "away"} in s


def test_total_signals_use_over_under_sides():
    s = detect_signals(g(total_pct_bets_over=75, total_pct_money_over=75))
    assert {"market": "total", "type": "public-heavy", "side": "over"} in s


def test_conflict_sharp_vs_rlm_opposite_sides():
    # sharp money toward AWAY while RLM says movement toward HOME -> conflict
    s = detect_signals(g(
        spread_pct_bets_away=40, spread_pct_money_away=60,
        spread_rlm=True, spread_line_move=1.0,
    ))
    assert {"market": "spread", "type": "conflict", "side": None} in s


def test_no_conflict_when_sharp_and_rlm_agree():
    s = detect_signals(g(
        spread_pct_bets_away=60, spread_pct_money_away=40,  # sharp toward home
        spread_rlm=True, spread_line_move=1.0,              # move toward home
    ))
    assert {"market": "spread", "type": "conflict", "side": None} not in s


def test_model_edge_joins_contract():
    s = detect_signals(g(), model={"edge_spread": 2.0, "edge_total": None})
    assert {"market": "spread", "type": "model-edge", "side": "away"} in s
    s2 = detect_signals(g(), model={"edge_spread": -2.0, "edge_total": 2.5})
    assert {"market": "spread", "type": "model-edge", "side": "home"} in s2
    assert {"market": "total", "type": "model-edge", "side": "over"} in s2


def test_model_edge_below_threshold_silent():
    s = detect_signals(g(), model={"edge_spread": 1.4, "edge_total": 1.9})
    assert s == []
