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
    sharp book, RLM, and the VSIN game id — and nothing Action Network owns."""
    assert set(VSIN_PRESERVE_COLUMNS) == {
        "spread_pct_bets_away", "spread_pct_money_away",
        "total_pct_bets_over", "total_pct_money_over",
        "ml_pct_bets_away", "ml_pct_money_away",
        "sharp_spread", "sharp_total", "sharp_ml_away", "sharp_ml_home",
        "sharp_book", "spread_rlm", "total_rlm", "ml_rlm", "vsin_game_id",
    }
    assert set(VSIN_PRESERVE_COLUMNS) <= set(BETTING_GAMES_COLUMNS)
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
