"""Read-only web app that serves the published data for offdutylocks.com.

A small Flask application that reads the Postgres serving tables (``team_stats``,
``betting_games``) and renders them as an HTML dashboard plus JSON endpoints.

It is strictly **read-only** (SELECT only), holds no secrets beyond
``DATABASE_URL`` (Railway-injected, never rendered), and takes no user input
into SQL except a whitelisted ``split`` value — so it is safe to expose
publicly. Missing data renders as a friendly empty state, and a database
outage returns a clean 503 rather than a stack trace.

Run:
    # production (Railway web service): port comes from $PORT via gunicorn.conf.py
    gunicorn --config gunicorn.conf.py wnba_pipeline.web:app
    # local:
    wnba-pipeline serve --port 3000
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
from decimal import Decimal
from html import escape
from typing import Any, Mapping

from flask import Flask, jsonify, render_template, request

from wnba_pipeline import db
from wnba_pipeline import presentation as pres
from wnba_pipeline.enrich import (
    enrich_games, find_team_stats, normalize_team_name, stats_by_team_name)
from wnba_pipeline.model import HOME_COURT_POINTS

logger = logging.getLogger("wnba_pipeline.web")

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

VALID_SPLITS = ("last7", "ytd")

# Which commit is actually serving. Railway injects RAILWAY_GIT_COMMIT_SHA on
# every build; without a stamp the only way to tell what is deployed is to
# byte-compare static assets against a checkout, which is how a shipped
# regression went unnoticed. Falls back to "unknown" rather than guessing.
BUILD_SHA = (
    os.environ.get("RAILWAY_GIT_COMMIT_SHA")
    or os.environ.get("GIT_COMMIT_SHA")
    or os.environ.get("SOURCE_COMMIT")
    or "unknown"
)
BUILD_SHORT = BUILD_SHA[:12] if BUILD_SHA != "unknown" else "unknown"

# Columns surfaced for team stats (order = display order).
TEAM_COLUMNS = (
    "team_name", "games_played", "wins", "losses", "win_pct", "minutes", "points",
    "fgm", "fga", "fg_pct", "fg3m", "fg3a", "fg3_pct", "ftm", "fta", "ft_pct",
    "oreb", "dreb", "reb", "ast", "tov", "stl", "blk", "pf",
    "possessions", "offensive_rating", "updated_at",
)


def _jsonable(value: Any) -> Any:
    """Coerce psycopg cell types (Decimal, date/datetime) to JSON-safe values."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    return value


