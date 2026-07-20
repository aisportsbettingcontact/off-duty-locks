"""Canonical data contract for the WNBA team-statistics pipeline.

This module is the single source of truth for the shapes that flow between
the extractor, validator, storage, and runner. It contains data only:
dataclasses, enums, constants, and exceptions. No I/O.

Module responsibilities (see docs/data-contract.md for full interface spec):
  - http_client.py / extractor.py : fetch -> RawFetchResult
  - teams.py                      : resolve -> ExpectedTeamSet
  - validation.py                 : RawFetchResult + ExpectedTeamSet -> ValidationOutcome
  - storage.py / locking.py       : persist snapshots, LKG pointer, quarantine
  - runner.py / __main__.py       : orchestrate, emit RunManifest, map to exit codes
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

SCHEMA_VERSION = "1.0.0"

SOURCE = "stats.wnba.com"
SOURCE_PAGE_URL = (
    "https://stats.wnba.com/teams/traditional/"
    "?Season=2026&SeasonType=Regular%20Season&LastNGames=7&sort=TEAM_NAME&dir=1"
)
SOURCE_ENDPOINT = "https://stats.wnba.com/stats/leaguedashteamstats"
WNBA_LEAGUE_ID = "10"


class FreshnessState(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    MISSING = "MISSING"
    INVALID = "INVALID"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"


class ValidationState(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_RUN = "NOT_RUN"


class RunStatus(str, Enum):
    SUCCESS = "SUCCESS"                        # fresh snapshot accepted
    SUCCESS_UNCHANGED = "SUCCESS_UNCHANGED"    # upstream identical to LKG; no new snapshot
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    VALIDATION_FAILED = "VALIDATION_FAILED"    # candidate quarantined, LKG preserved
    LOCK_HELD = "LOCK_HELD"
    CONFIG_ERROR = "CONFIG_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# Process exit codes (runner). Documented in docs/data-contract.md.
EXIT_OK = 0                      # SUCCESS or SUCCESS_UNCHANGED
EXIT_CONFIG_ERROR = 2
EXIT_UPSTREAM_UNAVAILABLE = 3
EXIT_VALIDATION_FAILED = 4
EXIT_LOCK_HELD = 5
EXIT_STORAGE_ERROR = 6
EXIT_INTERNAL_ERROR = 7

EXIT_CODE_BY_STATUS: dict[RunStatus, int] = {
    RunStatus.SUCCESS: EXIT_OK,
    RunStatus.SUCCESS_UNCHANGED: EXIT_OK,
    RunStatus.UPSTREAM_UNAVAILABLE: EXIT_UPSTREAM_UNAVAILABLE,
    RunStatus.VALIDATION_FAILED: EXIT_VALIDATION_FAILED,
    RunStatus.LOCK_HELD: EXIT_LOCK_HELD,
    RunStatus.CONFIG_ERROR: EXIT_CONFIG_ERROR,
    RunStatus.STORAGE_ERROR: EXIT_STORAGE_ERROR,
    RunStatus.INTERNAL_ERROR: EXIT_INTERNAL_ERROR,
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


@dataclass(frozen=True)
class ExtractionParams:
    """User-facing extraction parameters. All are configurable."""

    season: str = "2026"
    season_type: str = "Regular Season"
    last_n_games: int = 7
    measure_type: str = "Base"        # "traditional" on the page
    per_mode: str = "PerGame"         # page default for teams/traditional
    sort_field: str = "TEAM_NAME"
    sort_direction: str = "asc"       # page: dir=1 (ascending)

    def extraction_key(self) -> str:
        """Deterministic key identifying one logical dataset.

        Sort field/direction are intentionally excluded: sorting is applied
        deterministically during normalization and does not change dataset
        identity.
        """
        return (
            f"wnba-teamstats:v1"
            f":season={_slug(self.season)}"
            f":type={_slug(self.season_type)}"
            f":lastn={self.last_n_games}"
            f":measure={_slug(self.measure_type)}"
            f":permode={_slug(self.per_mode)}"
        )


# Map from official result-set headers to canonical snake_case field names.
# Fields present in the official response but absent here MUST be preserved
# in TeamRecord.extras, never discarded. (*_RANK fields, CFID/CFPARAMS, etc.
# go to extras.)
SOURCE_HEADER_MAP: dict[str, str] = {
    "TEAM_ID": "team_id",
    "TEAM_NAME": "team_name",
    "GP": "games_played",
    "W": "wins",
    "L": "losses",
    "W_PCT": "win_pct",
    "MIN": "minutes",
    "FGM": "field_goals_made",
    "FGA": "field_goals_attempted",
    "FG_PCT": "field_goal_pct",
    "FG3M": "three_pointers_made",
    "FG3A": "three_pointers_attempted",
    "FG3_PCT": "three_point_pct",
    "FTM": "free_throws_made",
    "FTA": "free_throws_attempted",
    "FT_PCT": "free_throw_pct",
    "OREB": "offensive_rebounds",
    "DREB": "defensive_rebounds",
    "REB": "total_rebounds",
    "AST": "assists",
    "TOV": "turnovers",
    "STL": "steals",
    "BLK": "blocks",
    "BLKA": "blocked_attempts",
    "PF": "personal_fouls",
    "PFD": "personal_fouls_drawn",
    "PTS": "points",
    "PLUS_MINUS": "plus_minus",
}

# Canonical stat fields (everything in SOURCE_HEADER_MAP except identity cols).
CANONICAL_STAT_FIELDS: tuple[str, ...] = tuple(
    v for v in SOURCE_HEADER_MAP.values() if v not in ("team_id", "team_name")
)

PERCENTAGE_FIELDS: tuple[str, ...] = (
    "win_pct",
    "field_goal_pct",
    "three_point_pct",
    "free_throw_pct",
)

# Counting stats that can never be negative. plus_minus MAY be negative.
NON_NEGATIVE_FIELDS: tuple[str, ...] = tuple(
    f for f in CANONICAL_STAT_FIELDS if f != "plus_minus"
)


def field_units(per_mode: str) -> dict[str, str]:
    """Unit label for every canonical stat field, given the PerMode."""
    per = "per_game" if per_mode == "PerGame" else "total"
    units: dict[str, str] = {}
    for name in CANONICAL_STAT_FIELDS:
        if name in PERCENTAGE_FIELDS:
            units[name] = "fraction_0_1"   # source scale: 0.0-1.0, 3 decimals
        elif name in ("games_played", "wins", "losses"):
            units[name] = "games"
        elif name == "minutes":
            units[name] = f"minutes_{per}"
        else:
            units[name] = per
    return units


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_checksum(obj: Any) -> str:
    """Reproducible checksum of a JSON-serializable object."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_hex(blob)


