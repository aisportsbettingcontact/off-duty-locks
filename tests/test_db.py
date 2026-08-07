"""Serving-layer pure builders: row projection and upsert SQL.

These exercise the functions the publisher relies on without psycopg or a live
Postgres instance — the DB I/O is a thin wrapper over them.
"""

from __future__ import annotations

import pytest

from wnba_pipeline.db import (
    STAT_COLUMNS,
    TEAM_STATS_PK,
    split_label,
    team_stats_rows,
    upsert_sql,
)

from tests._builders import make_snapshot


def test_split_label():
    assert split_label(0) == "ytd"
    assert split_label(7) == "last7"
    assert split_label(15) == "last15"


def test_team_stats_rows_shape_and_content():
    snap = make_snapshot()
    rows = team_stats_rows(snap)
    assert len(rows) == snap.team_count
    row = rows[0]
    for col in (
        "season", "season_type", "per_mode", "split", "team_id", "team_name",
        "extraction_key", "source_checksum", "normalized_checksum", "fetched_at_utc",
    ):
        assert col in row
    assert row["split"] == "last7"  # builder uses last_n_games=7
    for col in STAT_COLUMNS.values():
        assert col in row
    assert "possessions" in row and "offensive_rating" in row


def test_team_stats_rows_missing_stat_stays_none():
    snap = make_snapshot()
    snap.records[0].stats["points"] = None
    rows = team_stats_rows(snap)
    assert rows[0]["points"] is None
    assert rows[0]["offensive_rating"] is None  # never fabricated from None


def test_upsert_sql_targets_pk_and_updates_non_pk():
    sql = upsert_sql("team_stats", ["season", "team_id", "points"], TEAM_STATS_PK)
    assert "INSERT INTO team_stats" in sql
    assert "ON CONFLICT (season, season_type, per_mode, split, team_id)" in sql
    assert "points = EXCLUDED.points" in sql          # non-PK column updated
    assert "season = EXCLUDED.season" not in sql      # PK column not in SET
    assert "updated_at = now()" in sql


def test_snapshot_rows_map_current_values():
    import dataclasses

    from wnba_pipeline import db

    @dataclasses.dataclass
    class Game:
        game_key: str = "2026-08-02:PHX@LAS"
        fetched_at_utc: str = "2026-08-02T18:00:00Z"
        current_spread: float = -6.5
        current_total: float = 165.5
        current_ml_away: int = 220
        current_ml_home: int = -275
        spread_pct_bets_away: int = 72
        spread_pct_money_away: int = 81
        total_pct_bets_over: int = 47
        total_pct_money_over: int = 53
        ml_pct_bets_away: int = 30
        ml_pct_money_away: int = 25
        public_book: str = "draftkings"

    rows = db.snapshot_rows([Game()])
    assert len(rows) == 1
    row = rows[0]
    assert row["game_key"] == "2026-08-02:PHX@LAS"
    assert row["captured_at_utc"] == "2026-08-02T18:00:00Z"
    assert row["spread"] == -6.5
    assert row["total"] == 165.5
    assert row["ml_away"] == 220 and row["ml_home"] == -275
    assert row["public_book"] == "draftkings"


def test_snapshot_insert_sql_is_do_nothing():
    from wnba_pipeline import db

    sql = db.snapshot_insert_sql()
    assert "betting_line_snapshots" in sql
    assert "ON CONFLICT (game_key, captured_at_utc) DO NOTHING" in sql
    assert sql.count("%s") == len(db.SNAPSHOT_COLUMNS)


def test_schema_creates_snapshot_table():
    from pathlib import Path

    schema = (Path(__file__).resolve().parents[1] / "src/wnba_pipeline/schema.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS betting_line_snapshots" in schema


def _fake_psycopg(monkeypatch):
    """Stand-in psycopg module that records connect() kwargs.

    Installed via sys.modules so ``connect()``'s lazy ``import psycopg`` picks
    it up — the real driver is never needed, keeping this file's no-psycopg
    promise intact."""
    import sys
    import types

    calls: dict = {}

    def fake_connect(url, **kwargs):
        calls["url"] = url
        calls.update(kwargs)
        return "conn"

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=fake_connect))
    return calls


