"""Real-time freshness: /api/status endpoint + the dashboard's two stamps.

Owner requirement: pinpoint precision and real-time status. The endpoint is
the single cheap source of truth the page polls; the stamps must carry exact
DB timestamps (no rounding, no vague "recently") and must never overstate
freshness — when the team-stats splits diverge, the OLDER one wins.

DB access is monkeypatched, so these run offline with no Postgres.
"""

from __future__ import annotations

import datetime

import pytest

from wnba_pipeline import web


@pytest.fixture
def client():
    web.app.config.update(TESTING=True)
    return web.app.test_client()


def _status_row(**over):
    row = {
        "betting_rows": 4,
        "betting_fetched_at_utc": "2026-08-04T14:30:12+00:00",
        "last7_rows": 13,
        "last7_updated_at": "2026-08-04T10:31:07+00:00",
        "ytd_rows": 13,
        "ytd_updated_at": "2026-08-04T10:32:44+00:00",
    }
    row.update(over)
    return row


# --------------------------------------------------------------------------- #
# /api/status
# --------------------------------------------------------------------------- #

def test_api_status_shape_and_exact_timestamps(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_status_counts", lambda: _status_row())
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.get_json()
    assert body["db_ok"] is True
    # Exact DB values, no rounding.
    assert body["betting"] == {"rows": 4, "fetched_at_utc": "2026-08-04T14:30:12+00:00"}
    assert body["team_stats"]["last7"] == {
        "rows": 13, "updated_at": "2026-08-04T10:31:07+00:00"}
    assert body["team_stats"]["ytd"] == {
        "rows": 13, "updated_at": "2026-08-04T10:32:44+00:00"}
    now = datetime.datetime.fromisoformat(body["now"])
    assert now.tzinfo is not None  # timezone-aware server UTC clock


def test_api_status_is_never_cached(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_status_counts", lambda: _status_row())
    assert client.get("/api/status").headers.get("Cache-Control") == "no-store"


def test_api_status_empty_tables_report_null_not_fake_time(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_status_counts", lambda: _status_row(
        betting_rows=0, betting_fetched_at_utc=None,
        last7_rows=0, last7_updated_at=None,
        ytd_rows=0, ytd_updated_at=None))
    body = client.get("/api/status").get_json()
    assert body["betting"] == {"rows": 0, "fetched_at_utc": None}
    assert body["team_stats"]["last7"] == {"rows": 0, "updated_at": None}
    assert body["team_stats"]["ytd"] == {"rows": 0, "updated_at": None}


def test_api_status_db_error_degrades_to_wellformed_503(client, monkeypatch):
    def boom():
        raise RuntimeError("db down: secret-host:5432")

    monkeypatch.setattr(web, "fetch_status_counts", boom)
    r = client.get("/api/status")
    assert r.status_code == 503
    body = r.get_json()
    assert body["db_ok"] is False
    assert body["betting"] is None
    assert body["team_stats"] is None
    assert "now" in body
    assert r.headers.get("Cache-Control") == "no-store"
    # Never a stack trace or internals in the response.
    assert "secret-host" not in r.get_data(as_text=True)
    assert "RuntimeError" not in r.get_data(as_text=True)


def test_fetch_status_counts_is_one_select_round_trip(monkeypatch):
    calls: list[str] = []

    def fake_rows(sql, params=()):
        calls.append(sql)
        return [_status_row()]

    monkeypatch.setattr(web, "_rows", fake_rows)
    row = web.fetch_status_counts()
    assert row["betting_rows"] == 4
    assert len(calls) == 1  # one round trip, not one per table
    assert calls[0].lstrip().upper().startswith("SELECT")  # SELECT-only


# --------------------------------------------------------------------------- #
# Dashboard stamps (index() -> dashboard.html)
# --------------------------------------------------------------------------- #

def _dashboard_data(monkeypatch, status_row):
    monkeypatch.setattr(web, "fetch_betting", lambda: [{
        "game_key": "2026-08-04:PHX@LVA", "game_date": "2026-08-04",
        "away_abbr": "PHX", "home_abbr": "LVA",
        "away_name": "Phoenix Mercury", "home_name": "Las Vegas Aces",
        "away_team_id": "a", "home_team_id": "h",
        "current_spread": -6.5, "current_total": 169.0,
        "fetched_at_utc": "2026-08-04T14:30:12+00:00",
    }])
    monkeypatch.setattr(web, "fetch_stats_by_team", lambda split="last7": {})
    monkeypatch.setattr(web, "fetch_team_stats", lambda split: [])
    monkeypatch.setattr(web, "fetch_status_counts", lambda: status_row)


def test_dashboard_passes_both_stamps(client, monkeypatch):
    _dashboard_data(monkeypatch, _status_row())
    html = client.get("/").data.decode()
    # Slate stamp: unchanged role — max betting fetched_at_utc over the slate.
    assert 'id="updated"' in html
    assert 'data-iso="2026-08-04T14:30:12+00:00"' in html
    # Stats stamp: driven by team_stats.updated_at; last7 is older here.
    assert 'id="stats-updated"' in html
    assert 'data-iso="2026-08-04T10:31:07+00:00"' in html


def test_stats_stamp_shows_older_split_when_diverged(client, monkeypatch):
    # ytd went stale a day behind last7: never overstate — the OLDER one wins.
    _dashboard_data(monkeypatch, _status_row(
        last7_updated_at="2026-08-04T10:31:07+00:00",
        ytd_updated_at="2026-08-03T10:31:07+00:00"))
    html = client.get("/").data.decode()
    assert 'data-iso="2026-08-03T10:31:07+00:00"' in html
    assert 'data-iso="2026-08-04T10:31:07+00:00"' not in html


def test_stats_stamp_empty_when_no_team_stats(client, monkeypatch):
    _dashboard_data(monkeypatch, _status_row(
        last7_rows=0, last7_updated_at=None, ytd_rows=0, ytd_updated_at=None))
    html = client.get("/").data.decode()
    assert 'id="stats-updated" data-iso=""' in html


def test_stats_stamp_survives_status_query_failure(client, monkeypatch):
    # Only the stamp query fails: the page still renders its data with an
    # empty stamp — no false "unavailable" banner, no fabricated freshness.
    _dashboard_data(monkeypatch, _status_row())

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(web, "fetch_status_counts", boom)
    r = client.get("/")
    html = r.data.decode()
    assert r.status_code == 200
    assert "Mercury" in html
    assert 'id="stats-updated" data-iso=""' in html
    assert "temporarily unavailable" not in html


def test_older_iso_picks_older_and_tolerates_missing():
    older = web._older_iso
    assert older("2026-08-04T10:00:00+00:00",
                 "2026-08-03T10:00:00+00:00") == "2026-08-03T10:00:00+00:00"
    assert older("2026-08-03T10:00:00+00:00",
                 "2026-08-04T10:00:00+00:00") == "2026-08-03T10:00:00+00:00"
    # One split absent: the present one is the only honest claim left.
    assert older(None, "2026-08-04T10:00:00+00:00") == "2026-08-04T10:00:00+00:00"
    assert older("2026-08-04T10:00:00+00:00", None) == "2026-08-04T10:00:00+00:00"
    assert older(None, None) == ""
