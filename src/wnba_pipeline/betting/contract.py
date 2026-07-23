"""Data shapes and parse helpers for the betting feed.

``AnGame``   — one Action Network game: open + current DraftKings lines,
               moneylines, and DraftKings ticket/money percentages.
``VsinGame`` — one VSIN game: the book's spread/total/moneyline line values
               (used for the Circa sharp line).
``BettingGame`` — the merged, wide per-game row that maps 1:1 to the
               ``betting_games`` table and to a game card on the site.

Parse helpers are deliberately lenient: source cells arrive as numbers or as
strings that may carry ``+`` signs, arrow glyphs, or ``PK``/``EV`` tokens. They
extract the numeric content and return ``None`` when there is none — never a
fabricated zero (except an explicit pick'em spread, which is a real 0).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SIGNED_DECIMAL = re.compile(r"[-+]?\d+(?:\.\d+)?")
_SIGNED_INT = re.compile(r"[-+]?\d+")


def slugify_team(name: str | None) -> str:
    """``"Los Angeles Sparks"`` -> ``"los-angeles-sparks"`` (VSIN slug form)."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def slug_from_href(href: str | None) -> str:
    """Last path segment of a VSIN team href, e.g. ``/wnba/teams/x`` -> ``x``."""
    parts = [p for p in (href or "").split("/") if p]
    return parts[-1] if parts else ""


def parse_line(value: object) -> float | None:
    """Spread/total line as float. ``"PK"``/``"EV"`` (pick'em) -> 0.0; a cell
    with no numeric content -> ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if re.search(r"\b(pk|ev|even)\b", text, re.IGNORECASE) and not _SIGNED_DECIMAL.search(text):
        return 0.0
    match = _SIGNED_DECIMAL.search(text.replace("+", ""))
    if match is None:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def parse_american_odds(value: object) -> int | None:
    """American odds as int (``"+100"`` -> 100, ``-121`` -> -121). ``None`` when
    there is no numeric content."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    match = _SIGNED_INT.search(str(value).replace("+", ""))
    if match is None:
        return None
    try:
        return int(match.group())
    except ValueError:
        return None


@dataclass
class AnGame:
    """One Action Network WNBA game with open + DraftKings markets."""

    game_id: int
    game_date: str            # YYYY-MM-DD (the queried slate date, ET)
    start_time: str | None
    status: str | None
    away_team_id: int
    home_team_id: int
    away_name: str
    away_abbr: str
    home_name: str
    home_abbr: str
    # Opening line (book 30) — away spread, total, away/home moneyline
    open_spread_away: float | None
    open_total: float | None
    open_ml_away: int | None
    open_ml_home: int | None
    # Current DraftKings line (book 68)
    dk_spread_away: float | None
    dk_total: float | None
    dk_ml_away: int | None
    dk_ml_home: int | None
    # DraftKings splits (book 68 bet_info): away side / over side
    spread_pct_bets_away: int | None
    spread_pct_money_away: int | None
    total_pct_bets_over: int | None
    total_pct_money_over: int | None
    ml_pct_bets_away: int | None
    ml_pct_money_away: int | None


@dataclass
class VsinGame:
    """One VSIN game's line values (away spread, total, away/home moneyline)."""

    game_id: str              # VSIN gamecode, e.g. "20260722WNBA06104"
    game_date: str            # YYYY-MM-DD, parsed from the gamecode
    away_slug: str
    home_slug: str
    away_name: str
    home_name: str
    spread_away: float | None
    total: float | None
    ml_away: int | None
    ml_home: int | None


@dataclass
class BettingGame:
    """Merged, wide per-game betting row (maps 1:1 to ``betting_games``)."""

    game_key: str
    game_date: str
    start_time: str | None
    status: str | None
    away_team_id: str | None
    home_team_id: str | None
    away_abbr: str | None
    home_abbr: str | None
    away_name: str | None
    home_name: str | None
    # Spread (away side)
    open_spread: float | None
    current_spread: float | None
    sharp_spread: float | None
    spread_pct_bets_away: int | None
    spread_pct_money_away: int | None
    spread_line_move: float | None
    spread_rlm: bool | None
    # Total (over side)
    open_total: float | None
    current_total: float | None
    sharp_total: float | None
    total_pct_bets_over: int | None
    total_pct_money_over: int | None
    total_line_move: float | None
    total_rlm: bool | None
    # Moneyline
    open_ml_away: int | None
    open_ml_home: int | None
    current_ml_away: int | None
    current_ml_home: int | None
    sharp_ml_away: int | None
    sharp_ml_home: int | None
    ml_pct_bets_away: int | None
    ml_pct_money_away: int | None
    ml_rlm: bool | None
    # Provenance
    public_book: str | None
    sharp_book: str | None
    an_game_id: str | None
    vsin_game_id: str | None
    fetched_at_utc: str | None
