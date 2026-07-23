"""Betting runner: orchestration, publish wiring, and exit-code mapping.

Fetchers and the publisher are injected, so the whole flow runs offline.
"""

from __future__ import annotations

import json

from wnba_pipeline.contract import (
    EXIT_OK,
    EXIT_STORAGE_ERROR,
    EXIT_UPSTREAM_UNAVAILABLE,
    UpstreamUnavailable,
)
from wnba_pipeline.betting import actionnetwork, vsin
from wnba_pipeline.betting.runner import run_betting


def _load(fixtures_dir):
    an = actionnetwork.parse_scoreboard(
        json.loads((fixtures_dir / "betting" / "an_scoreboard_wnba.json").read_text()),
        "2026-07-22",
    )
    circa = vsin.parse_splits((fixtures_dir / "betting" / "vsin_circa_wnba.html").read_text())
    return an, circa


def test_run_betting_publishes(fixtures_dir, capsys):
    an, circa = _load(fixtures_dir)
    published: list = []
    summary = run_betting(
        dates=["2026-07-22"],
        an_fetch=lambda d: an,
        vsin_fetch=lambda source, view: circa if source == "circa" else [],
        publish_fn=lambda games: (published.extend(games) or len(games)),
    )
    assert summary["status"] == "SUCCESS"
    assert summary["exitCode"] == EXIT_OK
    assert summary["merged"] == 2
    assert summary["sharpMatched"] == 2
    assert summary["published"] == 2
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["feed"] == "betting"


def test_run_betting_upstream_unavailable(fixtures_dir):
    def bad_an(d):
        raise UpstreamUnavailable("http_403_forbidden", http_status=403)

    summary = run_betting(
        dates=["2026-07-22"],
        an_fetch=bad_an,
        vsin_fetch=lambda source, view: [],
        publish_fn=lambda games: len(games),
    )
    assert summary["status"] == "UPSTREAM_UNAVAILABLE"
    assert summary["exitCode"] == EXIT_UPSTREAM_UNAVAILABLE


def test_run_betting_publish_failure_is_storage_error(fixtures_dir):
    an, circa = _load(fixtures_dir)

    def boom(games):
        raise RuntimeError("db down")

    summary = run_betting(
        dates=["2026-07-22"],
        an_fetch=lambda d: an,
        vsin_fetch=lambda source, view: circa,
        publish_fn=boom,
    )
    assert summary["status"] == "STORAGE_ERROR"
    assert summary["exitCode"] == EXIT_STORAGE_ERROR
    assert summary["publishResult"] == "FAILED:RuntimeError"


def test_run_betting_no_games_is_success(fixtures_dir):
    summary = run_betting(
        dates=["2026-07-23"],
        an_fetch=lambda d: [],
        vsin_fetch=lambda source, view: [],
        publish_fn=lambda games: len(games),
    )
    assert summary["status"] == "SUCCESS"
    assert summary["exitCode"] == EXIT_OK
    assert summary["merged"] == 0
    assert summary["published"] == 0