@dataclass
class RawFetchResult:
    """Result of one successful upstream fetch. Raw bytes are preserved."""

    endpoint: str
    url: str                      # full sanitized URL (query params only, no headers)
    params: ExtractionParams
    payload: dict[str, Any]       # parsed JSON body
    raw_bytes: bytes
    source_checksum: str          # sha256 of raw_bytes
    fetched_at_utc: str           # ISO-8601 UTC
    http_status: int
    request_count: int
    retry_count: int
    source_observed_at_utc: str | None = None   # from response Date header, if any


@dataclass
class ExpectedTeamSet:
    """Authoritative active-team set for a season. Never hardcode team count."""

    season: str
    source: str                   # e.g. "stats.wnba.com/stats/commonteamyears"
    source_url: str
    resolved_at_utc: str
    teams: dict[str, str]         # team_id -> canonical team name
    version_checksum: str         # canonical_json_checksum of {season, teams}

    @property
    def team_count(self) -> int:
        return len(self.teams)


@dataclass
class TeamRecord:
    """One normalized team row. None means missing/unparseable — NEVER zero."""

    team_id: str
    team_name: str
    stats: dict[str, float | int | None]   # keys: CANONICAL_STAT_FIELDS
    extras: dict[str, Any] = field(default_factory=dict)  # preserved official fields

    def idempotency_key(self, extraction_key: str) -> str:
        return f"{extraction_key}:{self.team_id}"


@dataclass
class ValidationFailure:
    code: str                     # stable machine code, e.g. "DUPLICATE_TEAM_ID"
    message: str
    team_id: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "teamId": self.team_id}


@dataclass
class ValidationOutcome:
    state: ValidationState
    failures: list[ValidationFailure]
    snapshot: "Snapshot | None"   # None when state is FAILED
    expected_team_count: int
    actual_team_count: int
    valid_row_count: int
    rejected_row_count: int


