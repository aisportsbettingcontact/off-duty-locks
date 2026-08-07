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


def test_tables_obeys_brand_law(client, monkeypatch):
    # MASTER.md: one accent #FF5C1C, no second accent (GitHub blue #2f81f7),
    # RLM shares the accent (never green), Barlow Condensed for display.
    monkeypatch.setattr(web, "fetch_team_stats",
                        lambda split: [{"team_name": "Las Vegas Aces",
                                        "offensive_rating": 110.5}])
    monkeypatch.setattr(web, "fetch_betting",
                        lambda: [{"away_abbr": "PHX", "home_abbr": "LA",
                                  "game_date": "2026-07-22",
                                  "spread_rlm": True, "total_rlm": None}])
    html = client.get("/tables").get_data(as_text=True)
    assert "#FF5C1C" in html
    assert "2f81f7" not in html.lower()   # the old second accent
    assert "3fb950" not in html.lower()   # the old green RLM badge
    assert "Barlow Condensed" in html


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
    # The model surface is named, and the signal vocabulary is explained on the
    # page rather than left to a colour key.
    assert "Model v0" in html
    assert "How to read this page" in html
    for signal in ("Sharp Money", "Public Heavy", "Reverse Line Move",
                   "Conflict", "Model Edge"):
        assert signal in html, f"{signal} is never explained to the reader"


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
    # City on the muted line, nickname carrying identity.
    assert '<span class="team__city">Las Vegas</span>' in html
    assert '<span class="team__nick">Aces</span>' in html
    assert '<span class="team__city">Phoenix</span>' in html
    assert '<span class="team__nick">Mercury</span>' in html
    assert "phx.png" in html and "lv.png" in html  # logos still keyed by abbr
    assert "-7.0" in html          # away spread stays signed
    assert "+7.0" in html          # and the home side is its exact negation
    assert "+169.0" not in html    # totals are plain, never signed
    assert "O 169.0" in html and "U 169.0" in html   # each side is labelled
    assert "+110.0" not in html    # ratings are plain, never signed


def test_dashboard_splits_names_when_id_namespaces_differ(client, monkeypatch):
    # PRODUCTION regression: betting rows carry Action Network team ids while
    # team_stats carries stats.wnba.com ids, so the id join never matches.
    # The name join must still find the stats row (city/nickname split) and
    # feed Model v0.
    monkeypatch.setattr(web, "fetch_betting", lambda: [{
        "game_key": "2026-08-02:PHX@LVA", "game_date": "2026-08-02",
        "away_abbr": "PHX", "home_abbr": "LVA",
        "away_name": "Phoenix Mercury", "home_name": "Las Vegas Aces",
        "away_team_id": "1340", "home_team_id": "1341",
        "current_spread": -6.5, "current_total": 169.0,
    }])
    monkeypatch.setattr(web, "fetch_stats_by_team", lambda split="last7": {
        "1611661317": {"offensive_rating": 104.0, "possessions": 80.0,
                       "wins": 11, "losses": 12, "team_name": "Phoenix Mercury",
                       "points": 80.9},
        "1611661319": {"offensive_rating": 110.0, "possessions": 84.0,
                       "wins": 18, "losses": 4, "team_name": "Las Vegas Aces",
                       "points": 90.1},
    })
    monkeypatch.setattr(web, "fetch_team_stats", lambda split: [])
    html = client.get("/").data.decode()
    assert '<span class="team__city">Las Vegas</span>' in html
    assert '<span class="team__nick">Aces</span>' in html
    assert '<span class="team__city">Phoenix</span>' in html
    assert '<span class="team__nick">Mercury</span>' in html
    body = client.get("/api/betting").get_json()
    assert body["games"][0]["model"] is not None


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
    # A friendly, specific outage state — never a 500, never a stack trace.
    assert "Live data is unavailable" in r.data.decode()


# Exact header set the after_request hook must emit. The frame-ancestors-only
# CSP is the legacy /tables page's constraint: that page still builds its HTML
# with an inline <style> and an inline <script>. The dashboard and rankings
# pages carry no inline script or style at all (asserted below), so a stricter
# script-src policy becomes available the moment /tables is retired.
SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=15552000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def test_security_headers_on_every_response(client, monkeypatch):
    _empty_dashboard(monkeypatch)
    for path in ("/", "/healthz"):
        r = client.get(path)
        for name, value in SECURITY_HEADERS.items():
            assert r.headers.get(name) == value, f"{path}: {name!r} wrong or missing"


def test_jsonable_coercion():
    assert web._jsonable(Decimal("1.5")) == 1.5
    assert web._jsonable(datetime.date(2026, 7, 22)) == "2026-07-22"
    assert web._jsonable(datetime.datetime(2026, 7, 22, 12, 0, 0)) == "2026-07-22T12:00:00"
    assert web._jsonable("x") == "x"
    assert web._jsonable(None) is None


