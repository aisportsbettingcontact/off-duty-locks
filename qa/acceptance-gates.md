# Acceptance Gates — Independent Assessment

The 12 production-readiness gates, each with its verification procedure, evidence
source, and current status. Statuses are honest: offline-verifiable gates are
**PASS**; gates that require reaching the live source are **BLOCKED-sandbox**
(the build environment's network policy blocks `*.wnba.com`) and must be closed
by running the **Live Smoke** GitHub Actions workflow. A blocked gate is never
reported as passed.

Legend: ✅ PASS · ⛔ BLOCKED-sandbox (needs live run) · 🟡 PARTIAL

| # | Gate | Procedure | Evidence | Status |
|---|------|-----------|----------|--------|
| 1 | Official page↔endpoint relationship verified | Capture the XHR the page issues, diff against `leaguedashteamstats` contract | `scripts/capture_live_contract.py` + `docs/source-contract.md` claim registry C01–C17 | ⛔ BLOCKED-sandbox — documented from platform knowledge, pending live capture |
| 2 | Every requested filter maps correctly | Unit-test `build_api_params`; confirm live param echo | `tests/test_extractor.py`, `build_api_params` (Season/SeasonType/LastNGames/sort) ; live echo pending | 🟡 PARTIAL — mapping unit-verified offline; live echo BLOCKED-sandbox |
| 3 | All required fields normalized and documented | Field-by-field mapping + data dictionary | `contract.SOURCE_HEADER_MAP`, `docs/data-dictionary.md`, `tests/test_validation.py` | ✅ PASS |
| 4 | Complete validation suite passes | Run full suite | `qa/verify.py` §a: **163 passed, 0 failed, 0 skipped** | ✅ PASS |
| 5 | Repeated extraction is idempotent | Run twice, expect SUCCESS then SUCCESS_UNCHANGED, one snapshot | `qa/verify.py` §e; `tests/test_runner.py::test_idempotent_rerun_is_unchanged` | ✅ PASS |
| 6 | Failed extraction preserves last-known-good | Establish LKG, feed failing candidate, assert LKG byte-identical + exit 4 | `qa/verify.py` §f; `test_runner.py`, `test_http_challenges.py::test_failed_run_cannot_overwrite_lkg` | ✅ PASS |
| 7 | Rate limits and timeouts handled safely | Simulate 429/5xx/timeouts, assert bounded retries + Retry-After | `tests/test_http_client.py`, `tests/adversarial/test_http_challenges.py` | ✅ PASS (offline simulation) |
| 8 | Automation installed and testable | Parse workflows; run offline e2e in CI | `qa/verify.py` §g; `.github/workflows/{ci,extract,live-smoke}.yml`; CI job named `CI` | ✅ PASS |
| 9 | Live smoke extraction matches official page | Run Live Smoke workflow; compare teams/values to page | `.github/workflows/live-smoke.yml` (not yet executed) | ⛔ BLOCKED-sandbox — run on GitHub runners |
| 10 | Independent verification passes | Run the QA harness | `qa/verify.py`: **9 pass · 0 fail · 0 blocked** (offline sections) | ✅ PASS |
| 11 | No secrets in code, logs, fixtures, artifacts | Regex secret sweep + log-hygiene test | `qa/verify.py` §b (0 hits); `test_http_challenges.py::test_no_secrets_in_logs` | ✅ PASS |
| 12 | Rollback and disable procedures documented and tested | Exercise LKG rollback + disable switch | `docs/runbook.md`; `qa/verify.py` §f (LKG restore), §h (doc commands parse) | ✅ PASS |

## Summary

- **9 of 12 gates PASS** on offline evidence.
- **1 PARTIAL** (gate 2 — parameter mapping is unit-verified; live echo pending).
- **2 BLOCKED-sandbox** (gates 1 & 9 — the live page↔endpoint correspondence and
  the live smoke match), closable only by running **Live Smoke** where
  `*.wnba.com` is reachable.

## Closing the blocked gates

1. Trigger the **Live Smoke** workflow (`workflow_dispatch`).
2. Review `live-smoke-artifacts` → `live_capture_<date>.json` (per-claim report).
3. Flip confirmed claims in `docs/source-contract.md` to live-verified; update
   gates 1, 2, 9 here with the run URL as evidence.
4. Complete the robots.txt/ToS review in `docs/compliance.md` before enabling the
   daily schedule.