@dataclass
class Snapshot:
    """One accepted, normalized dataset. Serialized shape is the canonical
    contract required by the spec (camelCase keys)."""

    source: str
    source_url: str
    source_endpoint: str
    season: str
    season_type: str
    last_n_games: int
    sort_field: str
    sort_direction: str
    fetched_at_utc: str
    source_observed_at_utc: str | None
    schema_version: str
    source_checksum: str
    row_count: int
    team_count: int
    freshness_state: FreshnessState
    validation_state: ValidationState
    records: list[TeamRecord]
    extraction_key: str
    per_mode: str
    expected_teams_version: str | None = None

    def normalized_checksum(self) -> str:
        """Reproducible checksum of normalized content (excludes fetch time)."""
        return canonical_json_checksum(
            {
                "extractionKey": self.extraction_key,
                "schemaVersion": self.schema_version,
                "records": [
                    {
                        "teamId": r.team_id,
                        "teamName": r.team_name,
                        "stats": r.stats,
                        "extras": r.extras,
                    }
                    for r in self.records
                ],
            }
        )

    def to_json_dict(self) -> dict[str, Any]:
        units = field_units(self.per_mode)
        return {
            "source": self.source,
            "sourceUrl": self.source_url,
            "sourceEndpoint": self.source_endpoint,
            "season": self.season,
            "seasonType": self.season_type,
            "lastNGames": self.last_n_games,
            "sortField": self.sort_field,
            "sortDirection": self.sort_direction,
            "fetchedAtUtc": self.fetched_at_utc,
            "sourceObservedAtUtc": self.source_observed_at_utc,
            "schemaVersion": self.schema_version,
            "sourceChecksum": self.source_checksum,
            "normalizedChecksum": self.normalized_checksum(),
            "rowCount": self.row_count,
            "teamCount": self.team_count,
            "freshnessState": self.freshness_state.value,
            "validationState": self.validation_state.value,
            "extractionKey": self.extraction_key,
            "perMode": self.per_mode,
            "expectedTeamsVersion": self.expected_teams_version,
            "records": [
                {
                    "teamId": r.team_id,
                    "teamName": r.team_name,
                    "idempotencyKey": r.idempotency_key(self.extraction_key),
                    "extractedAtUtc": self.fetched_at_utc,
                    "source": self.source,
                    "sourceEndpoint": self.source_endpoint,
                    "units": units,
                    "stats": r.stats,
                    "extras": r.extras,
                }
                for r in self.records
            ],
        }


@dataclass
class RunManifest:
    """One structured run summary. Everything observability needs, no secrets."""

    run_id: str
    status: RunStatus
    extraction_key: str
    params: dict[str, Any]
    started_at_utc: str
    ended_at_utc: str
    duration_seconds: float
    request_count: int
    response_status: int | None
    retry_count: int
    raw_row_count: int | None
    valid_row_count: int | None
    rejected_row_count: int | None
    expected_team_count: int | None
    actual_team_count: int | None
    source_checksum: str | None
    normalized_checksum: str | None
    freshness_state: FreshnessState
    validation_state: ValidationState
    validation_failures: list[dict[str, Any]] = field(default_factory=list)
    storage_result: str | None = None
    last_known_good_preserved: bool = True
    failure_reason: str | None = None
    schema_version: str = SCHEMA_VERSION

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "status": self.status.value,
            "extractionKey": self.extraction_key,
            "params": self.params,
            "startedAtUtc": self.started_at_utc,
            "endedAtUtc": self.ended_at_utc,
            "durationSeconds": self.duration_seconds,
            "requestCount": self.request_count,
            "responseStatus": self.response_status,
            "retryCount": self.retry_count,
            "rawRowCount": self.raw_row_count,
            "validRowCount": self.valid_row_count,
            "rejectedRowCount": self.rejected_row_count,
            "expectedTeamCount": self.expected_team_count,
            "actualTeamCount": self.actual_team_count,
            "sourceChecksum": self.source_checksum,
            "normalizedChecksum": self.normalized_checksum,
            "freshnessState": self.freshness_state.value,
            "validationState": self.validation_state.value,
            "validationFailures": self.validation_failures,
            "storageResult": self.storage_result,
            "lastKnownGoodPreserved": self.last_known_good_preserved,
            "failureReason": self.failure_reason,
            "schemaVersion": self.schema_version,
        }


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PipelineError(Exception):
    """Base for all pipeline errors."""


class ConfigError(PipelineError):
    pass


class UpstreamUnavailable(PipelineError):
    """Upstream could not produce a usable response (403/404/429/5xx exhausted,
    timeouts, malformed JSON, circuit breaker open). NEVER masked as empty data."""

    def __init__(self, reason: str, http_status: int | None = None,
                 request_count: int = 0, retry_count: int = 0):
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status
        self.request_count = request_count
        self.retry_count = retry_count


class ValidationQuarantined(PipelineError):
    """Candidate dataset failed validation and was quarantined."""

    def __init__(self, failures: list[ValidationFailure]):
        super().__init__(f"{len(failures)} validation failure(s)")
        self.failures = failures


class LockHeld(PipelineError):
    pass


class StorageError(PipelineError):
    pass
