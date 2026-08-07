"""Regression tests for the two defects that made the site publish wrong data.

1. `run-team-stats --fixture <file>` ran BOTH windows (Last-N and Year-to-Date)
   against that one file. Live, each window is its own request; from a fixture
   it is the same rows twice, so `ytd` ended up holding Last-7 numbers under a
   Year-to-Date heading. Reproduced by the run logs, where both splits carried
   an identical sourceChecksum.

2. The dashboard's "current slate" query had no date filter. Rows are upserted
   by game_key and never deleted, so finished games accumulated indefinitely.
"""

from __future__ import annotations

import datetime

import argparse
import json

import pytest

from wnba_pipeline import __main__ as cli
from wnba_pipeline import web


# --------------------------------------------------------------------------- #
# 1. fixture window guard
# --------------------------------------------------------------------------- #

def write_fixture(tmp_path, last_n):
    """A minimal recorded envelope carrying the window it was captured at."""
    payload = {"resource": "leaguedashteamstats",
               "parameters": {"Season": "2026", "LastNGames": last_n},
               "resultSets": []}
    path = tmp_path / f"fixture_lastn{last_n}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_reads_declared_window(tmp_path):
    assert cli._fixture_last_n_games(str(write_fixture(tmp_path, 7))) == 7
    assert cli._fixture_last_n_games(str(write_fixture(tmp_path, 0))) == 0


def test_string_window_is_accepted(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"parameters": {"LastNGames": "7"}}), encoding="utf-8")
    assert cli._fixture_last_n_games(str(path)) == 7


@pytest.mark.parametrize("payload", [
    {"parameters": {}},                       # no LastNGames at all
    {"parameters": {"LastNGames": None}},
    {"parameters": {"LastNGames": True}},     # bool must not read as 1
    {"nonsense": 1},
])
def test_undeclared_window_reads_as_none(tmp_path, payload):
    path = tmp_path / "x.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert cli._fixture_last_n_games(str(path)) is None


