# Deployment Guide — WNBA Team-Statistics Pipeline

## Pattern selection (and why)

The repository was cleared to an empty tree before this pipeline was built, so
there was **no pre-existing job runner, database, or Railway configuration** to
reuse. The only automation pattern present in the repository's history was
**GitHub Actions**. Following the "smallest compatible pattern" rule:

- **Scheduler:** GitHub Actions `schedule` (cron). No second scheduling system
  is introduced — there is nothing to reuse and Actions already runs CI here.
- **Storage:** file-based, committed to the repository under `data/`. There is
  **no production database**, so there are no production migrations to authorize
  and no external datastore to provision. The committed files *are* the store;
  their history is the audit trail.

This keeps the whole system inspectable in git, trivially rolled back with
`git revert`, and free of external infrastructure. If a database is introduced
later, add a storage adapter behind the existing `Store` interface rather than a
new scheduler.

## Prerequisites

- Python 3.11+
- A GitHub repository with Actions enabled.
- **A non-datacenter egress to `stats.wnba.com` for any live run.** The stats
  edge (Akamai) blocks cloud/datacenter IPs, so **GitHub-hosted runners cannot
  reach it** (nor can the dev sandbox). Live verification and scheduled
  collection must run from a residential IP or a **self-hosted runner** on an
  allowed network — see `docs/runbook.md` → *Source reachability
  (datacenter-IP blocking)*. Offline CI (unit + fixture e2e) needs no network.

## Deploy from scratch

1. **Install and test locally (offline):**
   ```bash
   python -m pip install -e ".[dev]"
   pytest -q
   python3 qa/verify.py --repo-root .
   ```
2. **Confirm CI is the required check.** `.github/workflows/ci.yml` defines a job
   whose check name is exactly **`CI`**. In branch protection for `main`, require
   the `CI` status check and at least one approving review (already configured on
   this repo).
3. **Enable the scheduler.** `.github/workflows/extract.yml` runs daily at
   10:30 UTC, May–October, and on demand via *Run workflow*. It needs:
   - `permissions: contents: write` (commit accepted data) and `issues: write`
     (open alerts) — already declared in the workflow.
   - Repository variable `PIPELINE_ENABLED` — unset or `true` to run, `false` to
     pause. (Settings → Secrets and variables → Actions → Variables.)
4. **First real extraction.** Trigger **Extract** manually (`workflow_dispatch`).
   Confirm the run summary shows `status: SUCCESS`, `actualTeamCount ==
   expectedTeamCount`, and that a commit under `data/` was pushed.

No secrets are required: the pipeline uses only the public endpoint with public
headers. Do not add cookies, tokens, or API keys.

## Live verification (required before trusting live data)

The source contract (`docs/source-contract.md`) is written from documented
platform knowledge and is **pending live verification** because `*.wnba.com` is
blocked in the build sandbox. To confirm it:

1. Run the **Live Smoke** workflow (`workflow_dispatch`). It executes
   `scripts/capture_live_contract.py` (conservative: ≤5 requests, ≥3s spacing,
   honors `Retry-After`, aborts on 403) and one live extraction, then uploads
   sanitized captures as artifacts. Nothing is committed.
2. Download the `live-smoke-artifacts`, review `live_capture_<date>.json`'s
   per-claim report, and update `docs/source-contract.md` (flip confirmed claims
   to live-verified) and `qa/acceptance-gates.md`.
3. Complete the robots/ToS review listed in `docs/compliance.md` before enabling
   the daily schedule for ongoing collection.

## Configuration surface

| Setting | Where | Default |
|---|---|---|
| Season / season type / last-N / per-mode | CLI flags, `extract.yml` inputs | 2026 / Regular Season / 7 / PerGame |
| Data root | `--data-root` | `./data` |
| Freshness window | `--max-age-hours` | 36 |
| Schedule | `extract.yml` cron | `30 10 * 5-10 *` |
| Enable switch | repo variable `PIPELINE_ENABLED` | enabled |
| Retention | `storage.Store.prune` args | 50/50/50/200 |

## Rolling back a deploy

- **Code:** `git revert <commit>` and let CI re-run.
- **A bad accepted snapshot:** see *LKG rollback* in `docs/runbook.md`, or
  `git revert` the extraction commit (data is version-controlled).
- **Stop all collection immediately:** set `PIPELINE_ENABLED=false` or disable
  the **Extract** workflow (`docs/runbook.md`, *Disable / re-enable*).

## Running everything offline

The entire pipeline runs without network using recorded fixtures — this is how
CI validates it and how you reproduce issues locally:

```bash
wnba-pipeline run \
  --fixture fixtures/sanitized/leaguedashteamstats_2026_lastn7.json \
  --data-root ./data
wnba-pipeline status --data-root ./data
```
