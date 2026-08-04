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
from decimal import Decimal
from html import escape
from typing import Any

from flask import Flask, jsonify, render_template, request

from wnba_pipeline import db
from wnba_pipeline.enrich import enrich_games
from wnba_pipeline.model import HOME_COURT_POINTS

logger = logging.getLogger("wnba_pipeline.web")

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

VALID_SPLITS = ("last7", "ytd")

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


def fetch_betting() -> list[dict[str, Any]]:
    """The current slate: games from the last ``BETTING_LOOKBACK_DAYS`` onward."""
    cols = ", ".join(db.BETTING_GAMES_COLUMNS)
    return _rows(
        f"SELECT {cols} FROM betting_games "
        "WHERE game_date >= CURRENT_DATE - %s::integer "
        "ORDER BY game_date, game_key",
        (BETTING_LOOKBACK_DAYS,),
    )


def fetch_stats_by_team(split: str = "last7") -> dict[str, dict[str, Any]]:
    """team_id -> stats row, for Model v0 inputs and W-L records."""
    return {str(r["team_id"]): r for r in _rows(
        "SELECT team_id, team_name, wins, losses, possessions, offensive_rating, points "
        "FROM team_stats WHERE split = %s", (split,),
    )}


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


# --------------------------------------------------------------------------- #
# JSON API
# --------------------------------------------------------------------------- #

@app.get("/healthz")
def healthz():
    return "ok", 200


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
        games = enrich_games(fetch_betting(), fetch_stats_by_team("last7"))
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

def _dfmt(value: Any) -> str:
    """Plain dashboard number: one decimal for floats, em-dash for missing.

    Tolerates Jinja Undefined (a partial row never crashes the page)."""
    if isinstance(value, bool):
        return "—"
    if isinstance(value, float):
        return f"{value:.1f}"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    return "—"


def _dsigned(value: Any) -> str:
    """Signed line value (spreads, edges): +/- one decimal."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{value:+.1f}"


def _dpct(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{value}%"


def _dml(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"+{value}" if value > 0 else str(value)


def _ring_class(score: float) -> str:
    if score >= 7.5:
        return "hot"
    if score >= 5.0:
        return "warm"
    return "cool"


# ESPN CDN slugs for team logos (browser-side hotlink; not a pipeline request).
ESPN_LOGO_SLUGS = {
    "ATL": "atl", "CHI": "chi", "CONN": "conn", "DAL": "dal", "GS": "gs",
    "IND": "ind", "LA": "la", "LAS": "lv", "LVA": "lv", "MIN": "min", "NY": "ny",
    "PHX": "phx", "POR": "por", "SEA": "sea", "TOR": "tor", "WAS": "wsh",
}


def _logo_url(abbr: Any) -> str | None:
    slug = ESPN_LOGO_SLUGS.get(str(abbr or "").upper())
    return f"https://a.espncdn.com/i/teamlogos/wnba/500/{slug}.png" if slug else None


def _split_team_name(full_name: Any, nickname: Any) -> tuple[str, str]:
    """(city, nickname) for display: 'Las Vegas Aces' -> ('Las Vegas', 'Aces').

    The betting feed carries nicknames; team_stats carries full names. Prefer
    stripping the known nickname off the full name; fall back to a last-word
    split, then to whichever name exists alone."""
    full = str(full_name or "").strip()
    nick = str(nickname or "").strip()
    if full:
        if nick and full.lower().endswith(nick.lower()) and len(full) > len(nick):
            return full[: -len(nick)].strip(), full[-len(nick):]
        city, _, last = full.rpartition(" ")
        return (city, last) if city else ("", full)
    return "", nick


@app.get("/")
def index():
    """The research dashboard (design spec 2026-08-02; brand law MASTER.md)."""
    db_ok = True
    games: list[dict[str, Any]] = []
    rankings: list[dict[str, Any]] = []
    stats: dict[str, dict[str, Any]] = {}
    try:
        stats = fetch_stats_by_team("last7")
        games = enrich_games(fetch_betting(), stats)
        rankings = fetch_team_stats("last7")
    except Exception as exc:  # noqa: BLE001 - render an empty state, not a 500
        logger.warning("dashboard queries failed: %s", exc)
        db_ok = False
    for g in games:
        for side in ("away", "home"):
            row = stats.get(str(g.get(f"{side}_team_id"))) or {}
            city, nick = _split_team_name(row.get("team_name"), g.get(f"{side}_name"))
            g[f"{side}_city"], g[f"{side}_nick"] = city, nick
    updated = max((str(g.get("fetched_at_utc") or "") for g in games), default="")
    return render_template(
        "dashboard.html",
        games=games, rankings=rankings, db_ok=db_ok,
        updated_at=updated, home_court=HOME_COURT_POINTS,
        fmt=_dfmt, sfmt=_dsigned, pct=_dpct, ml=_dml, ring_class=_ring_class, logo=_logo_url,
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
    return _render_page(last7, ytd, betting, db_ok)


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


def _render_page(last7, ytd, betting, db_ok: bool) -> str:
    warn = "" if db_ok else "<p class='warn'>Live data is temporarily unavailable.</p>"
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
