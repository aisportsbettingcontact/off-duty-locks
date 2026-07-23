"""Action Network v2 scoreboard -> open + DraftKings odds/splits per WNBA game.

Port of WNBA-AN-Scraper.ts onto the pipeline's hardened HTTP client. For each
game the scoreboard carries per-book markets; the opening line is book 30 and
DraftKings is book 68. DraftKings outcomes also carry ``bet_info`` with ticket
(bets) and money percentages, so open line, current line, %bets and %money all
come from this one source. Pre-game outcomes (``is_live`` false) are preferred
over in-game lines.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from wnba_pipeline.contract import ExtractionParams  # noqa: F401  (kept for parity)
from wnba_pipeline.http_client import CircuitBreaker, HttpConfig, get_json
from wnba_pipeline.betting.contract import AnGame, parse_american_odds

logger = logging.getLogger("wnba_pipeline.betting.actionnetwork")

AN_SCOREBOARD = "https://api.actionnetwork.com/web/v2/scoreboard/wnba"
BOOK_IDS = "15,30,68,69,71,75,79"
OPEN_BOOK_ID = 30
DK_BOOK_ID = 68

AN_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.actionnetwork.com/",
    "Origin": "https://www.actionnetwork.com",
}


def _find_outcome(
    outcomes: list[dict[str, Any]] | None,
    *,
    side: str | None = None,
    team_id: int | None = None,
) -> dict[str, Any] | None:
    """First matching outcome, preferring pre-game over live in-game lines."""
    if not outcomes:
        return None
    pre = [o for o in outcomes if not o.get("is_live")]
    live = [o for o in outcomes if o.get("is_live")]

    def search(pool: list[dict[str, Any]]) -> dict[str, Any] | None:
        for o in pool:
            if side is not None and o.get("side") == side:
                return o
            if team_id is not None and o.get("team_id") == team_id:
                return o
        return None

    return search(pre) or search(live)


def _value(outcome: dict[str, Any] | None) -> float | None:
    if not outcome:
        return None
    v = outcome.get("value")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _odds(outcome: dict[str, Any] | None) -> int | None:
    return parse_american_odds(outcome.get("odds")) if outcome else None


def _pct(outcome: dict[str, Any] | None, kind: str) -> int | None:
    """``kind`` is 'tickets' (bets) or 'money'. Returns an int percent or None."""
    if not outcome:
        return None
    segment = (outcome.get("bet_info") or {}).get(kind) or {}
    pct = segment.get("percent")
    return int(pct) if isinstance(pct, (int, float)) and not isinstance(pct, bool) else None


def _event(markets: dict[str, Any], book_id: int) -> dict[str, Any]:
    book = markets.get(str(book_id)) or markets.get(book_id) or {}
    return book.get("event") or {}


def parse_scoreboard(payload: dict[str, Any], game_date: str) -> list[AnGame]:
    """Parse a scoreboard payload into AnGame rows for ``game_date``."""
    games = payload.get("games") or []
    out: list[AnGame] = []
    for g in games:
        team_map = {t.get("id"): t for t in (g.get("teams") or [])}
        away = team_map.get(g.get("away_team_id"))
        home = team_map.get(g.get("home_team_id"))
        if not away or not home:
            logger.warning("AN game %s missing team data; skipping", g.get("id"))
            continue

        markets = g.get("markets") or {}
        open_ev = _event(markets, OPEN_BOOK_ID)
        dk_ev = _event(markets, DK_BOOK_ID)
        away_id, home_id = g.get("away_team_id"), g.get("home_team_id")

        dk_spread_away = _find_outcome(dk_ev.get("spread"), side="away")
        dk_total_over = _find_outcome(dk_ev.get("total"), side="over")
        dk_ml_away = _find_outcome(dk_ev.get("moneyline"), team_id=away_id)
        dk_ml_home = _find_outcome(dk_ev.get("moneyline"), team_id=home_id)

        out.append(
            AnGame(
                game_id=g.get("id"),
                game_date=game_date,
                start_time=g.get("start_time"),
                status=g.get("status"),
                away_team_id=away_id,
                home_team_id=home_id,
                away_name=away.get("full_name") or away.get("display_name") or "",
                away_abbr=away.get("abbr") or "",
                home_name=home.get("full_name") or home.get("display_name") or "",
                home_abbr=home.get("abbr") or "",
                open_spread_away=_value(_find_outcome(open_ev.get("spread"), side="away")),
                open_total=_value(_find_outcome(open_ev.get("total"), side="over")),
                open_ml_away=_odds(_find_outcome(open_ev.get("moneyline"), team_id=away_id)),
                open_ml_home=_odds(_find_outcome(open_ev.get("moneyline"), team_id=home_id)),
                dk_spread_away=_value(dk_spread_away),
                dk_total=_value(dk_total_over),
                dk_ml_away=_odds(dk_ml_away),
                dk_ml_home=_odds(dk_ml_home),
                spread_pct_bets_away=_pct(dk_spread_away, "tickets"),
                spread_pct_money_away=_pct(dk_spread_away, "money"),
                total_pct_bets_over=_pct(dk_total_over, "tickets"),
                total_pct_money_over=_pct(dk_total_over, "money"),
                ml_pct_bets_away=_pct(dk_ml_away, "tickets"),
                ml_pct_money_away=_pct(dk_ml_away, "money"),
            )
        )
    return out


def fetch_wnba_odds(
    game_date: str,
    *,
    http: HttpConfig | None = None,
    session: Any = None,
    breaker: CircuitBreaker | None = None,
    sleep: Callable[[float], None] | None = None,
    rng: Any = None,
) -> list[AnGame]:
    """Fetch and parse the Action Network scoreboard for one ET date
    (``YYYY-MM-DD``). Raises ``UpstreamUnavailable`` on transport failure."""
    params = {
        "bookIds": BOOK_IDS,
        "date": game_date.replace("-", ""),
        "periods": "event",
    }
    kwargs: dict[str, Any] = {"session": session, "breaker": breaker, "headers": AN_HEADERS, "rng": rng}
    if sleep is not None:
        kwargs["sleep"] = sleep
    payload, *_ = get_json(AN_SCOREBOARD, params, http or HttpConfig(), **kwargs)
    games = parse_scoreboard(payload, game_date)
    logger.info("AN %s: %d game(s)", game_date, len(games))
    return games
