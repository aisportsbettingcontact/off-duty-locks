"""Betting feed orchestration: fetch -> merge -> publish -> summary.

Fetches Action Network for the target date(s) and VSIN Circa (today+tomorrow),
merges them into per-game rows, publishes to Postgres, and emits one JSON
summary line on stdout. Fetchers and the publisher are injectable so the whole
flow runs offline in tests.

Unlike team stats there is no file audit trail here — the database is the store
— so a requested-but-failed publish is a STORAGE_ERROR (exit 6), while Action
Network being unreachable for every requested date is UPSTREAM_UNAVAILABLE
(exit 3). No games for a date is a normal SUCCESS with zero rows.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from wnba_pipeline.contract import (
    EXIT_OK,
    EXIT_STORAGE_ERROR,
    EXIT_UPSTREAM_UNAVAILABLE,
    UpstreamUnavailable,
)
from wnba_pipeline.betting import actionnetwork
from wnba_pipeline.betting import vsin as vsin_mod
from wnba_pipeline.betting.merge import merge_games

logger = logging.getLogger("wnba_pipeline.betting.runner")

_ISO_Z = "%Y-%m-%dT%H:%M:%SZ"


def _et_now(now_utc: datetime) -> datetime:
    """Best-effort US/Eastern; falls back to UTC if tz data is unavailable."""
    try:
        from zoneinfo import ZoneInfo

        return now_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001 - tz data optional; UTC is an acceptable fallback
        return now_utc


def _default_dates(now_utc: datetime) -> list[str]:
    et = _et_now(now_utc)
    return [et.strftime("%Y-%m-%d"), (et + timedelta(days=1)).strftime("%Y-%m-%d")]


def run_betting(
    *,
    dates: list[str] | None = None,
    publish_fn: Callable[[list[Any]], int] | None = None,
    an_fetch: Callable[[str], list[Any]] | None = None,
    vsin_fetch: Callable[[str, str], list[Any]] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Run the betting feed once. Returns a summary dict including ``exitCode``.

    ``an_fetch(date)`` and ``vsin_fetch(source, view)`` default to the real
    modules; injecting them (and ``publish_fn``) keeps tests offline.
    """
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    an_fetch = an_fetch or actionnetwork.fetch_wnba_odds
    vsin_fetch = vsin_fetch or vsin_mod.fetch_vsin
    now = now_fn()
    fetched_at = now.astimezone(timezone.utc).strftime(_ISO_Z)
    if dates is None:
        dates = _default_dates(now)

    an_games: list[Any] = []
    errors: list[str] = []
    for d in dates:
        try:
            an_games.extend(an_fetch(d))
        except UpstreamUnavailable as exc:
            errors.append(f"an[{d}]:{exc.reason}")
            logger.warning("Action Network unavailable for %s: %s", d, exc.reason)

    circa: list[Any] = []
    for view in ("today", "tomorrow"):
        try:
            circa.extend(vsin_fetch("circa", view))
        except UpstreamUnavailable as exc:
            errors.append(f"vsin[{view}]:{exc.reason}")
            logger.warning("VSIN Circa unavailable for %s: %s", view, exc.reason)

    merged = merge_games(an_games, circa, fetched_at_utc=fetched_at)

    status = "SUCCESS"
    exit_code = EXIT_OK
    publish_result: str | None = None
    published = 0

    # Action Network is the backbone: if it produced nothing AND erred on every
    # requested date, that's an upstream failure (not a legitimate empty slate).
    if not an_games and any(e.startswith("an[") for e in errors):
        status = "UPSTREAM_UNAVAILABLE"
        exit_code = EXIT_UPSTREAM_UNAVAILABLE
    elif publish_fn is not None and merged:
        try:
            published = publish_fn(merged)
            publish_result = f"PUBLISHED:{published}"
        except Exception as exc:  # noqa: BLE001 - surface publish failure as STORAGE_ERROR
            status = "STORAGE_ERROR"
            exit_code = EXIT_STORAGE_ERROR
            publish_result = f"FAILED:{type(exc).__name__}"
            logger.warning("betting publish failed: %s", exc)

    sharp_matched = sum(1 for g in merged if g.vsin_game_id is not None)
    summary = {
        "feed": "betting",
        "status": status,
        "exitCode": exit_code,
        "dates": dates,
        "anGames": len(an_games),
        "vsinCircaGames": len(circa),
        "merged": len(merged),
        "sharpMatched": sharp_matched,
        "published": published,
        "publishResult": publish_result,
        "errors": errors,
        "fetchedAtUtc": fetched_at,
    }
    print(json.dumps(summary, separators=(",", ":")), file=sys.stdout, flush=True)
    logger.info(
        "betting run finished status=%s merged=%d sharpMatched=%d published=%d",
        status, len(merged), sharp_matched, published,
    )
    return summary
