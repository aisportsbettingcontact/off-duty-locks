# Canonical Data Contract — WNBA Team-Statistics Pipeline (offdutylocks)

Single source of truth for dataset identity, shapes, states, validation
tolerances, storage layout, and module interfaces. Code-level contract lives in
`src/wnba_pipeline/contract.py`.

## Target dataset

- Page: `https://stats.wnba.com/teams/traditional/?Season=2026&SeasonType=Regular%20Season&LastNGames=7&sort=TEAM_NAME&dir=1`
- Documented structured endpoint: `https://stats.wnba.com/stats/leaguedashteamstats`
  (WNBA runs the same stats platform as stats.nba.com; `LeagueID=10`).
  **Live verification status is tracked in `docs/source-contract.md`** — the
  development sandbox cannot reach WNBA hosts (network policy 403), so live
  page↔endpoint verification must be executed via the GitHub Actions
  `live-smoke` workflow, which has open network access.
- Filters: Season=2026, SeasonType="Regular Season", LastNGames=7,
  MeasureType=Base (traditional), PerMode=PerGame (page default),
  sorted by TEAM_NAME ascending.

## Extraction key (dataset identity)

`wnba-teamstats:v1:season=2026:type=regular-season:lastn=7:measure=base:permode=pergame`

Produced by `ExtractionParams.extraction_key()`. Sort field/direction are
excluded: sorting is deterministic presentation applied during normalization
(case-insensitive team-name ascending, team_id tiebreak) and does not change
dataset identity.

## Snapshot contract (accepted dataset)

Serialized by `Snapshot.to_json_dict()` with camelCase keys:
`source, sourceUrl, sourceEndpoint, season, seasonType, lastNGames, sortField,
sortDirection, fetchedAtUtc, sourceObservedAtUtc?, schemaVersion,
sourceChecksum, normalizedChecksum, rowCount, teamCount, freshnessState,
validationState, extractionKey, perMode, expectedTeamsVersion, records[]`.

Each record: `teamId, teamName, idempotencyKey, extractedAtUtc, source,
sourceEndpoint, units{}, stats{}, extras{}`.

- `stats` keys are the canonical snake_case fields in
  `contract.SOURCE_HEADER_MAP`. Missing/unparseable → `null`, **never 0**.
- `extras` preserves every additional official field (e.g. `*_RANK`, `CFID`)
  verbatim — official fields are never silently discarded.
- Percentages use source scale **fraction 0.0–1.0** (`fraction_0_1`).
- `idempotencyKey` = `{extractionKey}:{teamId}` — the natural upsert key.

## States

- Freshness: `FRESH | STALE | MISSING | INVALID | UPSTREAM_UNAVAILABLE`
- Validation: `PASSED | FAILED | NOT_RUN`
- Run status → exit codes: OK=0, CONFIG_ERROR=2, UPSTREAM_UNAVAILABLE=3,
  VALIDATION_FAILED=4, LOCK_HELD=5, STORAGE_ERROR=6, INTERNAL_ERROR=7.

Missing, invalid, or unavailable data is **never** converted to zero, and a
failed extraction is **never** reported as an empty-but-successful dataset.
Stale cached data is never presented as a fresh extraction.

## Validation rules and tolerances

All structural rules from the spec (header/row width, required columns, unique
team ids/names, no duplicate records per extraction key, numeric parsing,
LastN ≤ 7 games, non-negative counting stats, nulls ≠ zero, expected-team
coverage). Cross-field tolerances (justified by source rounding):

- `wins + losses == games_played` — exact (integer counts).
- PerGame mode: made/attempted are rounded to 1 decimal by the source, and
  percentages to 3 decimals. Recomputed percentage tolerance: **±0.02**
  (worst case for rounded per-game FGM/FGA at WNBA volumes);
  `oreb + dreb` vs `reb` tolerance: **±0.15** (two 1-decimal roundings).
- Totals mode: recomputed percentage tolerance ±0.001; rebounds exact.
- Makes ≤ attempts: compare with epsilon 1e-9 (float safety).
- `win_pct` recomputed from W/GP: tolerance ±0.001 (3-decimal rounding).

Expected active-team set: resolved from an authoritative official source
(`commonteamyears`, LeagueID=10) — **never hardcoded** — and versioned with the
extraction via `expectedTeamsVersion` (checksum). See `teams.py`.

Validation failure ⇒ candidate quarantined with reasons; last verified
snapshot (last-known-good) is preserved untouched.

## Storage layout (file-based, atomic)

```
data/
  raw/<extraction_key>/<run_id>.json         # immutable raw upstream payloads
  snapshots/<extraction_key>/<run_id>.json   # accepted normalized snapshots
  quarantine/<extraction_key>/<run_id>.json  # rejected candidates + failure reasons
  current/<extraction_key>.json              # last-known-good (LKG) normalized snapshot
  manifests/<run_id>.json                    # run manifests (one per run)
  teams/<season>.json                        # versioned expected-team sets
  .lock                                      # overlap lock (single active run)
```

All writes: temp file in the same directory + `os.replace` (atomic). LKG is
replaced only after the new snapshot file is fully written. Idempotency: if
`sourceChecksum` equals current LKG's, the run records `SUCCESS_UNCHANGED` and
does not duplicate snapshots. Retention: raw/snapshots/quarantine pruned to a
configurable count per key (default 50), manifests to 200; LKG never pruned.

## Module ownership & interfaces

| Module / path | Owner | Interface |
|---|---|---|
| `contract.py`, this doc, `pyproject.toml`, `tests/conftest.py` | orchestrator (frozen — propose changes via report, do not edit) | — |
| `http_client.py`, `extractor.py`, `tests/test_http_client*.py`, `tests/test_extractor*.py` | Subagent 2 | `fetch_team_stats(params: ExtractionParams, *, http: HttpConfig \| None) -> RawFetchResult`; raises `UpstreamUnavailable` |
| `validation.py`, `teams.py`, `tests/test_validation*.py`, `tests/test_teams*.py`, `docs/data-dictionary.md`, `fixtures/expected_teams/` | Subagent 3 | `validate_and_normalize(raw: RawFetchResult, expected: ExpectedTeamSet) -> ValidationOutcome`; `resolve_expected_teams(season, *, http, fallback_path) -> ExpectedTeamSet` |
| `storage.py`, `locking.py`, `runner.py`, `__main__.py`, `tests/test_storage*.py`, `tests/test_locking*.py`, `tests/test_runner*.py`, `.github/workflows/*`, `docs/runbook.md`, `docs/deployment.md`, `README.md` | Subagent 4 | `Store(root)`: `save_raw`, `accept_snapshot`, `quarantine`, `load_last_known_good`, `write_manifest`, `prune`; `run_once(config) -> RunManifest` |
| `docs/source-contract.md`, `docs/compliance.md`, `fixtures/sanitized/`, `scripts/capture_live_contract.py` | Subagent 1 | — |
| `qa/`, `fixtures/adversarial/`, `tests/adversarial/`, `docs/verification-report.md` | Subagent 5 | — |

## Sandbox constraint (development environment)

Outbound requests to `*.wnba.com` are denied by this sandbox's network policy
(proxy 403 / timeout). Therefore, in this environment: all tests run against
recorded/synthetic fixtures; the live smoke test and live contract capture are
implemented as GitHub Actions jobs (`live-smoke.yml`, manual dispatch + on
schedule) and are recorded as **BLOCKED**, not passed, until run there.
