"""PostgreSQL serving layer.

The file-based :class:`~wnba_pipeline.storage.Store` stays the source of truth
and audit trail. This module publishes the *accepted, validated* snapshot to
Postgres (the read model the site consumes), adding the two derived metrics as
plain columns so the site never recomputes them.

``psycopg`` is imported lazily inside the functions that talk to the database,
so importing this module — and unit-testing its pure row/SQL builders — needs
neither the driver nor a live database. The publisher never fabricates data: an
absent stat is written as SQL ``NULL``, never ``0``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from wnba_pipeline import contract
from wnba_pipeline.contract import Snapshot
from wnba_pipeline.derived import derived_metrics

logger = logging.getLogger("wnba_pipeline.db")

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Canonical stat field -> team_stats column. Only the fields the product
# requires; other preserved fields (plus_minus, etc.) stay in the file snapshot.
STAT_COLUMNS: dict[str, str] = {
    "games_played": "games_played",
    "wins": "wins",
    "losses": "losses",
    "win_pct": "win_pct",
    "minutes": "minutes",
    "points": "points",
    "field_goals_made": "fgm",
    "field_goals_attempted": "fga",
    "field_goal_pct": "fg_pct",
    "three_pointers_made": "fg3m",
    "three_pointers_attempted": "fg3a",
    "three_point_pct": "fg3_pct",
    "free_throws_made": "ftm",
    "free_throws_attempted": "fta",
    "free_throw_pct": "ft_pct",
    "offensive_rebounds": "oreb",
    "defensive_rebounds": "dreb",
    "total_rebounds": "reb",
    "assists": "ast",
    "turnovers": "tov",
    "steals": "stl",
    "blocks": "blk",
    "personal_fouls": "pf",
}

TEAM_STATS_PK: tuple[str, ...] = ("season", "season_type", "per_mode", "split", "team_id")


def split_label(last_n_games: int) -> str:
    """DB split label for a LastNGames window. 0 = full season = 'ytd'."""
    return "ytd" if last_n_games == 0 else f"last{last_n_games}"


# ---------------------------------------------------------------------------
# Pure builders (no I/O — unit-tested without a database)
# ---------------------------------------------------------------------------

def team_stats_rows(snapshot: Snapshot) -> list[dict[str, Any]]:
    """Snapshot -> one column dict per team for the ``team_stats`` upsert.

    Missing stats stay ``None`` (SQL NULL). Derived metrics are appended.
    """
    split = split_label(snapshot.last_n_games)
    normalized_checksum = snapshot.normalized_checksum()
    rows: list[dict[str, Any]] = []
    for record in snapshot.records:
        stats = record.stats
        row: dict[str, Any] = {
            "season": snapshot.season,
            "season_type": snapshot.season_type,
            "per_mode": snapshot.per_mode,
            "split": split,
            "team_id": record.team_id,
            "team_name": record.team_name,
        }
        for stat_field, column in STAT_COLUMNS.items():
            row[column] = stats.get(stat_field)
        row.update(derived_metrics(stats))
        row["extraction_key"] = snapshot.extraction_key
        row["source_checksum"] = snapshot.source_checksum
        row["normalized_checksum"] = normalized_checksum
        row["fetched_at_utc"] = snapshot.fetched_at_utc
        rows.append(row)
    return rows


def upsert_sql(table: str, columns: list[str], pk: tuple[str, ...]) -> str:
    """Parameterized ``INSERT ... ON CONFLICT (pk) DO UPDATE`` statement.

    ``updated_at`` is always refreshed to ``now()`` on conflict.
    """
    col_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    updates = [f"{c} = EXCLUDED.{c}" for c in columns if c not in pk]
    updates.append("updated_at = now()")
    return (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})\n"
        f"ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {', '.join(updates)}"
    )


def _split_statements(sql: str) -> list[str]:
    """Split a DDL script into individual statements.

    psycopg's extended protocol runs one statement per ``execute``; the schema
    is plain DDL with no string literals containing ``--`` or ``;``, so
    stripping line comments and splitting on ``;`` is safe here.
    """
    stripped = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [s.strip() for s in stripped.split(";") if s.strip()]


# ---------------------------------------------------------------------------
# Database I/O (psycopg imported lazily)
# ---------------------------------------------------------------------------

def _resolve_url(database_url: str | None) -> str:
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise contract.ConfigError(
            "DATABASE_URL is not set — no database to publish to "
            "(pass --database-url or set the env var)"
        )
    return url


def connect(database_url: str | None = None):
    """Open a psycopg connection to ``DATABASE_URL``. Caller closes it."""
    import psycopg  # lazy: driver only needed for live DB work

    return psycopg.connect(_resolve_url(database_url))


def bootstrap_schema(conn) -> None:
    """Create tables/indexes if absent. Idempotent."""
    with conn.cursor() as cur:
        for statement in _split_statements(SCHEMA_PATH.read_text(encoding="utf-8")):
            cur.execute(statement)
    conn.commit()


def init_db(database_url: str | None = None) -> None:
    """Bootstrap the schema against ``DATABASE_URL`` (CLI ``db-init``)."""
    conn = connect(database_url)
    try:
        bootstrap_schema(conn)
        logger.info(json.dumps({"event": "db_initialized"}))
    finally:
        conn.close()


class TeamStatsPublisher:
    """Publishes an accepted team-stats snapshot to Postgres (upsert per team)."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url

    def publish(self, snapshot: Snapshot) -> int:
        """Upsert every team row for this snapshot's split. Returns row count."""
        rows = team_stats_rows(snapshot)
        if not rows:
            return 0
        columns = list(rows[0].keys())
        sql = upsert_sql("team_stats", columns, TEAM_STATS_PK)
        params = [tuple(row[c] for c in columns) for row in rows]
        conn = connect(self.database_url)
        try:
            bootstrap_schema(conn)  # self-healing; safe if tables already exist
            with conn.cursor() as cur:
                cur.executemany(sql, params)
            conn.commit()
        finally:
            conn.close()
        logger.info(
            json.dumps(
                {
                    "event": "published",
                    "table": "team_stats",
                    "split": split_label(snapshot.last_n_games),
                    "rows": len(rows),
                    "extractionKey": snapshot.extraction_key,
                }
            )
        )
        return len(rows)