def _rows(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Run a read query and return JSON-safe row dicts. Caller handles errors."""
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d.name for d in cur.description]
            return [{c: _jsonable(v) for c, v in zip(cols, row)} for row in cur.fetchall()]
    finally:
        conn.close()


def fetch_team_stats(split: str) -> list[dict[str, Any]]:
    cols = ", ".join(TEAM_COLUMNS)
    return _rows(
        f"SELECT {cols} FROM team_stats WHERE split = %s "
        "ORDER BY offensive_rating DESC NULLS LAST, team_name",
        (split,),
    )


# How far back the slate reaches. The scrapers publish today and tomorrow, and
# rows are upserted by game_key, so nothing ever removes a finished game: an
# unfiltered SELECT grows without bound and the page fills up with history under
# a "current slate" heading. One day of lookback keeps last night's late tip-offs
# visible (a game starting 23:00 US local is already tomorrow in UTC, and
# game_date is UTC) without carrying the rest of the season.
BETTING_LOOKBACK_DAYS = 1


def slate_floor() -> _dt.date:
    """The earliest ``game_date`` the slate shows, resolved in league time.

    ``CURRENT_DATE`` is the database session's date, which on Railway is UTC.
    Since ``game_date`` is the ET slate date, a UTC pivot drops last night's
    slate four hours early — at 20:00 ET, inside the tip-off window. The floor
    is computed in Python against the league timezone and passed as a bound
    parameter so the query no longer depends on the server's zone at all.
    """
    return pres.slate_today() - _dt.timedelta(days=BETTING_LOOKBACK_DAYS)


# Columns added after the table shipped. `bootstrap_schema` creates them via
# ADD COLUMN IF NOT EXISTS, but only the WRITE path runs it — so between a web
# deploy and the next publish the read path can name a column the database does
# not have yet. Selecting them optionally removes that ordering hazard entirely:
# the site serves correctly either side of the migration, and the fallback stops
# being exercised as soon as one publish has run.
_OPTIONAL_BETTING_COLUMNS = ("vsin_fetched_at_utc",)


def fetch_betting() -> list[dict[str, Any]]:
    """The current slate: games from the last ``BETTING_LOOKBACK_DAYS`` onward."""
    def _select(columns: tuple[str, ...]) -> list[dict[str, Any]]:
        return _rows(
            f"SELECT {', '.join(columns)} FROM betting_games "
            "WHERE game_date >= %s "
            "ORDER BY game_date, game_key",
            (slate_floor(),),
        )

    try:
        return _select(db.BETTING_GAMES_COLUMNS)
    except Exception as exc:  # noqa: BLE001 - narrowed by the retry below
        legacy = tuple(c for c in db.BETTING_GAMES_COLUMNS
                       if c not in _OPTIONAL_BETTING_COLUMNS)
        if len(legacy) == len(db.BETTING_GAMES_COLUMNS):
            raise
        logger.warning(
            "betting select failed (%s); retrying without %s — the schema "
            "migration has not run yet", exc, _OPTIONAL_BETTING_COLUMNS)
        return _select(legacy)


def fetch_stats_by_team(split: str = "last7") -> dict[str, dict[str, Any]]:
    """team_id -> stats row, for Model v0 inputs and W-L records."""
    return {str(r["team_id"]): r for r in _rows(
        "SELECT team_id, team_name, wins, losses, possessions, offensive_rating, points "
        "FROM team_stats WHERE split = %s", (split,),
    )}


def fetch_status_counts() -> dict[str, Any]:
    """Freshness aggregates for /api/status and the dashboard stamps.

    ONE round trip of scalar subqueries — the endpoint is polled every 60s by
    every open dashboard, so it must stay a single cheap SELECT. The betting
    scope is the same slate window the page renders (``BETTING_LOOKBACK_DAYS``)
    so the poll and the header stamp always describe the same rows.
    """
    return _rows(
        "SELECT "
        "(SELECT count(*) FROM betting_games "
        " WHERE game_date >= %s) AS betting_rows, "
        "(SELECT max(fetched_at_utc) FROM betting_games "
        " WHERE game_date >= %s) AS betting_fetched_at_utc, "
        "(SELECT count(*) FROM team_stats WHERE split = 'last7') AS last7_rows, "
        "(SELECT max(updated_at) FROM team_stats WHERE split = 'last7') AS last7_updated_at, "
        "(SELECT count(*) FROM team_stats WHERE split = 'ytd') AS ytd_rows, "
        "(SELECT max(updated_at) FROM team_stats WHERE split = 'ytd') AS ytd_updated_at",
        (slate_floor(), slate_floor()),
    )[0]


def _older_iso(a: Any, b: Any) -> str:
    """The OLDER of two ISO timestamps — pinpoint accuracy never overstates.

    A missing split has no freshness to overstate (the UI shows its empty
    state instead), so the present value stands alone; both missing -> ""."""
    vals = [str(v) for v in (a, b) if v]
    if not vals:
        return ""
    if len(vals) == 1:
        return vals[0]

    def _key(iso: str):
        try:
            parsed = _dt.datetime.fromisoformat(iso)
        except ValueError:  # non-ISO junk never wins the "older" contest
            return _dt.datetime.max.replace(tzinfo=_dt.timezone.utc)
        if parsed.tzinfo is None:  # DB timestamps are UTC; keep them comparable
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed

    return min(vals, key=_key)


def fetch_line_history(game_key: str) -> dict[str, Any] | None:
    """Opening values + ordered snapshots for one game; None if unknown."""
    opening = _rows(
        "SELECT open_spread AS spread, open_total AS total, "
        "open_ml_away AS ml_away, open_ml_home AS ml_home "
        "FROM betting_games WHERE game_key = %s", (game_key,),
    )
    if not opening:
        return None
    cols = ", ".join(db.SNAPSHOT_COLUMNS)
    snapshots = _rows(
        f"SELECT {cols} FROM betting_line_snapshots "
        "WHERE game_key = %s ORDER BY captured_at_utc", (game_key,),
    )
    return {"game_key": game_key, "opening": opening[0], "snapshots": snapshots}


@app.context_processor
def _build_context():
    """Every page can state which commit rendered it."""
    return {"build_sha": BUILD_SHORT}


@app.after_request
def _security_headers(response):
    """Baseline security headers on every response.

    Railway terminates TLS for the domain, so the site is HTTPS-only already —
    HSTS (180 days) pins browsers to it. nosniff stops MIME sniffing, and
    X-Frame-Options + frame-ancestors shut off clickjacking (both forms so old
    and new browsers each honor one). Deliberately NO broader
    Content-Security-Policy: the dashboard uses inline script/styles, which a
    script-src/style-src policy would break.
    """
    response.headers["Strict-Transport-Security"] = "max-age=15552000"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# --------------------------------------------------------------------------- #
# JSON API
# --------------------------------------------------------------------------- #

@app.get("/healthz")
def healthz():
    return "ok", 200


@app.get("/api/status")
def api_status():
    """Real-time freshness truth: exact per-table timestamps, never cached.

    The dashboard polls this every 60s to re-render its stamps and to reload
    when strictly newer betting data lands. A database outage still answers a
    well-formed degraded shape (``db_ok: false``) — never a stack trace."""
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    try:
        row = fetch_status_counts()
    except Exception as exc:  # noqa: BLE001 - never leak internals to clients
        logger.warning("status query failed: %s", exc)
        resp = jsonify({"now": now, "build": BUILD_SHORT, "db_ok": False,
                        "betting": None, "team_stats": None})
        resp.headers["Cache-Control"] = "no-store"
        return resp, 503
    resp = jsonify({
        "now": now,
        "build": BUILD_SHORT,
        "db_ok": True,
        "betting": {
            "rows": row["betting_rows"],
            "fetched_at_utc": row["betting_fetched_at_utc"],
        },
        "team_stats": {
            "last7": {"rows": row["last7_rows"], "updated_at": row["last7_updated_at"]},
            "ytd": {"rows": row["ytd_rows"], "updated_at": row["ytd_updated_at"]},
        },
    })
    resp.headers["Cache-Control"] = "no-store"  # real-time truth, never cached
    return resp


@app.get("/api/team-stats")
def api_team_stats():
    split = request.args.get("split", "last7")
    if split not in VALID_SPLITS:
        return jsonify({"error": f"split must be one of {list(VALID_SPLITS)}"}), 400
    try:
        return jsonify({"split": split, "teams": fetch_team_stats(split)})
    except Exception as exc:  # noqa: BLE001 - never leak internals to clients
        logger.warning("team-stats query failed: %s", exc)
        return jsonify({"error": "data temporarily unavailable"}), 503


@app.get("/api/betting")
def api_betting():
    try:
        games = enrich_games(fetch_betting(), fetch_stats_by_team("last7"),
                             fetch_stats_by_team("ytd"))
        return jsonify({"games": games})
    except Exception as exc:  # noqa: BLE001
        logger.warning("betting query failed: %s", exc)
        return jsonify({"error": "data temporarily unavailable"}), 503


@app.get("/api/games/<path:game_key>/history")
def api_game_history(game_key: str):
    try:
        history = fetch_line_history(game_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("history query failed: %s", exc)
        return jsonify({"error": "data temporarily unavailable"}), 503
    if history is None:
        return jsonify({"error": "unknown game"}), 404
    return jsonify(history)


# --------------------------------------------------------------------------- #
# HTML dashboard
# --------------------------------------------------------------------------- #











# ESPN CDN slugs for team logos (browser-side hotlink; not a pipeline request).
ESPN_LOGO_SLUGS = {
    "ATL": "atl", "CHI": "chi", "CONN": "conn", "DAL": "dal", "GS": "gs",
    "IND": "ind", "LA": "la", "LAS": "lv", "LVA": "lv", "MIN": "min", "NY": "ny",
    "PHX": "phx", "POR": "por", "SEA": "sea", "TOR": "tor",
    # Washington arrives as WSH from the betting feed and WAS from ESPN; both
    # map to the same mark. Missing WSH left Washington logo-less in production.
    "WAS": "wsh", "WSH": "wsh",
    "GSV": "gs", "CON": "conn", "LV": "lv", "NYL": "ny", "PHO": "phx",
}


def _logo_url(abbr: Any) -> str | None:
    slug = ESPN_LOGO_SLUGS.get(str(abbr or "").upper())
    return f"https://a.espncdn.com/i/teamlogos/wnba/500/{slug}.png" if slug else None


# The rankings feed carries full team names, not the betting feed's
# abbreviations, so logos there resolve by name. Explicit pairs only — a
# guessed slug would render another team's mark.
TEAM_NAME_SLUGS = {
    "atlanta dream": "atl", "chicago sky": "chi", "connecticut sun": "conn",
    "dallas wings": "dal", "golden state valkyries": "gs", "indiana fever": "ind",
    "los angeles sparks": "la", "las vegas aces": "lv", "minnesota lynx": "min",
    "new york liberty": "ny", "phoenix mercury": "phx", "portland fire": "por",
    "seattle storm": "sea", "toronto tempo": "tor", "washington mystics": "wsh",
}


def _logo_for_name(team_name: Any) -> str | None:
    slug = TEAM_NAME_SLUGS.get(str(team_name or "").strip().lower())
    return f"https://a.espncdn.com/i/teamlogos/wnba/500/{slug}.png" if slug else None


def _split_team_name(full_name: Any, nickname: Any) -> tuple[str, str]:
    """(city, nickname) for display: 'Las Vegas Aces' -> ('Las Vegas', 'Aces').

    Both sources usually carry FULL names: team_stats carries them, and the
    betting feed's away_name/home_name are full names too (actionnetwork.py
    prefers ``full_name`` over ``display_name``). When ``nickname`` is a real
    suffix of ``full_name``, strip it off; when the two are the same string
    (the common full==full case) or the suffix doesn't match, fall back to a
    last-word split, then to whichever name exists alone."""
    full = str(full_name or "").strip()
    nick = str(nickname or "").strip()
    if full:
        if nick and full.lower().endswith(nick.lower()) and len(full) > len(nick):
            return full[: -len(nick)].strip(), full[-len(nick):]
        city, _, last = full.rpartition(" ")
        return (city, last) if city else ("", full)
    return "", nick


_NON_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(value: Any) -> str:
    """A game key as a DOM-id fragment: ``2026-08-06:LA@MIN`` -> ``2026-08-06-la-min``."""
    return _NON_SLUG.sub("-", str(value or "").lower()).strip("-")


def _et_time(start_time: Any, game_date: Any) -> str | None:
    """Tip-off as Eastern clock time.

    The whole league schedules in ET, so ET is the one label that never asks a
    reader to convert. Falls back to UTC (explicitly labelled) if the platform
    has no zone database, and to nothing at all when there is no timestamp —
    never an invented time.
    """
    if start_time is None:
        return None
    moment = start_time
    if isinstance(moment, str):
        try:
            moment = _dt.datetime.fromisoformat(moment)
        except ValueError:
            return None
    if not isinstance(moment, _dt.datetime):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        local = moment.astimezone(ZoneInfo("America/New_York"))
        suffix = "ET"
    except Exception:  # noqa: BLE001 - no tzdata: say UTC rather than mislabel
        local = moment.astimezone(_dt.timezone.utc)
        suffix = "UTC"
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'} {suffix}"


def _age_hours(iso: Any) -> float | None:
    """Whole hours since an ISO timestamp, or None if it is unusable."""
    if not iso:
        return None
    try:
        moment = _dt.datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    delta = _dt.datetime.now(_dt.timezone.utc) - moment
    return delta.total_seconds() / 3600.0


# Team statistics refresh daily; past this the dashboard says so out loud
# rather than letting a dated projection read as current.
STATS_STALE_AFTER_HOURS = 24.0

# The odds feed publishes every 30 minutes. Past this the board is not "the
# current market" any more and must say so. Team stats had a threshold, a
# banner and an "older-of-two" stamp while the higher-stakes odds surface had
# none of the three.
BETTING_STALE_AFTER_HOURS = 2.0

# VSIN-derived values (splits, sharp line, RLM) COALESCE-preserve on a miss, so
# they can outlive the market they describe. Past this a card says how old they
# actually are rather than presenting them beside fresh Action Network lines.
VSIN_STALE_AFTER_HOURS = 3.0


def _team_view(game: Mapping[str, Any], side: str,
               stats_row: Mapping[str, Any] | None,
               season_row: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One team's display identity.

    The record comes from the SEASON split, never from last-7: the last-7 row's
    wins/losses count only the games inside that window, so Los Angeles read
    "1-6" on a team that is 11-17 on the season.
    """
    row = stats_row or {}
    abbr = game.get(f"{side}_abbr")
    city, nick = _split_team_name(row.get("team_name"), game.get(f"{side}_name"))
    # Season record or nothing. The last-7 row's wins/losses count only games
    # inside that window; rendering them where a season record belongs is the
    # same wrong claim in different clothes.
    season = season_row or {}
    wins, losses = season.get("wins"), season.get("losses")
    return {
        "abbr": abbr,
        "city": city,
        "nick": nick,
        "full": row.get("team_name") or game.get(f"{side}_name"),
        "short": nick or abbr or "team",
        "record": f"{wins}-{losses}" if wins is not None and losses is not None else None,
        "logo": _logo_url(abbr),
    }


