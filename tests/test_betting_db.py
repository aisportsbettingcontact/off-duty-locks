"""Betting serving-layer builders: row projection and upsert SQL (no live DB)."""

from __future__ import annotations

import dataclasses

from wnba_pipeline.betting.contract import BettingGame
from wnba_pipeline.db import (
    BETTING_GAMES_COLUMNS,
    BETTING_GAMES_PK,
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
