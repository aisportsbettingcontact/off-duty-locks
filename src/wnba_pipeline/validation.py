"""Schema and data validation: RawFetchResult -> ValidationOutcome.

``validate_and_normalize`` applies every structural and cross-field rule
from docs/data-contract.md against the raw leaguedashteamstats payload and
either produces a normalized ``contract.Snapshot`` (state PASSED) or a
complete list of ``ValidationFailure``s (state FAILED, snapshot ``None``).

Design rules enforced here:
  - ALL failures are collected — validation never stops at the first defect.
  - Header lookup is strictly by name/index map; a reordered header row with
    identical content validates identically (never positional assumptions).
  - Unknown official columns are NOT failures: their values are preserved
    verbatim per row in ``TeamRecord.extras`` keyed by original header name.
  - ``None`` (missing/unparseable) is NEVER coerced to 0, and a non-numeric
    stat cell (even the string ``"12"``) is a failure, never coerced.
  - Percentages use the source scale: fraction 0.0-1.0.
  - An empty rowSet is a validation failure (EMPTY_DATASET) — an in-season
    request must never yield a "successful" empty dataset.

Failure codes (stable machine identifiers):
    MISSING_RESULT_SET, HEADER_ROW_WIDTH_MISMATCH, MISSING_REQUIRED_COLUMN,
    NON_NUMERIC_VALUE, PCT_SCALE_VIOLATION, DUPLICATE_TEAM_ID,
    DUPLICATE_TEAM_NAME, WL_GP_MISMATCH, LASTN_EXCEEDED,
    MAKES_EXCEED_ATTEMPTS, REB_RECONCILE_FAIL, PCT_RECOMPUTE_FAIL,
    NEGATIVE_COUNTING_STAT, UNEXPECTED_TEAM, MISSING_EXPECTED_TEAM,
    EMPTY_DATASET, DUPLICATE_RECORD.
"""

from __future__ import annotations

import logging
from typing import Any

from wnba_pipeline import contract
from wnba_pipeline.contract import (
    ExpectedTeamSet,
    FreshnessState,
    RawFetchResult,
    Snapshot,
    TeamRecord,
    ValidationFailure,
    ValidationOutcome,
    ValidationState,
)

logger = logging.getLogger("wnba_pipeline.validation")

RESULT_SET_NAME = "LeagueDashTeamStats"

# --- stable failure codes ---------------------------------------------------
MISSING_RESULT_SET = "MISSING_RESULT_SET"
HEADER_ROW_WIDTH_MISMATCH = "HEADER_ROW_WIDTH_MISMATCH"
MISSING_REQUIRED_COLUMN = "MISSING_REQUIRED_COLUMN"
NON_NUMERIC_VALUE = "NON_NUMERIC_VALUE"
PCT_SCALE_VIOLATION = "PCT_SCALE_VIOLATION"
DUPLICATE_TEAM_ID = "DUPLICATE_TEAM_ID"
DUPLICATE_TEAM_NAME = "DUPLICATE_TEAM_NAME"
WL_GP_MISMATCH = "WL_GP_MISMATCH"
LASTN_EXCEEDED = "LASTN_EXCEEDED"
MAKES_EXCEED_ATTEMPTS = "MAKES_EXCEED_ATTEMPTS"
REB_RECONCILE_FAIL = "REB_RECONCILE_FAIL"
PCT_RECOMPUTE_FAIL = "PCT_RECOMPUTE_FAIL"
NEGATIVE_COUNTING_STAT = "NEGATIVE_COUNTING_STAT"
UNEXPECTED_TEAM = "UNEXPECTED_TEAM"
MISSING_EXPECTED_TEAM = "MISSING_EXPECTED_TEAM"
EMPTY_DATASET = "EMPTY_DATASET"
DUPLICATE_RECORD = "DUPLICATE_RECORD"