def build_game_views(games: list[dict[str, Any]],
                     stats: Mapping[str, Mapping[str, Any]],
                     season_rows: list[dict[str, Any]] | None = None
                     ) -> list[dict[str, Any]]:
    """Enriched game rows -> everything the game component renders.

    All display derivation lives in ``presentation`` (pure and tested); this
    only joins team stats and assembles. Same id-first / name-fallback join as
    ``enrich_games``: betting rows carry Action Network ids, ``team_stats``
    carries stats.wnba.com ids (see enrich.py).
    """
    by_name = stats_by_team_name(stats)
    season_by_name = {normalize_team_name(r.get("team_name")): r
                      for r in (season_rows or [])}

    def _season_for(row: Mapping[str, Any] | None, fallback_name: Any):
        name = (row or {}).get("team_name") or fallback_name
        return season_by_name.get(normalize_team_name(name))

    views = []
    for game in games:
        away_stats = find_team_stats(
            stats, by_name, game.get("away_team_id"), game.get("away_name"))
        home_stats = find_team_stats(
            stats, by_name, game.get("home_team_id"), game.get("home_name"))
        away = _team_view(game, "away", away_stats,
                          _season_for(away_stats, game.get("away_name")))
        home = _team_view(game, "home", home_stats,
                          _season_for(home_stats, game.get("home_name")))
        away_abbr, home_abbr = away["abbr"], home["abbr"]

        spread_away, spread_home = pres.spread_sides(game.get("current_spread"))
        total_over, total_under = pres.total_sides(game.get("current_total"))
        ml_away, ml_home = pres.moneyline_sides(
            game.get("current_ml_away"), game.get("current_ml_home"))

        movement = {
            "spread": pres.line_movement("spread", game.get("open_spread"),
                                         game.get("current_spread"),
                                         away_abbr, home_abbr),
            "total": pres.line_movement("total", game.get("open_total"),
                                        game.get("current_total"),
                                        away_abbr, home_abbr),
        }
        open_away, _ = pres.spread_sides(game.get("open_spread"))
        sharp_away, _ = pres.spread_sides(game.get("sharp_spread"))
        movement_rows = [
            row for row in (
                {"label": "Open", "value": open_away},
                {"label": "Current", "value": spread_away},
                {"label": "Sharp", "value": sharp_away},
            ) if row["value"]
        ]

        books = " · ".join(
            part for part in (
                f"Public {game['public_book']}" if game.get("public_book") else None,
                f"Sharp {game['sharp_book']}" if game.get("sharp_book") else None,
            ) if part
        )

        status_text = pres.status_label(
            game.get("status"), game.get("start_time"), game.get("fetched_at_utc"))

        vsin_age = _age_hours(game.get("vsin_fetched_at_utc"))
        views.append({
            "key": game.get("game_key"),
            "fetched_age_hours": _age_hours(game.get("fetched_at_utc")),
            "vsin_fetched_at_utc": game.get("vsin_fetched_at_utc"),
            "vsin_stale_hours": (round(vsin_age) if vsin_age is not None
                                 and vsin_age >= VSIN_STALE_AFTER_HOURS else None),
            "slug": _slug(game.get("game_key")),
            "game_date": game.get("game_date"),
            "time_text": _et_time(game.get("start_time"), game.get("game_date")),
            "date_text": str(game.get("game_date") or "") or None,
            "status_text": status_text,
            "is_final": status_text in ("Final", "Postponed", "Canceled"),
            "is_live": status_text == "Live",
            "away": away,
            "home": home,
            "markets": {
                "spread_away": spread_away, "spread_home": spread_home,
                "total_over": total_over, "total_under": total_under,
                "ml_away": ml_away, "ml_home": ml_home,
            },
            "movement": movement,
            "movement_rows": movement_rows,
            "splits": pres.split_blocks(game, away_abbr, home_abbr),
            "model": pres.model_view(game.get("model"), game.get("current_spread"),
                                     game.get("current_total"), away_abbr, home_abbr),
            "signals": pres.describe_signals(game.get("signals") or [], game,
                                             away_abbr, home_abbr),
            "books": books or None,
        })
    return views


