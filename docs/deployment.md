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

## Architecture: Railway serves, GitHub Actions scrapes

One responsibility per platform, so nothing has to be wired up twice:

| Platform | Job | How |
|---|---|---|
| **Railway** | Serve `offdutylocks.com` | `railway.toml` → gunicorn (the web app) + Postgres in the same project |
| **GitHub Actions** | Scrape + publish the data | `.github/workflows/scrape.yml` → `wnba-pipeline` writes to Postgres via `DATABASE_URL` |

**Why this split:** `railway.toml` is the DEFAULT config every Railway service
reads, so making that default the *web server* means the service that owns the
domain serves HTTP no matter how it is wired up — a forgotten override fails
safe (serves the site) instead of running a non-HTTP job and returning the
`x-railway-fallback` 502. And team-stats can't run on Railway anyway
(stats.wnba.com blocks datacenter IPs), so keeping all scraping in Actions puts
both feeds in one place.

### Postgres serving layer (Railway)

1. **Add Postgres**: project → New → Database → PostgreSQL.
2. **Reference the URL** into the web service:
   `DATABASE_URL = ${{Postgres.DATABASE_URL}}`. The schema is created
   automatically on first publish (or run `wnba-pipeline db-init`).
3. **Give GitHub the PUBLIC URL**: repo → Settings → Secrets and variables →
   Actions → `DATABASE_URL` = the Railway **public** connection string
   (`postgresql://…@<name>.proxy.rlwy.net:<port>/railway`). The runner is
   outside Railway's private network, so the internal `*.railway.internal` host
   does not resolve there.

`DATABASE_URL` is the only secret. It is injected by Railway (internal
networking) for the web service and stored as a GitHub Actions secret (public
URL) for the scrapers. No database credentials are stored in the repository.

### Scrapers (GitHub Actions — `scrape.yml`)

- **betting**: runs every 30 min through the season's pregame/in-play windows,
  publishing `betting_games` (VSIN splits + Circa sharp line + Action Network
  lines). VSIN and Action Network are datacenter-reachable, so this works from a
  GitHub-hosted runner.
- **team-stats**: stats.wnba.com blocks datacenter IPs (GitHub-hosted runners
  included), so a live scrape can't run here yet. Run the workflow manually with
  **Seed team_stats from fixture = true** to (re)populate `team_stats` from the
  committed fixture; for live YTD/Last-7 use a residential or self-hosted runner:
  `wnba-pipeline run-team-stats --publish --database-url "<public url>"`.
- Pause everything with repository variable `PIPELINE_ENABLED=false`.

## Web service & custom domain (offdutylocks.com)

The public site is the Railway service that reads `railway.toml` (its default
config), so it starts gunicorn automatically:

| Service | Config | Start command | Networking |
|---|---|---|---|
| Web (site) | `railway.toml` (default) | `gunicorn --config /app/gunicorn.conf.py wnba_pipeline.web:app` | public + domain |

The bind port is read from `os.environ["PORT"]` inside `gunicorn.conf.py`
(default 8080) — **not** from a `$PORT` token in the start command. Railway runs
the start command without shell interpolation, so a literal `$PORT` reaches
gunicorn unexpanded and fails with `'$PORT' is not a valid port number`; reading
the env var in Python avoids that. `railway.web.json` carries the identical
command and is kept as an alias for any service explicitly pinned to it.

The bind *host* is `[::]` (IPv6) wherever the platform supports it, not
`0.0.0.0`. Railway's private network — which the public edge routes over — is
IPv6-only, so an IPv4-only listener is reachable by the healthcheck probe (it
arrives over IPv4 from 100.64.0.0/10) but *not* by the edge. The symptom of
getting this wrong is distinctive: the deploy log shows `[1/1] Healthcheck
succeeded!` while the public domain still returns 502 with an
`x-railway-fallback: true` response header.

`gunicorn.conf.py` probes the socket at startup rather than hardcoding `[::]`,
because gunicorn never sets `IPV6_V6ONLY` on the listener it creates — `[::]`
is dual-stack (IPv4 + IPv6 on one socket) only where `net.ipv6.bindv6only`
defaults to 0, as it does on Linux. The bind degrades safely: no usable IPv6 →
`0.0.0.0:PORT`; IPv6 with `bindv6only=1` → both `0.0.0.0:PORT` and `[::]:PORT`;
otherwise `[::]:PORT` alone. So an IPv4-only environment (a CI container, a
local box) still serves normally.

**Add / fix the web service:**

1. Railway → New → GitHub Repo → the `off-duty-locks` repo (or reuse the
   existing service that owns the domain).
2. Leave **Settings → Config-as-code** on the default (`railway.toml`) — it now
   *is* the web config. (If a service was overridden to run `wnba-pipeline
   betting`, clear that Custom Start Command so it falls back to the default.)
3. Add the variable reference `DATABASE_URL = ${{Postgres.DATABASE_URL}}`.
4. Deploy, then confirm `/healthz` returns 200 and `/` renders.

**Point the domain (Railway → Cloudflare):**

1. Web service → **Settings → Networking → Custom Domain** → add
   `offdutylocks.com` (and `www` separately). Railway returns a CNAME target
   like `xxxx.up.railway.app`.
2. Cloudflare → **DNS**: `CNAME @` → the Railway target, and `CNAME www` → the
   same target (the apex works via Cloudflare's CNAME flattening).
3. Start **DNS-only** (grey cloud) so Railway can issue its TLS certificate;
   once the domain shows **Active** in Railway, turn on the Cloudflare proxy
   (orange cloud) with **SSL/TLS → Full (strict)**. Enabling the proxy before
   the certificate is issued is the usual cause of failures.

The web app is read-only (SELECT only) and holds no secrets beyond
`DATABASE_URL`; it renders a friendly empty state when the database has no data
yet, so it is safe to expose before the first data run.

## Rolling back a deploy

- **Code:** `git revert <commit>` and let CI re-run.
- **A bad accepted snapshot:** see *LKG rollback* in `docs/runbook.md`, or
  `git revert` the extraction commit (data is version-controlled).
- **Stop all collection immediately:** set `PIPELINE_ENABLED=false` (gates both
  **Extract** and **Scrape + Publish**) or disable the workflows in the Actions
  tab (`docs/runbook.md`, *Disable / re-enable*).

## Running everything offline

The entire pipeline runs without network using recorded fixtures — this is how
CI validates it and how you reproduce issues locally:

```bash
wnba-pipeline run \
  --fixture fixtures/sanitized/leaguedashteamstats_2026_lastn7.json \
  --data-root ./data
wnba-pipeline status --data-root ./data
```
