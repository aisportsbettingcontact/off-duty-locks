"""Signal engine: thresholds, null-safety, conflict semantics, contract shape."""

from wnba_pipeline.model import MODEL_EDGE_SPREAD_MIN, MODEL_EDGE_TOTAL_MIN
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


# Stated relative to the thresholds, not as literals: the previous values were
# pinned just above 1.5/2.0, so raising the thresholds broke the tests rather
# than the behaviour they describe.
_OVER_SPREAD = MODEL_EDGE_SPREAD_MIN + 0.5
_OVER_TOTAL = MODEL_EDGE_TOTAL_MIN + 0.5
_UNDER_SPREAD = MODEL_EDGE_SPREAD_MIN - 0.1
_UNDER_TOTAL = MODEL_EDGE_TOTAL_MIN - 0.1


def test_model_edge_joins_contract():
    s = detect_signals(g(), model={"edge_spread": _OVER_SPREAD, "edge_total": None})
    assert {"market": "spread", "type": "model-edge", "side": "away"} in s
    s2 = detect_signals(g(), model={"edge_spread": -_OVER_SPREAD,
                                    "edge_total": _OVER_TOTAL})
    assert {"market": "spread", "type": "model-edge", "side": "home"} in s2
    assert {"market": "total", "type": "model-edge", "side": "over"} in s2


def test_model_edge_below_threshold_silent():
    s = detect_signals(g(), model={"edge_spread": _UNDER_SPREAD,
                                   "edge_total": _UNDER_TOTAL})
    assert s == []


def test_model_edge_fires_exactly_at_the_threshold():
    s = detect_signals(g(), model={"edge_spread": MODEL_EDGE_SPREAD_MIN,
                                   "edge_total": MODEL_EDGE_TOTAL_MIN})
    assert {"market": "spread", "type": "model-edge", "side": "away"} in s
    assert {"market": "total", "type": "model-edge", "side": "over"} in s


def test_moneyline_rlm_now_carries_a_side():
    """`_MOVE_FIELDS` had no moneyline entry, so `game.get("")` was always None:
    a moneyline RLM rendered with no side and a moneyline conflict could never
    fire. Both endpoints are stored, so the movement is derivable."""
    s = detect_signals(g(
        ml_pct_bets_away=80,          # public heavy on away
        ml_rlm=True,
        open_ml_away=110, current_ml_away=140,   # away price got longer
    ))
    rlm = [x for x in s if x["type"] == "rlm" and x["market"] == "moneyline"]
    assert rlm, "moneyline RLM did not fire"
    assert rlm[0]["side"] == "home", "a lengthening away price moves toward home"


def test_moneyline_rlm_side_is_none_without_both_prices():
    s = detect_signals(g(ml_pct_bets_away=80, ml_rlm=True, current_ml_away=140))
    rlm = [x for x in s if x["type"] == "rlm" and x["market"] == "moneyline"]
    assert rlm and rlm[0]["side"] is None


def test_moneyline_conflict_can_now_fire():
    s = detect_signals(g(
        ml_pct_bets_away=20, ml_pct_money_away=60,   # sharp money toward away
        ml_rlm=True,
        open_ml_away=110, current_ml_away=140,       # but the line moved to home
    ))
    assert {"market": "moneyline", "type": "conflict", "side": None} in s
