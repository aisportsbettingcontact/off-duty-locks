# Independent Verification Report

Prepared by the independent QA workstream, separate from the implementation.
Reproduce everything here with:

```bash
pip install -e ".[dev]"
pytest -q
python3 qa/verify.py --repo-root .
```

## What was verified (now, offline)

**Test suite — 163 passed, 0 failed, 0 skipped, 0 blocked** (JUnit-counted by
`qa/verify.py` §a). Breakdown:

| Area | File(s) | Tests |
|---|---|---|
| HTTP client (retries, backoff, 429/403/404, circuit breaker, log hygiene) | `test_http_client.py` | 26 |
| Extractor (param fidelity, envelope checks, RawFetchResult) | `test_extractor.py` | 17 |
| Validation + normalization (every failure code, tolerances, sort) | `test_validation.py` | 45 |
| Storage (atomic writes, immutability, LKG, quarantine, prune) | `test_storage.py` | 13 |
| Locking (overlap rejection, stale takeover, owner-safe release) | `test_locking.py` | 7 |
| Runner (all outcome paths, exit codes, manifest completeness, CLI) | `test_runner.py` | 13 |
| Adversarial validation challenges (21 payloads) | `adversarial/test_challenges.py` | 27 |
| Adversarial HTTP + concurrency challenges | `adversarial/test_http_challenges.py` | 15 |

**Independent harness — `qa/verify.py`: 9 sections PASS, 0 FAIL, 0 BLOCKED.**
Sections: (a) test suite, (b) secret scan — 0 hits across `src/ tests/ fixtures/
qa/ docs/ .github/ scripts/`, (c) fixture provenance — every JSON fixture carries
`_provenance.synthetic`, (d) checksum reproducibility — normalized checksum
stable across serializations, (e) idempotency — CLI run twice yields SUCCESS then
SUCCESS_UNCHANGED with one snapshot, (f) LKG protection — a failing candidate
leaves `current/` byte-identical and exits 4, (g) automation audit — CI job named
`CI`, month-gated cron, concurrency group, disable switch, no plaintext secrets,
(h) docs executability — offline shell blocks in runbook/deployment/README parse
with `bash -n`, (i) artifact hygiene — `.gitignore` excludes debug material and no
`.pyc`/`__pycache__`/`.egg-info` are tracked.

**Adversarial challenges exercised** (each a single mutation of the known-good
envelope; generator: `qa/gen_adversarial_fixtures.py`):

- *Must pass:* changed header order (proves lookup-by-name; normalized checksum
  identical to the base), added column (preserved in `extras`), null percentage
  (stays `null`, never `0`), short season (GP ≤ 3 is legal), stale upstream.
- *Must fail with the right code:* empty/offseason (`EMPTY_DATASET`), partial
  (`MISSING_EXPECTED_TEAM`), missing result set, removed required column, row-width
  mismatch, duplicate team/record, unknown team, string-for-number, negative stat,
  wrong percentage scale, W+L≠GP, makes>attempts, rebound mismatch, LastN exceeded.
- *Transport hostility:* 403 (fail-fast, single request), 404, 429 with/without
  `Retry-After` (honored + capped), 500 storm (bounded retries), connection/read
  timeouts (retried), malformed + truncated JSON (raise `UpstreamUnavailable`).
- *Operational:* concurrent runs (one proceeds, one `LOCK_HELD`), interrupted LKG
  write (previous LKG intact, run not reported as success), failed run cannot
  overwrite LKG, idempotent rerun creates no duplicate.

## What is pending integration

Nothing internal — all workstreams are integrated and the full suite is green.

## What is blocked by the sandbox network policy

The build environment blocks outbound requests to `*.wnba.com` (verified: proxy
`403` / connection timeout). Therefore the following are **BLOCKED**, not passed,
and must be run on GitHub-hosted runners (open network) via the **Live Smoke**
workflow:

1. **Live page↔endpoint correspondence** (acceptance gate 1). Confirms the real
   `leaguedashteamstats` response matches the documented contract and that the
   page's filters map to the captured request. Command: the workflow runs
   `python3 scripts/capture_live_contract.py`, which emits a per-claim (C01–C17)
   report.
2. **Live smoke match** (acceptance gate 9). A conservative live extraction whose
   teams/values are compared against the official page.
3. **Expansion-team IDs.** `fixtures/teams-2026-reference.json` marks the 2026
   expansion franchises (Golden State, Toronto, Portland) IDs as placeholders; the
   capture script's C11 check prints the real IDs on mismatch.
4. **robots.txt / ToS review** (`docs/compliance.md` §1 lists the exact URLs).

## Commands to close each pending item

```bash
# On GitHub (Actions tab): run the "Live Smoke" workflow (workflow_dispatch).
# Then download the live-smoke-artifacts and:
#   - review live_capture_<date>.json's claimReport (C01–C17),
#   - update docs/source-contract.md (flip confirmed claims to live-verified),
#   - update qa/acceptance-gates.md gates 1, 2, 9 with the run URL,
#   - reconcile expansion-team IDs in fixtures/teams-2026-reference.json if flagged,
#   - record the robots/ToS review outcome in docs/compliance.md.
```

## Residual risks

- **Contract drift.** The stats platform can change headers/params without notice.
  Mitigation: validation fails closed (quarantine, LKG preserved) and the Live
  Smoke claim report surfaces drift; re-run it after any suspected change.
- **Edge blocking (403) from datacenter IPs.** GitHub runners may be rate-limited
  or blocked by the platform's edge. The extractor fails fast on 403 (no evasion,
  by policy). If it recurs, run from a different network — do not add bypasses.
- **Expansion-team placeholders** until the first live capture (above).
- **File-based store growth.** Bounded by `prune()` (50/50/50/200); revisit if
  deep history is needed.
