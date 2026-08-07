"""VSIN betting-splits page -> per-game line values for a book source.

Port of the sp-table parsing in WNBASplitsScraper.ts using BeautifulSoup. Each
WNBA game is a pair of rows (away then home) with 11 cells. Line values:
td[2] (spread, away side), td[5] (total), td[8] (moneyline). Splits (from the
away row): td[3]/td[4] = spread money/bets, td[6]/td[7] = total money/bets
(over side), td[9]/td[10] = moneyline money/bets (away side); the home/under
side is the complement. The game's date is parsed from the gamecode
(``YYYYMMDD...``) so games can be matched to Action Network by date.

VSIN is the source of %bets / %money; the ``source=circa`` view additionally
provides the sharp line that Action Network does not carry.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from bs4 import BeautifulSoup

from wnba_pipeline.http_client import HttpConfig, get_text
from wnba_pipeline.betting.contract import (
    VsinGame,
    parse_american_odds,
    parse_line,
    parse_percent,
    slug_from_href,
)

logger = logging.getLogger("wnba_pipeline.betting.vsin")

VSIN_URL = "https://data.vsin.com/betting-splits/"

VSIN_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://data.vsin.com/",
}

_GAMECODE_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})")

# Cell indices within an 11-td sp-row (0-indexed), per the VSIN layout.
_TD_SPREAD = 2
_TD_SPREAD_MONEY = 3       # spread handle % (away row = away side)
_TD_SPREAD_BETS = 4        # spread bets %
_TD_TOTAL = 5
_TD_TOTAL_MONEY = 6        # total handle % (away row = over side)
_TD_TOTAL_BETS = 7         # total bets %
_TD_MONEYLINE = 8
_TD_ML_MONEY = 9           # moneyline handle % (away row = away side)
_TD_ML_BETS = 10           # moneyline bets %


def _date_from_gamecode(gamecode: str) -> str:
    m = _GAMECODE_DATE.match(gamecode or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def _cell_text(td: Any) -> str:
    """Text of a cell, preferring its sp-badge span; arrow glyphs and other
    decoration are tolerated (the parse helpers extract the numeric content)."""
    badge = td.select_one("span.sp-badge")
    return (badge.get_text(" ", strip=True) if badge else td.get_text(" ", strip=True)).strip()


def parse_splits(html: str, *, sport: str = "WNBA") -> list[VsinGame]:
    """Parse VSIN splits HTML into VsinGame rows for the given sport block."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[VsinGame] = []
    for table in soup.select("table.sp-table"):
        header = table.select_one("th.sp-sport-name")
        if not header or sport not in header.get_text():
            continue
        # Group the two rows of a game by their SHARED gamecode rather than by
        # position. Positional pairing (rows 0/1, 2/3, ...) desynchronises on a
        # single unexpected row and then pairs one game's away row with the
        # next game's home row — publishing a matchup that does not exist,
        # with one team's own price as its opponent's. Both rows carry the same
        # data-gamecode, so grouping cannot desynchronise.
        rows = table.select("tr.sp-row")
        grouped: dict[str, list[Any]] = {}
        unpaired = 0
        for row in rows:
            button = row.select_one("button[data-gamecode]")
            code = button.get("data-gamecode") if button else None
            if not code or sport not in code:
                unpaired += 1
                continue
            grouped.setdefault(code, []).append(row)

        for gamecode, members in grouped.items():
            if len(members) != 2:
                unpaired += len(members)
                logger.warning(
                    "VSIN game %s: expected 2 rows, found %d; skipping",
                    gamecode, len(members))
                continue
            away_row, home_row = members
            away_link = away_row.select_one("a.sp-team-link")
            home_link = home_row.select_one("a.sp-team-link")
            if not away_link or not home_link:
                continue
            away_tds = away_row.find_all("td")
            home_tds = home_row.find_all("td")
            if len(away_tds) <= _TD_ML_BETS or len(home_tds) <= _TD_MONEYLINE:
                logger.warning("VSIN game %s: unexpected cell count; skipping", gamecode)
                continue
            out.append(
                VsinGame(
                    game_id=gamecode,
                    game_date=_date_from_gamecode(gamecode),
                    away_slug=slug_from_href(away_link.get("href")),
                    home_slug=slug_from_href(home_link.get("href")),
                    away_name=away_link.get_text(strip=True),
                    home_name=home_link.get_text(strip=True),
                    spread_away=parse_line(_cell_text(away_tds[_TD_SPREAD])),
                    total=parse_line(_cell_text(away_tds[_TD_TOTAL])),
                    ml_away=parse_american_odds(_cell_text(away_tds[_TD_MONEYLINE])),
                    ml_home=parse_american_odds(_cell_text(home_tds[_TD_MONEYLINE])),
                    spread_pct_money_away=parse_percent(_cell_text(away_tds[_TD_SPREAD_MONEY])),
                    spread_pct_bets_away=parse_percent(_cell_text(away_tds[_TD_SPREAD_BETS])),
                    total_pct_money_over=parse_percent(_cell_text(away_tds[_TD_TOTAL_MONEY])),
                    total_pct_bets_over=parse_percent(_cell_text(away_tds[_TD_TOTAL_BETS])),
                    ml_pct_money_away=parse_percent(_cell_text(away_tds[_TD_ML_MONEY])),
                    ml_pct_bets_away=parse_percent(_cell_text(away_tds[_TD_ML_BETS])),
                )
            )
        if unpaired:
            # Loud, because a shape change here is how a fabricated matchup
            # would reach the board.
            logger.warning(
                "VSIN %s: %d row(s) could not be paired to a game", sport, unpaired)
    return out


def fetch_vsin(
    source: str,
    view: str,
    *,
    http: HttpConfig | None = None,
    session: Any = None,
    sleep: Callable[[float], None] | None = None,
    rng: Any = None,
) -> list[VsinGame]:
    """Fetch and parse one VSIN view (``source`` e.g. 'DK'/'circa';
    ``view`` 'today'/'tomorrow'). Raises ``UpstreamUnavailable`` on failure."""
    params = {"source": source, "view": view}
    kwargs: dict[str, Any] = {"session": session, "headers": VSIN_HEADERS, "rng": rng}
    if sleep is not None:
        kwargs["sleep"] = sleep
    text, *_ = get_text(VSIN_URL, params, http or HttpConfig(), **kwargs)
    games = parse_splits(text)
    logger.info("VSIN source=%s view=%s: %d game(s)", source, view, len(games))
    return games
