# Data Dictionary — WNBA Traditional Team Statistics

Canonical field reference for every normalized `TeamRecord.stats` value produced
by `wnba_pipeline.validation`. The machine-readable source of truth is
`SOURCE_HEADER_MAP`, `CANONICAL_STAT_FIELDS`, `PERCENTAGE_FIELDS`,
`NON_NEGATIVE_FIELDS`, and `field_units()` in `src/wnba_pipeline/contract.py`.

## Conventions

- **Source header** — the column name in the official `LeagueDashTeamStats`
  result set.
- **Canonical field** — the snake_case key under `record.stats`.
- **Unit** — depends on `perMode`. `PerGame` (the page default) yields per-game
  averages; `Totals` yields season totals. Reported per field in
  `record.units`.
- **Null semantics** — a missing or unparseable cell is normalized to `null`
  (JSON) / `None` (Python), **never** `0`. Zero is a real measured value and is
  preserved as `0`. Consumers MUST distinguish `null` (absent/unknown) from `0`.
- **Percentage scale** — all `*_pct` fields use the **source scale, a fraction
  in `[0, 1]`** with 3 decimal places (e.g. `0.472`, not `47.2`). A value
  outside `[0, 1]` is a `PCT_SCALE_VIOLATION` validation failure.

## Identity fields

| Source | Canonical | Type | Notes |
|---|---|---|---|
| `TEAM_ID` | `team_id` | string | 10-digit stats-platform franchise id (stringified). Stable across seasons. Unique per snapshot. The idempotency key is `{extractionKey}:{team_id}`. |
| `TEAM_NAME` | `team_name` | string | Canonical franchise name. Unique per snapshot (case-insensitive). Snapshot records are sorted by this field, ascending, case-insensitive, `team_id` tiebreak — matching the page's `sort=TEAM_NAME&dir=1`. |

## Record / games fields

| Source | Canonical | Type | Unit (PerGame / Totals) | Range | Notes |
|---|---|---|---|---|---|
| `GP` | `games_played` | int | games / games | `0 … last_n_games` | For a LastN=7 request, `games_played ≤ 7` (`LASTN_EXCEEDED` otherwise). Fewer than 7 completed games is legal early-season. |
| `W` | `wins` | int | games | `≥ 0` | `wins + losses == games_played` exactly (`WL_GP_MISMATCH`). |
| `L` | `losses` | int | games | `≥ 0` | see above |
| `W_PCT` | `win_pct` | float | fraction `[0,1]` | `[0,1]` | Recomputed vs `wins/games_played` within ±0.001. Skipped when `games_played == 0`. |
| `MIN` | `minutes` | float | minutes/game / minutes | `≥ 0` | Team minutes. ~40 in PerGame (regulation), higher with overtime. |

## Shooting fields

| Source | Canonical | Type | Unit | Range | Notes |
|---|---|---|---|---|---|
| `FGM` | `field_goals_made` | number | per-game / total | `≥ 0` | `field_goals_made ≤ field_goals_attempted` (`MAKES_EXCEED_ATTEMPTS`, ε=1e-9). |
| `FGA` | `field_goals_attempted` | number | per-game / total | `≥ 0` | |
| `FG_PCT` | `field_goal_pct` | float | fraction `[0,1]` | `[0,1]` | Recomputed vs `FGM/FGA` within tolerance (PerGame ±0.02, Totals ±0.001). Skipped when `FGA == 0`. |
| `FG3M` | `three_pointers_made` | number | per-game / total | `≥ 0` | Subset of FGM. |
| `FG3A` | `three_pointers_attempted` | number | per-game / total | `≥ 0` | Subset of FGA. |
| `FG3_PCT` | `three_point_pct` | float | fraction `[0,1]` | `[0,1]` | Recomputed vs `FG3M/FG3A`. Skipped when `FG3A == 0`. |
| `FTM` | `free_throws_made` | number | per-game / total | `≥ 0` | `FTM ≤ FTA`. |
| `FTA` | `free_throws_attempted` | number | per-game / total | `≥ 0` | |
| `FT_PCT` | `free_throw_pct` | float | fraction `[0,1]` | `[0,1]` | Recomputed vs `FTM/FTA`. Skipped when `FTA == 0`. |

## Rebounding / playmaking / defense

| Source | Canonical | Type | Unit | Range | Notes |
|---|---|---|---|---|---|
| `OREB` | `offensive_rebounds` | number | per-game / total | `≥ 0` | |
| `DREB` | `defensive_rebounds` | number | per-game / total | `≥ 0` | |
| `REB` | `total_rebounds` | number | per-game / total | `≥ 0` | `offensive_rebounds + defensive_rebounds` reconciles with `total_rebounds` within ±0.15 (PerGame, two 1-decimal roundings) / exact (Totals). `REB_RECONCILE_FAIL` otherwise. |
| `AST` | `assists` | number | per-game / total | `≥ 0` | |
| `TOV` | `turnovers` | number | per-game / total | `≥ 0` | |
| `STL` | `steals` | number | per-game / total | `≥ 0` | |
| `BLK` | `blocks` | number | per-game / total | `≥ 0` | Blocks by the team. |
| `BLKA` | `blocked_attempts` | number | per-game / total | `≥ 0` | Team shots that were blocked by opponents. |
| `PF` | `personal_fouls` | number | per-game / total | `≥ 0` | |
| `PFD` | `personal_fouls_drawn` | number | per-game / total | `≥ 0` | |

## Scoring / margin

| Source | Canonical | Type | Unit | Range | Notes |
|---|---|---|---|---|---|
| `PTS` | `points` | number | per-game / total | `≥ 0` | |
| `PLUS_MINUS` | `plus_minus` | number | per-game / total | **may be negative** | The ONLY counting field that may be `< 0`. Excluded from `NON_NEGATIVE_FIELDS`. |

## Preserved official fields (`extras`)

Every additional column the source emits that is not in `SOURCE_HEADER_MAP` is
preserved verbatim under `record.extras`, keyed by its original header name —
never silently discarded. Commonly present:

- `*_RANK` (e.g. `GP_RANK`, `W_RANK`, `PTS_RANK`, …): the source's competition
  ranking per stat. Preserved as-is; the pipeline does not recompute or trust
  them for validation.
- `CFID`, `CFPARAMS`: platform internal filter identifiers. Preserved as-is.

Field lookup during normalization is strictly by header **name**, never by
position, so a changed header order and added columns both normalize correctly.

## States and freshness

`freshnessState` (see `docs/data-contract.md`): `FRESH`, `STALE`, `MISSING`,
`INVALID`, `UPSTREAM_UNAVAILABLE`. Missing, invalid, or unavailable data is
never converted to zero.

## Offseason / empty-season semantics

An in-season request that returns **headers but zero rows** is a validation
failure (`EMPTY_DATASET`) and is quarantined — an empty response never
masquerades as a fresh all-zero dataset. During the offseason the same empty
response is expected; operators gate scheduled runs to the season months
(see `docs/runbook.md`) so an empty offseason payload does not raise a false
alert. A season with **fewer than seven completed games** is fully valid: teams
simply report `games_played < 7`.

## Expected-team-set versioning

The set of teams that MUST appear is resolved from the authoritative official
source (`commonteamyears`, `LeagueID=10`), never hardcoded, and pinned into each
snapshot as `expectedTeamsVersion` (a checksum of `{season, teams}`). Missing an
expected team is `MISSING_EXPECTED_TEAM`; an unknown team id is `UNEXPECTED_TEAM`.
