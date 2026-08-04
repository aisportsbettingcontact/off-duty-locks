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
import os
import sys
from datetime import datetime, timezone
from typing import Sequence

from wnba_pipeline import __version__, contract
from wnba_pipeline.contract import (
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    EXIT_VALIDATION_FAILED,
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


def _fixture_last_n_games(fixture_path: str | None) -> int | None:
    """The LastNGames the fixture itself declares, or None if it does not say.

    A recorded stats.wnba.com envelope carries the parameters it was captured
    with. That is the only trustworthy statement of which window the rows
    describe, and it is what makes the guard in `_cmd_run_team_stats` possible.
    """
    if not fixture_path:
        return None
    try:
        with open(fixture_path, "rb") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    params = payload.get("parameters") or payload.get("Parameters") or {}
    value = params.get("LastNGames")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _cmd_run_team_stats(args: argparse.Namespace) -> int:
    """Run BOTH splits — Last-N (default 7) and Year-to-Date (LastNGames=0) —
    publishing each, so the site's 'Last 7 Games' and 'Year-to-Date' sections
    stay in sync. Returns the highest-severity exit code across the splits.

    Each split is a full, independent locked run with its own manifest emitted
    on stdout (one JSON line per split).

    Live, each window is a separate request with its own LastNGames, so the two
    splits genuinely describe different periods. In **fixture** mode there is
    only one recorded file, and it was captured at one window — running both
    windows against it publishes the same numbers under two different split
    labels. That is how `ytd` came to hold Last-7 data: one
    `run-team-stats --fixture <last7 file>` wrote both splits from that file,
    and per-snapshot validation cannot see it because each snapshot is
    individually valid.

    So in fixture mode we publish only the window the fixture attests to, and
    say plainly which windows were skipped and why.
    """
    import dataclasses

    publish_fn = _make_publish_fn(args)
    base = _params_from_args(args)
    windows: list[int] = []
    for window in (args.last_n_games, 0):
        if window not in windows:
            windows.append(window)

    fixture_window = _fixture_last_n_games(getattr(args, "fixture", None))
    if getattr(args, "fixture", None):
        if fixture_window is None:
            skipped = [w for w in windows if w != args.last_n_games]
            windows = [args.last_n_games]
            if skipped:
                print(json.dumps({
                    "event": "window_skipped",
                    "reason": "fixture does not declare LastNGames; refusing to "
                              "label it as another window",
                    "fixture": args.fixture,
                    "ran": windows,
                    "skipped": skipped,
                }), file=sys.stderr)
        else:
            skipped = [w for w in windows if w != fixture_window]
            windows = [w for w in windows if w == fixture_window]
            if skipped:
                print(json.dumps({
                    "event": "window_skipped",
                    "reason": "fixture was captured at a single window; publishing "
                              "it under another split label would mislabel the data",
                    "fixture": args.fixture,
                    "fixtureLastNGames": fixture_window,
                    "ran": windows,
                    "skipped": skipped,
                }), file=sys.stderr)
            if not windows:
                print(json.dumps({
                    "error": "fixture window mismatch",
                    "fixtureLastNGames": fixture_window,
                    "requested": [args.last_n_games, 0],
                    "hint": "pass --last-n-games %d to match the fixture, or use a "
                            "fixture captured at the window you want"
                            % fixture_window,
                }), file=sys.stderr)
                return EXIT_CONFIG_ERROR

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


def _cmd_validate_data(args: argparse.Namespace) -> int:
    """Audit the serving tables the site reads. Read-only: SELECTs only.

    Extraction validation runs before anything is written and sees one snapshot
    at a time. This runs after, over the rows actually being served, and can
    therefore catch the class of fault a single snapshot cannot expose — most
    importantly two splits that were derived from the same source and so carry
    identical numbers under different labels.
    """
    from wnba_pipeline import dataquality as dq
    from wnba_pipeline import db
    from wnba_pipeline.runner import EXPECTED_TEAMS_FIXTURE_DIR

    expected: list[str] | None = None
    fixture = EXPECTED_TEAMS_FIXTURE_DIR / f"{args.season}.json"
    if fixture.exists():
        try:
            blob = json.loads(fixture.read_text(encoding="utf-8"))
            teams = blob.get("teams") if isinstance(blob, dict) else blob
            if isinstance(teams, list):
                expected = [t.get("team_name") if isinstance(t, dict) else str(t)
                            for t in teams]
                expected = [t for t in expected if t]
        except (OSError, json.JSONDecodeError, AttributeError):
            expected = None

    try:
        conn = db.connect(getattr(args, "database_url", None))
    except Exception as exc:  # noqa: BLE001 - report, do not traceback
        print(json.dumps({"error": f"cannot connect: {type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        with conn, conn.cursor() as cur:
            def rows(sql, params=()):
                cur.execute(sql, params)
                cols = [d.name for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]

            cur.execute("SELECT DISTINCT split FROM team_stats ORDER BY split")
            splits = [r[0] for r in cur.fetchall()]
            by_split = {
                s: rows("SELECT * FROM team_stats WHERE split = %s", (s,))
                for s in splits
            }
            betting = rows("SELECT * FROM betting_games")
            cur.execute("SELECT max(updated_at) FROM team_stats")
            newest = (cur.fetchone() or [None])[0]
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - already reporting
            pass

    now = datetime.now(timezone.utc)
    findings = dq.run_all(by_split, betting, newest, now,
                          expected_teams=expected, last_split=args.last_split,
                          scope=getattr(args, "scope", "full"),
                          betting_fresh_hours=getattr(args, "betting_fresh_hours", 6.0))

    if getattr(args, "as_json", False):
        for f in findings:
            print(json.dumps({"severity": f.severity, "code": f.code,
                              "message": f.message, "detail": f.detail}))
    else:
        width = 66
        print("=" * width)
        print(" DATA VALIDATION — serving tables")
        print("=" * width)
        print(f"  splits           : {', '.join(splits) or '(none)'}")
        print(f"  betting rows     : {len(betting)}")
        print(f"  expected teams   : "
              f"{len(expected) if expected else 'unknown (no fixture)'}")
        print("-" * width)
        for sev in (dq.FAIL, dq.WARN, dq.INFO):
            group = [f for f in findings if f.severity == sev]
            if not group:
                continue
            print(f"  {sev} ({len(group)})")
            for f in group:
                print(f"    {f.code:<34} {f.message}")
        print("-" * width)

    worst = dq.worst_severity(findings)
    n_fail = sum(1 for f in findings if f.severity == dq.FAIL)
    n_warn = sum(1 for f in findings if f.severity == dq.WARN)
    if not getattr(args, "as_json", False):
        print(f"  RESULT: {worst}   ({n_fail} FAIL, {n_warn} WARN)")

    if worst == dq.FAIL:
        return EXIT_VALIDATION_FAILED
    if worst == dq.WARN and getattr(args, "warn_is_failure", False):
        return EXIT_VALIDATION_FAILED
    return EXIT_OK


def _cmd_repair_data(args: argparse.Namespace) -> int:
    """Remove a split that is provably a duplicate of another.

    Deliberately narrow. It re-runs the cross-split check and deletes only when
    that check FAILS with ``cross.splits_identical`` — i.e. only when the split
    named by ``--remove-split`` is numerically identical to ``--against`` for
    every shared team. Legitimate, distinct data therefore cannot be deleted by
    this command: if the splits differ at all, it refuses and exits non-zero.

    Dry-run by default. ``--yes`` is required to actually write.
    """
    from wnba_pipeline import dataquality as dq
    from wnba_pipeline import db

    target, against = args.remove_split, args.against
    if target == against:
        print(json.dumps({"error": "--remove-split and --against must differ"}),
              file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        conn = db.connect(getattr(args, "database_url", None))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"cannot connect: {type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        return EXIT_CONFIG_ERROR

    width = 66
    print("=" * width)
    print(" DATA REPAIR — remove a provably duplicated split")
    print("=" * width)
    print(f"  remove split     : {target}")
    print(f"  proven against   : {against}")
    print(f"  mode             : {'APPLY (--yes given)' if args.yes else 'DRY RUN'}")
    print("-" * width)

    try:
        with conn, conn.cursor() as cur:
            def rows(split):
                cur.execute("SELECT * FROM team_stats WHERE split = %s", (split,))
                cols = [d.name for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]

            target_rows, against_rows = rows(target), rows(against)
            print(f"  {against:<16} : {len(against_rows)} rows")
            print(f"  {target:<16} : {len(target_rows)} rows")

            if not target_rows:
                print(f"  RESULT: nothing to do — '{target}' has no rows")
                return EXIT_OK
            if not against_rows:
                print(f"  RESULT: REFUSED — '{against}' has no rows, so '{target}' "
                      "cannot be proven a duplicate")
                return EXIT_VALIDATION_FAILED

            findings = dq.check_cross_split(against_rows, target_rows,
                                            last_split=against)
            identical = any(f.code == "cross.splits_identical" for f in findings)
            for f in findings:
                print(f"    {f.severity:<5} {f.code:<34} {f.message}")
            print("-" * width)

            if not identical:
                print(f"  RESULT: REFUSED — '{target}' is not a proven duplicate of "
                      f"'{against}'. Nothing deleted.")
                return EXIT_VALIDATION_FAILED

            if not args.yes:
                print(f"  RESULT: DRY RUN — would delete {len(target_rows)} rows "
                      f"from split '{target}'. Re-run with --yes to apply.")
                return EXIT_OK

            cur.execute("DELETE FROM team_stats WHERE split = %s", (target,))
            deleted = cur.rowcount
            cur.execute("SELECT count(*) FROM team_stats WHERE split = %s", (target,))
            remaining = (cur.fetchone() or [0])[0]
            print(f"  deleted          : {deleted} rows")
            print(f"  remaining        : {remaining} rows in '{target}'")
            print(f"  RESULT: APPLIED — '{target}' removed; the site now renders an "
                  "empty state there instead of mislabelled numbers")
            return EXIT_OK
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


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


def _cmd_betting(args: argparse.Namespace) -> int:
    """Fetch VSIN + Action Network betting markets, merge, and publish."""
    from wnba_pipeline.betting.runner import run_betting

    publish_fn = None
    if getattr(args, "publish", True):
        from wnba_pipeline.db import BettingPublisher

        publish_fn = BettingPublisher(getattr(args, "database_url", None)).publish
    dates = [args.date] if getattr(args, "date", None) else None
    summary = run_betting(dates=dates, publish_fn=publish_fn)
    return int(summary["exitCode"])


def _cmd_serve(args: argparse.Namespace) -> int:
    """Run the read-only web app locally (production uses gunicorn — see
    railway.web.json). Binds the given host/port; defaults to $PORT or 3000."""
    from wnba_pipeline.web import app

    app.run(host=args.host, port=args.port)
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

    # validate-data: read-only audit of what the site is actually serving.
    vd_p = sub.add_parser(
        "validate-data",
        help="audit the serving tables for internal consistency (read-only)")
    vd_p.add_argument("--database-url", default=None,
                      help="Postgres connection string (default: $DATABASE_URL)")
    vd_p.add_argument("--season", default=ExtractionParams().season,
                      help="season whose expected team set to check against")
    vd_p.add_argument("--last-split", default="last7",
                      help="the Last-N split label to compare against ytd")
    vd_p.add_argument("--warn-is-failure", action="store_true",
                      help="exit non-zero on WARN as well as FAIL")
    vd_p.add_argument("--scope", choices=("full", "betting"), default="full",
                      help="betting = gate only the betting checks (the 30-min "
                           "scrape cannot refresh team stats; full validation "
                           "still runs wherever team stats are written)")
    vd_p.add_argument("--betting-fresh-hours", type=float, default=6.0,
                      dest="betting_fresh_hours",
                      help="FAIL when the upcoming slate's newest fetched_at_utc "
                           "is older than this many hours (default: 6)")
    vd_p.add_argument("--json", action="store_true", dest="as_json",
                      help="emit findings as JSON lines instead of a report")
    vd_p.set_defaults(func=_cmd_validate_data)

    # repair-data: delete a split only when it is provably a duplicate.
    rp_p = sub.add_parser(
        "repair-data",
        help="remove a split that is provably a duplicate of another (dry-run by default)")
    rp_p.add_argument("--database-url", default=None,
                      help="Postgres connection string (default: $DATABASE_URL)")
    rp_p.add_argument("--remove-split", default="ytd",
                      help="split to remove if proven duplicate (default: ytd)")
    rp_p.add_argument("--against", default="last7",
                      help="split it must be identical to for removal (default: last7)")
    rp_p.add_argument("--yes", action="store_true",
                      help="actually delete; without this the command only reports")
    rp_p.set_defaults(func=_cmd_repair_data)

    # betting: VSIN + Action Network -> betting_games.
    bet_p = sub.add_parser(
        "betting",
        help="fetch VSIN + Action Network betting markets and publish to Postgres",
    )
    bet_p.add_argument("--date", default=None,
                       help="ET date YYYY-MM-DD (default: today + tomorrow)")
    bet_p.add_argument("--no-publish", dest="publish", action="store_false",
                       help="skip the Postgres publish (default: publish)")
    bet_p.add_argument("--database-url", default=None,
                       help="Postgres connection string (default: $DATABASE_URL)")
    bet_p.set_defaults(func=_cmd_betting, publish=True)

    # serve: read-only web app for local dev (production uses gunicorn).
    serve_p = sub.add_parser("serve", help="run the read-only web app (local dev)")
    serve_p.add_argument("--host", default="0.0.0.0",
                         help="bind host (default: 0.0.0.0)")
    serve_p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "3000")),
                         help="bind port (default: $PORT or 3000)")
    serve_p.set_defaults(func=_cmd_serve)

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
