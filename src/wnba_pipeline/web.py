"""Read-only web app that serves the published data for offdutylocks.com.

A small Flask application that reads the Postgres serving tables (``team_stats``,
``betting_games``) and renders them as an HTML dashboard plus JSON endpoints.

It is strictly **read-only** (SELECT only), holds no secrets beyond
``DATABASE_URL`` (Railway-injected, never rendered), and takes no user input
into SQL except a whitelisted ``split`` value — so it is safe to expose
publicly. Missing data renders as a friendly empty state, and a database
outage returns a clean 503 rather than a stack trace.

Run:
    # production (Railway web service):
    gunicorn wnba_pipeline.web:app -b 0.0.0.0:$PORT --workers 2 --timeout 60
    # local:
    wnba-pipeline serve --port 8080
"""

from __future__ import annotations

import datetime as _dt
import logging
from decimal import Decimal
from html import escape
from typing import Any

from flask import Flask, jsonify, request

from wnba_pipeline import db

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


def fetch_betting() -> list[dict[str, Any]]:
    cols = ", ".join(db.BETTING_GAMES_COLUMNS)
    return _rows(f"SELECT {cols} FROM betting_games ORDER BY game_date, game_key")


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
        return jsonify({"games": fetch_betting()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("betting query failed: %s", exc)
        return jsonify({"error": "data temporarily unavailable"}), 503


# --------------------------------------------------------------------------- #
# HTML dashboard
# --------------------------------------------------------------------------- #

@app.get("/")
def index():
    try:
        last7 = fetch_team_stats("last7")
        ytd = fetch_team_stats("ytd")
        betting = fetch_betting()
        db_ok = True
    except Exception as exc:  # noqa: BLE001 - render an empty state, not a 500
        logger.warning("dashboard query failed: %s", exc)
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
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#0d1117; color:#e6edf3; font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }}
  header {{ padding:24px 20px; border-bottom:1px solid #21262d; background:#0b0f14; }}
  h1 {{ margin:0; font-size:22px; letter-spacing:.2px; }}
  header .sub {{ color:#8b949e; margin-top:4px; }}
  main {{ max-width:1200px; margin:0 auto; padding:20px; }}
  section {{ margin:28px 0; }}
  h2 {{ font-size:16px; color:#c9d1d9; border-left:3px solid #2f81f7; padding-left:10px; margin:0 0 12px; }}
  .scroll {{ overflow-x:auto; border:1px solid #21262d; border-radius:8px; }}
  table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }}
  th,td {{ padding:8px 10px; text-align:right; white-space:nowrap; border-bottom:1px solid #161b22; }}
  th {{ background:#161b22; color:#8b949e; font-weight:600; position:sticky; top:0; }}
  td.team {{ text-align:left; font-weight:600; }}
  td.team .sub {{ color:#6e7681; font-weight:400; font-size:12px; }}
  td.hi {{ color:#2f81f7; font-weight:700; }}
  tbody tr:hover {{ background:#12181f; }}
  .badge {{ display:inline-block; padding:1px 6px; border-radius:4px; font-size:11px; font-weight:700; }}
  .badge.rlm {{ background:#3fb95022; color:#3fb950; border:1px solid #3fb95055; margin-left:4px; }}
  .empty {{ color:#8b949e; font-style:italic; padding:12px; }}
  .warn {{ color:#f0883e; }}
  footer {{ color:#6e7681; text-align:center; padding:24px; font-size:12px; }}
  .tabs {{ display:flex; gap:8px; margin-bottom:12px; }}
  .tabs button {{ background:#161b22; color:#c9d1d9; border:1px solid #21262d; border-radius:6px; padding:6px 14px; cursor:pointer; font:inherit; }}
  .tabs button.active {{ background:#2f81f7; color:#fff; border-color:#2f81f7; }}
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
