---
name: pipeline-verify
description: Use when verifying pipeline changes, interpreting run manifests, or checking exit codes / freshness — runs the offline suite + QA harness and reads RunManifest evidence correctly
---

# Pipeline verification

## Commands (all offline, deterministic)

1. `python -m pytest -q` — full unit + integration suite. Must be green.
2. `python3 qa/verify.py --repo-root .` — independent QA harness (secret sweep,
   idempotency, LKG protection, doc-command parsing). All offline sections must pass.
3. End-to-end fixture run (what CI does):
   `wnba-pipeline run --fixture fixtures/sanitized/leaguedashteamstats_2026_lastn7.json --data-root "$(mktemp -d)"`
   — first run exits 0 with status SUCCESS; an identical rerun exits 0 with
   SUCCESS_UNCHANGED and writes no second snapshot.

## Reading a RunManifest

One JSON line on stdout, persisted to `data/manifests/<run_id>.json`. Start from
`status` and `failureReason`. Exit codes: 0 SUCCESS / SUCCESS_UNCHANGED ·
2 CONFIG_ERROR · 3 UPSTREAM_UNAVAILABLE · 4 VALIDATION_FAILED · 5 LOCK_HELD ·
6 STORAGE_ERROR · 7 INTERNAL_ERROR.

`freshnessState` describes the stored last-known-good, never a failed candidate:
FRESH (≤36h default) · STALE · MISSING · INVALID · UPSTREAM_UNAVAILABLE.

## Laws

- A failed run must leave LKG byte-identical. If LKG changed on a failed run,
  that is a severity-1 bug — stop and report.
- A BLOCKED gate (network-dependent, e.g. Live Smoke) is never reported as PASS.
- Report real command output. Never assume success.
