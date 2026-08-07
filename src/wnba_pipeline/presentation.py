"""Presentation-layer derivations for the research interface (pure, null-safe).

This module turns stored values into the things a reader actually needs: which
TEAM is favored rather than a signed away-side number, which way a line MOVED
rather than two numbers to subtract, what a signal MEANS rather than a colored
dot, and how recent form compares to the season.

Derivation law (spec §37). A value may be computed here only when the
computation is mathematically deterministic from stored values:

    home spread      = -away spread
    percentage pair  = 100 - known side          (done upstream in enrich.py)
    line movement    = current - opening
    form difference  = last7 offensive rating - season offensive rating

Anything else is unknown. Unknown stays ``None`` and renders as an em-dash.
Nothing here invents a probability, a price, a rank movement, or a missing
half of a split. Nothing here changes Model v0 or signal mathematics — it only
translates their output into language.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable, Mapping, Sequence

from wnba_pipeline.enrich import normalize_team_name

# Brand-law rating-ring thresholds (design-system/off-duty-locks/MASTER.md).
RING_HOT_MIN = 7.5
RING_WARM_MIN = 5.0

# model.py caps edge_score here; a capped score is a floor ("10 or more"), not
# a measurement, and the UI labels it as such.
EDGE_SCORE_CAP = 10.0

_MARKET_LABELS = {"spread": "Spread", "total": "Total", "moneyline": "Moneyline"}

# Rarest / most actionable first. model-edge sits last deliberately: it fires on
# nearly every game, so leading with it would bury a conflict or an RLM.
_SIGNAL_ORDER = ("conflict", "rlm", "sharp-money", "public-heavy", "model-edge")

_SIGNAL_LABELS = {
    "conflict": "Conflict",
    "rlm": "Reverse Line Move",
    "sharp-money": "Sharp Money",
    "public-heavy": "Public Heavy",
    "model-edge": "Model Edge",
}

# market -> (away/over bets column, away/over money column)
_SPLIT_COLUMNS = {
    "spread": ("spread_pct_bets_away", "spread_pct_money_away"),
    "total": ("total_pct_bets_over", "total_pct_money_over"),
    "moneyline": ("ml_pct_bets_away", "ml_pct_money_away"),
}

# market -> (first-side key, second-side key) on an already-both-sided game row
_SPLIT_PAIRS = {
    "spread": (("spread_pct_bets_away", "spread_pct_bets_home"),
               ("spread_pct_money_away", "spread_pct_money_home")),
    "total": (("total_pct_bets_over", "total_pct_bets_under"),
              ("total_pct_money_over", "total_pct_money_under")),
    "moneyline": (("ml_pct_bets_away", "ml_pct_bets_home"),
                  ("ml_pct_money_away", "ml_pct_money_home")),
}


def _num(value: Any) -> float | None:
    """A stat cell as float, or None. ``bool`` is never a numeric value."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _one(value: float) -> str:
    """One decimal, no trailing sign."""
    return f"{value:.1f}"


# --------------------------------------------------------------------------- #
# Market sides — every number appears on the row of the team it belongs to
# --------------------------------------------------------------------------- #

def spread_sides(away_spread: Any) -> tuple[str | None, str | None]:
    """``(away, home)`` spread strings. The home side is the exact negation —
    the one derivation that is always valid on a two-sided line."""
    value = _num(away_spread)
    if value is None:
        return (None, None)
    if value == 0:
        return ("PK", "PK")          # pick 'em: a signed zero would be a lie
    return (f"{value:+.1f}", f"{-value:+.1f}")


def moneyline_sides(ml_away: Any, ml_home: Any) -> tuple[str | None, str | None]:
    """American odds per side. Each side is independent — one missing price
    never implies the other."""
    def fmt(value: Any) -> str | None:
        num = _num(value)
        if num is None:
            return None
        return f"+{int(num)}" if num > 0 else str(int(num))
    return (fmt(ml_away), fmt(ml_home))


def total_sides(total: Any) -> tuple[str | None, str | None]:
    """``(over, under)`` labels for one total, so each row carries its side."""
    value = _num(total)
    if value is None:
        return (None, None)
    return (f"O {_one(value)}", f"U {_one(value)}")


# --------------------------------------------------------------------------- #
# Line movement — the delta, and the side it moved toward, stated in words
# --------------------------------------------------------------------------- #

