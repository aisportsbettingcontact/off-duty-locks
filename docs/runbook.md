# Operations Runbook — WNBA Team-Statistics Pipeline

On-call reference. Every scheduled run emits a `RunManifest` (one JSON line on
stdout, persisted to `data/manifests/<run_id>.json`, and appended to the
Actions run summary). Start triage from the manifest's `status` and
`failureReason`.

## Run states and exit codes

| Status | Exit | Meaning | LKG | Action |
|---|---|---|---|---|
| `SUCCESS` | 0 | Fresh snapshot accepted, promoted to LKG | replaced by validated data | none |
| `SUCCESS_UNCHANGED` | 0 | Upstream identical to LKG (idempotent) | kept | none |
| `UPSTREAM_UNAVAILABLE` | 3 | 403/404/429/5xx/timeout/malformed after retries | preserved | see *Upstream* below |
| `VALIDATION_FAILED` | 4 | Candidate failed data-quality gates, quarantined | preserved | see *Quarantine* below |
| `LOCK_HELD` | 5 | Another run holds the lock | preserved | see *Lock takeover* |
| `CONFIG_ERROR` | 2 | Expected-team set unresolvable, bad args | preserved | fix config; check `teams/` + fixture |
| `STORAGE_ERROR` | 6 | Disk/write failure (incl. manifest write) | best-effort preserved | check disk/permissions |
| `INTERNAL_ERROR` | 7 | Unexpected exception (lock released) | preserved | file bug with manifest + logs |

Freshness (`freshnessState`) reflects the **stored LKG's** age, never a failed
candidate: `FRESH` (≤ max-age, default 36h), `STALE` (older), `MISSING` (no LKG),
`INVALID` (LKG unreadable), `UPSTREAM_UNAVAILABLE`.

## Alert triage by `failureReason`

Failures open/append to a single open issue labeled `pipeline-alert`.

- **`http_403_forbidden`** — the edge blocked us (datacenter IP or bot
  heuristics). Do NOT retry aggressively or add evasion. Confirm compliance
  (`docs/compliance.md`), consider running from a different network, and check
  whether headers/params drifted (run **Live Smoke**). The extractor already
  fails fast on 403 without hammering.
- **`http_429_rate_limited`** — we exceeded the source's rate limit. The
  extractor honored `Retry-After` and still exhausted retries. Increase spacing,
  reduce schedule frequency. LKG is preserved; the next run should recover.
- **`http_404_not_found`** — endpoint/param path changed. Run **Live Smoke** and
  reconcile `docs/source-contract.md`.
- **`http_5xx` / timeouts / `circuit breaker open`** — transient upstream
  problem. Usually self-heals by the next run; if persistent, the source is down.
- **`malformed_json` / `unexpected_envelope`** — the response shape changed. Run
  **Live Smoke**, inspect the sanitized capture, update the contract/validator.
- **`VALIDATION_FAILED`** — see *Quarantine triage*.
- **`CONFIG_ERROR`** — the expected-team set could not be resolved from live,
  stored, or fixture. Verify `data/teams/<season>.json` or
  `fixtures/expected_teams/<season>.json` exists and is valid.

## Quarantine triage

A failed candidate is written to `data/quarantine/<key>/<run_id>.json` with its
raw payload and `failures[]` (each has a stable `code`). The LKG is untouched.

1. Open the quarantine file; read `failures[].code` (see `docs/data-dictionary.md`
   for each rule).
2. **Source-shape changes** (`MISSING_REQUIRED_COLUMN`, `HEADER_ROW_WIDTH_MISMATCH`,
   `MISSING_RESULT_SET`, `PCT_SCALE_VIOLATION`): the upstream schema changed.
   Run **Live Smoke**, update `docs/source-contract.md` + `validation.py`, add a
   fixture, ship a fix.
3. **Data anomalies** (`MAKES_EXCEED_ATTEMPTS`, `WL_GP_MISMATCH`,
   `REB_RECONCILE_FAIL`, `NEGATIVE_COUNTING_STAT`): usually a genuine bad upstream
   row. Confirm against the live page; if the source is transiently wrong, the
   next run recovers. Do not relax tolerances without evidence.
4. **Coverage** (`MISSING_EXPECTED_TEAM`, `UNEXPECTED_TEAM`): the team universe
   changed (expansion/relocation) or the source returned a partial set.
   Re-resolve the expected-team set (`teams/`), verify via **Live Smoke**.
5. `EMPTY_DATASET` **in-season** = real problem; **in the offseason** it is
   expected — scheduled runs are month-gated (`extract.yml` cron `5-10`) so this
   should not fire out of season.

## LKG rollback (restore a previous verified snapshot)

The LKG pointer is `data/current/<key>.json`; every accepted snapshot is kept
under `data/snapshots/<key>/`. To roll back:

```bash
KEY_ENC='wnba-teamstats__v1__season=2026__type=regular-season__lastn=7__measure=base__permode=pergame'
# 1. Pick a known-good snapshot (sorted by run id / timestamp):
ls -1 data/snapshots/$KEY_ENC/
# 2. Atomically restore it as current (copy to a temp then move):
cp "data/snapshots/$KEY_ENC/<good_run_id>.json" "data/current/$KEY_ENC.json.tmp"
mv "data/current/$KEY_ENC.json.tmp" "data/current/$KEY_ENC.json"
# 3. Verify:
wnba-pipeline status --data-root ./data
# 4. Commit the rollback (data is version-controlled):
git add data/current/ && git commit -m "Rollback LKG to <good_run_id>"
```

Because data is committed to git, `git revert <bad_commit>` is an equally valid
rollback of an accepted-but-wrong snapshot.

## Lock takeover

`data/.lock` holds `{pid, acquiredAtUtc, runId}` (no secrets). A crashed run's
lock auto-expires after 2h and the next run takes it over. To clear a wedged
lock immediately (only after confirming no run is active):

```bash
cat data/.lock         # inspect the holder
rm -f data/.lock       # safe once you've confirmed the holder is dead
```

The scheduled job uses `concurrency: wnba-extract` so scheduled runs never
overlap; manual `workflow_dispatch` runs coalesce behind the same group.

## Retention

`store.prune()` keeps the newest 50 raw / 50 snapshots / 50 quarantine per key
and 200 manifests; `current/` (LKG) is never pruned. Adjust in
`runner`/`storage` if you need deeper history.

## Disable / re-enable

**Disable** (two independent switches, either suffices):

1. Set repository variable `PIPELINE_ENABLED=false`
   (Settings → Secrets and variables → Actions → Variables). The `extract` job's
   `if:` guard skips all runs.
2. Or disable the **Extract** workflow in the Actions UI.

**Re-enable:** set `PIPELINE_ENABLED=true` (or delete the variable) / re-enable
the workflow. The next scheduled tick resumes. To backfill immediately, trigger
**Extract** via `workflow_dispatch`. Because runs are idempotent, a manual
catch-up run is always safe.

## Verifying a fix

```bash
pytest -q                                   # offline suite must be green
python3 qa/verify.py --repo-root .          # independent checks
# Then trigger Live Smoke (workflow_dispatch) to confirm against the real source.
```
