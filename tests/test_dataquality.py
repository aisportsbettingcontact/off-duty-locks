"""Data-quality checks over the serving tables.

These run offline against plain dicts — no Postgres — because every check in
:mod:`wnba_pipeline.dataquality` is a pure function over rows.

The headline case is `test_identical_splits_are_caught`, which reproduces the
defect that actually shipped: `ytd` populated from a Last-7 fixture, so both
splits held identical numbers under different labels. Per-snapshot validation
passed throughout, because each snapshot was individually valid — only a
cross-split comparison can see it.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from wnba_pipeline import dataquality as dq


def team(name: str, gp: int = 7, wins: int = 4, **over):
    row = {
        "team_name": name,
        "games_played": gp,
        "wins": wins,
        "losses": gp - wins,
        "win_pct": round(wins / gp, 3) if gp else 0.0,
        "points": 85.0,
        "fgm": 31.0, "fga": 70.0, "fg_pct": 0.443,
        "fg3_pct": 0.34, "ft_pct": 0.80,
        "reb": 34.0, "ast": 20.0, "tov": 13.0, "stl": 7.0, "blk": 4.0,
        "offensive_rating": 104.0,
    }
    row.update(over)
    return row


def codes(findings):
    return {f.code for f in findings}


def fails(findings):
    return [f for f in findings if f.severity == dq.FAIL]


# --------------------------------------------------------------------------- #
# the defect that shipped
# --------------------------------------------------------------------------- #

def test_identical_splits_are_caught():
    """A Last-7 window and a full season cannot match to the digit."""
    rows = [team("Lynx"), team("Aces"), team("Liberty")]
    last7 = [dict(r) for r in rows]
    ytd = [dict(r) for r in rows]          # same source -> same numbers

    findings = dq.check_cross_split(last7, ytd)
    assert "cross.splits_identical" in codes(findings)
    assert fails(findings), "identical splits must FAIL, not merely warn"


def test_distinct_splits_pass():
    last7 = [team("Lynx", gp=7, wins=5, points=90.0)]
    ytd = [team("Lynx", gp=24, wins=15, points=86.5)]
    findings = dq.check_cross_split(last7, ytd)
    assert "cross.splits_distinct" in codes(findings)
    assert not fails(findings)


def test_partial_overlap_warns_but_does_not_fail():
    last7 = [team("Lynx", gp=7, wins=5), team("Aces", gp=7, wins=3)]
    ytd = [team("Lynx", gp=7, wins=5), team("Aces", gp=22, wins=14)]
    findings = dq.check_cross_split(last7, ytd)
    assert "cross.splits_partially_identical" in codes(findings)
    assert not fails(findings)


def test_ytd_with_fewer_games_than_last7_fails():
    """Year-to-date covering fewer games than a recent window is impossible."""
    last7 = [team("Lynx", gp=7, wins=4)]
    ytd = [team("Lynx", gp=3, wins=2)]
    findings = dq.check_cross_split(last7, ytd)
    assert "cross.ytd_fewer_games" in codes(findings)


# --------------------------------------------------------------------------- #
# within a split
# --------------------------------------------------------------------------- #

def test_clean_split_has_no_failures():
    rows = [team("Lynx"), team("Aces")]
    assert not fails(dq.check_team_split("last7", rows, ["Lynx", "Aces"]))


def test_missing_and_unexpected_teams():
    findings = dq.check_team_split("last7", [team("Lynx")], ["Lynx", "Aces"])
    assert "split.missing_teams" in codes(findings)

    findings = dq.check_team_split("last7", [team("Ghosts")], ["Lynx"])
    assert "split.unexpected_teams" in codes(findings)


def test_duplicate_team_fails():
    findings = dq.check_team_split("last7", [team("Lynx"), team("Lynx")])
    assert "split.duplicate_team" in codes(findings)


def test_record_arithmetic_must_hold():
    findings = dq.check_team_split("last7", [team("Lynx", gp=7, wins=4, losses=9)])
    assert "row.record_mismatch" in codes(findings)


def test_win_pct_must_agree_with_record():
    findings = dq.check_team_split("last7", [team("Lynx", gp=7, wins=4, win_pct=0.9)])
    assert "row.win_pct_mismatch" in codes(findings)


@pytest.mark.parametrize("column", ["win_pct", "fg_pct", "fg3_pct", "ft_pct"])
def test_percentages_outside_zero_one_fail(column):
    findings = dq.check_team_split("last7", [team("Lynx", **{column: 42.0})])
    assert "row.pct_out_of_range" in codes(findings)


def test_games_played_cannot_exceed_the_window():
    findings = dq.check_team_split("last7", [team("Lynx", gp=30, wins=20, losses=10,
                                                  win_pct=0.667)])
    assert "row.gp_exceeds_window" in codes(findings)


def test_empty_split_fails():
    assert "split.empty" in codes(dq.check_team_split("ytd", []))


def test_null_record_fields_fail():
    findings = dq.check_team_split("last7", [team("Lynx", games_played=None)])
    assert "row.null_record" in codes(findings)


def test_decimal_values_are_accepted():
    """psycopg returns NUMERIC as Decimal; checks must not choke on it."""
    rows = [team("Lynx", offensive_rating=Decimal("104.5"), points=Decimal("85.0"))]
    assert not fails(dq.check_team_split("last7", rows))


def test_implausible_rating_warns_only():
    findings = dq.check_team_split("last7", [team("Lynx", offensive_rating=999.0)])
    assert "row.ortg_implausible" in codes(findings)
    assert not fails(findings)


# --------------------------------------------------------------------------- #
# betting
# --------------------------------------------------------------------------- #

def game(key: str, day: dt.date, **over):
    row = {
        "game_key": key, "game_date": day,
        "open_spread": -3.5, "current_spread": -4.0, "sharp_spread": -4.0,
        "spread_pct_bets_away": 55, "spread_pct_money_away": 60,
        "total_pct_bets_over": 48, "total_pct_money_over": 52,
        "ml_pct_bets_away": 57, "ml_pct_money_away": 63,
        "current_total": 160.5,
        # Fresh relative to the suite's canonical "now" (2026-07-29 12:00 UTC),
        # so a clean slate stays clean under the betting freshness gate.
        "fetched_at_utc": dt.datetime(2026, 7, 29, 11, 30, tzinfo=dt.timezone.utc),
    }
    row.update(over)
    return row


def test_clean_slate_passes():
    findings = dq.check_betting([game("a", dt.date(2026, 7, 29))])
    assert not fails(findings)


def test_duplicate_game_key_fails():
    today = dt.date(2026, 7, 29)
    findings = dq.check_betting([game("a", today), game("a", today)])
    assert "betting.duplicate_key" in codes(findings)


@pytest.mark.parametrize("column", [
    "spread_pct_bets_away", "spread_pct_money_away",
    "total_pct_bets_over", "total_pct_money_over",
    "ml_pct_bets_away", "ml_pct_money_away",
])
def test_percentages_outside_hundred_fail(column):
    today = dt.date(2026, 7, 29)
    for bad in (140, -5):
        findings = dq.check_betting([game("a", today, **{column: bad})])
        assert "betting.pct_out_of_range" in codes(findings), f"{column}={bad}"
        assert fails(findings)


# --------------------------------------------------------------------------- #
# betting freshness — the scope=betting gate's teeth
# --------------------------------------------------------------------------- #

NOW = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.timezone.utc)


def test_fresh_upcoming_slate_passes():
    rows = [game("a", NOW.date(), fetched_at_utc=NOW - dt.timedelta(minutes=30))]
    findings = dq.check_betting_freshness(rows, NOW)
    assert "betting.fetch_ok" in codes(findings)
    assert not fails(findings)


def test_stale_upcoming_slate_fails():
    """A dead scraper leaves yesterday's fetch on today's games — the one
    condition the old gate could never see (betting.empty cannot fire once
    history exists, and check_freshness is team_stats-only)."""
    rows = [game("a", NOW.date(), fetched_at_utc=NOW - dt.timedelta(hours=20)),
            game("b", NOW.date() + dt.timedelta(days=1),
                 fetched_at_utc=NOW - dt.timedelta(hours=21))]
    findings = dq.check_betting_freshness(rows, NOW)
    assert "betting.fetch_stale" in codes(findings)
    assert fails(findings)


def test_freshness_threshold_is_configurable():
    rows = [game("a", NOW.date(), fetched_at_utc=NOW - dt.timedelta(hours=12))]
    assert fails(dq.check_betting_freshness(rows, NOW))                 # default 6h
    assert not fails(dq.check_betting_freshness(rows, NOW, fresh_after_hours=24))


def test_only_upcoming_rows_gate_freshness():
    """History's old fetched_at must not fail a healthy run: one fresh
    upcoming game passes even with months of stale rows behind it."""
    rows = [game("old", dt.date(2026, 6, 1), fetched_at_utc=NOW - dt.timedelta(days=58)),
            game("a", NOW.date(), fetched_at_utc=NOW - dt.timedelta(minutes=30))]
    assert not fails(dq.check_betting_freshness(rows, NOW))


def test_no_upcoming_games_is_not_a_freshness_failure():
    """All-Star/Olympic breaks and the offseason are legitimate empty slates."""
    rows = [game("old", dt.date(2026, 6, 1), fetched_at_utc=NOW - dt.timedelta(days=58))]
    findings = dq.check_betting_freshness(rows, NOW)
    assert "betting.no_upcoming" in codes(findings)
    assert not fails(findings)
    assert not fails(dq.check_betting_freshness([], NOW))


def test_unknown_fetch_time_fails_closed():
    """An upcoming slate whose freshness cannot be proven must not pass."""
    rows = [game("a", NOW.date(), fetched_at_utc=None)]
    findings = dq.check_betting_freshness(rows, NOW)
    assert "betting.fetch_unknown" in codes(findings)
    assert fails(findings)


def test_naive_fetch_timestamps_are_treated_as_utc():
    rows = [game("a", NOW.date(),
                 fetched_at_utc=dt.datetime(2026, 7, 29, 11, 30))]
    assert not fails(dq.check_betting_freshness(rows, NOW))


def test_datetime_game_date_counts_as_upcoming():
    rows = [game("a", dt.datetime(2026, 7, 29, 23, 0),
                 fetched_at_utc=NOW - dt.timedelta(hours=20))]
    assert "betting.fetch_stale" in codes(dq.check_betting_freshness(rows, NOW))


# --------------------------------------------------------------------------- #
# freshness
# --------------------------------------------------------------------------- #

def test_freshness_bands():
    now = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.timezone.utc)
    fresh = now - dt.timedelta(hours=1)
    aging = now - dt.timedelta(hours=12)
    stale = now - dt.timedelta(hours=100)

    assert "freshness.ok" in codes(dq.check_freshness(fresh, now))
    assert "freshness.aging" in codes(dq.check_freshness(aging, now))
    assert "freshness.stale" in codes(dq.check_freshness(stale, now))
    assert "freshness.unknown" in codes(dq.check_freshness(None, now))


def test_naive_timestamps_are_treated_as_utc():
    now = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.timezone.utc)
    naive = dt.datetime(2026, 7, 29, 11, 0)
    assert not fails(dq.check_freshness(naive, now))


# --------------------------------------------------------------------------- #
# composition
# --------------------------------------------------------------------------- #

def test_run_all_surfaces_the_mislabelling_end_to_end():
    now = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.timezone.utc)
    rows = [team("Lynx"), team("Aces")]
    findings = dq.run_all(
        {"last7": [dict(r) for r in rows], "ytd": [dict(r) for r in rows]},
        [game("a", now.date())],
        now - dt.timedelta(hours=1),
        now,
        expected_teams=["Lynx", "Aces"],
    )
    assert "cross.splits_identical" in codes(findings)
    assert dq.worst_severity(findings) == dq.FAIL


def test_run_all_clean_dataset_is_not_a_failure():
    now = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.timezone.utc)
    findings = dq.run_all(
        {"last7": [team("Lynx", gp=7, wins=5, points=90.0)],
         "ytd": [team("Lynx", gp=24, wins=15, points=86.5)]},
        [game("a", now.date())],
        now - dt.timedelta(hours=1),
        now,
        expected_teams=["Lynx"],
    )
    assert dq.worst_severity(findings) != dq.FAIL


def test_run_all_betting_scope_gates_on_betting_only():
    """Scheduled betting scrapes gate on betting health; team-stats findings
    (freshness, split mislabelling) belong to the team-stats path."""
    now = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.timezone.utc)
    rows = [team("Lynx"), team("Aces")]
    findings = dq.run_all(
        {"last7": [dict(r) for r in rows], "ytd": [dict(r) for r in rows]},
        [game("a", now.date())],
        now - dt.timedelta(hours=200),  # very stale team stats
        now,
        expected_teams=["Lynx", "Aces"],
        scope="betting",
    )
    assert all(c.startswith("betting.") for c in codes(findings))
    assert dq.worst_severity(findings) != dq.FAIL


def test_run_all_betting_scope_fails_on_stale_fetch():
    """The gate's teeth end-to-end: scope=betting goes red when the upcoming
    slate stopped being fetched, with no team-stats finding required."""
    now = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.timezone.utc)
    stale = [game("a", now.date(), fetched_at_utc=now - dt.timedelta(hours=20))]
    findings = dq.run_all({}, stale, None, now, scope="betting")
    assert "betting.fetch_stale" in codes(findings)
    assert dq.worst_severity(findings) == dq.FAIL


def test_run_all_plumbs_betting_fresh_hours():
    now = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.timezone.utc)
    stale = [game("a", now.date(), fetched_at_utc=now - dt.timedelta(hours=20))]
    findings = dq.run_all({}, stale, None, now, scope="betting",
                          betting_fresh_hours=48.0)
    assert dq.worst_severity(findings) != dq.FAIL


def test_run_all_full_scope_is_default():
    now = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.timezone.utc)
    rows = [team("Lynx"), team("Aces")]
    findings = dq.run_all(
        {"last7": [dict(r) for r in rows], "ytd": [dict(r) for r in rows]},
        [game("a", now.date())],
        now - dt.timedelta(hours=200),
        now,
        expected_teams=["Lynx", "Aces"],
    )
    assert "freshness.stale" in codes(findings)
    assert "cross.splits_identical" in codes(findings)