def test_missing_or_unparseable_fixture_reads_as_none(tmp_path):
    assert cli._fixture_last_n_games(None) is None
    assert cli._fixture_last_n_games(str(tmp_path / "nope.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert cli._fixture_last_n_games(str(bad)) is None


def _args(fixture, last_n=7):
    return argparse.Namespace(
        fixture=str(fixture) if fixture else None,
        last_n_games=last_n, season="2026", season_type="Regular Season",
        per_mode="PerGame", data_root="./data", max_age_hours=36, publish=False,
        database_url=None,
    )


def _windows_actually_run(monkeypatch, args):
    """Capture the LastNGames each run_once call was given."""
    seen: list[int] = []

    def fake_run_once(params, data_root, **kw):
        seen.append(params.last_n_games)
        return ({}, 0)

    monkeypatch.setattr(cli, "run_once", fake_run_once)
    code = cli._cmd_run_team_stats(args)
    return seen, code


def test_last7_fixture_publishes_only_last7(monkeypatch, tmp_path):
    """The core regression: a Last-7 fixture must not also populate ytd."""
    seen, code = _windows_actually_run(monkeypatch, _args(write_fixture(tmp_path, 7)))
    assert seen == [7], f"expected only the Last-7 window, ran {seen}"
    assert 0 not in seen, "a Last-7 fixture must never be published as Year-to-Date"
    assert code == 0


def test_ytd_fixture_publishes_only_ytd(monkeypatch, tmp_path):
    seen, code = _windows_actually_run(
        monkeypatch, _args(write_fixture(tmp_path, 0), last_n=0))
    assert seen == [0]
    assert code == 0


def test_window_mismatch_is_a_config_error(monkeypatch, tmp_path):
    """Asking for a window the fixture cannot support must fail loudly."""
    seen, code = _windows_actually_run(
        monkeypatch, _args(write_fixture(tmp_path, 5), last_n=7))
    assert seen == [], "nothing may be published when no window matches"
    assert code == cli.EXIT_CONFIG_ERROR


def test_undeclared_fixture_runs_only_the_requested_window(monkeypatch, tmp_path):
    path = tmp_path / "u.json"
    path.write_text(json.dumps({"parameters": {}}), encoding="utf-8")
    seen, _ = _windows_actually_run(monkeypatch, _args(path, last_n=7))
    assert seen == [7]


def test_live_mode_still_runs_both_windows(monkeypatch):
    """Without a fixture each window is its own request — keep both."""
    seen, _ = _windows_actually_run(monkeypatch, _args(None, last_n=7))
    assert seen == [7, 0]


# --------------------------------------------------------------------------- #
# 2. current-slate date filter
# --------------------------------------------------------------------------- #

def test_betting_query_is_bounded(monkeypatch):
    captured: dict = {}

    def fake_rows(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(web, "_rows", fake_rows)
    web.fetch_betting()

    assert "WHERE" in captured["sql"], "the slate query must be bounded by date"
    assert "game_date >=" in captured["sql"]
    # The bound is a concrete date computed in LEAGUE time, not CURRENT_DATE.
    # CURRENT_DATE is the database session's zone (UTC on Railway), and
    # game_date is the ET slate date, so a server-side pivot drops last night's
    # slate at 20:00 ET — inside the tip-off window.
    assert "CURRENT_DATE" not in captured["sql"]
    assert captured["params"] == (web.slate_floor(),)
    assert isinstance(captured["params"][0], datetime.date)


def test_slate_floor_is_lookback_days_before_league_today():
    import wnba_pipeline.presentation as pres
    assert web.slate_floor() == pres.slate_today() - datetime.timedelta(
        days=web.BETTING_LOOKBACK_DAYS)


def test_lookback_keeps_late_tipoffs():
    """A full day of lookback, so a game that rolls past midnight UTC survives."""
    assert web.BETTING_LOOKBACK_DAYS >= 1


# --------------------------------------------------------------------------- #
# 3. repair-data — may only delete a split it can prove is a duplicate
# --------------------------------------------------------------------------- #

class _Col:
    def __init__(self, name): self.name = name


class _FakeCursor:
    """Minimal psycopg-shaped cursor over an in-memory team_stats table."""

    def __init__(self, store):
        self.store = store
        self.description = None
        self._result = []
        self.rowcount = 0
        self.deleted: list[str] = []

    def execute(self, sql, params=()):
        sql_l = " ".join(sql.split()).lower()
        if sql_l.startswith("select * from team_stats"):
            split = params[0]
            rows = self.store.get(split, [])
            cols = list(rows[0].keys()) if rows else ["team_name"]
            self.description = [_Col(c) for c in cols]
            self._result = [tuple(r.get(c) for c in cols) for r in rows]
        elif sql_l.startswith("delete from team_stats"):
            split = params[0]
            self.rowcount = len(self.store.get(split, []))
            self.store[split] = []
            self.deleted.append(split)
        elif sql_l.startswith("select count(*) from team_stats"):
            split = params[0]
            self._result = [(len(self.store.get(split, [])),)]
        else:  # pragma: no cover - unexpected query
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchall(self): return list(self._result)
    def fetchone(self): return self._result[0] if self._result else None
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, store):
        self.cur = _FakeCursor(store)
        self.closed = False

    def cursor(self): return self.cur
    def close(self): self.closed = True
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _repair(monkeypatch, store, **over):
    from wnba_pipeline import db

    conn = _FakeConn(store)
    monkeypatch.setattr(db, "connect", lambda *a, **k: conn)
    args = argparse.Namespace(database_url=None, remove_split="ytd",
                              against="last7", yes=False)
    for k, v in over.items():
        setattr(args, k, v)
    code = cli._cmd_repair_data(args)
    return code, conn, store


def _identical_store():
    rows = [{"team_name": "Lynx", "games_played": 7, "wins": 4, "losses": 3,
             "points": 85.0, "offensive_rating": 104.0}]
    return {"last7": [dict(r) for r in rows], "ytd": [dict(r) for r in rows]}


def _distinct_store():
    return {
        "last7": [{"team_name": "Lynx", "games_played": 7, "wins": 4, "losses": 3,
                   "points": 90.0, "offensive_rating": 108.0}],
        "ytd": [{"team_name": "Lynx", "games_played": 24, "wins": 15, "losses": 9,
                 "points": 86.0, "offensive_rating": 104.0}],
    }


def test_repair_refuses_when_splits_are_distinct(monkeypatch):
    """The safety property: legitimate data must survive."""
    code, conn, store = _repair(monkeypatch, _distinct_store(), yes=True)
    assert code != 0
    assert conn.cur.deleted == [], "must not delete a split that is not a duplicate"
    assert len(store["ytd"]) == 1


def test_repair_dry_run_does_not_delete(monkeypatch):
    code, conn, store = _repair(monkeypatch, _identical_store())
    assert code == 0
    assert conn.cur.deleted == []
    assert len(store["ytd"]) == 1


def test_repair_applies_only_with_yes(monkeypatch):
    code, conn, store = _repair(monkeypatch, _identical_store(), yes=True)
    assert code == 0
    assert conn.cur.deleted == ["ytd"]
    assert store["ytd"] == []
    assert len(store["last7"]) == 1, "the source split must be untouched"


def test_repair_is_idempotent(monkeypatch):
    store = _identical_store()
    _repair(monkeypatch, store, yes=True)
    code, conn, store = _repair(monkeypatch, store, yes=True)
    assert code == 0
    assert conn.cur.deleted == [], "second run has nothing to delete"


def test_repair_refuses_when_comparison_split_is_empty(monkeypatch):
    store = {"last7": [], "ytd": [{"team_name": "Lynx", "games_played": 7,
                                   "wins": 4, "losses": 3}]}
    code, conn, _ = _repair(monkeypatch, store, yes=True)
    assert code != 0
    assert conn.cur.deleted == []


def test_repair_rejects_identical_split_arguments(monkeypatch):
    code, conn, _ = _repair(monkeypatch, _identical_store(),
                            remove_split="ytd", against="ytd", yes=True)
    assert code == cli.EXIT_CONFIG_ERROR


# --------------------------------------------------------------------------- #
# 4. repair-odds — NULL prices no book could ever have posted
# --------------------------------------------------------------------------- #

class _OddsCursor:
    """psycopg-shaped cursor over one in-memory betting_games table."""

    def __init__(self, rows):
        self.rows = rows
        self._result = []
        self.rowcount = 0
        self.updates = 0

    def execute(self, sql, params=()):
        low = " ".join(sql.split()).lower()
        if low.startswith("select game_key"):
            cols = ["open_ml_away", "open_ml_home", "current_ml_away",
                    "current_ml_home", "sharp_ml_away", "sharp_ml_home"]
            self._result = [
                tuple([r["game_key"]] + [r.get(c) for c in cols])
                for r in self.rows
                if any(r.get(c) is not None and (r[c] == 0 or abs(r[c]) < 100)
                       for c in cols)
            ]
        elif low.startswith("update betting_games"):
            cols = ["open_ml_away", "open_ml_home", "current_ml_away",
                    "current_ml_home", "sharp_ml_away", "sharp_ml_home"]
            touched = 0
            for r in self.rows:
                hit = False
                for c in cols:
                    v = r.get(c)
                    if v is not None and (v == 0 or abs(v) < 100):
                        r[c] = None
                        hit = True
                if hit:
                    touched += 1
            self.rowcount = touched
            self.updates += 1
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchall(self): return list(self._result)
    def fetchone(self): return self._result[0] if self._result else None
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _OddsConn:
    def __init__(self, rows):
        self.cur = _OddsCursor(rows)

    def cursor(self): return self.cur
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _odds_rows():
    return [
        # The real production defect: a truncated "-1,650" stored as -1.
        {"game_key": "2026-08-06:LA@MIN", "current_ml_home": -1650,
         "sharp_ml_home": -1, "current_ml_away": 950, "sharp_ml_away": 925,
         "open_ml_away": 750, "open_ml_home": -1200},
        # A wholly legitimate row that must not be touched.
        {"game_key": "2026-08-07:ATL@WSH", "current_ml_home": 145,
         "sharp_ml_home": 145, "current_ml_away": -175, "sharp_ml_away": -165,
         "open_ml_away": -170, "open_ml_home": 140},
    ]


def _run_repair_odds(monkeypatch, rows, yes=False):
    from wnba_pipeline import db
    conn = _OddsConn(rows)
    monkeypatch.setattr(db, "connect", lambda *a, **k: conn)
    args = argparse.Namespace(database_url="postgres://x", yes=yes)
    return cli._cmd_repair_odds(args), conn


def test_repair_odds_dry_run_changes_nothing(monkeypatch):
    rows = _odds_rows()
    code, conn = _run_repair_odds(monkeypatch, rows)
    assert code == 0
    assert conn.cur.updates == 0
    assert rows[0]["sharp_ml_home"] == -1, "dry run must not write"


def test_repair_odds_nulls_only_the_impossible_value(monkeypatch):
    rows = _odds_rows()
    code, conn = _run_repair_odds(monkeypatch, rows, yes=True)
    assert code == 0
    assert rows[0]["sharp_ml_home"] is None, "the impossible price must be cleared"
    # Everything else on that row survives — repair never invents a correction.
    assert rows[0]["current_ml_home"] == -1650
    assert rows[0]["sharp_ml_away"] == 925
    # The legitimate row is untouched.
    assert rows[1] == _odds_rows()[1]


def test_repair_odds_is_idempotent(monkeypatch):
    rows = _odds_rows()
    _run_repair_odds(monkeypatch, rows, yes=True)
    after_first = [dict(r) for r in rows]
    _run_repair_odds(monkeypatch, rows, yes=True)
    assert [dict(r) for r in rows] == after_first


def test_repair_odds_reports_clean_when_nothing_is_wrong(monkeypatch):
    rows = [_odds_rows()[1]]
    code, conn = _run_repair_odds(monkeypatch, rows, yes=True)
    assert code == 0
    assert conn.cur.updates == 0
