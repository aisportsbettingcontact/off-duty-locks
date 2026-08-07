"""Presentation-layer derivations: sides, movement, model translation, signals.

Every function here is pure and null-safe. The law under test throughout is
docs law + spec §37: a value may be DERIVED only when the derivation is
mathematically deterministic (home spread = -away spread, complement = 100-x,
movement = current - open, form = last7 - ytd). Anything unknown stays None and
renders as an em-dash — never a fabricated zero, never a guessed side.
"""

from __future__ import annotations

import pytest

from wnba_pipeline import presentation as p


# --------------------------------------------------------------------------- #
# spread sides — the sign convention that everything else depends on
# --------------------------------------------------------------------------- #

def test_spread_sides_mirror_around_zero():
    # betting_games.current_spread is the AWAY side. LA +16.5 at MIN -16.5.
    away, home = p.spread_sides(16.5)
    assert away == "+16.5"
    assert home == "-16.5"


def test_spread_sides_when_away_is_favored():
    away, home = p.spread_sides(-3.5)
    assert away == "-3.5"
    assert home == "+3.5"


def test_spread_sides_pick_em_has_no_false_sign():
    assert p.spread_sides(0.0) == ("PK", "PK")


def test_spread_sides_missing_stays_missing():
    assert p.spread_sides(None) == (None, None)


def test_moneyline_sides_format_american_odds():
    assert p.moneyline_sides(950, -1650) == ("+950", "-1650")
    assert p.moneyline_sides(-102, -118) == ("-102", "-118")


def test_moneyline_sides_null_safe_per_side():
    assert p.moneyline_sides(None, -118) == (None, "-118")


def test_total_sides_label_over_and_under():
    assert p.total_sides(188.5) == ("O 188.5", "U 188.5")
    assert p.total_sides(None) == (None, None)


# --------------------------------------------------------------------------- #
# line movement — direction must name a real team, never a bare sign
# --------------------------------------------------------------------------- #

def test_spread_movement_up_is_toward_home():
    # away line 12.5 -> 16.5: the away dog is getting more points, so the
    # market moved toward the home team. Same convention as signals._rlm_side.
    move = p.line_movement("spread", 12.5, 16.5, "LA", "MIN")
    assert move["delta"] == pytest.approx(4.0)
    assert move["toward"] == "MIN"
    assert move["text"] == "4.0 toward MIN"


def test_spread_movement_down_is_toward_away():
    move = p.line_movement("spread", -3.5, -4.5, "ATL", "WSH")
    assert move["delta"] == pytest.approx(-1.0)
    assert move["toward"] == "ATL"
    assert move["text"] == "1.0 toward ATL"


def test_total_movement_up_is_toward_over():
    move = p.line_movement("total", 181.5, 188.5, "LA", "MIN")
    assert move["toward"] == "Over"
    assert move["text"] == "7.0 toward Over"


def test_no_movement_is_reported_as_unchanged_not_omitted():
    move = p.line_movement("spread", 1.5, 1.5, "GS", "DAL")
    assert move["delta"] == 0
    assert move["text"] == "Unchanged"


def test_movement_needs_both_endpoints():
    assert p.line_movement("spread", None, 16.5, "LA", "MIN") is None
    assert p.line_movement("spread", 12.5, None, "LA", "MIN") is None


# --------------------------------------------------------------------------- #
# model v0 — translated out of away-side sign convention into basketball
# --------------------------------------------------------------------------- #

def _model(spread, total, edge_spread, edge_total, edge_score):
    return {"spread": spread, "total": total, "edge_spread": edge_spread,
            "edge_total": edge_total, "edge_score": edge_score}


def test_model_view_names_the_favorite_rather_than_a_signed_number():
    # model spread is away-side: +15.0 means the HOME team is favored by 15.
    view = p.model_view(_model(14.99, 191.68, 1.51, 3.18, 6.2),
                        16.5, 188.5, "LA", "MIN")
    assert view["projected_spread"]["text"] == "MIN by 15.0"
    assert view["market_spread"]["text"] == "MIN by 16.5"


def test_model_view_names_away_favorite_when_model_spread_negative():
    view = p.model_view(_model(-6.38, 174.5, -0.12, -2.75, 2.99),
                        -6.5, 174.5, "PHX", "CONN")
    assert view["projected_spread"]["text"] == "PHX by 6.4"


