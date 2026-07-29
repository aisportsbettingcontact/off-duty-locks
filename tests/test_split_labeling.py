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
    assert captured["params"] == (web.BETTING_LOOKBACK_DAYS,)


def test_lookback_keeps_late_tipoffs():
    """A full day of lookback, so a game that rolls past midnight UTC survives."""
    assert web.BETTING_LOOKBACK_DAYS >= 1
