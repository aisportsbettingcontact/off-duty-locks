"""Merge Action Network (lines) with VSIN (splits + Circa sharp line).

Action Network supplies opening and current DraftKings lines. VSIN's DK view
supplies %bets / %money, and VSIN's Circa view supplies the sharp line. VSIN
games are matched to Action Network by ``(date, unordered team-slug pair)`` and
oriented to Action Network's away/home — a swapped pairing takes the complement
of the away-side percentages. Line movement is ``current - open``; reverse line
movement (RLM) is when the line moves toward the side the public (ticket
majority) is NOT on — computed from VSIN's bets%.
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


def _complement(pct: int | None) -> int | None:
    """100 - pct; used when VSIN lists the teams in the opposite order."""
    return None if pct is None else 100 - pct


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
    vsin_dk: Iterable[VsinGame],
    vsin_circa: Iterable[VsinGame],
    *,
    fetched_at_utc: str,
) -> list[BettingGame]:
    """Merge Action Network lines with VSIN splits (DK view) and sharp (Circa)."""
    dk_index = index_vsin(vsin_dk)
    circa_index = index_vsin(vsin_circa)
    out: list[BettingGame] = []
    for a in an_games:
        away_slug = slugify_team(a.away_name)
        home_slug = slugify_team(a.home_name)
        key = _key(a.game_date, away_slug, home_slug)

        # --- splits from VSIN DK, oriented to AN's away / over ---
        dk = dk_index.get(key)
        sp_bets = sp_money = tot_bets = tot_money = ml_bets = ml_money = None
        if dk is not None:
            if dk.away_slug == away_slug:
                sp_bets, sp_money = dk.spread_pct_bets_away, dk.spread_pct_money_away
                ml_bets, ml_money = dk.ml_pct_bets_away, dk.ml_pct_money_away
            else:
                # VSIN listed the teams the other way: its away side is AN's home.
                sp_bets = _complement(dk.spread_pct_bets_away)
                sp_money = _complement(dk.spread_pct_money_away)
                ml_bets = _complement(dk.ml_pct_bets_away)
                ml_money = _complement(dk.ml_pct_money_away)
            # Over/under is orientation-independent.
            tot_bets, tot_money = dk.total_pct_bets_over, dk.total_pct_money_over

        # --- sharp line from VSIN Circa ---
        circa = circa_index.get(key)
        sharp_spread = sharp_total = None
        sharp_ml_away = sharp_ml_home = None
        sharp_book = None
        if circa is not None:
            sharp_book = SHARP_BOOK
            sharp_total = circa.total
            if circa.away_slug == away_slug:
                sharp_spread = circa.spread_away
                sharp_ml_away, sharp_ml_home = circa.ml_away, circa.ml_home
            else:
                sharp_spread = -circa.spread_away if circa.spread_away is not None else None
                sharp_ml_away, sharp_ml_home = circa.ml_home, circa.ml_away

        vsin_id = (circa.game_id if circa is not None
                   else dk.game_id if dk is not None else None)
        # Advances ONLY when VSIN actually carried this game. A miss leaves it
        # None, the upsert's COALESCE keeps the previous value, and the age of
        # the preserved splits becomes visible instead of invisible.
        vsin_seen_at = fetched_at_utc if (dk is not None or circa is not None) else None
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
                spread_pct_bets_away=sp_bets,
                spread_pct_money_away=sp_money,
                spread_line_move=spread_move,
                spread_rlm=rlm_spread(sp_bets, spread_move),
                open_total=a.open_total,
                current_total=a.dk_total,
                sharp_total=sharp_total,
                total_pct_bets_over=tot_bets,
                total_pct_money_over=tot_money,
                total_line_move=total_move,
                total_rlm=rlm_total(tot_bets, total_move),
                open_ml_away=a.open_ml_away,
                open_ml_home=a.open_ml_home,
                current_ml_away=a.dk_ml_away,
                current_ml_home=a.dk_ml_home,
                sharp_ml_away=sharp_ml_away,
                sharp_ml_home=sharp_ml_home,
                ml_pct_bets_away=ml_bets,
                ml_pct_money_away=ml_money,
                ml_rlm=rlm_moneyline(ml_bets, a.open_ml_away, a.dk_ml_away),
                public_book=PUBLIC_BOOK,
                sharp_book=sharp_book,
                an_game_id=str(a.game_id),
                vsin_game_id=vsin_id,
                fetched_at_utc=fetched_at_utc,
                vsin_fetched_at_utc=vsin_seen_at,
            )
        )
    return out
