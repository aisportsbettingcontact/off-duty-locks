"""Betting-signal detection (serving layer, pure functions).

Signals derive ONLY from stored scrape data (and Model v0 edges when
provided). Null inputs never fire a signal — missing data is missing, not
neutral. Colors are a UI concern (design-system/off-duty-locks/MASTER.md).

Contract: detect_signals(game, model) -> list of
    {"market": "spread"|"total"|"moneyline",
     "type": "sharp-money"|"public-heavy"|"rlm"|"model-edge"|"conflict",
     "side": "away"|"home"|"over"|"under"|None}
"""

from __future__ import annotations

from typing import Any, Mapping

from wnba_pipeline.model import MODEL_EDGE_SPREAD_MIN, MODEL_EDGE_TOTAL_MIN

SHARP_DIVERGENCE_PCT = 15  # |money% - tickets%| to call money sharp
PUBLIC_HEAVY_PCT = 70      # tickets% on one side to call the public heavy

# (market, bets column, money column, side the pct refers to, other side)
_MARKETS = (
    ("spread", "spread_pct_bets_away", "spread_pct_money_away", "away", "home"),
    ("total", "total_pct_bets_over", "total_pct_money_over", "over", "under"),
    ("moneyline", "ml_pct_bets_away", "ml_pct_money_away", "away", "home"),
)

_RLM_FIELDS = {"spread": "spread_rlm", "total": "total_rlm", "moneyline": "ml_rlm"}
_MOVE_FIELDS = {"spread": "spread_line_move", "total": "total_line_move"}


def _sig(market: str, type_: str, side: str | None) -> dict[str, Any]:
    return {"market": market, "type": type_, "side": side}


def _moneyline_move(game: Mapping[str, Any]) -> float | None:
    """Away-side moneyline movement, derived from the two stored prices.

    There is no ``ml_line_move`` column, so the moneyline had no entry in
    ``_MOVE_FIELDS`` and ``_rlm_side`` looked up ``game.get("")`` — always None.
    A moneyline RLM therefore always rendered with no side, and a moneyline
    conflict (which needs a direction to disagree with) could never fire at all.
    Both endpoints ARE stored, so the movement is derivable.

    American odds are not linear across zero, so only the SIGN of the shift is
    used: a rising away price means the away side got longer, i.e. the market
    moved toward the home team.
    """
    opening, current = game.get("open_ml_away"), game.get("current_ml_away")
    if opening is None or current is None:
        return None
    return float(current) - float(opening)


def _rlm_side(market: str, game: Mapping[str, Any], pct_side: str, other: str) -> str | None:
    """Direction the line moved (the side RLM points toward)."""
    if market == "moneyline":
        move = _moneyline_move(game)
    else:
        move = game.get(_MOVE_FIELDS.get(market, ""), None)
    if move is None or move == 0:
        return None
    if market == "total":
        return "over" if move > 0 else "under"
    # away-side line: moving UP = toward home, DOWN = toward away
    return other if move > 0 else pct_side


def detect_signals(
    game: Mapping[str, Any], model: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for market, bets_col, money_col, pct_side, other in _MARKETS:
        bets, money = game.get(bets_col), game.get(money_col)
        sharp_side: str | None = None
        if bets is not None and money is not None:
            if abs(money - bets) >= SHARP_DIVERGENCE_PCT:
                sharp_side = pct_side if money > bets else other
                out.append(_sig(market, "sharp-money", sharp_side))
        if bets is not None:
            if bets >= PUBLIC_HEAVY_PCT:
                out.append(_sig(market, "public-heavy", pct_side))
            elif (100 - bets) >= PUBLIC_HEAVY_PCT:
                out.append(_sig(market, "public-heavy", other))

        rlm_toward: str | None = None
        if game.get(_RLM_FIELDS[market]) is True:
            rlm_toward = _rlm_side(market, game, pct_side, other)
            out.append(_sig(market, "rlm", rlm_toward))

        # Conflict: sharp money points one way while the line moved the other.
        if sharp_side and rlm_toward and sharp_side != rlm_toward:
            out.append(_sig(market, "conflict", None))

    if model:
        es = model.get("edge_spread")
        if es is not None and abs(es) >= MODEL_EDGE_SPREAD_MIN:
            out.append(_sig("spread", "model-edge", "away" if es > 0 else "home"))
        et = model.get("edge_total")
        if et is not None and abs(et) >= MODEL_EDGE_TOTAL_MIN:
            out.append(_sig("total", "model-edge", "over" if et > 0 else "under"))

    return out