def test_model_view_reports_spread_disagreement_toward_a_team():
    view = p.model_view(_model(14.99, 191.68, 1.51, 3.18, 6.2),
                        16.5, 188.5, "LA", "MIN")
    # edge_spread > 0 -> the model likes the AWAY side of the spread.
    assert view["spread_lean"] == "LA"
    assert view["spread_diff_text"] == "1.5 pts"


def test_model_view_reports_total_lean_in_words():
    view = p.model_view(_model(14.99, 191.68, 1.51, 3.18, 6.2),
                        16.5, 188.5, "LA", "MIN")
    assert view["total_lean"] == "Over"
    assert view["total_diff_text"] == "3.2 pts"


def test_model_view_total_lean_under_when_edge_negative():
    view = p.model_view(_model(11.6, 172.6, -9.1, -14.9, 10.0),
                        2.5, 187.5, "TOR", "POR")
    assert view["total_lean"] == "Under"


def test_model_view_band_matches_brand_law_ring_thresholds():
    # MASTER.md: green >= 7.5 - yellow 5.0-7.4 - orange < 5.0
    assert p.model_view(_model(1, 2, 1, 1, 7.5), 1, 2, "A", "B")["band"] == "hot"
    assert p.model_view(_model(1, 2, 1, 1, 5.0), 1, 2, "A", "B")["band"] == "warm"
    assert p.model_view(_model(1, 2, 1, 1, 4.9), 1, 2, "A", "B")["band"] == "cool"


def test_model_view_flags_a_capped_score_so_the_ui_can_say_so():
    # edge_score saturates at 10.0 by design; the UI must be able to mark it
    # rather than imply 10.0 is a measured maximum.
    assert p.model_view(_model(1, 2, 9, 9, 10.0), 1, 2, "A", "B")["capped"] is True
    assert p.model_view(_model(1, 2, 1, 1, 6.2), 1, 2, "A", "B")["capped"] is False


def test_model_view_absent_when_model_is_none():
    assert p.model_view(None, 16.5, 188.5, "LA", "MIN") is None


def test_model_view_survives_missing_market_values():
    view = p.model_view(_model(14.99, 191.68, None, None, None),
                        None, None, "LA", "MIN")
    assert view["market_spread"] is None
    assert view["spread_lean"] is None
    assert view["edge_score"] is None


# --------------------------------------------------------------------------- #
# signals — self-explanatory labels, ordered rarest-first
# --------------------------------------------------------------------------- #

def test_signal_gets_a_human_label_and_a_named_side():
    out = p.describe_signals(
        [{"market": "spread", "type": "sharp-money", "side": "away"}],
        {"spread_pct_bets_away": 13, "spread_pct_money_away": 89},
        "LA", "MIN")
    assert out[0]["label"] == "Sharp Money"
    assert out[0]["side_label"] == "LA"
    assert out[0]["market_label"] == "Spread"


def test_sharp_money_detail_quotes_the_two_percentages():
    out = p.describe_signals(
        [{"market": "spread", "type": "sharp-money", "side": "away"}],
        {"spread_pct_bets_away": 13, "spread_pct_money_away": 89},
        "LA", "MIN")
    assert out[0]["detail"] == "89% of money on LA vs 13% of tickets."


def test_total_side_labels_are_over_under_not_team_names():
    out = p.describe_signals(
        [{"market": "total", "type": "rlm", "side": "over"}], {}, "LA", "MIN")
    assert out[0]["side_label"] == "Over"


def test_conflict_has_no_side_and_still_explains_itself():
    out = p.describe_signals(
        [{"market": "spread", "type": "conflict", "side": None}], {}, "LA", "MIN")
    assert out[0]["side_label"] is None
    assert "opposite" in out[0]["detail"]


def test_signals_order_rarest_first_so_model_edge_never_buries_a_conflict():
    raw = [
        {"market": "spread", "type": "model-edge", "side": "away"},
        {"market": "total", "type": "public-heavy", "side": "over"},
        {"market": "spread", "type": "conflict", "side": None},
        {"market": "spread", "type": "rlm", "side": "home"},
        {"market": "moneyline", "type": "sharp-money", "side": "away"},
    ]
    types = [s["type"] for s in p.describe_signals(raw, {}, "LA", "MIN")]
    assert types == ["conflict", "rlm", "sharp-money", "public-heavy", "model-edge"]


