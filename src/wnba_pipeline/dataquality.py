"""Data-quality checks for the serving tables the site publishes.

The pipeline already validates each *extraction* (``validation.py``): shape,
row counts, the expected team set. That happens before anything is written and
looks at one snapshot in isolation. These checks look at the opposite end — the
rows actually sitting in ``team_stats`` and ``betting_games`` — and ask a
different question: *is what we are publishing internally consistent and
believable?*

That gap is not theoretical. Extraction validation passed on every run while
the ``ytd`` split was being populated from a Last-7 fixture, because nothing in
a single snapshot can reveal that a *different* snapshot was derived from the
same source. Only a cross-split comparison shows it, and that is what
:func:`check_cross_split` does.

Every check is a pure function over plain row dicts, so the whole suite is
unit-testable without a database. :func:`run_all` composes them and returns
:class:`Finding` objects; the caller decides how to render and whether to fail.

Severities:
  FAIL  the data is wrong or self-contradictory — do not publish it
  WARN  suspicious but legitimately possible; a human should look
  INFO  context that makes a report readable
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Sequence

Row = dict[str, Any]

FAIL = "FAIL"
WARN = "WARN"
INFO = "INFO"


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"[{self.severity}] {self.code}: {self.message}"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _num(value: Any) -> float | None:
    """Coerce a cell to float, or None when it is not numeric.

    psycopg hands back Decimal for NUMERIC columns and int for INTEGER, so
    every numeric check has to accept all three without silently treating a
    bool as 1/0.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# team_stats — within a single split
# --------------------------------------------------------------------------- #

def check_team_split(
    split: str,
    rows: Sequence[Row],
    expected_teams: Iterable[str] | None = None,
) -> list[Finding]:
    """Completeness, uniqueness, arithmetic and range checks for one split."""
    out: list[Finding] = []
    out.append(Finding(INFO, "split.rows", f"{split}: {len(rows)} rows",
                       {"split": split, "rows": len(rows)}))

    if not rows:
        out.append(Finding(FAIL, "split.empty",
                           f"{split}: no rows — the site renders an empty state",
                           {"split": split}))
        return out

    names = [str(r.get("team_name") or "") for r in rows]

    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        out.append(Finding(FAIL, "split.duplicate_team",
                           f"{split}: duplicate teams {dupes}",
                           {"split": split, "teams": dupes}))

    if expected_teams is not None:
        expected = set(expected_teams)
        actual = set(names)
        missing, extra = sorted(expected - actual), sorted(actual - expected)
        if missing:
            out.append(Finding(FAIL, "split.missing_teams",
                               f"{split}: missing {len(missing)} expected team(s): {missing}",
                               {"split": split, "missing": missing}))
        if extra:
            out.append(Finding(FAIL, "split.unexpected_teams",
                               f"{split}: {len(extra)} unrecognised team(s): {extra}",
                               {"split": split, "extra": extra}))

    for r in rows:
        team = r.get("team_name")
        gp, w, l = _num(r.get("games_played")), _num(r.get("wins")), _num(r.get("losses"))

        if None in (gp, w, l):
            out.append(Finding(FAIL, "row.null_record",
                               f"{split}/{team}: games_played/wins/losses contains NULL",
                               {"split": split, "team": team}))
        elif not _close(w + l, gp, 0.0):
            out.append(Finding(FAIL, "row.record_mismatch",
                               f"{split}/{team}: wins({w:g}) + losses({l:g}) != games_played({gp:g})",
                               {"split": split, "team": team, "wins": w,
                                "losses": l, "games_played": gp}))

        wp = _num(r.get("win_pct"))
        if wp is not None and gp:
            if not _close(wp, w / gp if w is not None else 0.0, 0.005):
                out.append(Finding(FAIL, "row.win_pct_mismatch",
                                   f"{split}/{team}: win_pct {wp:.3f} disagrees with wins/games_played",
                                   {"split": split, "team": team, "win_pct": wp}))

        for col in ("win_pct", "fg_pct", "fg3_pct", "ft_pct"):
            v = _num(r.get(col))
            if v is not None and not (0.0 <= v <= 1.0):
                out.append(Finding(FAIL, "row.pct_out_of_range",
                                   f"{split}/{team}: {col}={v} outside 0..1",
                                   {"split": split, "team": team, "column": col, "value": v}))

        pts = _num(r.get("points"))
        if pts is not None and not (40.0 <= pts <= 130.0):
            out.append(Finding(WARN, "row.points_implausible",
                               f"{split}/{team}: points per game {pts:g} outside 40..130",
                               {"split": split, "team": team, "points": pts}))

        ortg = _num(r.get("offensive_rating"))
        if ortg is not None and not (70.0 <= ortg <= 140.0):
            out.append(Finding(WARN, "row.ortg_implausible",
                               f"{split}/{team}: offensive_rating {ortg:g} outside 70..140",
                               {"split": split, "team": team, "offensive_rating": ortg}))

        # possessions is the OTHER Model v0 input and had no gate at all. A bad
        # value here moves every projected spread and total on the board.
        poss = _num(r.get("possessions"))
        if poss is not None and not (55.0 <= poss <= 115.0):
            out.append(Finding(WARN, "row.possessions_implausible",
                               f"{split}/{team}: possessions {poss:g} outside 55..115",
                               {"split": split, "team": team, "possessions": poss}))
        if poss is not None and poss <= 0:
            out.append(Finding(FAIL, "row.possessions_nonpositive",
                               f"{split}/{team}: possessions {poss:g} is not positive",
                               {"split": split, "team": team, "possessions": poss}))

        # A "lastN" split cannot contain more games than its own window.
        if split.startswith("last"):
            try:
                window = int(split[4:])
            except ValueError:
                window = 0
            if window and gp is not None and gp > window:
                out.append(Finding(FAIL, "row.gp_exceeds_window",
                                   f"{split}/{team}: games_played {gp:g} exceeds the {window}-game window",
                                   {"split": split, "team": team,
                                    "games_played": gp, "window": window}))
    return out


