"""The ``validate-data`` command — the gate the scheduled scrapes actually run.

The checks themselves are pure and covered in ``test_dataquality.py``; what was
never covered is the CLI layer that CI depends on: which checks a scope runs,
how findings map to exit codes, and the flags that tune the gate. These tests
drive ``_cmd_validate_data`` against a fake psycopg connection (the pattern
from ``test_split_labeling.py``), so they stay offline and deterministic.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

from wnba_pipeline import __main__ as cli
from wnba_pipeline import db
from wnba_pipeline.contract import EXIT_CONFIG_ERROR, EXIT_VALIDATION_FAILED

NOW = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- #
# harness: a fake psycopg connection over in-memory serving tables
# --------------------------------------------------------------------------- #

class _Col:
    def __init__(self, name): self.name = name


class _FakeCursor:
    """Answers exactly the four read queries _cmd_validate_data issues."""

    def __init__(self, data):
        self.data = data
        self.description = None
        self._result = []

    def execute(self, sql, params=()):
        q = " ".join(sql.split()).lower()
        if q == "select distinct split from team_stats order by split":
            self._result = [(s,) for s in sorted(self.data["team_stats"])]
        elif q.startswith("select * from team_stats where split"):
            self._project(self.data["team_stats"].get(params[0], []), "team_name")
        elif q == "select * from betting_games":
            self._project(self.data["betting_games"], "game_key")
        elif q == "select max(updated_at) from team_stats":
            self._result = [(self.data["newest"],)]
        else:  # pragma: no cover - unexpected query
            raise AssertionError(f"unexpected SQL: {sql}")

    def _project(self, rows, fallback_col):
        cols = list(rows[0].keys()) if rows else [fallback_col]
        self.description = [_Col(c) for c in cols]
        self._result = [tuple(r.get(c) for c in cols) for r in rows]

    def fetchall(self): return list(self._result)
    def fetchone(self): return self._result[0] if self._result else None
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, data):
        self.cur = _FakeCursor(data)
        self.closed = False

    def cursor(self): return self.cur
    def close(self): self.closed = True
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FixedDatetime:
    """Stands in for the CLI's datetime so the gate's 'now' is pinned."""

    @classmethod
    def now(cls, tz=None):
        return NOW


def _args(**over):
    # season 1900 has no expected-teams fixture, so the team set is not part
    # of what these tests exercise.
    args = argparse.Namespace(
        database_url=None, season="1900", last_split="last7",
        warn_is_failure=False, scope="full", as_json=True,
        betting_fresh_hours=6.0)
    for k, v in over.items():
        setattr(args, k, v)
    return args


def _validate(monkeypatch, data, **over):
    monkeypatch.setattr(db, "connect", lambda *a, **k: _FakeConn(data))
    monkeypatch.setattr(cli, "datetime", _FixedDatetime)
    return cli._cmd_validate_data(_args(**over))


def _codes(capsys):
    return {json.loads(line)["code"]
            for line in capsys.readouterr().out.splitlines() if line}


def team(name, gp, wins, points):
    return {"team_name": name, "games_played": gp, "wins": wins,
            "losses": gp - wins, "win_pct": round(wins / gp, 3),
            "points": points, "offensive_rating": 104.0}


def game(key, day, **over):
    row = {"game_key": key, "game_date": day,
           "open_spread": -3.5, "current_spread": -4.0,
           "spread_pct_bets_away": 55, "spread_pct_money_away": 60,
           "total_pct_bets_over": 48, "total_pct_money_over": 52,
           "ml_pct_bets_away": 57, "ml_pct_money_away": 63,
           "current_total": 160.5,
           "fetched_at_utc": NOW - dt.timedelta(minutes=30)}
    row.update(over)
    return row


def _clean_store():
    return {
        "team_stats": {"last7": [team("Lynx", 7, 5, 90.0)],
                       "ytd": [team("Lynx", 24, 15, 86.5)]},
        "betting_games": [game("a", NOW.date())],
        "newest": NOW - dt.timedelta(hours=1),
    }


def _poisoned_team_stats_store():
    """Identical splits + ancient team stats — FAILs the full scope only."""
    rows = [team("Lynx", 7, 5, 90.0)]
    return {
        "team_stats": {"last7": [dict(r) for r in rows],
                       "ytd": [dict(r) for r in rows]},
        "betting_games": [game("a", NOW.date())],
        "newest": NOW - dt.timedelta(hours=200),
    }


# --------------------------------------------------------------------------- #
# flags
# --------------------------------------------------------------------------- #

def test_betting_fresh_hours_flag_parses_with_default():
    """--betting-fresh-hours exists, is a float, and defaults to the single
    module-level constant — the CLI default must never drift from run_all's."""
    from wnba_pipeline import dataquality as dq

    args = cli.build_parser().parse_args(["validate-data"])
    assert args.betting_fresh_hours == dq.DEFAULT_BETTING_FRESH_HOURS == 6.0
    args = cli.build_parser().parse_args(
        ["validate-data", "--betting-fresh-hours", "3"])
    assert args.betting_fresh_hours == 3.0