def test_connect_passes_default_connect_timeout(monkeypatch):
    # Without a bounded handshake a blackholed DB pins a sync gunicorn worker
    # for the ~2-minute OS default; 5 seconds fails fast instead.
    from wnba_pipeline import db

    calls = _fake_psycopg(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/wnba")
    monkeypatch.delenv("ODL_DB_CONNECT_TIMEOUT", raising=False)
    assert db.connect() == "conn"
    assert calls["url"] == "postgresql://db.example/wnba"
    assert calls["connect_timeout"] == 5


def test_connect_timeout_env_override(monkeypatch):
    from wnba_pipeline import db

    calls = _fake_psycopg(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/wnba")
    monkeypatch.setenv("ODL_DB_CONNECT_TIMEOUT", "30")
    db.connect()
    assert calls["connect_timeout"] == 30  # int, not the raw env string


@pytest.mark.parametrize("junk", ["soon", "", "5.0"])
def test_connect_timeout_malformed_env_falls_back_to_default(monkeypatch, junk):
    """A typo'd ODL_DB_CONNECT_TIMEOUT must not raise ValueError at request
    time — a bad env value would otherwise 500 every DB-backed request. The
    guard falls back to the 5-second default instead."""
    from wnba_pipeline import db

    calls = _fake_psycopg(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/wnba")
    monkeypatch.setenv("ODL_DB_CONNECT_TIMEOUT", junk)
    assert db.connect() == "conn"
    assert calls["connect_timeout"] == 5


def test_schema_alters_never_precede_their_create():
    """Every ALTER TABLE must run after the CREATE TABLE for the same relation.

    REGRESSION: the model-snapshot columns were added as `ALTER TABLE ... ADD
    COLUMN IF NOT EXISTS` placed ABOVE the CREATE for betting_line_snapshots.
    `ADD COLUMN IF NOT EXISTS` guards the column, not the relation, so on a
    fresh database bootstrap_schema raised "relation does not exist" at the
    first ALTER and created nothing — breaking every new environment and DR
    restore. This asserts the invariant structurally, since the suite mocks the
    DB and never executes real DDL.
    """
    import re
    from wnba_pipeline.db import _split_statements, SCHEMA_PATH

    created: set[str] = set()
    for stmt in _split_statements(SCHEMA_PATH.read_text(encoding="utf-8")):
        cm = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", stmt)
        if cm:
            created.add(cm.group(1))
        am = re.search(r"ALTER TABLE (?:IF EXISTS )?(\w+)", stmt)
        if am:
            assert am.group(1) in created, (
                f"ALTER TABLE {am.group(1)} runs before its CREATE — bootstrap "
                "would raise 'relation does not exist' on a fresh database")


def test_bootstrap_runs_the_full_schema_in_order_against_a_fresh_db():
    """Exercise bootstrap_schema against a fake DB that models Postgres's rule:
    ALTER on a not-yet-created table raises. Proves fresh-DB bootstrap survives,
    not just that the statement order looks right."""
    import re
    from wnba_pipeline.db import bootstrap_schema

    tables: set[str] = set()

    class Cur:
        def execute(self, sql, params=()):
            cm = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)
            if cm:
                tables.add(cm.group(1))
                return
            am = re.search(r"ALTER TABLE (IF EXISTS )?(\w+)", sql)
            if am:
                if am.group(2) not in tables and not am.group(1):
                    raise RuntimeError(f'relation "{am.group(2)}" does not exist')
                return
            # CREATE INDEX on a missing table would also raise in Postgres.
            im = re.search(r"CREATE INDEX IF NOT EXISTS \w+\s+ON (\w+)", sql)
            if im and im.group(1) not in tables:
                raise RuntimeError(f'relation "{im.group(1)}" does not exist')

        def __enter__(self): return self
        def __exit__(self, *a): return False

    class Conn:
        def cursor(self): return Cur()
        def commit(self): pass

    bootstrap_schema(Conn())  # must not raise
    assert {"team_stats", "betting_games", "betting_line_snapshots"} <= tables