def line_movement(market: str, opening: Any, current: Any,
                  away_abbr: Any, home_abbr: Any) -> dict[str, Any] | None:
    """``current - opening`` plus the side it moved toward.

    Spread is an away-side line, so moving UP means the away team is getting
    more points and the market has moved toward the HOME team — the same
    convention ``signals._rlm_side`` uses. Returns None when either endpoint is
    unknown: a movement needs two points, and one is not half a movement.
    """
    start, end = _num(opening), _num(current)
    if start is None or end is None:
        return None
    delta = end - start
    if delta == 0:
        return {"delta": 0, "toward": None, "magnitude": "0.0",
                "text": "Unchanged"}
    if market == "total":
        toward = "Over" if delta > 0 else "Under"
    else:
        toward = str(home_abbr or "home") if delta > 0 else str(away_abbr or "away")
    magnitude = _one(abs(delta))
    return {"delta": delta, "toward": toward, "magnitude": magnitude,
            "text": f"{magnitude} toward {toward}"}


# --------------------------------------------------------------------------- #
# Model v0 — translated out of the away-side sign convention
# --------------------------------------------------------------------------- #

def _favorite(away_side_line: float | None, away_abbr: Any, home_abbr: Any
              ) -> dict[str, Any] | None:
    """An away-side line as ``{team, by, text}``. Positive = home favored."""
    if away_side_line is None:
        return None
    if away_side_line == 0:
        return {"team": None, "by": 0.0, "text": "Pick 'em"}
    team = str(home_abbr or "Home") if away_side_line > 0 else str(away_abbr or "Away")
    by = abs(away_side_line)
    return {"team": team, "by": by, "text": f"{team} by {_one(by)}"}


def band_for(edge_score: Any) -> str | None:
    """Rating-ring band from the brand-law thresholds."""
    score = _num(edge_score)
    if score is None:
        return None
    if score >= RING_HOT_MIN:
        return "hot"
    if score >= RING_WARM_MIN:
        return "warm"
    return "cool"


def model_view(model: Mapping[str, Any] | None, current_spread: Any,
               current_total: Any, away_abbr: Any, home_abbr: Any
               ) -> dict[str, Any] | None:
    """Model v0 output as basketball language, beside the market it disagrees
    with. Returns None when there is no model at all (missing team stats)."""
    if not model:
        return None

    projected = _favorite(_num(model.get("spread")), away_abbr, home_abbr)
    market = _favorite(_num(current_spread), away_abbr, home_abbr)

    edge_spread = _num(model.get("edge_spread"))
    edge_total = _num(model.get("edge_total"))
    score = _num(model.get("edge_score"))

    spread_lean = None
    if edge_spread is not None and edge_spread != 0:
        # edge_spread > 0 -> the model sees value on the AWAY side.
        spread_lean = str(away_abbr) if edge_spread > 0 else str(home_abbr)

    total_lean = None
    if edge_total is not None and edge_total != 0:
        total_lean = "Over" if edge_total > 0 else "Under"

    return {
        "projected_spread": projected,
        "market_spread": market,
        "spread_lean": spread_lean,
        "spread_diff": abs(edge_spread) if edge_spread is not None else None,
        "spread_diff_text": (f"{_one(abs(edge_spread))} pts"
                             if edge_spread is not None else None),
        "projected_total": _num(model.get("total")),
        "projected_total_text": (_one(_num(model.get("total")))
                                 if _num(model.get("total")) is not None else None),
        "market_total": _num(current_total),
        "market_total_text": (_one(_num(current_total))
                              if _num(current_total) is not None else None),
        "total_lean": total_lean,
        "total_diff": abs(edge_total) if edge_total is not None else None,
        "total_diff_text": (f"{_one(abs(edge_total))} pts"
                            if edge_total is not None else None),
        "edge_score": score,
        "edge_score_text": _one(score) if score is not None else None,
        "band": band_for(score),
        "capped": score is not None and score >= EDGE_SCORE_CAP,
    }


# --------------------------------------------------------------------------- #
# Signals — a label, a named side, and a sentence saying why it fired
# --------------------------------------------------------------------------- #

def _side_label(market: str, side: Any, away_abbr: Any, home_abbr: Any) -> str | None:
    if side is None:
        return None
    if market == "total":
        return "Over" if side == "over" else "Under"
    return str(away_abbr) if side == "away" else str(home_abbr)


