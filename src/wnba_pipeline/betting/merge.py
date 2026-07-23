"""Merge Action Network (backbone) with the VSIN Circa (sharp) line.

Action Network supplies open/current/%bets/%money; VSIN Circa supplies the
sharp line, matched by ``(date, unordered team-slug pair)`` and oriented to
Action Network's away/home. Line movement is ``current - open``; reverse line
movement (RLM) is when the line moves toward the side the public (ticket
majority) is NOT on — a classic sharp-money signal.
"""

from __future__ import annotations

from collections.abc import Iterable

from wnba_pipeline.betting.contract import AnGame, BettingGame, VsinGame, slugify_team

PUBLIC_BOOK = "DraftKings"
SHARP_BOOK = "Circa"


def _key(game_date: str, slug_a: str, slug_b: str) -> tuple[str, frozenset[str]]:
    return (game_date, frozenset((slug_a, slug_b)))


def index_vsin(games: Iterable[VsinGame]) -> dict[tuple[str, frozenset[str]], VsinGame]:
    """Index VSIN games by (date, unordered slug pair); first wins on collision."""
    index: dict[tuple[str, frozenset[str]], VsinGame] = {}
    for g in games:
        index.setdefault(_key(g.game_date, g.away_slug, g.home_slug), g)
    return index


def line_move(current: float | None, open_: float | None) -> float | None:
    """current - open, rounded to 2 dp; None if either side is missing."""
    if current is None or open_ is None:
        return None
    return round(current - open_, 2)


def rlm_spread(pct_bets_away: int | None, move: float | None) -> bool | None:
    """RLM on the spread (away perspective). The away line moving up (less
    negative) is movement toward the home side. RLM = the line moved toward the
    side the ticket majority is NOT on."""
    if pct_bets_away is None or move is None or move == 0 or pct_bets_away == 50:
        return None
    public_on_away = pct_bets_away > 50
    moved_toward_home = move > 0
    return public_on_away == moved_toward_home


def rlm_total(pct_bets_over: int | None, move: float | None) -> bool | None:
    """RLM on the total (over perspective). The total moving down is movement
    toward the under. RLM = the line moved toward the side the ticket majority
    is NOT on."""
    if pct_bets_over is None or move is None or move == 0 or pct_bets_over == 50:
        return None
    public_on_over = pct_bets_over > 50
    moved_toward_under = move < 0
    return public_on_over == moved_toward_under


def rlm_moneyline(
    pct_bets_away: int | None,
    open_ml_away: int | None,
    current_ml_away: int | None,
) -> bool | None:
    """RLM on the moneyline (away perspective). American odds increase as a
    side becomes less favored, so a rising away number means the away price
    drifted weaker. RLM = away weakened while the public is on away (or vice
    versa)."""
    if (
        pct_bets_away is None
        or open_ml_away is None
        or current_ml_away is None
        or pct_bets_away == 50
    ):
        return None
    move = current_ml_away - open_ml_away
    if move == 0:
        return None
    public_on_away = pct_bets_away > 50
    away_weakened = move > 0
    return public_on_away == away_weakened


def merge_games(
    an_games: Iterable[AnGame],
    vsin_circa: Iterable[VsinGame],
    *,
    fetched_at_utc: str,
) -> list[BettingGame]:
    """Merge Action Network games with matched VSIN Circa sharp lines."""
    vindex = index_vsin(vsin_circa)
    out: list[BettingGame] = []
    for a in an_games:
        away_slug = slugify_team(a.away_name)
        home_slug = slugify_team(a.home_name)
        v = vindex.get(_key(a.game_date, away_slug, home_slug))

        sharp_spread = sharp_total = None
        sharp_ml_away = sharp_ml_home = None
        vsin_id = None
        sharp_book = None
        if v is not None:
            vsin_id = v.game_id
            sharp_book = SHARP_BOOK
            sharp_total = v.total
            if v.away_slug == away_slug:
                sharp_spread = v.spread_away
                sharp_ml_away, sharp_ml_home = v.ml_away, v.ml_home
            else:
                # VSIN listed the teams in the opposite order — flip to AN's
                # away/home orientation (away spread is the negation of home's).
                sharp_spread = -v.spread_away if v.spread_away is not None else None
                sharp_ml_away, sharp_ml_home = v.ml_home, v.ml_away

        spread_move = line_move(a.dk_spread_away, a.open_spread_away)
        total_move = line_move(a.dk_total, a.open_total)

        out.append(
            BettingGame(
                game_key=f"{a.game_date}:{a.away_abbr}@{a.home_abbr}",
                game_date=a.game_date,
                start_time=a.start_time,
                status=a.status,
                away_team_id=str(a.away_team_id),
                home_team_id=str(a.home_team_id),
                away_abbr=a.away_abbr,
                home_abbr=a.home_abbr,
                away_name=a.away_name,
                home_name=a.home_name,
                open_spread=a.open_spread_away,
                current_spread=a.dk_spread_away,
                sharp_spread=sharp_spread,
                spread_pct_bets_away=a.spread_pct_bets_away,
                spread_pct_money_away=a.spread_pct_money_away,
                spread_line_move=spread_move,
                spread_rlm=rlm_spread(a.spread_pct_bets_away, spread_move),
                open_total=a.open_total,
                current_total=a.dk_total,
                sharp_total=sharp_total,
                total_pct_bets_over=a.total_pct_bets_over,
                total_pct_money_over=a.total_pct_money_over,
                total_line_move=total_move,
                total_rlm=rlm_total(a.total_pct_bets_over, total_move),
                open_ml_away=a.open_ml_away,
                open_ml_home=a.open_ml_home,
                current_ml_away=a.dk_ml_away,
                current_ml_home=a.dk_ml_home,
                sharp_ml_away=sharp_ml_away,
                sharp_ml_home=sharp_ml_home,
                ml_pct_bets_away=a.ml_pct_bets_away,
                ml_pct_money_away=a.ml_pct_money_away,
                ml_rlm=rlm_moneyline(a.ml_pct_bets_away, a.open_ml_away, a.dk_ml_away),
                public_book=PUBLIC_BOOK,
                sharp_book=sharp_book,
                an_game_id=str(a.game_id),
                vsin_game_id=vsin_id,
                fetched_at_utc=fetched_at_utc,
            )
        )
    return out
