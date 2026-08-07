#!/usr/bin/env python3
"""Assert that the live site is serving CURRENT data, not merely answering 200.

deploy-verify's data step used to write row counts into the step summary with
``|| echo "n/a"`` on every parse, and gated only on HTTP 200. It would have
certified a 72-hour-stale production as healthy. A check that cannot fail is a
green rubber stamp on a deploy.

Reads ``/api/status`` and ``/api/betting`` and exits non-zero when:
  - the payload is not valid JSON,
  - ``db_ok`` is false,
  - a feed's timestamp is missing,
  - a feed is older than its limit.

Usage: check_live_status.py BASE_URL [--summary PATH]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request

# Hours a feed may be behind before a deploy is not verified. Betting publishes
# every 30 minutes; team statistics daily.
BETTING_MAX_AGE_H = 6.0
TEAM_STATS_MAX_AGE_H = 48.0

TIMEOUT_S = 30


def _get_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def _age_hours(iso: str, now: dt.datetime) -> float | None:
    try:
        moment = dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return (now - moment).total_seconds() / 3600.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("--summary", default=None,
                        help="append a markdown report here (GITHUB_STEP_SUMMARY)")
    args = parser.parse_args(argv)

    base = args.base.rstrip("/")
    lines: list[str] = []
    failed = False

    def note(text: str) -> None:
        lines.append(text)

    def fail(text: str) -> None:
        nonlocal failed
        failed = True
        print(f"::error::{text}")

    try:
        status = _get_json(f"{base}/api/status")
    except Exception as exc:  # noqa: BLE001 - any failure here is a failed deploy
        fail(f"/api/status unreachable or unparseable: {exc}")
        status = None

    if isinstance(status, dict):
        note(f"- build: **{status.get('build', 'unknown')}**")
        if not status.get("db_ok"):
            fail("/api/status reports db_ok=false — the app cannot reach its database")

        now = dt.datetime.now(dt.timezone.utc)
        checks = (
            ("betting", (status.get("betting") or {}).get("fetched_at_utc"),
             BETTING_MAX_AGE_H),
            ("team_stats last7",
             ((status.get("team_stats") or {}).get("last7") or {}).get("updated_at"),
             TEAM_STATS_MAX_AGE_H),
        )
        for name, iso, limit in checks:
            if not iso:
                fail(f"{name} has no timestamp in /api/status")
                continue
            age = _age_hours(iso, now)
            if age is None:
                fail(f"{name} timestamp is unparseable: {iso!r}")
                continue
            note(f"- {name} age: **{age:.1f}h** (limit {limit:.0f}h)")
            if age > limit:
                fail(f"{name} is {age:.1f}h old, over its {limit:.0f}h limit")

    try:
        betting = _get_json(f"{base}/api/betting")
        games = betting.get("games") if isinstance(betting, dict) else None
        if not isinstance(games, list):
            fail("/api/betting did not return a games array")
        else:
            note(f"- betting rows: **{len(games)}**")
    except Exception as exc:  # noqa: BLE001
        fail(f"/api/betting unreachable or unparseable: {exc}")

    for split in ("last7", "ytd"):
        try:
            payload = _get_json(f"{base}/api/team-stats?split={split}")
            teams = payload.get("teams") if isinstance(payload, dict) else None
            if not isinstance(teams, list) or not teams:
                fail(f"team-stats {split} served no rows")
            else:
                note(f"- team-stats `{split}`: **{len(teams)}** teams")
        except Exception as exc:  # noqa: BLE001
            fail(f"/api/team-stats?split={split} unreachable or unparseable: {exc}")

    report = "\n".join(lines)
    print(report)
    if args.summary:
        try:
            with open(args.summary, "a", encoding="utf-8") as handle:
                handle.write("\n### Live data\n" + report + "\n")
        except OSError:
            pass
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