# --- tolerances (documented in docs/data-contract.md) -----------------------
_EPS = 1e-9
_PCT_TOL_PER_GAME = 0.02     # 1-decimal made/att rounding at WNBA volumes
_PCT_TOL_TOTALS = 0.001      # 3-decimal percentage rounding
_W_PCT_TOL = 0.001           # W_PCT is 3-decimal rounded W/GP in both modes
_REB_TOL_PER_GAME = 0.15     # two 1-decimal roundings
_REB_TOL_TOTALS = _EPS       # totals rebounds are exact integers

# (made, attempted, pct) canonical triples subject to recompute/ordering rules.
_SHOT_TRIPLES: tuple[tuple[str, str, str], ...] = (
    ("field_goals_made", "field_goals_attempted", "field_goal_pct"),
    ("three_pointers_made", "three_pointers_attempted", "three_point_pct"),
    ("free_throws_made", "free_throws_attempted", "free_throw_pct"),
)

# Fields normalized to int when the source value is an integral float.
_INTEGER_FIELDS: tuple[str, ...] = ("games_played", "wins", "losses")

_IDENTITY_HEADERS = ("TEAM_ID", "TEAM_NAME")


def _is_number(value: Any) -> bool:
    """True for real JSON numbers. bool is an int subclass but NOT a stat."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _canonical_team_id(value: Any) -> str | None:
    """Deterministic string form of a TEAM_ID cell; None if unusable."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


class _Collector:
    """Accumulates every failure plus the set of implicated (rejected) rows."""

    def __init__(self) -> None:
        self.failures: list[ValidationFailure] = []
        self.rejected_rows: set[int] = set()

    def add(
        self,
        code: str,
        message: str,
        *,
        team_id: str | None = None,
        rows: tuple[int, ...] = (),
    ) -> None:
        self.failures.append(ValidationFailure(code=code, message=message, team_id=team_id))
        self.rejected_rows.update(rows)