def _stats_freshness(db_ok: bool) -> tuple[str, float | None, str]:
    """``(iso, stale_hours, human_time)`` for the team-statistics feed.

    The iso is the OLDER of the two splits, so the page never claims more
    freshness than its stalest visible split. Isolated failure: a broken stamp
    query blanks the stamp, it does not take down a page whose data already
    loaded.
    """
    if not db_ok:
        return "", None, ""
    try:
        counts = fetch_status_counts()
    except Exception as exc:  # noqa: BLE001
        logger.warning("status query failed: %s", exc)
        return "", None, ""
    iso = _older_iso(counts.get("last7_updated_at"), counts.get("ytd_updated_at"))
    age = _age_hours(iso)
    stale = round(age) if age is not None and age >= STATS_STALE_AFTER_HOURS else None
    human = ""
    if iso:
        try:
            human = _dt.datetime.fromisoformat(iso).strftime("%B %-d at %H:%M UTC")
        except ValueError:
            human = iso
    return iso, stale, human


@app.get("/")
def index():
    """The research dashboard (brand law: design-system/off-duty-locks/MASTER.md)."""
    db_ok = True
    games: list[dict[str, Any]] = []
    last7: list[dict[str, Any]] = []
    ytd: list[dict[str, Any]] = []
    stats: dict[str, dict[str, Any]] = {}
    try:
        stats = fetch_stats_by_team("last7")
        season_stats = fetch_stats_by_team("ytd")
        games = enrich_games(fetch_betting(), stats, season_stats)
        last7 = fetch_team_stats("last7")
        ytd = fetch_team_stats("ytd")
    except Exception as exc:  # noqa: BLE001 - render an empty state, not a 500
        logger.warning("dashboard queries failed: %s", exc)
        db_ok = False

    views = build_game_views(games, stats, ytd)
    groups = pres.group_by_date(views, today=pres.slate_today())
    ranked, scale = pres.rankings_view_with_scale(last7, ytd)
    for row in ranked:
        row["logo"] = _logo_for_name(row.get("team_name"))

    # The OLDEST row on the board, not the newest. max() let one refreshed game
    # stamp the whole page: an 8-hour-old row rendered under a 6-minute-old
    # header. This is the same law _older_iso already applies to team stats.
    stamps = [str(g.get("fetched_at_utc")) for g in games if g.get("fetched_at_utc")]
    updated = min(stamps) if stamps else ""
    betting_age = _age_hours(updated)
    betting_stale_hours = (round(betting_age)
                           if betting_age is not None
                           and betting_age >= BETTING_STALE_AFTER_HOURS else None)
    stats_iso, stale_hours, stats_human = _stats_freshness(db_ok)

    return render_template(
        "dashboard.html",
        page="dashboard",
        groups=groups,
        game_count=len(views),
        betting_stale_hours=betting_stale_hours,
        rankings=ranked[:5],
        scale=scale,
        db_ok=db_ok,
        updated_at=updated,
        freshness_label="Markets updated",
        stats_updated_at=stats_iso,
        stats_stale_hours=stale_hours,
        stats_updated_text=stats_human,
        home_court=HOME_COURT_POINTS,
    )


