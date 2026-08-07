"""Betting serving-layer builders: row projection and upsert SQL (no live DB)."""

from __future__ import annotations

import dataclasses

from wnba_pipeline import db
from wnba_pipeline.betting.contract import BettingGame
from wnba_pipeline.db import (
    BETTING_GAMES_COLUMNS,
    BETTING_GAMES_PK,
    VSIN_PRESERVE_COLUMNS,
    betting_games_rows,
    upsert_sql,
)


def _game(**overrides) -> BettingGame:
    base = {f.name: None for f in dataclasses.fields(BettingGame)}
    base.update(
        game_key="2026-07-22:PHX@LA",
        game_date="2026-07-22",
        current_spread=1.5,
        public_book="DraftKings",
    )
    base.update(overrides)
    return BettingGame(**base)


def test_betting_games_rows_match_columns():
    rows = betting_games_rows([_game()])
    assert list(rows[0].keys()) == list(BETTING_GAMES_COLUMNS)
    assert rows[0]["game_key"] == "2026-07-22:PHX@LA"
    assert rows[0]["current_spread"] == 1.5
    assert rows[0]["sharp_spread"] is None  # missing stays None, never fabricated


def test_betting_games_rows_empty():
    assert betting_games_rows([]) == []


def test_betting_upsert_sql_targets_game_key():
    sql = upsert_sql("betting_games", list(BETTING_GAMES_COLUMNS), BETTING_GAMES_PK)
    assert "INSERT INTO betting_games" in sql
    assert "ON CONFLICT (game_key)" in sql
    assert "current_spread = EXCLUDED.current_spread" in sql
    assert "game_key = EXCLUDED.game_key" not in sql   # PK not in the SET list
    assert "updated_at = now()" in sql


# --------------------------------------------------------------------------- #
# NULL-preserving upsert for the VSIN-derived columns
# --------------------------------------------------------------------------- #

def test_vsin_preserve_columns_are_exactly_the_vsin_derived_set():
    """The COALESCE set is the VSIN-derived columns — splits, sharp lines,
    sharp book, RLM, the VSIN game id, and the VSIN confirmation stamp — and
    nothing Action Network owns."""
    assert set(VSIN_PRESERVE_COLUMNS) == {
        "spread_pct_bets_away", "spread_pct_money_away",
        "total_pct_bets_over", "total_pct_money_over",
        "ml_pct_bets_away", "ml_pct_money_away",
        "sharp_spread", "sharp_total", "sharp_ml_away", "sharp_ml_home",
        "sharp_book", "spread_rlm", "total_rlm", "ml_rlm", "vsin_game_id",
        "vsin_fetched_at_utc",
    }
    assert set(VSIN_PRESERVE_COLUMNS) <= set(BETTING_GAMES_COLUMNS)


def test_the_vsin_stamp_is_preserved_alongside_the_values_it_dates():
    """The stamp must COALESCE exactly like the columns it describes.

    If the stamp were plain EXCLUDED it would NULL on a VSIN miss while the
    preserved values stayed, leaving the surviving splits undateable — the
    opposite of the point.
    """
    preserved = set(VSIN_PRESERVE_COLUMNS)
    assert "vsin_fetched_at_utc" in preserved
    for dated in ("sharp_spread", "sharp_ml_away", "spread_rlm",
                  "spread_pct_bets_away"):
        assert dated in preserved, f"{dated} is dated by the stamp but not preserved with it"
    # Action Network's own stamp stays authoritative (plain EXCLUDED).
    assert "fetched_at_utc" not in preserved
    assert not set(VSIN_PRESERVE_COLUMNS) & set(BETTING_GAMES_PK)


def test_betting_upsert_coalesces_vsin_columns_only():
    """A VSIN miss merges as None; on conflict that NULL must keep the stored
    value (COALESCE) instead of erasing it, while AN-derived columns stay
    plain EXCLUDED so the backbone always overwrites."""
    sql = upsert_sql("betting_games", list(BETTING_GAMES_COLUMNS),
                     BETTING_GAMES_PK, preserve_on_null=VSIN_PRESERVE_COLUMNS)
    for col in VSIN_PRESERVE_COLUMNS:
        assert f"{col} = COALESCE(EXCLUDED.{col}, betting_games.{col})" in sql
        assert f"{col} = EXCLUDED.{col}" not in sql
    for col in ("open_spread", "current_spread", "current_total",
                "current_ml_away", "away_name", "status", "fetched_at_utc"):
        assert f"{col} = EXCLUDED.{col}" in sql
        assert f"COALESCE(EXCLUDED.{col}" not in sql


class _RecordingCursor:
    """Captures every executemany the publisher issues."""

    def __init__(self):
        self.batches: list[tuple[str, list[tuple]]] = []

    def executemany(self, sql, params):
        self.batches.append((sql, list(params)))

    def __enter__(self): return self
    def __exit__(self, *a): return False


class _RecordingConn:
    def __init__(self):
        self.cur = _RecordingCursor()
        self.committed = False
        self.closed = False

    def cursor(self): return self.cur
    def commit(self): self.committed = True
    def close(self): self.closed = True