def _find_result_set(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Locate the LeagueDashTeamStats result set at ANY index; other result
    sets are tolerated and ignored."""
    result_sets = payload.get("resultSets")
    if not isinstance(result_sets, list):
        return None
    for entry in result_sets:
        if isinstance(entry, dict) and entry.get("name") == RESULT_SET_NAME:
            return entry
    return None


def _outcome_failed(
    collector: _Collector,
    expected: ExpectedTeamSet,
    *,
    actual_team_count: int,
    total_rows: int,
) -> ValidationOutcome:
    rejected = len(collector.rejected_rows)
    return ValidationOutcome(
        state=ValidationState.FAILED,
        failures=collector.failures,
        snapshot=None,
        expected_team_count=expected.team_count,
        actual_team_count=actual_team_count,
        valid_row_count=max(total_rows - rejected, 0),
        rejected_row_count=rejected,
    )


def validate_and_normalize(
    raw: RawFetchResult, expected: ExpectedTeamSet
) -> ValidationOutcome:
    """Validate one raw fetch against the full rule set and, on success,
    build the normalized ``Snapshot`` (records sorted case-insensitively by
    team name ascending, team_id tiebreak — matching the page's
    sort=TEAM_NAME&dir=1)."""
    collector = _Collector()
    params = raw.params

    # ---- structural: result set ---------------------------------------
    result_set = _find_result_set(raw.payload)
    if result_set is None:
        collector.add(
            MISSING_RESULT_SET,
            f"no result set named {RESULT_SET_NAME!r} in payload",
        )
        return _outcome_failed(collector, expected, actual_team_count=0, total_rows=0)

    headers = result_set.get("headers")
    rows = result_set.get("rowSet")
    if (
        not isinstance(headers, list)
        or not headers
        or not all(isinstance(h, str) for h in headers)
        or not isinstance(rows, list)
    ):
        collector.add(
            MISSING_RESULT_SET,
            f"result set {RESULT_SET_NAME!r} is malformed"
            " (headers/rowSet missing or wrong type)",
        )
        return _outcome_failed(collector, expected, actual_team_count=0, total_rows=0)

    # Header lookup strictly by name — column order is irrelevant.
    index_of: dict[str, int] = {}
    for i, name in enumerate(headers):
        index_of.setdefault(name, i)

    # ---- structural: required columns ----------------------------------
    missing_columns = [h for h in contract.SOURCE_HEADER_MAP if h not in index_of]
    for column in missing_columns:
        collector.add(
            MISSING_REQUIRED_COLUMN, f"required column {column!r} absent from headers"
        )
    have_team_id = "TEAM_ID" in index_of
    have_team_name = "TEAM_NAME" in index_of
    extra_headers = [h for h in headers if h not in contract.SOURCE_HEADER_MAP]

    # ---- structural: empty dataset --------------------------------------
    if len(rows) == 0:
        collector.add(
            EMPTY_DATASET,
            "headers present but zero rows — an in-season request must never"
            " produce an empty dataset (never reported as fresh zeros)",
        )

    # ---- per-row parsing + row-scoped rules ------------------------------
    per_mode = params.per_mode
    pct_tol = _PCT_TOL_PER_GAME if per_mode == "PerGame" else _PCT_TOL_TOTALS
    reb_tol = _REB_TOL_PER_GAME if per_mode == "PerGame" else _REB_TOL_TOTALS

    parsed: list[tuple[int, str | None, str, dict[str, Any], dict[str, Any]]] = []
    # (row_index, team_id, team_name, stats, extras)

    for row_index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != len(headers):
            width = len(row) if isinstance(row, list) else "non-list"
            collector.add(
                HEADER_ROW_WIDTH_MISMATCH,
                f"rowSet[{row_index}] width {width} != headers length {len(headers)}",
                rows=(row_index,),
            )
            continue  # cells cannot be mapped safely; content checks skipped

        team_id = _canonical_team_id(row[index_of["TEAM_ID"]]) if have_team_id else None
        raw_name = row[index_of["TEAM_NAME"]] if have_team_name else None
        team_name = raw_name.strip() if isinstance(raw_name, str) else ""

        stats: dict[str, Any] = {}
        for header, canonical in contract.SOURCE_HEADER_MAP.items():
            if header in _IDENTITY_HEADERS or header not in index_of:
                continue
            value = row[index_of[header]]
            if value is None:
                stats[canonical] = None  # null stays None — NEVER zero
            elif _is_number(value):
                stats[canonical] = value
            else:
                collector.add(
                    NON_NUMERIC_VALUE,
                    f"rowSet[{row_index}] {header}={value!r} is neither a number"
                    " nor null (no coercion is ever applied)",
                    team_id=team_id,
                    rows=(row_index,),
                )
                stats[canonical] = None

        # Unknown official columns preserved verbatim, keyed by source header.
        extras = {h: row[index_of[h]] for h in extra_headers}

        g = stats.get

        # PCT_SCALE_VIOLATION — source scale is fraction 0.0-1.0.
        for field_name in contract.PERCENTAGE_FIELDS:
            value = g(field_name)
            if value is not None and _is_number(value) and not (0.0 <= value <= 1.0):
                collector.add(
                    PCT_SCALE_VIOLATION,
                    f"rowSet[{row_index}] {field_name}={value!r} outside [0, 1]"
                    " (percentages use fraction scale, not 0-100)",
                    team_id=team_id,
                    rows=(row_index,),
                )

        # NEGATIVE_COUNTING_STAT — plus_minus is the only signed field.
        for field_name in contract.NON_NEGATIVE_FIELDS:
            value = g(field_name)
            if value is not None and value < 0:
                collector.add(
                    NEGATIVE_COUNTING_STAT,
                    f"rowSet[{row_index}] {field_name}={value!r} is negative",
                    team_id=team_id,
                    rows=(row_index,),
                )

        gp, wins, losses = g("games_played"), g("wins"), g("losses")

        # WL_GP_MISMATCH — exact integer identity.
        if gp is not None and wins is not None and losses is not None:
            if wins + losses != gp:
                collector.add(
                    WL_GP_MISMATCH,
                    f"rowSet[{row_index}] W({wins}) + L({losses}) != GP({gp})",
                    team_id=team_id,
                    rows=(row_index,),
                )

        # LASTN_EXCEEDED — GP can never exceed the LastNGames window. A window
        # of 0 means "all games" (the Year-to-Date split), which imposes no cap.
        if gp is not None and params.last_n_games > 0 and gp > params.last_n_games:
            collector.add(
                LASTN_EXCEEDED,
                f"rowSet[{row_index}] GP({gp}) > LastNGames({params.last_n_games})",
                team_id=team_id,
                rows=(row_index,),
            )

        # MAKES_EXCEED_ATTEMPTS + PCT_RECOMPUTE_FAIL per shooting triple.
        for made_f, att_f, pct_f in _SHOT_TRIPLES:
            made, att, pct = g(made_f), g(att_f), g(pct_f)
            if made is not None and att is not None and made > att + _EPS:
                collector.add(
                    MAKES_EXCEED_ATTEMPTS,
                    f"rowSet[{row_index}] {made_f}({made}) > {att_f}({att})",
                    team_id=team_id,
                    rows=(row_index,),
                )
            # Recompute skipped when any operand is None or attempts == 0
            # (0-attempt rows: pct of None or 0.0 both acceptable; 0/0 must
            # never crash or fabricate a value).
            if made is None or att is None or pct is None or att == 0:
                continue
            recomputed = made / att
            if abs(recomputed - pct) > pct_tol:
                collector.add(
                    PCT_RECOMPUTE_FAIL,
                    f"rowSet[{row_index}] {pct_f}={pct} but {made_f}/{att_f}"
                    f"={recomputed:.4f} (tolerance ±{pct_tol})",
                    team_id=team_id,
                    rows=(row_index,),
                )

        # W_PCT vs W/GP (±0.001 in both modes).
        w_pct = g("win_pct")
        if wins is not None and gp not in (None, 0) and w_pct is not None:
            recomputed = wins / gp
            if abs(recomputed - w_pct) > _W_PCT_TOL:
                collector.add(
                    PCT_RECOMPUTE_FAIL,
                    f"rowSet[{row_index}] win_pct={w_pct} but W/GP"
                    f"={recomputed:.4f} (tolerance ±{_W_PCT_TOL})",
                    team_id=team_id,
                    rows=(row_index,),
                )

        # REB_RECONCILE_FAIL — OREB + DREB must reconcile with REB.
        oreb, dreb, reb = g("offensive_rebounds"), g("defensive_rebounds"), g("total_rebounds")
        if oreb is not None and dreb is not None and reb is not None:
            if abs(oreb + dreb - reb) > reb_tol:
                collector.add(
                    REB_RECONCILE_FAIL,
                    f"rowSet[{row_index}] |OREB({oreb}) + DREB({dreb}) - REB({reb})|"
                    f" > {reb_tol}",
                    team_id=team_id,
                    rows=(row_index,),
                )

        parsed.append((row_index, team_id, team_name, stats, extras))

    total_rows = len(rows)

    # ---- duplicates (dataset scope, implicating every involved row) -----
    if have_team_id:
        by_id: dict[str, list[int]] = {}
        for row_index, team_id, _name, _stats, _extras in parsed:
            if team_id is not None:
                by_id.setdefault(team_id, []).append(row_index)
        extraction_key = params.extraction_key()
        for team_id, indices in by_id.items():
            if len(indices) > 1:
                collector.add(
                    DUPLICATE_TEAM_ID,
                    f"TEAM_ID {team_id} appears in rows {indices}",
                    team_id=team_id,
                    rows=tuple(indices),
                )
                collector.add(
                    DUPLICATE_RECORD,
                    f"rows {indices} yield the same idempotency key"
                    f" {extraction_key}:{team_id}",
                    team_id=team_id,
                    rows=tuple(indices),
                )

    if have_team_name:
        by_name: dict[str, list[int]] = {}
        name_ids: dict[str, str | None] = {}
        for row_index, team_id, name, _stats, _extras in parsed:
            if name:
                key = name.casefold()  # case-insensitive
                by_name.setdefault(key, []).append(row_index)
                name_ids.setdefault(key, team_id)
        for key, indices in by_name.items():
            if len(indices) > 1:
                collector.add(
                    DUPLICATE_TEAM_NAME,
                    f"team name {key!r} (case-insensitive) appears in rows {indices}",
                    team_id=name_ids.get(key),
                    rows=tuple(indices),
                )

    # ---- expected-team coverage ------------------------------------------
    present_ids = {team_id for _i, team_id, _n, _s, _e in parsed if team_id is not None}
    if have_team_id:
        for row_index, team_id, name, _stats, _extras in parsed:
            if team_id is not None and team_id not in expected.teams:
                collector.add(
                    UNEXPECTED_TEAM,
                    f"rowSet[{row_index}] team_id {team_id} ({name or 'unnamed'})"
                    f" not in expected set (version {expected.version_checksum[:12]})",
                    team_id=team_id,
                    rows=(row_index,),
                )
        for team_id in sorted(expected.teams):  # deterministic order
            if team_id not in present_ids:
                collector.add(
                    MISSING_EXPECTED_TEAM,
                    f"expected active team {team_id}"
                    f" ({expected.teams[team_id]}) absent from data",
                    team_id=team_id,
                )

    actual_team_count = len(present_ids)

    if collector.failures:
        logger.warning(
            "validation FAILED key=%s failures=%d rejected_rows=%d/%d",
            params.extraction_key(),
            len(collector.failures),
            len(collector.rejected_rows),
            total_rows,
        )
        return _outcome_failed(
            collector,
            expected,
            actual_team_count=actual_team_count,
            total_rows=total_rows,
        )

    # ---- success: build normalized snapshot ------------------------------
    records: list[TeamRecord] = []
    for _row_index, team_id, team_name, stats, extras in parsed:
        normalized_stats: dict[str, float | int | None] = {}
        for field_name in contract.CANONICAL_STAT_FIELDS:
            value = stats.get(field_name)
            if (
                field_name in _INTEGER_FIELDS
                and isinstance(value, float)
                and value.is_integer()
            ):
                value = int(value)
            normalized_stats[field_name] = value
        records.append(
            TeamRecord(
                team_id=team_id,  # type: ignore[arg-type]  # non-None on success
                team_name=team_name,
                stats=normalized_stats,
                extras=extras,
            )
        )

    # Deterministic presentation sort: case-insensitive team name ascending,
    # team_id tiebreak — matches the page's sort=TEAM_NAME&dir=1.
    records.sort(key=lambda r: (r.team_name.casefold(), r.team_id))

    snapshot = Snapshot(
        source=contract.SOURCE,
        source_url=raw.url,
        source_endpoint=raw.endpoint,
        season=params.season,
        season_type=params.season_type,
        last_n_games=params.last_n_games,
        sort_field=params.sort_field,
        sort_direction=params.sort_direction,
        fetched_at_utc=raw.fetched_at_utc,
        source_observed_at_utc=raw.source_observed_at_utc,
        schema_version=contract.SCHEMA_VERSION,
        source_checksum=raw.source_checksum,
        row_count=len(records),
        team_count=len(present_ids),
        freshness_state=FreshnessState.FRESH,
        validation_state=ValidationState.PASSED,
        records=records,
        extraction_key=params.extraction_key(),
        per_mode=params.per_mode,
        expected_teams_version=expected.version_checksum,
    )
    logger.info(
        "validation PASSED key=%s rows=%d teams=%d normalized=%s",
        snapshot.extraction_key,
        snapshot.row_count,
        snapshot.team_count,
        snapshot.normalized_checksum()[:12],
    )
    return ValidationOutcome(
        state=ValidationState.PASSED,
        failures=[],
        snapshot=snapshot,
        expected_team_count=expected.team_count,
        actual_team_count=actual_team_count,
        valid_row_count=total_rows,
        rejected_row_count=0,
    )