def test_no_signals_yields_an_empty_list_not_a_placeholder_signal():
    assert p.describe_signals([], {}, "LA", "MIN") == []


# --------------------------------------------------------------------------- #
# betting splits — both sides, labelled, never a fabricated 50/50
# --------------------------------------------------------------------------- #

def test_split_block_labels_both_sides_with_team_names():
    blocks = p.split_blocks(
        {"spread_pct_bets_away": 13, "spread_pct_bets_home": 87,
         "spread_pct_money_away": 89, "spread_pct_money_home": 11},
        "LA", "MIN")
    spread = next(b for b in blocks if b["market"] == "spread")
    assert spread["tickets"][0] == {"label": "LA", "pct": 13}
    assert spread["tickets"][1] == {"label": "MIN", "pct": 87}
    assert spread["money"][0] == {"label": "LA", "pct": 89}


def test_split_block_reports_divergence_between_money_and_tickets():
    blocks = p.split_blocks(
        {"spread_pct_bets_away": 13, "spread_pct_bets_home": 87,
         "spread_pct_money_away": 89, "spread_pct_money_home": 11},
        "LA", "MIN")
    spread = next(b for b in blocks if b["market"] == "spread")
    assert spread["divergence"] == 76


def test_split_block_absent_when_the_market_has_no_data():
    blocks = p.split_blocks({"spread_pct_bets_away": None,
                             "spread_pct_money_away": None}, "LA", "MIN")
    assert [b["market"] for b in blocks if b["tickets"] or b["money"]] == []


def test_split_block_never_invents_the_missing_half():
    blocks = p.split_blocks({"spread_pct_bets_away": 40,
                             "spread_pct_bets_home": None}, "LA", "MIN")
    spread = next(b for b in blocks if b["market"] == "spread")
    assert spread["tickets"][1]["pct"] is None


# --------------------------------------------------------------------------- #
# rankings — form difference is a real derivation from two stored splits
# --------------------------------------------------------------------------- #

def test_rankings_join_last7_to_season_and_derive_form():
    rows = p.rankings_view(
        [{"team_name": "Indiana Fever", "offensive_rating": 123.7,
          "possessions": 84.6, "points": 104.7, "wins": 5, "losses": 2}],
        [{"team_name": "Indiana Fever", "offensive_rating": 110.4,
          "possessions": 82.0, "points": 90.1, "wins": 19, "losses": 11}])
    assert rows[0]["rank"] == 1
    assert rows[0]["form_delta"] == pytest.approx(13.3, abs=0.05)
    assert rows[0]["form_text"] == "+13.3 vs season"


def test_rankings_form_is_none_when_the_other_split_is_absent():
    rows = p.rankings_view(
        [{"team_name": "Toronto Tempo", "offensive_rating": 98.0}], [])
    assert rows[0]["form_delta"] is None
    assert rows[0]["form_text"] is None


def test_rankings_bar_scale_is_shared_and_labelled_not_zero_truncated():
    rows = p.rankings_view(
        [{"team_name": "A", "offensive_rating": 120.0},
         {"team_name": "B", "offensive_rating": 100.0},
         {"team_name": "C", "offensive_rating": 110.0}], [])
    # The widest bar is the max, the narrowest is the min, and the scale the
    # UI prints is the real domain — no invisible truncation.
    assert rows[0]["bar_pct"] == 100
    assert rows[1]["bar_pct"] == pytest.approx(50, abs=1)   # 110 is midway
    assert rows[2]["bar_pct"] == 0


def test_rankings_bar_is_neutral_when_every_rating_is_equal():
    rows = p.rankings_view(
        [{"team_name": "A", "offensive_rating": 100.0},
         {"team_name": "B", "offensive_rating": 100.0}], [])
    assert rows[0]["bar_pct"] == 100 and rows[1]["bar_pct"] == 100


def test_rankings_skip_bar_for_a_team_with_no_rating():
    rows = p.rankings_view(
        [{"team_name": "A", "offensive_rating": 120.0},
         {"team_name": "B", "offensive_rating": None}], [])
    assert rows[1]["bar_pct"] is None