@app.get("/rankings")
def rankings():
    """Offensive power rankings — the comparative team-performance view."""
    split = request.args.get("split", "last7")
    if split not in VALID_SPLITS:
        split = "last7"
    other = "ytd" if split == "last7" else "last7"

    db_ok = True
    primary: list[dict[str, Any]] = []
    comparison: list[dict[str, Any]] = []
    try:
        primary = fetch_team_stats(split)
        comparison = fetch_team_stats(other)
    except Exception as exc:  # noqa: BLE001 - empty state, never a 500
        logger.warning("rankings queries failed: %s", exc)
        db_ok = False

    # Form always reads "this window vs the season", whichever window is shown;
    # on the season view the two are the same split, so no form is derivable.
    ranked, scale = pres.rankings_view_with_scale(
        primary, comparison if split == "last7" else [],
        primary_is_season=(split == "ytd"))
    for row in ranked:
        row["logo"] = _logo_for_name(row.get("team_name"))

    stats_iso, stale_hours, stats_human = _stats_freshness(db_ok)
    return render_template(
        "rankings.html",
        page="rankings",
        split=split,
        split_label="last-7" if split == "last7" else "season",
        split_heading="Last 7 Games" if split == "last7" else "Season",
        rankings=ranked,
        scale=scale,
        db_ok=db_ok,
        updated_at=stats_iso,
        freshness_label="Statistics as of",
        stats_updated_at=stats_iso,
        stats_stale_hours=stale_hours,
        stats_updated_text=stats_human,
    )


