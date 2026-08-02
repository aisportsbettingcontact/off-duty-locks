"""Web app: routes, JSON-safe coercion, and empty / DB-outage states.

DB access is monkeypatched, so these run offline with no Postgres.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from wnba_pipeline import web


@pytest.fixture
def client():
    web.app.config.update(TESTING=True)
    return web.app.test_client()


def test_healthz_needs_no_db(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.data == b"ok"


def test_api_team_stats_ok(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_team_stats",
                        lambda split: [{"team_name": "Aces", "offensive_rating": 110.5}])
    r = client.get("/api/team-stats?split=ytd")
    assert r.status_code == 200
    body = r.get_json()
    assert body["split"] == "ytd"
    assert body["teams"][0]["team_name"] == "Aces"


def test_api_team_stats_rejects_bad_split(client):
    assert client.get("/api/team-stats?split=bogus").status_code == 400


def test_api_team_stats_db_error_is_503(client, monkeypatch):
    def boom(split):
        raise RuntimeError("db down")

    monkeypatch.setattr(web, "fetch_team_stats", boom)
    r = client.get("/api/team-stats?split=last7")
    assert r.status_code == 503
    assert "unavailable" in r.get_json()["error"]


def test_api_betting_ok(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_betting",
                        lambda: [{"game_key": "2026-07-22:PHX@LA", "current_spread": 1.5}])
    monkeypatch.setattr(web, "fetch_stats_by_team", lambda split="last7": {})
    r = client.get("/api/betting")
    assert r.status_code == 200
    assert r.get_json()["games"][0]["game_key"] == "2026-07-22:PHX@LA"


def test_api_betting_is_enriched(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_betting", lambda: [{
        "game_key": "2026-08-02:PHX@LAS",
        "away_team_id": "a", "home_team_id": "h",
        "current_spread": 6.0, "current_total": 170.0,
        "spread_pct_bets_away": 72, "spread_pct_money_away": 81,
    }])
    monkeypatch.setattr(web, "fetch_stats_by_team", lambda split="last7": {
        "a": {"offensive_rating": 104.0, "possessions": 80.0},
        "h": {"offensive_rating": 110.0, "possessions": 84.0},
    })
    body = client.get("/api/betting").get_json()
    game = body["games"][0]
    assert game["spread_pct_bets_home"] == 28
    assert game["model"]["edge_spread"] is not None
    assert isinstance(game["signals"], list)


def test_index_renders_data(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_team_stats",
                        lambda split: [{"team_name": "Las Vegas Aces",
                                        "offensive_rating": 110.5, "points": 85.0}])
    monkeypatch.setattr(web, "fetch_betting",
                        lambda: [{"away_abbr": "PHX", "home_abbr": "LA",
                                  "game_date": "2026-07-22", "current_spread": 1.5,
                                  "spread_rlm": True, "total_rlm": None}])
    html = client.get("/").get_data(as_text=True)
    assert "Las Vegas Aces" in html
    assert "PHX @ LA" in html
    assert "RLM" in html  # spread_rlm True -> badge


def test_index_empty_state(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_team_stats", lambda split: [])
    monkeypatch.setattr(web, "fetch_betting", lambda: [])
    html = client.get("/").get_data(as_text=True)
    assert "No team stats published yet." in html
    assert "No games on the current slate." in html


def test_index_db_error_renders_warning_not_500(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(web, "fetch_team_stats", boom)
    monkeypatch.setattr(web, "fetch_betting", boom)
    r = client.get("/")
    assert r.status_code == 200  # friendly empty state, never a 500
    assert "temporarily unavailable" in r.get_data(as_text=True)


def test_jsonable_coercion():
    assert web._jsonable(Decimal("1.5")) == 1.5
    assert web._jsonable(datetime.date(2026, 7, 22)) == "2026-07-22"
    assert web._jsonable(datetime.datetime(2026, 7, 22, 12, 0, 0)) == "2026-07-22T12:00:00"
    assert web._jsonable("x") == "x"
    assert web._jsonable(None) is None