# --------------------------------------------------------------------------- #
# Research interface: game object, market matrix, rankings page
# --------------------------------------------------------------------------- #

GAME_ROW = {
    "game_key": "2026-08-07:LA@MIN", "game_date": "2026-08-07",
    "start_time": "2026-08-08T01:00:00+00:00", "status": "scheduled",
    "away_abbr": "LA", "home_abbr": "MIN",
    "away_name": "Los Angeles Sparks", "home_name": "Minnesota Lynx",
    "away_team_id": "1338", "home_team_id": "1339",
    "open_spread": 12.5, "current_spread": 16.5, "sharp_spread": 17.0,
    "open_total": 181.5, "current_total": 188.5,
    "current_ml_away": 950, "current_ml_home": -1650,
    "spread_pct_bets_away": 13, "spread_pct_money_away": 89,
    "public_book": "DraftKings", "sharp_book": "Circa",
    "fetched_at_utc": "2026-08-07T09:01:50+00:00",
}

LAST7 = [
    {"team_name": "Los Angeles Sparks", "offensive_rating": 106.7,
     "possessions": 85.7, "points": 91.4, "wins": 1, "losses": 6},
    {"team_name": "Minnesota Lynx", "offensive_rating": 121.6,
     "possessions": 82.3, "points": 100.0, "wins": 7, "losses": 0},
]
YTD = [
    {"team_name": "Los Angeles Sparks", "offensive_rating": 105.3,
     "possessions": 85.1, "points": 89.6, "wins": 11, "losses": 17},
    {"team_name": "Minnesota Lynx", "offensive_rating": 113.0,
     "possessions": 82.0, "points": 92.7, "wins": 25, "losses": 6},
]


def _slate(monkeypatch, games=None, last7=None, ytd=None):
    monkeypatch.setattr(web, "fetch_betting", lambda: list(games if games is not None else [GAME_ROW]))
    rows = {"last7": last7 if last7 is not None else LAST7,
            "ytd": ytd if ytd is not None else YTD}
    monkeypatch.setattr(web, "fetch_team_stats", lambda split: list(rows[split]))
    # Production's team_stats ids are stats.wnba.com ids that never match the
    # betting feed's Action Network ids, so the name join is what fires.
    monkeypatch.setattr(web, "fetch_stats_by_team",
                        lambda split="last7": {f"s{i}": r for i, r in enumerate(rows[split])})
    monkeypatch.setattr(web, "fetch_status_counts", lambda: {
        "betting_rows": 1, "betting_fetched_at_utc": "2026-08-07T09:01:50+00:00",
        "last7_rows": 2, "last7_updated_at": "2026-08-07T08:00:00+00:00",
        "ytd_rows": 2, "ytd_updated_at": "2026-08-07T08:00:00+00:00"})


def test_every_market_value_sits_on_its_own_team_row(client, monkeypatch):
    """LAW 2: the reader never translates an away-side number onto a team."""
    _slate(monkeypatch)
    html = client.get("/").data.decode()
    away_row = html.split('game__row--away')[1].split('game__row--home')[0]
    home_row = html.split('game__row--home')[1].split('</tbody>')[0]
    assert "+16.5" in away_row and "O 188.5" in away_row and "+950" in away_row
    assert "-16.5" in home_row and "U 188.5" in home_row and "-1650" in home_row


def test_market_columns_are_labelled_not_positional(client, monkeypatch):
    _slate(monkeypatch)
    html = client.get("/").data.decode()
    for label in ("Spread", "Total", "Moneyline"):
        assert f'class="label">{label}</th>' in html


def test_team_record_is_the_season_record_not_the_last7_window(client, monkeypatch):
    # The last-7 split says Los Angeles is 1-6; the season says 11-17. Only one
    # of those is what a reader means by "record".
    _slate(monkeypatch)
    html = client.get("/").data.decode()
    assert "11-17" in html and "25-6" in html
    assert ">1-6<" not in html


def _flat(html: str) -> str:
    """Rendered text with runs of whitespace collapsed, so an assertion about
    copy is not defeated by where the template happens to wrap a line."""
    import re
    return re.sub(r"\s+", " ", html)


def test_model_is_translated_into_basketball_language(client, monkeypatch):
    _slate(monkeypatch)
    html = _flat(client.get("/").data.decode())
    assert "Projected spread" in html and "Market spread" in html
    assert "MIN by " in html                 # a named favourite, not "+15.0"
    assert "Model leans" in html
    assert "not a trained machine-learning model" in html
    assert "no defensive term" in html       # the model's real limitation