@app.get("/tables")
def tables():
    """Legacy stat/betting tables (the pre-dashboard index)."""
    try:
        last7 = fetch_team_stats("last7")
        ytd = fetch_team_stats("ytd")
        betting = fetch_betting()
        db_ok = True
    except Exception as exc:  # noqa: BLE001 - render an empty state, not a 500
        logger.warning("tables query failed: %s", exc)
        last7, ytd, betting, db_ok = [], [], [], False
    # The legacy page renders the same stale rows the other two disclose. A
    # reader who has learned to trust the dashboard's honesty has no reason to
    # suspect this page, which makes silence here worse than it looks.
    _iso, stats_stale_hours, stats_updated_text = _stats_freshness(db_ok)
    return _render_page(last7, ytd, betting, db_ok,
                        stats_stale_hours, stats_updated_text)


def _fmt(value: Any, nd: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int,)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{nd}f}"
    return escape(str(value))


def _team_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class='empty'>No team stats published yet.</p>"
    head = ("Team", "GP", "W", "L", "Win%", "PTS", "FG%", "3P%", "REB", "AST",
            "TOV", "STL", "BLK", "Poss", "OffRtg")
    ths = "".join(f"<th>{escape(h)}</th>" for h in head)
    body = []
    for r in rows:
        cells = [
            f"<td class='team'>{escape(str(r.get('team_name','')))}</td>",
            f"<td>{_fmt(r.get('games_played'))}</td>",
            f"<td>{_fmt(r.get('wins'))}</td>",
            f"<td>{_fmt(r.get('losses'))}</td>",
            f"<td>{_fmt(r.get('win_pct'), 3)}</td>",
            f"<td>{_fmt(r.get('points'))}</td>",
            f"<td>{_fmt(r.get('fg_pct'), 3)}</td>",
            f"<td>{_fmt(r.get('fg3_pct'), 3)}</td>",
            f"<td>{_fmt(r.get('reb'))}</td>",
            f"<td>{_fmt(r.get('ast'))}</td>",
            f"<td>{_fmt(r.get('tov'))}</td>",
            f"<td>{_fmt(r.get('stl'))}</td>",
            f"<td>{_fmt(r.get('blk'))}</td>",
            f"<td>{_fmt(r.get('possessions'), 1)}</td>",
            f"<td class='hi'>{_fmt(r.get('offensive_rating'), 1)}</td>",
        ]
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{ths}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _rlm_badge(flag: Any) -> str:
    if flag is True:
        return "<span class='badge rlm'>RLM</span>"
    return ""