def _side_pcts(market: str, side: Any, game: Mapping[str, Any]
               ) -> tuple[int | None, int | None]:
    """``(tickets%, money%)`` for the side a signal points at."""
    bets_col, money_col = _SPLIT_COLUMNS[market]
    bets, money = game.get(bets_col), game.get(money_col)
    first_side = "over" if market == "total" else "away"
    if side is not None and side != first_side:
        bets = None if bets is None else 100 - bets
        money = None if money is None else 100 - money
    return bets, money


def _detail(kind: str, market: str, side_label: str | None,
            bets: int | None, money: int | None) -> str:
    market_word = _MARKET_LABELS[market].lower()
    if kind == "sharp-money" and side_label and bets is not None and money is not None:
        return f"{money}% of money on {side_label} vs {bets}% of tickets."
    if kind == "public-heavy" and side_label and bets is not None:
        return f"{bets}% of tickets on {side_label}."
    if kind == "rlm" and side_label:
        return (f"The {market_word} moved toward {side_label} "
                "against the ticket majority.")
    if kind == "conflict":
        return (f"Sharp money and the {market_word} move point in "
                "opposite directions.")
    if kind == "model-edge" and side_label:
        return f"Model v0 projects the {market_word} away from the market toward {side_label}."
    return f"{_SIGNAL_LABELS.get(kind, kind)} on the {market_word}."


def describe_signals(signals: Iterable[Mapping[str, Any]],
                     game: Mapping[str, Any],
                     away_abbr: Any, home_abbr: Any) -> list[dict[str, Any]]:
    """Raw signal dicts -> self-explanatory display records, rarest first.

    Purely additive: the signal engine stays authoritative, this only names
    what it produced.
    """
    out: list[dict[str, Any]] = []
    for signal in signals or ():
        market = signal.get("market")
        kind = signal.get("type")
        if market not in _MARKET_LABELS or kind not in _SIGNAL_LABELS:
            continue
        side_label = _side_label(market, signal.get("side"), away_abbr, home_abbr)
        bets, money = _side_pcts(market, signal.get("side"), game)
        out.append({
            "type": kind,
            "market": market,
            "market_label": _MARKET_LABELS[market],
            "label": _SIGNAL_LABELS[kind],
            "side": signal.get("side"),
            "side_label": side_label,
            "detail": _detail(kind, market, side_label, bets, money),
        })
    out.sort(key=lambda s: _SIGNAL_ORDER.index(s["type"]))
    return out


# --------------------------------------------------------------------------- #
# Betting splits — both sides, named, with the divergence made explicit
# --------------------------------------------------------------------------- #

def _pair(game: Mapping[str, Any], keys: tuple[str, str],
          labels: tuple[str, str]) -> list[dict[str, Any]]:
    """Two labelled percentages, or [] when the market has no data at all.

    A present side beside a missing one keeps the missing one None — the
    complement is only derivable from a known value.
    """
    first, second = game.get(keys[0]), game.get(keys[1])
    if first is None and second is None:
        return []
    return [{"label": labels[0], "pct": first},
            {"label": labels[1], "pct": second}]


def split_blocks(game: Mapping[str, Any], away_abbr: Any, home_abbr: Any
                 ) -> list[dict[str, Any]]:
    """One block per market: tickets, money, and their divergence."""
    away, home = str(away_abbr or "Away"), str(home_abbr or "Home")
    sides = {"spread": (away, home), "total": ("Over", "Under"),
             "moneyline": (away, home)}
    blocks = []
    for market, (bets_keys, money_keys) in _SPLIT_PAIRS.items():
        labels = sides[market]
        tickets = _pair(game, bets_keys, labels)
        money = _pair(game, money_keys, labels)
        bets_first, money_first = game.get(bets_keys[0]), game.get(money_keys[0])
        divergence = (abs(money_first - bets_first)
                      if bets_first is not None and money_first is not None else None)
        blocks.append({
            "market": market,
            "market_label": _MARKET_LABELS[market],
            "tickets": tickets,
            "money": money,
            "divergence": divergence,
        })
    return blocks


# --------------------------------------------------------------------------- #
# Power rankings — ordering, a shared honest bar scale, and recent form
# --------------------------------------------------------------------------- #

