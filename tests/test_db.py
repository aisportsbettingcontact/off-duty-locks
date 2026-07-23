"""Serving-layer pure builders: row projection and upsert SQL.

These exercise the functions the publisher relies on without psycopg or a live
Postgres instance — the DB I/O is a thin wrapper over them.
"""

from __future__ import annotations

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