def test_line_movement_is_stated_not_left_as_arithmetic(client, monkeypatch):
    _slate(monkeypatch)
    html = client.get("/").data.decode()
    assert "Spread moved" in html
    assert "4.0 toward MIN" in html          # 12.5 -> 16.5 on the away line
    assert "7.0 toward Over" in html         # 181.5 -> 188.5


def test_signals_are_named_and_explained_not_bare_dots(client, monkeypatch):
    _slate(monkeypatch)
    html = client.get("/").data.decode()
    assert "Sharp Money" in html
    assert "89% of money on LA vs 13% of tickets." in html


def test_slate_is_grouped_by_day_with_relative_labels(client, monkeypatch):
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).date()
    rows = [dict(GAME_ROW, game_key="a", game_date=today.isoformat()),
            dict(GAME_ROW, game_key="b",
                 game_date=(today + dt.timedelta(days=1)).isoformat())]
    _slate(monkeypatch, games=rows)
    html = client.get("/").data.decode()
    assert ">Today<" in html and ">Tomorrow<" in html


def test_missing_market_renders_an_em_dash_never_a_zero(client, monkeypatch):
    bare = {k: v for k, v in GAME_ROW.items()
            if k not in ("current_total", "current_ml_away", "current_ml_home")}
    _slate(monkeypatch, games=[bare])
    html = client.get("/").data.decode()
    import re
    empty_cells = re.findall(
        r'<span class="market market--empty".*?</span>\s*</span>', html, re.S)
    assert empty_cells, "a missing market did not render the empty cell"
    assert "not available" in html
    # A missing market must never become a number — no digits at all inside the
    # cell, so a fabricated 0, 0.0 or +0 cannot slip through.
    for cell in empty_cells:
        assert not re.search(r"\d", cell), f"empty market cell shows a number: {cell}"
        assert "—" in cell


def test_dashboard_and_rankings_ship_no_inline_script_or_style(client, monkeypatch):
    """The stricter-CSP precondition, asserted rather than assumed."""
    import re
    _slate(monkeypatch)
    for path in ("/", "/rankings"):
        html = client.get(path).data.decode()
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), f"{path} has inline script"
        assert "<style" not in html, f"{path} has an inline style block"


def test_rankings_page_renders_both_splits(client, monkeypatch):
    _slate(monkeypatch)
    last7 = client.get("/rankings").data.decode()
    assert "Offensive Power Rankings" in last7
    assert "Minnesota Lynx" in last7
    assert "vs season" in last7                 # form derivation is shown
    season = client.get("/rankings?split=ytd").data.decode()
    assert ">Season<" in season
    # Season vs itself is not a form comparison, so none is claimed.
    assert "vs season" not in season


def test_rankings_bar_scale_is_disclosed_not_silently_truncated(client, monkeypatch):
    _slate(monkeypatch)
    html = client.get("/rankings").data.decode()
    assert "not from zero" in html
    assert "League-wide recent form" in html    # the calibration baseline


def test_rankings_rejects_an_unknown_split_without_erroring(client, monkeypatch):
    _slate(monkeypatch)
    r = client.get("/rankings?split=../etc/passwd")
    assert r.status_code == 200
    assert "Last 7 Games" in r.data.decode()    # falls back, never 500s


def test_rankings_db_error_still_200(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(web, "fetch_team_stats", boom)
    monkeypatch.setattr(web, "fetch_status_counts", boom)
    r = client.get("/rankings")
    assert r.status_code == 200
    assert "Live data is unavailable" in r.data.decode()


def test_stale_team_stats_are_declared_on_both_pages(client, monkeypatch):
    _slate(monkeypatch)
    monkeypatch.setattr(web, "fetch_status_counts", lambda: {
        "betting_rows": 1, "betting_fetched_at_utc": "2026-08-07T09:01:50+00:00",
        "last7_rows": 2, "last7_updated_at": "2026-08-04T11:23:56+00:00",
        "ytd_rows": 2, "ytd_updated_at": "2026-08-04T11:24:15+00:00"})
    assert "hours old" in client.get("/").data.decode()
    assert "hours old" in client.get("/rankings").data.decode()


def test_fresh_team_stats_raise_no_staleness_notice(client, monkeypatch):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    _slate(monkeypatch)
    monkeypatch.setattr(web, "fetch_status_counts", lambda: {
        "betting_rows": 1, "betting_fetched_at_utc": now,
        "last7_rows": 2, "last7_updated_at": now,
        "ytd_rows": 2, "ytd_updated_at": now})
    assert "hours old" not in client.get("/").data.decode()


def test_washington_resolves_a_logo_from_either_abbreviation(client, monkeypatch):
    # PRODUCTION regression: the betting feed says WSH, the map only had WAS,
    # so Washington rendered with no logo.
    assert web._logo_url("WSH") == web._logo_url("WAS")
    assert "wsh.png" in web._logo_url("WSH")


def test_game_ids_are_dom_safe_and_deep_linkable(client, monkeypatch):
    _slate(monkeypatch)
    html = client.get("/").data.decode()
    assert 'id="game-2026-08-07-la-min"' in html
    assert 'id="analysis-2026-08-07-la-min"' in html
    assert 'aria-controls="analysis-2026-08-07-la-min"' in html


def test_page_stamp_is_the_oldest_row_not_the_newest(client, monkeypatch):
    """PRODUCTION DEFECT: max() let one refreshed game stamp the whole board.

    Live at 11:46Z the header read 11:30:52Z while three cards carried their own
    fetched_at_utc of 03:31:58Z — an 8-hour-old row under a 6-minute-old stamp.
    """
    old, new = "2026-08-07T03:31:58+00:00", "2026-08-07T12:01:51+00:00"
    _slate(monkeypatch, games=[dict(GAME_ROW, game_key="a", fetched_at_utc=old),
                               dict(GAME_ROW, game_key="b", fetched_at_utc=new)])
    html = client.get("/").data.decode()
    assert f'id="updated" data-iso="{old}"' in html
    assert new not in html.split('id="updated"')[1][:120]


def test_a_stale_board_says_so(client, monkeypatch):
    import datetime as dt
    stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).isoformat()
    _slate(monkeypatch, games=[dict(GAME_ROW, fetched_at_utc=stale)])
    html = client.get("/").data.decode()
    assert "has not refreshed for" in html