def test_rankings_scale_domain_is_exposed_for_the_axis_caption():
    rows, scale = p.rankings_view_with_scale(
        [{"team_name": "A", "offensive_rating": 120.0},
         {"team_name": "B", "offensive_rating": 100.0}], [])
    assert scale["min"] == 100.0
    assert scale["max"] == 120.0
    assert len(rows) == 2


def test_rankings_publish_league_average_form_so_a_delta_can_be_calibrated():
    # Recent form moves league-wide: on real data 13 of 15 teams were positive.
    # Without the baseline, "+6.0 vs season" reads as team improvement when the
    # whole league is +4.0.
    _, scale = p.rankings_view_with_scale(
        [{"team_name": "A", "offensive_rating": 110.0},
         {"team_name": "B", "offensive_rating": 106.0}],
        [{"team_name": "A", "offensive_rating": 104.0},
         {"team_name": "B", "offensive_rating": 104.0}])
    assert scale["form_league_avg"] == pytest.approx(4.0)


def test_league_average_form_is_none_when_no_team_has_both_splits():
    _, scale = p.rankings_view_with_scale(
        [{"team_name": "A", "offensive_rating": 110.0}], [])
    assert scale["form_league_avg"] is None


# --------------------------------------------------------------------------- #
# date grouping / time
# --------------------------------------------------------------------------- #

def test_games_group_by_date_in_order():
    groups = p.group_by_date([
        {"game_key": "b", "game_date": "2026-08-08"},
        {"game_key": "a", "game_date": "2026-08-07"},
        {"game_key": "c", "game_date": "2026-08-08"},
    ], today="2026-08-07")
    assert [g["date"] for g in groups] == ["2026-08-07", "2026-08-08"]
    assert groups[0]["label"] == "Today"
    assert groups[1]["label"] == "Tomorrow"
    assert len(groups[1]["games"]) == 2


def test_group_label_falls_back_to_a_weekday_for_further_dates():
    groups = p.group_by_date([{"game_key": "x", "game_date": "2026-08-12"}],
                             today="2026-08-07")
    assert groups[0]["label"] == "Wednesday"
    assert groups[0]["long_label"] == "August 12, 2026"


def test_past_dates_are_labelled_final_not_today():
    groups = p.group_by_date([{"game_key": "x", "game_date": "2026-08-06"}],
                             today="2026-08-07")
    assert groups[0]["label"] == "Yesterday"


def test_group_by_date_tolerates_a_missing_date():
    groups = p.group_by_date([{"game_key": "x", "game_date": None}],
                             today="2026-08-07")
    assert groups[0]["date"] is None
    assert groups[0]["label"] == "Scheduled"


def test_rankings_record_is_the_season_record_not_the_window_record():
    # PRODUCTION regression: the last-7 row's wins/losses count only the games
    # inside that 7-game window. Rendering "5-2" beside a team that is 19-11 on
    # the season reads as a season record and is wrong.
    rows = p.rankings_view(
        [{"team_name": "Indiana Fever", "offensive_rating": 123.7,
          "wins": 5, "losses": 2}],
        [{"team_name": "Indiana Fever", "offensive_rating": 113.5,
          "wins": 19, "losses": 11}])
    assert rows[0]["record"] == "19-11"


def test_season_rows_use_their_own_record_when_told_they_are_the_season():
    rows = p.rankings_view(
        [{"team_name": "Indiana Fever", "offensive_rating": 113.5,
          "wins": 19, "losses": 11}], [], primary_is_season=True)
    assert rows[0]["record"] == "19-11"


def test_a_recent_window_alone_claims_no_season_record():
    """An absent comparison must not license the window's own record.

    Inferring "this must be the season split because no other was supplied" is
    exactly how a 7-game 5-2 came to render where a 19-11 belonged.
    """
    rows = p.rankings_view(
        [{"team_name": "Indiana Fever", "offensive_rating": 123.7,
          "wins": 5, "losses": 2}], [])
    assert rows[0]["record"] is None


# --------------------------------------------------------------------------- #
# slate timezone — the reference date must be the league's, not the server's
# --------------------------------------------------------------------------- #

