"""Command-line interface for the WNBA team-statistics pipeline.

::

    wnba-pipeline run [--season 2026] [--season-type "Regular Season"]
                      [--last-n-games 7] [--per-mode PerGame]
                      [--data-root ./data] [--fixture PATH]
                      [--max-age-hours 36]
    wnba-pipeline status [--season 2026] ... [--data-root ./data]

``run`` executes exactly one extraction run (see :mod:`wnba_pipeline.runner`)
and exits with the contract's status→exit-code mapping. ``--fixture`` selects
offline/e2e mode: the recorded envelope is used instead of any network I/O, so
the whole pipeline (team resolution → validation → storage → manifest) can run
deterministically in CI with no external dependency.

``status`` is read-only: it prints the last-known-good summary and freshness
for the selected extraction key without contacting the source or mutating
anything.

The process emits the run manifest as one JSON line on stdout (from the
runner) and structured logs as JSON lines on stderr. Exit codes are defined in
:mod:`wnba_pipeline.contract` (EXIT_OK=0, CONFIG_ERROR=2, UPSTREAM_UNAVAILABLE=3,
VALIDATION_FAILED=4, LOCK_HELD=5, STORAGE_ERROR=6, INTERNAL_ERROR=7).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Sequence

from wnba_pipeline import __version__, contract
from wnba_pipeline.contract import (
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    ExtractionParams,
    FreshnessState,
)
from wnba_pipeline.runner import DEFAULT_MAX_AGE_HOURS, _parse_iso, run_once
from wnba_pipeline.storage import Store

_ISO_Z = "%Y-%m-%dT%H:%M:%SZ"


def _configure_logging(verbose: bool) -> None:
    """JSON-ish structured logs on stderr. Never stdout (reserved for the
    manifest) and never any header/secret material — the pipeline modules only
    ever log sanitized fields."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root = logging.getLogger("wnba_pipeline")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def _params_from_args(args: argparse.Namespace) -> ExtractionParams:
    defaults = ExtractionParams()
    return ExtractionParams(
        season=args.season,
        season_type=args.season_type,
        last_n_games=args.last_n_games,
        measure_type=defaults.measure_type,
        per_mode=args.per_mode,
        sort_field=defaults.sort_field,
        sort_direction=defaults.sort_direction,
    )


def _add_param_args(p: argparse.ArgumentParser) -> None:
    d = ExtractionParams()
    p.add_argument("--season", default=d.season,
                   help=f"season year (default: {d.season})")
    p.add_argument("--season-type", default=d.season_type,
                   help=f'season type (default: "{d.season_type}")')
    p.add_argument("--last-n-games", type=int, default=d.last_n_games,
                   help=f"last N games window (default: {d.last_n_games})")
    p.add_argument("--per-mode", default=d.per_mode,
                   choices=["PerGame", "Totals"],
                   help=f"per-game or season totals (default: {d.per_mode})")
    p.add_argument("--data-root", default="./data",
                   help="storage root directory (default: ./data)")


def _make_publish_fn(args: argparse.Namespace):
    """A DB publish callable when --publish is in effect, else None."""
    if not getattr(args, "publish", False):
        return None
    from wnba_pipeline.db import TeamStatsPublisher

    return TeamStatsPublisher(getattr(args, "database_url", None)).publish


def _cmd_run(args: argparse.Namespace) -> int:
    params = _params_from_args(args)
    _, exit_code = run_once(
        params,
        args.data_root,
        fixture_path=args.fixture,
        max_age_hours=args.max_age_hours,
        publish_fn=_make_publish_fn(args),
    )
    # The runner already printed the manifest JSON line on stdout.
    return exit_code


def _cmd_run_team_stats(args: argparse.Namespace) -> int:
    """Run BOTH splits — Last-N (default 7) and Year-to-Date (LastNGames=0) —
    publishing each, so the site's 'Last 7 Games' and 'Year-to-Date' sections
    stay in sync. Returns the highest-severity exit code across the splits.

    Each split is a full, independent locked run with its own manifest emitted
    on stdout (one JSON line per split)."""
    import dataclasses

    publish_fn = _make_publish_fn(args)
    base = _params_from_args(args)
    windows: list[int] = []
    for window in (args.last_n_games, 0):
        if window not in windows:
            windows.append(window)
    worst = EXIT_OK
    for window in windows:
        params = dataclasses.replace(base, last_n_games=window)
        _, code = run_once(
            params,
            args.data_root,
            fixture_path=args.fixture,
            max_age_hours=args.max_age_hours,
            publish_fn=publish_fn,
        )
        worst = max(worst, code)
    return worst


def _cmd_db_init(args: argparse.Namespace) -> int:
    """Create the serving-layer schema in the target database (idempotent)."""
    from wnba_pipeline import db

    try:
        db.init_db(getattr(args, "database_url", None))
    except contract.ConfigError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return EXIT_CONFIG_ERROR
    print(json.dumps({"result": "db_initialized"}, indent=2))
    return EXIT_OK