# --------------------------------------------------------------------------- #
# team_stats — across splits (this is the check that catches mislabelling)
# --------------------------------------------------------------------------- #

_COMPARE_COLS = ("games_played", "wins", "losses", "points", "fgm", "fga",
                 "reb", "ast", "tov", "stl", "blk", "offensive_rating")


def check_cross_split(last_rows: Sequence[Row], ytd_rows: Sequence[Row],
                      last_split: str = "last7") -> list[Finding]:
    """Compare a Last-N split against Year-to-Date.

    Two invariants hold for any real mid-season data:

    1. Year-to-Date must cover at least as many games as a Last-N window.
       ``ytd.games_played >= lastN.games_played`` for every team.
    2. The two splits must not be numerically identical. A Last-7 window and a
       full season agreeing to the digit across every team and every column is
       not a plausible coincidence — it means one split was derived from the
       other's source.

    Invariant 2 is precisely the failure that shipped: ``run-team-stats
    --fixture <last7 file>`` published both windows from that one file, so
    ``ytd`` held Last-7 numbers under a Year-to-Date heading. Nothing in
    single-snapshot validation can see that.
    """
    out: list[Finding] = []
    if not last_rows or not ytd_rows:
        out.append(Finding(WARN, "cross.split_absent",
                           "cannot compare splits: one of them has no rows",
                           {"last_rows": len(last_rows), "ytd_rows": len(ytd_rows)}))
        return out

    by_name_last = {str(r.get("team_name")): r for r in last_rows}
    by_name_ytd = {str(r.get("team_name")): r for r in ytd_rows}
    shared = sorted(set(by_name_last) & set(by_name_ytd))

    if not shared:
        out.append(Finding(FAIL, "cross.no_common_teams",
                           "the two splits share no team names",
                           {}))
        return out

    identical_teams: list[str] = []
    for name in shared:
        a, b = by_name_last[name], by_name_ytd[name]

        gp_a, gp_b = _num(a.get("games_played")), _num(b.get("games_played"))
        if gp_a is not None and gp_b is not None and gp_b < gp_a:
            out.append(Finding(FAIL, "cross.ytd_fewer_games",
                               f"{name}: ytd games_played ({gp_b:g}) < {last_split} ({gp_a:g}) — "
                               "year-to-date cannot cover fewer games than a recent window",
                               {"team": name, "ytd": gp_b, last_split: gp_a}))

        same = all(
            (_num(a.get(c)) is None and _num(b.get(c)) is None)
            or (_num(a.get(c)) is not None and _num(b.get(c)) is not None
                and _close(_num(a.get(c)), _num(b.get(c)), 1e-9))
            for c in _COMPARE_COLS
        )
        if same:
            identical_teams.append(name)

    if identical_teams and len(identical_teams) == len(shared):
        out.append(Finding(
            FAIL, "cross.splits_identical",
            f"{last_split} and ytd are numerically identical for all {len(shared)} teams — "
            "one split was derived from the other's source, so the data is mislabelled",
            {"teams": len(shared), "compared_columns": list(_COMPARE_COLS)}))
    elif identical_teams:
        out.append(Finding(
            WARN, "cross.splits_partially_identical",
            f"{len(identical_teams)}/{len(shared)} teams are identical across "
            f"{last_split} and ytd: {identical_teams[:5]}",
            {"teams": identical_teams}))
    else:
        out.append(Finding(INFO, "cross.splits_distinct",
                           f"{last_split} and ytd differ across all {len(shared)} shared teams",
                           {"teams": len(shared)}))
    return out