def test_a_fresh_board_raises_no_notice(client, monkeypatch):
    import datetime as dt
    fresh = dt.datetime.now(dt.timezone.utc).isoformat()
    _slate(monkeypatch, games=[dict(GAME_ROW, fetched_at_utc=fresh)])
    assert "has not refreshed for" not in client.get("/").data.decode()


def test_preserved_vsin_values_carry_their_own_age(client, monkeypatch):
    """A sharp price and an RLM badge must not read as current when VSIN last
    confirmed them hours ago — the columns COALESCE-preserve, so nothing else
    on the card reveals it."""
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    _slate(monkeypatch, games=[dict(
        GAME_ROW,
        fetched_at_utc=now.isoformat(),
        vsin_fetched_at_utc=(now - dt.timedelta(hours=9)).isoformat())])
    html = client.get("/").data.decode()
    assert "9h old" in html


def test_team_record_is_omitted_rather_than_taken_from_the_wrong_window(client, monkeypatch):
    """With no season split there is no season record, so none is claimed."""
    _slate(monkeypatch, ytd=[])
    html = client.get("/").data.decode()
    assert "team__record" not in html
    assert ">1-6<" not in html and ">7-0<" not in html   # the last-7 window


def test_tables_declares_stale_statistics_like_the_other_pages(client, monkeypatch):
    """/tables is one click from every page and rendered 72h-old rows silently
    while / and /rankings both disclosed them."""
    _slate(monkeypatch)
    monkeypatch.setattr(web, "fetch_status_counts", lambda: {
        "betting_rows": 1, "betting_fetched_at_utc": "2026-08-07T09:01:50+00:00",
        "last7_rows": 2, "last7_updated_at": "2026-08-04T11:23:56+00:00",
        "ytd_rows": 2, "ytd_updated_at": "2026-08-04T11:24:15+00:00"})
    html = client.get("/tables").data.decode()
    assert "hours old" in html
    for path in ("/", "/rankings", "/tables"):
        assert "hours old" in client.get(path).data.decode(), f"{path} hides staleness"


def test_betting_select_survives_a_database_without_the_new_column(client, monkeypatch):
    """Deploy-order safety.

    `vsin_fetched_at_utc` is created by bootstrap_schema, which only the WRITE
    path runs. Between a web deploy and the next publish the read path would
    otherwise name a column the database does not have, and the whole board
    would render its outage state.
    """
    calls = []

    def fake_rows(sql, params=()):
        calls.append(sql)
        if "vsin_fetched_at_utc" in sql:
            raise RuntimeError('column "vsin_fetched_at_utc" does not exist')
        return [{"game_key": "k", "game_date": "2026-08-07"}]

    monkeypatch.setattr(web, "_rows", fake_rows)
    rows = web.fetch_betting()
    assert rows and rows[0]["game_key"] == "k"
    assert len(calls) == 2, "expected one failed attempt then one legacy retry"
    assert "vsin_fetched_at_utc" not in calls[1]


def test_betting_select_does_not_mask_a_real_database_failure(client, monkeypatch):
    """The retry is scoped to the optional column, not a blanket except."""
    def always_boom(sql, params=()):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(web, "_rows", always_boom)
    monkeypatch.setattr(web, "_OPTIONAL_BETTING_COLUMNS", ())
    with pytest.raises(RuntimeError, match="connection refused"):
        web.fetch_betting()