def _cmd_status(args: argparse.Namespace) -> int:
    """Read-only: print LKG summary + freshness for the key. No network."""
    params = _params_from_args(args)
    key = params.extraction_key()
    store = Store(args.data_root)
    try:
        lkg, path = store.load_last_known_good(key)
    except contract.StorageError as exc:
        summary = {
            "extractionKey": key,
            "dataRoot": args.data_root,
            "freshnessState": FreshnessState.INVALID.value,
            "lastKnownGood": None,
            "error": str(exc),
        }
        print(json.dumps(summary, indent=2))
        return EXIT_CONFIG_ERROR

    if lkg is None:
        summary = {
            "extractionKey": key,
            "dataRoot": args.data_root,
            "freshnessState": FreshnessState.MISSING.value,
            "lastKnownGood": None,
        }
        print(json.dumps(summary, indent=2))
        return EXIT_OK

    fetched = _parse_iso(lkg.get("fetchedAtUtc"))
    now = datetime.now(timezone.utc)
    if fetched is None:
        freshness = FreshnessState.STALE
        age_hours = None
    else:
        age_hours = (now - fetched).total_seconds() / 3600.0
        freshness = (
            FreshnessState.FRESH
            if age_hours <= args.max_age_hours
            else FreshnessState.STALE
        )
    summary = {
        "extractionKey": key,
        "dataRoot": args.data_root,
        "currentPath": str(path),
        "freshnessState": freshness.value,
        "ageHours": None if age_hours is None else round(age_hours, 2),
        "maxAgeHours": args.max_age_hours,
        "lastKnownGood": {
            "season": lkg.get("season"),
            "seasonType": lkg.get("seasonType"),
            "lastNGames": lkg.get("lastNGames"),
            "fetchedAtUtc": lkg.get("fetchedAtUtc"),
            "sourceObservedAtUtc": lkg.get("sourceObservedAtUtc"),
            "teamCount": lkg.get("teamCount"),
            "rowCount": lkg.get("rowCount"),
            "sourceChecksum": lkg.get("sourceChecksum"),
            "normalizedChecksum": lkg.get("normalizedChecksum"),
            "validationState": lkg.get("validationState"),
            "schemaVersion": lkg.get("schemaVersion"),
        },
    }
    print(json.dumps(summary, indent=2))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wnba-pipeline",
        description="Automated WNBA traditional team-statistics extraction pipeline.",
    )
    parser.add_argument("--version", action="version",
                        version=f"wnba-pipeline {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="debug-level structured logs on stderr")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="execute one extraction run")
    _add_param_args(run_p)
    run_p.add_argument("--fixture", default=None,
                       help="offline mode: use this recorded envelope JSON "
                            "instead of any network request")
    run_p.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS,
                       help=f"LKG freshness window in hours "
                            f"(default: {DEFAULT_MAX_AGE_HOURS})")
    run_p.add_argument("--publish", action="store_true",
                       help="publish the accepted snapshot to Postgres (DATABASE_URL)")
    run_p.add_argument("--database-url", default=None,
                       help="Postgres connection string (default: $DATABASE_URL)")
    run_p.set_defaults(func=_cmd_run)

    # run-team-stats: both splits (Last-N + Year-to-Date), publishing each.
    sync_p = sub.add_parser(
        "run-team-stats",
        help="run Last-N and Year-to-Date splits and publish both to Postgres",
    )
    _add_param_args(sync_p)
    sync_p.add_argument("--fixture", default=None,
                        help="offline mode: recorded envelope JSON, used for both splits")
    sync_p.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS,
                        help=f"LKG freshness window in hours "
                             f"(default: {DEFAULT_MAX_AGE_HOURS})")
    sync_p.add_argument("--no-publish", dest="publish", action="store_false",
                        help="skip the Postgres publish (default: publish)")
    sync_p.add_argument("--database-url", default=None,
                        help="Postgres connection string (default: $DATABASE_URL)")
    sync_p.set_defaults(func=_cmd_run_team_stats, publish=True)

    # db-init: create the serving-layer schema (idempotent).
    db_p = sub.add_parser("db-init", help="create the Postgres serving-layer schema")
    db_p.add_argument("--database-url", default=None,
                      help="Postgres connection string (default: $DATABASE_URL)")
    db_p.set_defaults(func=_cmd_db_init)

    status_p = sub.add_parser("status", help="print last-known-good summary (read-only)")
    _add_param_args(status_p)
    status_p.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS,
                          help=f"LKG freshness window in hours "
                               f"(default: {DEFAULT_MAX_AGE_HOURS})")
    status_p.set_defaults(func=_cmd_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(getattr(args, "verbose", False))
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