# --------------------------------------------------------------------------- #
# betting_games
# --------------------------------------------------------------------------- #

# Every stored American price. Kept as one list so a new moneyline column
# cannot be added without the gate seeing it.
MONEYLINE_COLUMNS: tuple[str, ...] = (
    "open_ml_away", "open_ml_home",
    "current_ml_away", "current_ml_home",
    "sharp_ml_away", "sharp_ml_home",
)


def check_betting(rows: Sequence[Row]) -> list[Finding]:
    """Range and uniqueness checks for the published slate.

    A game-date staleness check (``betting.stale_rows``) used to live here,
    but the 1-day serving window dissolved its premise: a row the site would
    render cannot also be more than a day old, so the check could never fire.
    Real staleness — the scraper going dead — is a fetch-time property and is
    covered by the ``betting.fetch_stale`` FAIL in
    :func:`check_betting_freshness`.
    """
    out: list[Finding] = []
    out.append(Finding(INFO, "betting.rows", f"betting_games: {len(rows)} rows",
                       {"rows": len(rows)}))
    if not rows:
        out.append(Finding(WARN, "betting.empty",
                           "betting_games is empty — the slate renders as empty",
                           {}))
        return out

    keys = [str(r.get("game_key") or "") for r in rows]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        out.append(Finding(FAIL, "betting.duplicate_key",
                           f"duplicate game_key values: {dupes}", {"keys": dupes}))

    for r in rows:
        key = r.get("game_key")

        for col in ("spread_pct_bets_away", "spread_pct_money_away",
                    "total_pct_bets_over", "total_pct_money_over",
                    "ml_pct_bets_away", "ml_pct_money_away"):
            v = _num(r.get(col))
            if v is not None and not (0.0 <= v <= 100.0):
                out.append(Finding(FAIL, "betting.pct_out_of_range",
                                   f"{key}: {col}={v} outside 0..100",
                                   {"game_key": key, "column": col, "value": v}))

        for col in ("open_spread", "current_spread", "sharp_spread"):
            v = _num(r.get(col))
            if v is not None and abs(v) > 40.0:
                out.append(Finding(WARN, "betting.spread_implausible",
                                   f"{key}: {col}={v} exceeds +/-40",
                                   {"game_key": key, "column": col, "value": v}))

        for col in ("open_total", "current_total", "sharp_total"):
            total = _num(r.get(col))
            if total is not None and not (100.0 <= total <= 220.0):
                out.append(Finding(WARN, "betting.total_implausible",
                                   f"{key}: {col}={total} outside 100..220",
                                   {"game_key": key, "column": col, "value": total}))

        # Moneylines had NO check at all, while percentages had a FAIL gate —
        # which is how a truncated "-1,650" published as -1 and stayed. No
        # American price lies strictly between -100 and +100, or is 0.
        for col in MONEYLINE_COLUMNS:
            odds = _num(r.get(col))
            if odds is not None and abs(odds) < 100.0:
                out.append(Finding(FAIL, "betting.moneyline_impossible",
                                   f"{key}: {col}={odds} is not a possible American price",
                                   {"game_key": key, "column": col, "value": odds}))
            elif odds is not None and abs(odds) > 20000.0:
                out.append(Finding(WARN, "betting.moneyline_implausible",
                                   f"{key}: {col}={odds} exceeds +/-20000",
                                   {"game_key": key, "column": col, "value": odds}))

        # Two books pricing the same side cannot disagree by an order of
        # magnitude. This catches a truncation even when the bad value is
        # individually plausible.
        for current_col, sharp_col in (("current_ml_away", "sharp_ml_away"),
                                       ("current_ml_home", "sharp_ml_home")):
            cur, sharp = _num(r.get(current_col)), _num(r.get(sharp_col))
            if cur is None or sharp is None or cur == 0 or sharp == 0:
                continue
            if (cur > 0) != (sharp > 0):
                out.append(Finding(WARN, "betting.moneyline_sign_disagreement",
                                   f"{key}: {current_col}={cur} and {sharp_col}={sharp} "
                                   "disagree on favourite",
                                   {"game_key": key, "current": cur, "sharp": sharp}))
            elif max(abs(cur), abs(sharp)) / min(abs(cur), abs(sharp)) >= 5.0:
                out.append(Finding(FAIL, "betting.moneyline_magnitude_mismatch",
                                   f"{key}: {current_col}={cur} vs {sharp_col}={sharp} "
                                   "differ by 5x or more on the same side",
                                   {"game_key": key, "current": cur, "sharp": sharp}))

        for col in ("spread_line_move", "total_line_move"):
            move = _num(r.get(col))
            if move is not None and abs(move) > 15.0:
                out.append(Finding(WARN, "betting.line_move_implausible",
                                   f"{key}: {col}={move} exceeds +/-15",
                                   {"game_key": key, "column": col, "value": move}))
    return out