def test_publisher_uses_the_null_preserving_upsert(monkeypatch):
    """The behavioral guarantee: BettingPublisher must actually send the
    COALESCE form, or the SQL builder's option is dead code."""
    conn = _RecordingConn()
    monkeypatch.setattr(db, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(db, "bootstrap_schema", lambda c: None)

    written = db.BettingPublisher().publish([_game(sharp_spread=None)])

    assert written == 1
    assert conn.committed and conn.closed
    upsert = conn.cur.batches[0][0]
    assert "COALESCE(EXCLUDED.sharp_spread, betting_games.sharp_spread)" in upsert
    assert "current_spread = EXCLUDED.current_spread" in upsert  # AN stays plain


# --------------------------------------------------------------------------- #
# Snapshot storage: the history a future grader will stand on
# --------------------------------------------------------------------------- #

def _snap_game(**over):
    from wnba_pipeline.betting.contract import BettingGame
    base = dict(
        game_key="2026-08-07:LA@MIN", game_date="2026-08-07",
        start_time="2026-08-08T01:00:00+00:00", status="scheduled",
        away_team_id="1338", home_team_id="1339",
        away_abbr="LA", home_abbr="MIN",
        away_name="Los Angeles Sparks", home_name="Minnesota Lynx",
        open_spread=12.5, current_spread=16.5, sharp_spread=17.0,
        spread_pct_bets_away=13, spread_pct_money_away=89,
        spread_line_move=4.0, spread_rlm=False,
        open_total=181.5, current_total=188.5, sharp_total=187.0,
        total_pct_bets_over=45, total_pct_money_over=32,
        total_line_move=7.0, total_rlm=True,
        open_ml_away=750, open_ml_home=-1200,
        current_ml_away=950, current_ml_home=-1650,
        sharp_ml_away=925, sharp_ml_home=None,
        ml_pct_bets_away=13, ml_pct_money_away=89, ml_rlm=False,
        public_book="DraftKings", sharp_book="Circa",
        an_game_id="285973", vsin_game_id="20260807WNBA1",
        fetched_at_utc="2026-08-07T12:00:00+00:00",
        vsin_fetched_at_utc="2026-08-07T12:00:00+00:00",
    )
    base.update(over)
    return BettingGame(**base)


def test_snapshot_skips_a_row_with_no_market_content():
    """11% of stored history was all-null rows, each still asserting a book."""
    from wnba_pipeline.db import snapshot_rows
    empty = _snap_game(current_spread=None, current_total=None,
                  current_ml_away=None, current_ml_home=None)
    assert snapshot_rows([empty]) == []
    assert len(snapshot_rows([_snap_game()])) == 1


def test_snapshot_stops_capturing_at_tipoff():
    """Post-tip captures were observed frozen — five identical rows on one
    game. Excluding them makes the closing line simply the LAST snapshot."""
    from wnba_pipeline.db import snapshot_rows
    post_tip = _snap_game(start_time="2026-08-07T01:00:00+00:00")  # tipped 11h ago
    assert snapshot_rows([post_tip]) == []
    pre_tip = _snap_game()  # tips 13h from the capture stamp
    assert len(snapshot_rows([pre_tip])) == 1


def test_snapshot_keeps_the_row_when_timestamps_are_unparseable():
    from wnba_pipeline.db import snapshot_rows
    weird = _snap_game(start_time="not-a-time")
    assert len(snapshot_rows([weird])) == 1


def test_snapshot_records_the_model_as_of_capture():
    """team_stats is overwrite-in-place, so the model's inputs at capture time
    are unrecoverable later — the output must ride with the snapshot."""
    import json
    from wnba_pipeline.db import snapshot_rows
    enriched = [{
        "model": {"spread": 14.99, "total": 191.68,
                  "edge_spread": 1.51, "edge_total": 3.18, "edge_score": 4.4},
        "signals": [{"market": "total", "type": "rlm", "side": "over"}],
    }]
    row = snapshot_rows([_snap_game()], enriched)[0]
    assert row["model_spread"] == 14.99
    assert row["model_total"] == 191.68
    assert row["edge_score"] == 4.4
    assert json.loads(row["signals"]) == [
        {"market": "total", "type": "rlm", "side": "over"}]


def test_snapshot_model_fields_stay_null_without_enrichment():
    from wnba_pipeline.db import snapshot_rows
    row = snapshot_rows([_snap_game()])[0]
    for field in ("model_spread", "model_total", "edge_score", "signals"):
        assert row[field] is None


def test_snapshot_alignment_survives_a_skipped_row():
    """enriched is aligned by GAME index, so a skipped all-null game must not
    shift a later game's model onto the wrong row."""
    from wnba_pipeline.db import snapshot_rows
    empty = _snap_game(game_key="a", current_spread=None, current_total=None,
                  current_ml_away=None, current_ml_home=None)
    real = _snap_game(game_key="b")
    enriched = [
        {"model": {"spread": 1.0, "total": 2.0, "edge_score": 0.1}, "signals": []},
        {"model": {"spread": 9.0, "total": 8.0, "edge_score": 7.7}, "signals": []},
    ]
    rows = snapshot_rows([empty, real], enriched)
    assert len(rows) == 1
    assert rows[0]["game_key"] == "b"
    assert rows[0]["model_spread"] == 9.0, "model attached from the wrong game"
