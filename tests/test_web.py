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


def test_history_endpoint_ok(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_line_history", lambda key: {
        "game_key": key,
        "opening": {"spread": -6.5, "total": 168.5, "ml_away": 220, "ml_home": -275},
        "snapshots": [
            {"captured_at_utc": "2026-08-02T12:00:00+00:00", "spread": -6.5},
            {"captured_at_utc": "2026-08-02T15:00:00+00:00", "spread": -7.0},
        ],
    })
    body = client.get("/api/games/2026-08-02:PHX@LAS/history").get_json()
    assert body["opening"]["spread"] == -6.5
    assert len(body["snapshots"]) == 2


def test_history_unknown_game_404(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_line_history", lambda key: None)
    assert client.get("/api/games/nope/history").status_code == 404


def test_history_db_error_503(client, monkeypatch):
    def boom(key):
        raise RuntimeError("db down")
    monkeypatch.setattr(web, "fetch_line_history", boom)
    assert client.get("/api/games/x/history").status_code == 503


def test_tables_renders_data(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_team_stats",
                        lambda split: [{"team_name": "Las Vegas Aces",
                                        "offensive_rating": 110.5, "points": 85.0}])
    monkeypatch.setattr(web, "fetch_betting",
                        lambda: [{"away_abbr": "PHX", "home_abbr": "LA",
                                  "game_date": "2026-07-22", "current_spread": 1.5,
                                  "spread_rlm": True, "total_rlm": None}])
    html = client.get("/tables").get_data(as_text=True)
    assert "Las Vegas Aces" in html
    assert "PHX @ LA" in html
    assert "RLM" in html  # spread_rlm True -> badge


def test_tables_empty_state(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_team_stats", lambda split: [])
    monkeypatch.setattr(web, "fetch_betting", lambda: [])
    html = client.get("/tables").get_data(as_text=True)
    assert "No team stats published yet." in html
    assert "No games on the current slate." in html


def test_tables_db_error_renders_warning_not_500(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(web, "fetch_team_stats", boom)
    monkeypatch.setattr(web, "fetch_betting", boom)
    r = client.get("/tables")
    assert r.status_code == 200  # friendly empty state, never a 500
    assert "temporarily unavailable" in r.get_data(as_text=True)


def _empty_dashboard(monkeypatch):
    monkeypatch.setattr(web, "fetch_betting", lambda: [])
    monkeypatch.setattr(web, "fetch_stats_by_team", lambda split="last7": {})
    monkeypatch.setattr(web, "fetch_team_stats", lambda split: [])


def test_dashboard_renders_with_brand_copy(client, monkeypatch):
    _empty_dashboard(monkeypatch)
    r = client.get("/")
    html = r.data.decode()
    assert r.status_code == 200
    assert "OFF DUTY" in html and "LOCKS" in html
    assert "MODEL v0" in html
    assert "Signal Legend" in html


def test_dashboard_renders_games_and_rankings(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_betting", lambda: [{
        "game_key": "2026-08-02:PHX@LAS", "game_date": "2026-08-02",
        "away_abbr": "PHX", "home_abbr": "LAS",
        "away_name": "Mercury", "home_name": "Aces",
        "away_team_id": "a", "home_team_id": "h",
        "open_spread": -6.5, "current_spread": -7.0, "sharp_spread": -7.5,
        "spread_pct_bets_away": 72, "spread_pct_money_away": 81,
        "open_total": 168.5, "current_total": 169.0, "sharp_total": 170.5,
        "total_pct_bets_over": 47, "total_pct_money_over": 53,
        "current_ml_away": 220, "current_ml_home": -275,
        "spread_rlm": True, "spread_line_move": -0.5,
    }])
    monkeypatch.setattr(web, "fetch_stats_by_team", lambda split="last7": {
        "a": {"offensive_rating": 104.0, "possessions": 80.0,
              "wins": 11, "losses": 12, "team_name": "Phoenix Mercury", "points": 80.9},
        "h": {"offensive_rating": 110.0, "possessions": 84.0,
              "wins": 18, "losses": 4, "team_name": "Las Vegas Aces", "points": 90.1},
    })
    monkeypatch.setattr(web, "fetch_team_stats", lambda split: [
        {"team_name": "Las Vegas Aces", "offensive_rating": 110.0, "possessions": 84.0,
         "points": 90.1, "wins": 18, "losses": 4},
    ])
    html = client.get("/").data.decode()
    assert "Offensive Power Rankings" in html
    # City muted, nickname bolded, abbreviations gone from the cell.
    assert '<span class="tcity">Las Vegas</span> <span class="tnick">Aces</span>' in html
    assert '<span class="tcity">Phoenix</span> <span class="tnick">Mercury</span>' in html
    assert 'class="abbr"' not in html
    assert "phx.png" in html and "lv.png" in html  # logos still keyed by abbr
    assert "-7.0" in html          # spread stays signed
    assert "+169.0" not in html    # totals are plain, never signed
    assert "169.0" in html
    assert "+110.0" not in html    # ratings are plain, never signed


def test_dashboard_partial_game_row_never_crashes(client, monkeypatch):
    # A row missing most columns must render em-dashes, not raise.
    monkeypatch.setattr(web, "fetch_betting", lambda: [{
        "game_key": "k", "game_date": "2026-08-02",
        "away_abbr": "PHX", "home_abbr": "LVA",
        "away_name": "Mercury", "home_name": "Aces",
        "away_team_id": "a", "home_team_id": "h",
        "current_spread": -6.5,
    }])
    monkeypatch.setattr(web, "fetch_stats_by_team", lambda split="last7": {})
    monkeypatch.setattr(web, "fetch_team_stats", lambda split: [])
    r = client.get("/")
    assert r.status_code == 200
    html = r.data.decode()
    assert "phx.png" in html and "lv.png" in html
    assert "a.espncdn.com/i/teamlogos/wnba/500/phx.png" in html
    assert "a.espncdn.com/i/teamlogos/wnba/500/lv.png" in html
    assert "Mercury" in html
    assert "data-iso=" in html


def test_dashboard_db_error_still_200(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(web, "fetch_betting", boom)
    monkeypatch.setattr(web, "fetch_stats_by_team", boom)
    monkeypatch.setattr(web, "fetch_team_stats", boom)
    r = client.get("/")
    assert r.status_code == 200
    assert "temporarily unavailable" in r.data.decode()


def test_jsonable_coercion():
    assert web._jsonable(Decimal("1.5")) == 1.5
    assert web._jsonable(datetime.date(2026, 7, 22)) == "2026-07-22"
    assert web._jsonable(datetime.datetime(2026, 7, 22, 12, 0, 0)) == "2026-07-22T12:00:00"
    assert web._jsonable("x") == "x"
    assert web._jsonable(None) is None