# One authority for the betting-freshness gate's threshold. The CLI flag
# default (__main__.build_parser), the getattr fallback in _cmd_validate_data,
# and the two signatures below all read this constant, so they cannot drift.
DEFAULT_BETTING_FRESH_HOURS = 6.0


def check_betting_freshness(rows: Sequence[Row], now: _dt.datetime,
                            fresh_after_hours: float = DEFAULT_BETTING_FRESH_HOURS,
                            ) -> list[Finding]:
    """FAIL when the upcoming slate has stopped being fetched.

    The scope=betting gate needs a check that can actually fire on a dead
    scraper: rows are upserted by game_key and never deleted, so
    ``betting.empty`` cannot trigger once a single game has ever been written,
    and :func:`check_freshness` reads team_stats, which that scope skips. The
    teeth: when any game is dated today or later, the newest ``fetched_at_utc``
    among those games must be within ``fresh_after_hours``.

    No upcoming games is NOT a failure — All-Star/Olympic breaks and the
    offseason are legitimate empty slates — so this check only reports then;
    the empty-table WARN in :func:`check_betting` keeps its meaning.

    ``fetched_at_utc`` is timestamptz, so ages are computed in UTC (naive
    values are assumed UTC, as in :func:`check_freshness`). "Today" is
    ``now.date()``.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    today = now.date()

    upcoming: list[Row] = []
    for r in rows:
        gd = r.get("game_date")
        if isinstance(gd, _dt.datetime):
            gd = gd.date()
        if isinstance(gd, _dt.date) and gd >= today:
            upcoming.append(r)

    if not upcoming:
        return [Finding(INFO, "betting.no_upcoming",
                        "no games dated today or later — nothing to gate on freshness",
                        {"rows": len(rows)})]

    newest: _dt.datetime | None = None
    for r in upcoming:
        ts = r.get("fetched_at_utc")
        if not isinstance(ts, _dt.datetime):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
        if newest is None or ts > newest:
            newest = ts

    if newest is None:
        return [Finding(FAIL, "betting.fetch_unknown",
                        f"{len(upcoming)} upcoming game(s) carry no fetched_at_utc — "
                        "freshness cannot be proven, so the gate fails closed",
                        {"upcoming": len(upcoming)})]

    age_h = (now - newest).total_seconds() / 3600.0
    detail = {"upcoming": len(upcoming), "newest": newest.isoformat(),
              "age_hours": round(age_h, 2)}
    if age_h > fresh_after_hours:
        return [Finding(FAIL, "betting.fetch_stale",
                        f"newest fetch for the upcoming slate is {age_h:.1f}h old "
                        f"(limit {fresh_after_hours:g}h) — the scraper has stopped writing",
                        detail)]
    return [Finding(INFO, "betting.fetch_ok",
                    f"newest fetch for the upcoming slate is {age_h:.1f}h old", detail)]


# --------------------------------------------------------------------------- #
# freshness
# --------------------------------------------------------------------------- #

def check_freshness(newest: _dt.datetime | None, now: _dt.datetime,
                    warn_after_hours: float = 6.0,
                    fail_after_hours: float = 48.0) -> list[Finding]:
    if newest is None:
        return [Finding(FAIL, "freshness.unknown",
                        "no updated_at timestamp found in team_stats", {})]
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    age_h = (now - newest).total_seconds() / 3600.0
    detail = {"newest": newest.isoformat(), "age_hours": round(age_h, 2)}
    if age_h > fail_after_hours:
        return [Finding(FAIL, "freshness.stale",
                        f"newest team_stats row is {age_h:.1f}h old (limit {fail_after_hours:g}h)",
                        detail)]
    if age_h > warn_after_hours:
        return [Finding(WARN, "freshness.aging",
                        f"newest team_stats row is {age_h:.1f}h old (warn after {warn_after_hours:g}h)",
                        detail)]
    return [Finding(INFO, "freshness.ok",
                    f"newest team_stats row is {age_h:.1f}h old", detail)]


# --------------------------------------------------------------------------- #
# composition
# --------------------------------------------------------------------------- #

def run_all(team_rows_by_split: dict[str, Sequence[Row]],
            betting_rows: Sequence[Row],
            newest_updated_at: _dt.datetime | None,
            now: _dt.datetime,
            expected_teams: Iterable[str] | None = None,
            last_split: str = "last7",
            scope: str = "full",
            betting_fresh_hours: float = DEFAULT_BETTING_FRESH_HOURS,
            ) -> list[Finding]:
    """Run checks and return all findings, most severe concerns included.

    scope="betting" runs only the betting-table checks — the gate for the
    30-minute betting scrapes, which cannot refresh team stats (stats.wnba.com
    blocks datacenter runners) and must not go red for that known limitation.
    Its teeth are the range checks plus :func:`check_betting_freshness`, which
    fails when the upcoming slate stops being fetched (``betting_fresh_hours``).
    The full scope stays the gate wherever team stats are actually written."""
    findings: list[Finding] = []
    if scope != "betting":
        for split in sorted(team_rows_by_split):
            findings += check_team_split(split, team_rows_by_split[split], expected_teams)
        findings += check_cross_split(
            team_rows_by_split.get(last_split, []),
            team_rows_by_split.get("ytd", []),
            last_split=last_split,
        )
    findings += check_betting(betting_rows)
    findings += check_betting_freshness(betting_rows, now, betting_fresh_hours)
    if scope != "betting":
        findings += check_freshness(newest_updated_at, now)
    return findings


def worst_severity(findings: Iterable[Finding]) -> str:
    sevs = {f.severity for f in findings}
    if FAIL in sevs:
        return FAIL
    if WARN in sevs:
        return WARN
    return INFO