def _betting_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class='empty'>No games on the current slate.</p>"
    head = ("Game", "Open", "Current (DK)", "Sharp (Circa)", "% Bets", "% Money",
            "Total", "Signals")
    ths = "".join(f"<th>{escape(h)}</th>" for h in head)
    body = []
    for r in rows:
        game = f"{escape(str(r.get('away_abbr','')))} @ {escape(str(r.get('home_abbr','')))}"
        signals = _rlm_badge(r.get("spread_rlm")) + _rlm_badge(r.get("total_rlm"))
        cells = [
            f"<td class='team'>{game}<div class='sub'>{escape(str(r.get('game_date','')))}</div></td>",
            f"<td>{_fmt(r.get('open_spread'), 1)}</td>",
            f"<td class='hi'>{_fmt(r.get('current_spread'), 1)}</td>",
            f"<td>{_fmt(r.get('sharp_spread'), 1)}</td>",
            f"<td>{_fmt(r.get('spread_pct_bets_away'))}%</td>",
            f"<td>{_fmt(r.get('spread_pct_money_away'))}%</td>",
            f"<td>{_fmt(r.get('current_total'), 1)}</td>",
            f"<td>{signals or '—'}</td>",
        ]
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{ths}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _render_page(last7, ytd, betting, db_ok: bool,
                 stats_stale_hours: float | None = None,
                 stats_updated_text: str = "") -> str:
    warn = "" if db_ok else "<p class='warn'>Live data is temporarily unavailable.</p>"
    stale = ""
    if stats_stale_hours:
        stale = (f"<p class='stale'><strong>Team statistics are "
                 f"{escape(str(stats_stale_hours))} hours old.</strong> The betting "
                 f"board below is current; the team tables were last published "
                 f"{escape(stats_updated_text)}.</p>")
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Off-Duty Locks — WNBA</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700&family=Inter:wght@400;600&display=swap" rel="stylesheet">
<style>
  /* Brand law: design-system/off-duty-locks/MASTER.md — one accent #FF5C1C,
     graphite surfaces, Barlow Condensed display + Inter tabular data grids. */
  :root {{ color-scheme: dark;
    --odl-bg:#0B0B0D; --odl-panel:#141417; --odl-border:#26262B;
    --odl-text:#E7E7EA; --odl-text-muted:#9CA3AF;
    --odl-accent:#FF5C1C; --odl-signal-warn:#EF4444; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--odl-bg); color:var(--odl-text); font:14px/1.5 Inter,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }}
  header {{ padding:24px 20px; border-bottom:1px solid var(--odl-border); background:var(--odl-panel); }}
  h1 {{ margin:0; font:700 26px/1.1 "Barlow Condensed",Inter,sans-serif; text-transform:uppercase; letter-spacing:.03em; }}
  header .sub {{ color:var(--odl-text-muted); margin-top:4px; }}
  main {{ max-width:1200px; margin:0 auto; padding:20px; }}
  section {{ margin:28px 0; }}
  h2 {{ font:700 18px/1.2 "Barlow Condensed",Inter,sans-serif; text-transform:uppercase; letter-spacing:.03em; color:var(--odl-text); border-left:3px solid var(--odl-accent); padding-left:10px; margin:0 0 12px; }}
  .scroll {{ overflow-x:auto; border:1px solid var(--odl-border); border-radius:8px; background:var(--odl-panel); }}
  table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }}
  th,td {{ padding:8px 10px; text-align:right; white-space:nowrap; border-bottom:1px solid var(--odl-border); }}
  th {{ background:var(--odl-panel); color:var(--odl-text-muted); font:700 12px/1.5 "Barlow Condensed",Inter,sans-serif; text-transform:uppercase; letter-spacing:.04em; position:sticky; top:0; }}
  td.team {{ text-align:left; font-weight:600; }}
  td.team .sub {{ color:var(--odl-text-muted); font-weight:400; font-size:12px; }}
  td.hi {{ color:var(--odl-accent); font-weight:700; }}
  tbody tr:hover {{ background:rgba(255,92,28,0.06); }}
  .badge {{ display:inline-block; padding:1px 6px; border-radius:4px; font-size:11px; font-weight:700; }}
  .badge.rlm {{ background:rgba(255,92,28,0.13); color:var(--odl-accent); border:1px solid rgba(255,92,28,0.33); margin-left:4px; }}
  .empty {{ color:var(--odl-text-muted); font-style:italic; padding:12px; }}
  .warn {{ color:var(--odl-signal-warn); }}
  .stale {{ color:var(--odl-text-muted); border-left:3px solid var(--odl-signal-warn);
    background:#17171B; padding:10px 14px; border-radius:6px; margin-bottom:16px; }}
  .stale strong {{ color:var(--odl-text); }}
  footer {{ color:var(--odl-text-muted); text-align:center; padding:24px; font-size:12px; }}
  .tabs {{ display:flex; gap:8px; margin-bottom:12px; }}
  .tabs button {{ background:var(--odl-panel); color:var(--odl-text-muted); border:1px solid var(--odl-border); border-radius:6px; padding:6px 14px; cursor:pointer; font:inherit; transition:background 140ms ease-out,color 140ms ease-out,border-color 140ms ease-out; }}
  .tabs button.active {{ background:var(--odl-accent); color:#0B0B0D; border-color:var(--odl-accent); font-weight:600; }}
  .pane[hidden] {{ display:none; }}
</style></head>
<body>
<header>
  <h1>Off-Duty Locks</h1>
  <div class="sub">WNBA team statistics &amp; betting markets · 2026 regular season</div>
</header>
<main>
  {warn}
  {stale}
  <section>
    <h2>Betting board</h2>
    <div class="scroll">{_betting_table(betting)}</div>
  </section>
  <section>
    <h2>Team statistics</h2>
    <div class="tabs">
      <button class="active" data-pane="last7" onclick="show('last7')">Last 7 games</button>
      <button data-pane="ytd" onclick="show('ytd')">Year-to-date</button>
    </div>
    <div class="pane" id="pane-last7"><div class="scroll">{_team_table(last7)}</div></div>
    <div class="pane" id="pane-ytd" hidden><div class="scroll">{_team_table(ytd)}</div></div>
  </section>
</main>
<footer>Data via stats.wnba.com, Action Network &amp; VSIN · updated automatically</footer>
<script>
  function show(which) {{
    for (const p of document.querySelectorAll('.pane')) p.hidden = (p.id !== 'pane-'+which);
    for (const b of document.querySelectorAll('.tabs button')) b.classList.toggle('active', b.dataset.pane===which);
  }}
</script>
</body></html>"""