def test_slate_today_resolves_in_league_time_not_utc():
    """PRODUCTION REGRESSION (shipped 2026-08-07, live ~4h/night).

    ``game_date`` is the ET slate date. Resolving "today" in UTC rolls the
    reference forward at 20:00 ET — inside the tip-off window — so a slate that
    had not started yet rendered under "Yesterday" every night from 8pm to
    midnight ET.
    """
    import datetime as dt
    # 00:30 UTC on Aug 8 IS 20:30 ET on Aug 7.
    instant = dt.datetime(2026, 8, 8, 0, 30, tzinfo=dt.timezone.utc)
    assert p.slate_today(instant) == dt.date(2026, 8, 7)
    assert instant.date() == dt.date(2026, 8, 8)   # what the old code used


# 00:00-03:59 UTC is 20:00-23:59 the PREVIOUS day in ET (EDT, UTC-4) — the
# whole tip-off window, and exactly the span the old UTC pivot got wrong.
@pytest.mark.parametrize("utc_hour", [0, 1, 2, 3])
def test_evening_et_slate_is_today_not_yesterday(utc_hour):
    import datetime as dt
    instant = dt.datetime(2026, 8, 8, utc_hour, 30, tzinfo=dt.timezone.utc)
    groups = p.group_by_date([{"game_key": "g", "game_date": "2026-08-07"}],
                             today=p.slate_today(instant))
    assert groups[0]["label"] == "Today"


def test_the_day_flips_at_midnight_et_not_midnight_utc():
    import datetime as dt
    just_before = dt.datetime(2026, 8, 8, 3, 59, tzinfo=dt.timezone.utc)   # 23:59 ET Aug 7
    just_after = dt.datetime(2026, 8, 8, 4, 1, tzinfo=dt.timezone.utc)     # 00:01 ET Aug 8
    assert p.slate_today(just_before) == dt.date(2026, 8, 7)
    assert p.slate_today(just_after) == dt.date(2026, 8, 8)


def test_slate_today_still_correct_during_the_afternoon():
    import datetime as dt
    instant = dt.datetime(2026, 8, 7, 16, 30, tzinfo=dt.timezone.utc)  # 12:30pm ET
    assert p.slate_today(instant) == dt.date(2026, 8, 7)


# --------------------------------------------------------------------------- #
# status — "Live" is a present-tense claim and must be checked against now
# --------------------------------------------------------------------------- #

import datetime as _d

NOW = _d.datetime(2026, 8, 7, 12, 0, tzinfo=_d.timezone.utc)


def test_recent_tipoff_still_reads_live():
    assert p.status_label("inprogress", start_time=NOW - _d.timedelta(minutes=40),
                          fetched_at=NOW - _d.timedelta(minutes=5), now=NOW) == "Live"


def test_stale_inprogress_row_never_claims_live():
    """PRODUCTION REGRESSION (shipped 2026-08-07).

    2026-08-06:TOR@POR tipped at 02:00Z and still rendered a green "Live" badge
    at 12:01Z — ten hours later. The feed had stopped updating that game.
    """
    label = p.status_label("inprogress",
                           start_time=_d.datetime(2026, 8, 7, 2, 0, tzinfo=_d.timezone.utc),
                           fetched_at=_d.datetime(2026, 8, 7, 3, 31, tzinfo=_d.timezone.utc),
                           now=_d.datetime(2026, 8, 7, 12, 1, tzinfo=_d.timezone.utc))
    assert label == p.STATUS_STALE_LABEL
    assert label != "Live"


def test_live_claim_needs_a_fresh_row_even_within_the_game_window():
    assert p.status_label("inprogress", start_time=NOW - _d.timedelta(minutes=30),
                          fetched_at=NOW - _d.timedelta(hours=3), now=NOW) == p.STATUS_STALE_LABEL


def test_unverifiable_live_claim_is_not_made():
    assert p.status_label("inprogress", None, None, now=NOW) == p.STATUS_STALE_LABEL


def test_terminal_and_pregame_statuses_map_directly():
    assert p.status_label("complete") == "Final"
    assert p.status_label("postponed") == "Postponed"
    assert p.status_label("scheduled") is None
    assert p.status_label(None) is None
    assert p.status_label("") is None


def test_unknown_status_token_says_nothing_rather_than_echoing_the_feed():
    assert p.status_label("weird_new_token") is None