# --------------------------------------------------------------------------- #
# scope passthrough
# --------------------------------------------------------------------------- #

def test_scope_betting_runs_only_betting_checks(monkeypatch, capsys):
    """The 30-min scrape's gate must not go red for team-stats faults it
    cannot fix — and must emit no team-stats findings at all."""
    code = _validate(monkeypatch, _poisoned_team_stats_store(), scope="betting")
    codes = _codes(capsys)
    assert code == 0
    assert codes and all(c.startswith("betting.") for c in codes)


def test_scope_full_still_catches_the_team_stats_poison(monkeypatch, capsys):
    code = _validate(monkeypatch, _poisoned_team_stats_store(), scope="full")
    codes = _codes(capsys)
    assert code == EXIT_VALIDATION_FAILED
    assert "cross.splits_identical" in codes
    assert "freshness.stale" in codes


# --------------------------------------------------------------------------- #
# exit-code mapping
# --------------------------------------------------------------------------- #

def test_clean_serving_tables_exit_zero(monkeypatch):
    assert _validate(monkeypatch, _clean_store()) == 0


def test_failing_betting_finding_exits_validation_failed(monkeypatch, capsys):
    store = _clean_store()
    store["betting_games"] = [game("a", NOW.date(), ml_pct_money_away=140)]
    code = _validate(monkeypatch, store, scope="betting")
    assert code == EXIT_VALIDATION_FAILED
    assert "betting.pct_out_of_range" in _codes(capsys)


def test_warn_is_failure_is_usable_again(monkeypatch):
    """With history no longer poisoning WARN, a clean slate passes even under
    --warn-is-failure, and a genuine WARN still trips it."""
    store = _clean_store()
    store["betting_games"].append(game("old", dt.date(2026, 6, 1)))  # history
    assert _validate(monkeypatch, store, scope="betting",
                     warn_is_failure=True) == 0

    store["betting_games"][0]["current_spread"] = 45.0  # implausible -> WARN
    assert _validate(monkeypatch, store, scope="betting",
                     warn_is_failure=True) == EXIT_VALIDATION_FAILED


def test_connect_failure_is_a_config_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no database")

    monkeypatch.setattr(db, "connect", boom)
    assert cli._cmd_validate_data(_args()) == EXIT_CONFIG_ERROR


# --------------------------------------------------------------------------- #
# the freshness gate, end to end through the CLI
# --------------------------------------------------------------------------- #

def test_stale_fetch_fails_the_betting_gate(monkeypatch, capsys):
    store = _clean_store()
    store["betting_games"] = [
        game("a", NOW.date(), fetched_at_utc=NOW - dt.timedelta(hours=20))]
    code = _validate(monkeypatch, store, scope="betting")
    assert code == EXIT_VALIDATION_FAILED
    assert "betting.fetch_stale" in _codes(capsys)


def test_betting_fresh_hours_is_plumbed_through(monkeypatch):
    store = _clean_store()
    store["betting_games"] = [
        game("a", NOW.date(), fetched_at_utc=NOW - dt.timedelta(hours=20))]
    assert _validate(monkeypatch, store, scope="betting",
                     betting_fresh_hours=48.0) == 0


def test_offseason_slate_passes_the_betting_gate(monkeypatch):
    """Only history in the table (break/offseason) must not fail the gate."""
    store = _clean_store()
    store["betting_games"] = [
        game("old", dt.date(2026, 6, 1),
             fetched_at_utc=NOW - dt.timedelta(days=58))]
    assert _validate(monkeypatch, store, scope="betting") == 0
