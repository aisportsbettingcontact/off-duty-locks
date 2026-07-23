# offdutylocks — WNBA Team-Statistics Extraction Pipeline

Production-grade, automated extraction of **WNBA traditional team statistics**
from the official stats platform, normalized, validated, versioned, and
refreshed on a conservative schedule.

Target dataset (the page this pipeline reproduces):

```
https://stats.wnba.com/teams/traditional/?Season=2026&SeasonType=Regular%20Season&LastNGames=7&sort=TEAM_NAME&dir=1
```

- **Season:** 2026 · **Season type:** Regular Season · **Last N games:** 7
- **Sort:** team name, ascending (applied deterministically during normalization)
- **Method:** the official structured JSON endpoint
  (`stats.wnba.com/stats/leaguedashteamstats`), not HTML scraping.

## Status

- ✅ Extractor, validation, storage, automation, CLI, and full offline test
  suite are implemented and passing.
- ⏳ **Live source verification is pending.** This project was built in a sandbox
  whose network policy blocks `*.wnba.com`, so every claim about the live
  endpoint is labeled *documented-platform-knowledge (pending live verification)*
  in `docs/source-contract.md`. Run the **Live Smoke** GitHub Actions workflow
  (open network on runners) to confirm the contract and flip those claims to
  live-verified. Until then, live checks are reported as **BLOCKED**, never
  passed. See `qa/acceptance-gates.md`.

## Architecture

```mermaid
flowchart TD
    A[Scheduler: GitHub Actions cron<br/>month-gated, daily] --> R[runner.run_once]
    C[CLI: wnba-pipeline run] --> R
    R --> L{overlap lock}
    L -- held --> LH[LOCK_HELD exit 5]
    L -- acquired --> T[resolve expected teams<br/>live → stored → fixture]
    T --> F[extractor.fetch_team_stats<br/>retries · backoff · circuit breaker]
    F -- UpstreamUnavailable --> UU[UPSTREAM_UNAVAILABLE exit 3<br/>LKG preserved]
    F --> RAW[(save_raw — immutable)]
    RAW --> V[validation.validate_and_normalize]
    V -- FAILED --> Q[(quarantine)<br/>VALIDATION_FAILED exit 4<br/>LKG preserved]
    V -- PASSED --> I{source checksum<br/>== LKG?}
    I -- yes --> U[SUCCESS_UNCHANGED exit 0]
    I -- no --> S[(accept_snapshot<br/>atomic LKG swap)]
    S --> OK[SUCCESS exit 0]
    R --> M[(RunManifest<br/>stdout + manifests/)]
```

## Quickstart

```bash
python -m pip install -e ".[dev]"     # install with dev/test extras
pytest -q                             # full offline test suite
python3 qa/verify.py --repo-root .    # independent verification harness

# Run against a recorded fixture (fully offline, deterministic):
wnba-pipeline run \
  --fixture fixtures/sanitized/leaguedashteamstats_2026_lastn7.json \
  --data-root ./data

# Inspect the last-known-good snapshot (read-only, no network):
wnba-pipeline status --data-root ./data

# A real live run (only where *.wnba.com is reachable, e.g. CI runners):
wnba-pipeline run --season 2026 --last-n-games 7 --data-root ./data
```

Exit codes: `0` success/unchanged · `2` config · `3` upstream unavailable ·
`4` validation failed · `5` lock held · `6` storage · `7` internal.

## Data layout

Everything the pipeline persists lives under `--data-root` (default `./data`):

```
data/
  raw/<key>/<run_id>.json         immutable raw upstream payloads
  snapshots/<key>/<run_id>.json   accepted normalized snapshots
  quarantine/<key>/<run_id>.json  rejected candidates + failure reasons
  current/<key>.json              last-known-good (LKG) pointer
  manifests/<run_id>.json         one run manifest per run
  teams/<season>.json             versioned expected-team set
```

The dataset identity (`<key>`) is the extraction key, e.g.
`wnba-teamstats:v1:season=2026:type=regular-season:lastn=7:measure=base:permode=pergame`.

## Database serving layer & betting feed

Two feeds publish to a PostgreSQL database (Railway) that the site reads. The
file store above stays the source of truth and audit trail for team stats;
Postgres is the read model.

**Team stats** → `team_stats` (one row per team per split):
- `split = 'last7'` (Last 7 Games) and `split = 'ytd'` (Year-to-Date,
  `LastNGames=0`), refreshed together;
- all traditional fields plus two derived columns computed at publish:
  `possessions = FGA − OREB + TOV + 0.44·FTA` and
  `offensive_rating = (points ÷ possessions) × 100`.

**Betting** → `betting_games` (one wide row per game): opening + current
(DraftKings) + sharp (Circa) spread/total/moneyline, % of bets, % of money,
line movement, and reverse-line-movement flags. Sourced from Action Network
(odds + splits) and VSIN (the Circa sharp line), merged by date + matchup.

```bash
wnba-pipeline db-init                    # create the schema (idempotent)
wnba-pipeline run-team-stats --publish   # YTD + Last-7 -> team_stats
wnba-pipeline betting --publish          # VSIN + Action Network -> betting_games
# All accept --database-url; the default is $DATABASE_URL.
```

**Where each runs:** the betting feed runs on Railway (VSIN + Action Network are
datacenter-reachable). Team stats need a non-datacenter egress (stats.wnba.com
blocks cloud IPs) and run off-Railway, publishing to the same Postgres via its
public URL. See `docs/deployment.md`.

## Documentation

| Doc | Contents |
|---|---|
| `docs/data-contract.md` | Canonical shapes, states, storage layout, module interfaces |
| `docs/data-dictionary.md` | Every field: units, ranges, null semantics, tolerances |
| `docs/source-contract.md` | Page↔endpoint mapping, parameters, headers, response schema |
| `docs/compliance.md` | robots/ToS review checklist, rate-limit policy |
| `docs/deployment.md` | Deploy from scratch, pattern justification, rollback |
| `docs/runbook.md` | On-call: states, alert triage, quarantine, LKG rollback, disable |
| `docs/verification-report.md` | Independent QA findings and pending items |
| `qa/acceptance-gates.md` | The 12 acceptance gates and their evidence/status |

## Compliance

Uses only the public structured endpoint with conservative frequency (one
scheduled run per day, ≥3s spacing, ≤5 requests/run, full `Retry-After`
support). No access controls, CAPTCHAs, authentication, or rate limits are
bypassed. No cookies, tokens, or credentials are sent, stored, or logged. See
`docs/compliance.md`.
