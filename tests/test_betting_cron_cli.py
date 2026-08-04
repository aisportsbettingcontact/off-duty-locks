"""The ``betting-cron`` command — the single process a Railway cron tick runs.

Railway's cron schedule (railway.scrape.json) is intentionally year-round
(``*/30 * * * *``): a Railway schedule expression cannot be month-gated without
someone remembering to edit it twice a year, so the season gate lives HERE, in
code, where it is testable. At each tick Railway starts the container, runs
this one command, and records its exit code — so the command must compose the
two gates scrape.yml used to run as separate workflow steps (betting publish,
then betting-scope validate-data) into one honest exit code.

Everything here is offline: the clock, the environment, and both sub-commands
are injected fakes. Nothing touches the network or a database.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

import pytest

from wnba_pipeline import __main__ as cli
from wnba_pipeline.contract import (
    EXIT_STORAGE_ERROR,
    EXIT_UPSTREAM_UNAVAILABLE,
    EXIT_VALIDATION_FAILED,
)

UTC = dt.timezone.utc


def _args(**over):
    args = argparse.Namespace(
        database_url=None, season="1900", date=None, publish=True,
        last_split="last7", scope="betting", as_json=False,
        warn_is_failure=False, betting_fresh_hours=6.0)
    for k, v in over.items():
        setattr(args, k, v)
    return args


class _Recorder:
    """A fake sub-command: records calls, returns a fixed exit code."""

    def __init__(self, code=0):
        self.code = code
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        return self.code


def _run(now, *, env=None, betting_code=0, validate_code=0, args=None):
    betting = _Recorder(betting_code)
    validate = _Recorder(validate_code)
    code = cli._cmd_betting_cron(
        args or _args(),
        now_fn=lambda: now,
        environ={} if env is None else env,
        betting_fn=betting,
        validate_fn=validate,
    )
    return code, betting, validate


def _events(capsys):
    return [json.loads(line)
            for line in capsys.readouterr().out.splitlines() if line]


# --------------------------------------------------------------------------- #
# season gate: month boundaries, checked in UTC
# --------------------------------------------------------------------------- #

def test_april_30_is_offseason_no_work_touched(capsys):
    code, betting, validate = _run(dt.datetime(2026, 4, 30, 23, 59, tzinfo=UTC))
    assert code == 0
    assert betting.calls == [] and validate.calls == []
    events = _events(capsys)
    assert [e["event"] for e in events] == ["offseason_skip"]
    assert events[0]["month"] == 4


def test_may_1_first_tick_is_in_season():
    code, betting, validate = _run(dt.datetime(2026, 5, 1, 0, 0, tzinfo=UTC))
    assert code == 0
    assert len(betting.calls) == 1 and len(validate.calls) == 1


def test_october_31_last_tick_is_in_season():
    code, betting, validate = _run(dt.datetime(2026, 10, 31, 23, 30, tzinfo=UTC))
    assert code == 0
    assert len(betting.calls) == 1 and len(validate.calls) == 1


def test_november_1_is_offseason(capsys):
    code, betting, validate = _run(dt.datetime(2026, 11, 1, 0, 0, tzinfo=UTC))
    assert code == 0
    assert betting.calls == [] and validate.calls == []
    assert _events(capsys)[0]["event"] == "offseason_skip"


def test_month_is_judged_in_utc_not_local_time():
    """Nov 1 00:30 at UTC+14 is still Oct 31 10:30 UTC — in season."""
    kiritimati = dt.timezone(dt.timedelta(hours=14))
    local = dt.datetime(2026, 11, 1, 0, 30, tzinfo=kiritimati)
    assert local.astimezone(UTC).month == 10   # the premise of the test
    code, betting, validate = _run(local)
    assert code == 0
    assert len(betting.calls) == 1 and len(validate.calls) == 1


# --------------------------------------------------------------------------- #
# kill switch: PIPELINE_ENABLED=false pauses Railway exactly like Actions
# --------------------------------------------------------------------------- #

def test_kill_switch_logs_and_exits_zero_without_running(capsys):
    code, betting, validate = _run(
        dt.datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        env={"PIPELINE_ENABLED": "false"})
    assert code == 0
    assert betting.calls == [] and validate.calls == []
    events = _events(capsys)
    assert [e["event"] for e in events] == ["pipeline_disabled"]


@pytest.mark.parametrize("value", ["False", "FALSE", " false "])
def test_kill_switch_matches_actions_case_insensitive_compare(value, capsys):
    """GitHub's `vars.PIPELINE_ENABLED != 'false'` compares case-insensitively;
    the Railway switch must not be stricter than the Actions one it mirrors."""
    code, betting, _ = _run(dt.datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
                            env={"PIPELINE_ENABLED": value})
    assert code == 0 and betting.calls == []
    assert _events(capsys)[0]["event"] == "pipeline_disabled"


@pytest.mark.parametrize("env", [{}, {"PIPELINE_ENABLED": "true"},
                                 {"PIPELINE_ENABLED": ""}])
def test_switch_only_pauses_on_false(env):
    """Unset / 'true' / empty all mean run — mirroring `!= 'false'`."""
    code, betting, validate = _run(dt.datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
                                   env=env)
    assert code == 0
    assert len(betting.calls) == 1 and len(validate.calls) == 1


def test_kill_switch_beats_the_season_gate(capsys):
    """Paused is paused: no offseason_skip noise while disabled."""
    code, betting, validate = _run(
        dt.datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
        env={"PIPELINE_ENABLED": "false"})
    assert code == 0
    assert [e["event"] for e in _events(capsys)] == ["pipeline_disabled"]


# --------------------------------------------------------------------------- #
# exit-code composition: one honest exit code for Railway's run history
# --------------------------------------------------------------------------- #

def test_both_ok_exits_zero(capsys):
    code, *_ = _run(dt.datetime(2026, 7, 15, 12, 0, tzinfo=UTC))
    assert code == 0
    finished = _events(capsys)[-1]
    assert finished["event"] == "betting_cron_finished"
    assert finished["bettingExit"] == 0 and finished["validateExit"] == 0
    assert finished["exitCode"] == 0


@pytest.mark.parametrize("betting_code", [EXIT_UPSTREAM_UNAVAILABLE,
                                          EXIT_STORAGE_ERROR])
def test_publish_failure_is_the_exit_code(betting_code, capsys):
    code, _, validate = _run(dt.datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
                             betting_code=betting_code)
    assert code == betting_code
    # Mirrors scrape.yml: validate still runs after a failed publish, so the
    # tick's log answers BOTH questions (did we publish? is the data sane?).
    assert len(validate.calls) == 1
    assert _events(capsys)[-1]["bettingExit"] == betting_code


def test_validate_failure_is_the_exit_code(capsys):
    code, betting, _ = _run(dt.datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
                            validate_code=EXIT_VALIDATION_FAILED)
    assert code == EXIT_VALIDATION_FAILED
    assert len(betting.calls) == 1
    assert _events(capsys)[-1]["validateExit"] == EXIT_VALIDATION_FAILED


def test_publish_failure_wins_over_validate_failure():
    """When both fail, report the publish failure — it is the root cause the
    runbook triages first (3=upstream, 6=storage)."""
    code, *_ = _run(dt.datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
                    betting_code=EXIT_UPSTREAM_UNAVAILABLE,
                    validate_code=EXIT_VALIDATION_FAILED)
    assert code == EXIT_UPSTREAM_UNAVAILABLE


# --------------------------------------------------------------------------- #
# CLI wiring: the command the Railway startCommand actually invokes
# --------------------------------------------------------------------------- #

def test_parser_wires_betting_cron_with_betting_scope_defaults():
    args = cli.build_parser().parse_args(["betting-cron"])
    assert args.func is cli._cmd_betting_cron
    # The validate gate must be betting-scope (the tick cannot refresh team
    # stats) and the publish must be on — the whole point of the tick.
    assert args.scope == "betting"
    assert args.publish is True
    assert args.date is None
    assert args.season == "2026"
    assert args.betting_fresh_hours == 6.0


def test_parser_accepts_database_url_override():
    args = cli.build_parser().parse_args(
        ["betting-cron", "--database-url", "postgresql://example/db"])
    assert args.database_url == "postgresql://example/db"


def test_sub_commands_receive_the_same_args_namespace():
    """betting-cron composes the existing commands — the args object it was
    given flows through unchanged, so flags keep meaning the same thing."""
    ns = _args(season="2026")
    _, betting, validate = _run(dt.datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
                                args=ns)
    assert betting.calls == [ns] and validate.calls == [ns]


def test_offseason_skip_line_is_machine_readable(capsys):
    code, *_ = _run(dt.datetime(2026, 12, 25, 6, 30, tzinfo=UTC))
    assert code == 0
    event = _events(capsys)[0]
    assert event["event"] == "offseason_skip"
    assert event["month"] == 12
    assert event["seasonMonths"] == "5-10"
    assert event["exitCode"] == 0