def rankings_view_with_scale(last7_rows: Sequence[Mapping[str, Any]],
                             ytd_rows: Sequence[Mapping[str, Any]]
                             ) -> tuple[list[dict[str, Any]], dict[str, float] | None]:
    """Ranked rows plus the real bar-scale domain.

    The bar is normalized across the visible set, so it compares teams to each
    other rather than to an invented zero. The domain travels with the rows so
    the UI can print it — a truncated axis that says what it is stays honest;
    a silent one does not.

    The scale also carries ``form_league_avg``: the mean of every team's
    last7-minus-season difference. Recent form moves league-wide (on real
    2026-08-07 data every-team-but-two was positive, mean +4.5), so a bare
    "+10.2 vs season" reads as team improvement when most of it is the league.
    Publishing the mean lets the UI caption the baseline instead of implying
    one.
    """
    season_by_name = {normalize_team_name(r.get("team_name")): r
                      for r in ytd_rows or ()}

    ordered = sorted(
        last7_rows or (),
        key=lambda r: (_num(r.get("offensive_rating")) is None,
                       -(_num(r.get("offensive_rating")) or 0.0),
                       str(r.get("team_name") or "")),
    )

    ratings = [_num(r.get("offensive_rating")) for r in ordered]
    present = [v for v in ratings if v is not None]
    scale = {"min": min(present), "max": max(present)} if present else None
    span = (scale["max"] - scale["min"]) if scale else 0.0

    rows: list[dict[str, Any]] = []
    for index, row in enumerate(ordered):
        rating = _num(row.get("offensive_rating"))
        if rating is None or scale is None:
            bar_pct = None
        elif span == 0:
            bar_pct = 100          # every team equal: one full, comparable bar
        else:
            bar_pct = round((rating - scale["min"]) / span * 100)

        season = season_by_name.get(normalize_team_name(row.get("team_name")))
        season_rating = _num(season.get("offensive_rating")) if season else None
        form_delta = (rating - season_rating
                      if rating is not None and season_rating is not None else None)

        # Record always reads as the SEASON record. The last-7 row's wins and
        # losses count only games inside that window, so a 5-2 there belongs to
        # a team that may be 19-11 on the year — two different claims that look
        # identical on screen.
        record_row = season if season is not None else row
        wins, losses = record_row.get("wins"), record_row.get("losses")
        rows.append({
            "rank": index + 1,
            "team_name": row.get("team_name"),
            "offensive_rating": rating,
            "possessions": _num(row.get("possessions")),
            "points": _num(row.get("points")),
            "wins": wins,
            "losses": losses,
            "record": f"{wins}-{losses}" if wins is not None and losses is not None else None,
            "bar_pct": bar_pct,
            "season_rating": season_rating,
            "form_delta": form_delta,
            "form_text": (f"{form_delta:+.1f} vs season"
                          if form_delta is not None else None),
        })

    deltas = [r["form_delta"] for r in rows if r["form_delta"] is not None]
    if scale is not None:
        scale = dict(scale)
        scale["form_league_avg"] = (sum(deltas) / len(deltas)) if deltas else None
    return rows, scale


def rankings_view(last7_rows: Sequence[Mapping[str, Any]],
                  ytd_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return rankings_view_with_scale(last7_rows, ytd_rows)[0]


# --------------------------------------------------------------------------- #
# Slate grouping — a date a reader recognizes without doing calendar math
# --------------------------------------------------------------------------- #

_RELATIVE = {-1: "Yesterday", 0: "Today", 1: "Tomorrow"}


def _as_date(value: Any) -> _dt.date | None:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def group_by_date(games: Sequence[Mapping[str, Any]], today: Any = None
                  ) -> list[dict[str, Any]]:
    """Games grouped into dated sections, earliest first.

    Undated rows collect into a trailing "Scheduled" group rather than being
    dropped or guessed into a day.
    """
    reference = _as_date(today) or _dt.datetime.now(_dt.timezone.utc).date()
    buckets: dict[Any, list[Mapping[str, Any]]] = {}
    for game in games or ():
        buckets.setdefault(_as_date(game.get("game_date")), []).append(game)

    groups = []
    for date in sorted((d for d in buckets if d is not None)):
        offset = (date - reference).days
        groups.append({
            "date": date.isoformat(),
            "label": _RELATIVE.get(offset) or date.strftime("%A"),
            "long_label": f"{date.strftime('%B')} {date.day}, {date.year}",
            "weekday": date.strftime("%A"),
            "games": buckets[date],
        })
    if None in buckets:
        groups.append({"date": None, "label": "Scheduled", "long_label": None,
                       "weekday": None, "games": buckets[None]})
    return groups
