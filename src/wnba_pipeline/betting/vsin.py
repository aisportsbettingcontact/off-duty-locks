"""VSIN betting-splits page -> per-game line values for a book source.

Port of the sp-table parsing in WNBASplitsScraper.ts using BeautifulSoup. Each
WNBA game is a pair of rows (away then home) with 11 cells; the line values are
td[2] (spread, away side), td[5] (total), td[8] (moneyline). We consume those
line values — primarily to obtain the Circa sharp line (``source=circa``),
which Action Network does not carry. The game's date is parsed from the
gamecode (``YYYYMMDD...``) so games can be matched to Action Network by date.

Percentages are intentionally not consumed here: Action Network's structured
``bet_info`` is the authoritative source for %bets and %money.
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
_TD_TOTAL = 5
_TD_MONEYLINE = 8


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
        rows = table.select("tr.sp-row")
        for i in range(0, len(rows) - 1, 2):
            away_row, home_row = rows[i], rows[i + 1]
            button = away_row.select_one("button[data-gamecode]")
            gamecode = button.get("data-gamecode") if button else None
            if not gamecode or sport not in gamecode:
                continue
            away_link = away_row.select_one("a.sp-team-link")
            home_link = home_row.select_one("a.sp-team-link")
            if not away_link or not home_link:
                continue
            away_tds = away_row.find_all("td")
            home_tds = home_row.find_all("td")
            if len(away_tds) <= _TD_MONEYLINE or len(home_tds) <= _TD_MONEYLINE:
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
                )
            )
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
