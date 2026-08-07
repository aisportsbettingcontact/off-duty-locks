"""Data shapes and parse helpers for the betting feed.

``AnGame``   — one Action Network game: opening and current DraftKings lines
               and moneylines (odds only).
``VsinGame`` — one VSIN game: spread/total/moneyline line values (used for the
               Circa sharp line) plus the DK-view %bets / %money splits.
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
_PERCENT = re.compile(r"(\d{1,3})\s*%")

# Characters upstream formatters emit where ASCII hyphen-minus belongs. Left
# untranslated they do not match the sign group, so the regex matches only the
# digits and a FAVOURITE silently becomes an UNDERDOG: "−1650" -> +1650.
_DASH_FORMS = {
    "−": "-",   # MINUS SIGN
    "–": "-",   # EN DASH
    "—": "-",   # EM DASH
    "‒": "-",   # FIGURE DASH
    "‐": "-",   # HYPHEN
    "‑": "-",   # NON-BREAKING HYPHEN
    "－": "-",   # FULLWIDTH HYPHEN-MINUS
    "⁃": "-",   # HYPHEN BULLET
}

# Thousands separator between digits only, so a European decimal comma is not
# silently eaten. Without this "-1,650" matched "-1" and published as -1.
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
# A sign detached from its digits, e.g. "- 110" from a two-node sign badge.
_LOOSE_SIGN = re.compile(r"([-+])[\s ]+(?=\d)")

# No spread or total is ever this large. A value beyond it means a moneyline
# (or a gamecode) landed in a line cell — reject rather than publish it.
MAX_LINE_MAGNITUDE = 500.0
# American odds never fall strictly between -100 and +100, and are never 0.
MIN_AMERICAN_ODDS = 100


def normalize_numeric_text(value: object) -> str:
    """Upstream numeric cell -> a string the parse regexes can read correctly.

    One shared normalizer for every betting parser: dash variants folded to
    ASCII, thousands separators removed, detached signs reattached. Each of
    these produced a wrong published number, not a missing one, which is why
    they are handled before parsing rather than validated after.
    """
    text = str(value).replace(" ", " ")
    for form, ascii_dash in _DASH_FORMS.items():
        text = text.replace(form, ascii_dash)
    text = _THOUSANDS.sub("", text)
    text = _LOOSE_SIGN.sub(r"\1", text)
    return text.strip()


def slugify_team(name: str | None) -> str:
    """``"Los Angeles Sparks"`` -> ``"los-angeles-sparks"`` (VSIN slug form)."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def slug_from_href(href: str | None) -> str:
    """Last path segment of a VSIN team href, e.g. ``/wnba/teams/x`` -> ``x``."""
    parts = [p for p in (href or "").split("/") if p]
    return parts[-1] if parts else ""


def parse_line(value: object) -> float | None:
    """Spread/total line as float. ``"PK"``/``"EV"`` (pick'em) -> 0.0; a cell
    with no numeric content, or a magnitude no line can have -> ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if abs(value) <= MAX_LINE_MAGNITUDE else None
    text = normalize_numeric_text(value)
    if not text:
        return None
    # A pick'em cell names the LINE, and any number beside it is the price:
    # "PK -110" is a zero line at -110 juice, not a -110 line.
    if re.search(r"\b(pk|ev|even)\b", text, re.IGNORECASE):
        return 0.0
    match = _SIGNED_DECIMAL.search(text.replace("+", ""))
    if match is None:
        return None
    try:
        line = float(match.group())
    except ValueError:
        return None
    return line if abs(line) <= MAX_LINE_MAGNITUDE else None


def parse_american_odds(value: object) -> int | None:
    """American odds as int (``"+100"`` -> 100, ``-121`` -> -121).

    ``None`` when there is no numeric content, or when the magnitude is
    impossible for American odds. The guard is the point: a truncated
    ``"-1,650"`` used to publish as ``-1``, a 44-point implied-probability
    error sitting beside the correct ``-1650`` from the other feed.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        odds = int(value)
    else:
        match = _SIGNED_INT.search(normalize_numeric_text(value).replace("+", ""))
        if match is None:
            return None
        try:
            odds = int(match.group())
        except ValueError:
            return None
    return odds if abs(odds) >= MIN_AMERICAN_ODDS else None


def parse_percent(value: object) -> int | None:
    """Integer percent from a VSIN badge like ``"75%"`` or ``"▲ 75%"``.

    Requires a percent SHAPE, not merely a number in range: a layout change can
    land a price or a gamecode in the badge cell, and ``"1,650"`` yielding a
    plausible-looking ``1%`` is worse than yielding nothing.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        pct = int(value)
    else:
        text = normalize_numeric_text(value)
        match = _PERCENT.search(text)
        if match is not None:
            pct = int(match.group(1))
        elif re.fullmatch(r"\d{1,3}", text):
            pct = int(text)          # a bare integer cell, nothing else in it
        else:
            return None
    return pct if 0 <= pct <= 100 else None


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


@dataclass
class VsinGame:
    """One VSIN game: line values plus the book's %bets / %money splits.

    Splits are the away side (spread/moneyline) and the over side (total); the
    home/under side is the complement (100 - away/over)."""

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
    spread_pct_bets_away: int | None = None
    spread_pct_money_away: int | None = None
    total_pct_bets_over: int | None = None
    total_pct_money_over: int | None = None
    ml_pct_bets_away: int | None = None
    ml_pct_money_away: int | None = None


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
    # When VSIN last CONFIRMED this row's splits and sharp line. Distinct from
    # fetched_at_utc, which is Action Network's. The VSIN-derived columns
    # COALESCE-preserve on a miss, so without a separate stamp a sharp price
    # and an RLM badge can outlive the market they describe with nothing on the
    # page or in any gate able to reveal it.
    vsin_fetched_at_utc: str | None = None
