# Deployment Guide — WNBA Team-Statistics Pipeline

## Pattern selection (and why)

The repository was cleared to an empty tree before this pipeline was built, so
there was **no pre-existing job runner, database, or Railway configuration** to
reuse. The only automation pattern present in the repository's history was
**GitHub Actions**. Following the "smallest compatible pattern" rule:

- **Scheduler:** GitHub Actions `schedule` (cron) for the daily jobs (extract,
  audit), where best-effort delivery is acceptable. The 30-minute **betting**
  scrape outgrew that: Actions delivered its ticks 30 minutes to 3.5 hours
  apart, so it runs as a dedicated **Railway cron service** instead
  (owner-authorized, 2026-08-04 — see *Architecture* below).
- **Storage:** file-based, committed to the repository under `data/`, plus a
  **Railway Postgres serving layer** the site reads (added later — see
  *Postgres serving layer* below). The committed files remain the source of
  truth and audit trail; Postgres is the read model.

This keeps the extraction pipeline inspectable in git and trivially rolled back
with `git revert`. The serving layer is deliberately thin: its schema is
additive and self-applying — see *Schema changes & rollout order* below.

## Prerequisites

- Python 3.11+
- A GitHub repository with Actions enabled.
- **No special egress.** Team stats come from ESPN's public APIs (owner
  decision 2026-08-04), which serve datacenter IPs — GitHub-hosted runners
  work as-is. The old requirement for a residential/self-hosted runner is
  **retired**: it existed for stats.wnba.com, which proved unreachable for
  every unattended client — datacenter runners (14 days of `read_timeout`),
  residential curl, the pipeline's own client run residentially, and a real
  Chromium session all stall (evidence in `docs/compliance.md` section 1), so
  a runner move would not have cured it. Offline CI (unit + fixture e2e)
  needs no network.

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
   10:30 UTC, May–October, and on demand via *Run workflow*. It extracts BOTH
   splits (`last7` + `ytd`) from ESPN and publishes them to Postgres, gated by
   full-scope `validate-data`. It needs:
   - the `DATABASE_URL` secret (the PUBLIC Railway Postgres URL — same secret
     `scrape.yml` uses) and `permissions: issues: write` (open alerts) —
     already declared in the workflow.
   - Repository variable `PIPELINE_ENABLED` — unset or `true` to run, `false` to
     pause. (Settings → Secrets and variables → Actions → Variables.)
4. **First real extraction.** Trigger **Extract** manually (`workflow_dispatch`).
   Confirm the run summary shows both splits with `status: SUCCESS`,
   `actualTeamCount == expectedTeamCount`, and a green validate-data section.

No secrets are required: the pipeline uses only the public endpoint with public
headers. Do not add cookies, tokens, or API keys.

## Live verification (stats.wnba.com — PARKED)

The stats.wnba.com source contract (`docs/source-contract.md`) and the **Live
Smoke** workflow describe the parked source. Live verification there is moot:
the host is unreachable for every unattended client (4-way evidence in
`docs/compliance.md` section 1), which is why team stats come from ESPN. The
ESPN contract was verified live on 2026-08-04 while recording
`fixtures/espn/` — endpoint shapes, scales, and definitional decisions are
documented in `src/wnba_pipeline/espn.py` and asserted by `tests/test_espn.py`
against those real captures.

## Configuration surface

| Setting | Where | Default |
|---|---|---|
| Season / season type / last-N / per-mode | CLI flags, `extract.yml` inputs | 2026 / Regular Season / 7 / PerGame |
| Data root | `--data-root` | `./data` |
| Freshness window | `--max-age-hours` | 36 |
| Schedule (team stats) | `extract.yml` cron | `30 10 * 5-10 *` (daily, May–October; ESPN source — restored 2026-08-04) |
| Schedule (betting) | `railway.scrape.json` `cronSchedule` | `*/30 * * * *` (year-round; May–October gate in `betting-cron`) |
| Enable switch | repo variable `PIPELINE_ENABLED` (Actions) + service variable on the Railway cron | enabled |
| Retention | `storage.Store.prune` args | 50/50/50/200 |

## Architecture: Railway runs the site AND the betting cron; Actions runs the daily jobs

Two Railway services plus Postgres in one project, with GitHub Actions kept
for the jobs where best-effort scheduling is good enough:

| Platform | Job | How |
|---|---|---|
| **Railway (web service)** | Serve `offdutylocks.com` | `railway.toml` (the default config) → gunicorn (the web app) |
| **Railway (cron service)** | Betting scrape every 30 min | explicit `railway.scrape.json` → `wnba-pipeline betting-cron`, exact `:00`/`:30` UTC ticks |
| **GitHub Actions** | Daily ESPN extract, daily full-scope audit, CI, manual backup publish | `extract.yml`, `data-audit.yml`, `ci.yml`, `scrape.yml` (`workflow_dispatch` only) |

**Why the betting scrape moved back to Railway (owner-authorized, 2026-08-04):**
GitHub delivers `schedule` triggers best-effort — measured gaps between the
30-minute betting ticks ran 30 minutes to 3.5 hours, which starves the
line-history snapshots and the dashboard's 30-minute refresh. Railway cron
starts the container at each tick on the tick. Railway is the **single**
scheduler for betting: `scrape.yml` keeps no `schedule:` trigger (two
schedulers would double-write the same rows; the COALESCE upsert makes that
race benign, but one scheduler is the design) and stays available as the
manual backup publish path.

**The fail-safe asymmetry (commit `ab09843` — do not undo it):** the ORIGINAL
Railway betting cron died because `railway.toml`'s DEFAULT start command
launched the cron, the web service inherited it, nothing bound `$PORT`, and
the whole site 502'd. `railway.toml` — the default config **every** Railway
service reads — therefore serves the site, so a service that forgets its
config override fails *safe* (serves HTTP, harmless) instead of running a
non-HTTP job and returning the `x-railway-fallback` 502. The cron runs only
in a service **explicitly pinned** to `railway.scrape.json` under Settings →
Config-as-code. Never point the default at a cron.

### The betting cron service (Railway — `railway.scrape.json`)

- **Schedule:** `*/30 * * * *`, year-round. The **season gate lives in code**:
  outside May–October (UTC) `wnba-pipeline betting-cron` logs one
  `offseason_skip` JSON line and exits 0 without touching the network or the
  database, so nobody edits the cron expression twice a year.
- **One command, one exit code:** `wnba-pipeline betting-cron` runs the
  betting publish (VSIN + Action Network → `betting_games`) and then the
  betting-scope `validate-data` gate — the same two gates `scrape.yml` ran as
  separate steps — and exits nonzero if either fails, so Railway's run
  history is honest.
- **Cron semantics:** at each tick Railway starts the container running the
  start command; the process **must exit** (it does). `restartPolicyType` is
  `NEVER` — a failed run must show up red, not loop. No healthcheck: a cron
  container serves nothing.
- **Expected environment:**
  - `DATABASE_URL = ${{Postgres.DATABASE_URL}}` — the same **internal**
    reference variable the web service uses. The cron runs inside Railway's
    private network, so the public-proxy guard that `scrape.yml` needs does
    not apply here.
  - `PIPELINE_ENABLED` — the kill switch, mirroring the Actions repository
    variable: set `false` on the service to make every tick log
    `pipeline_disabled` and exit 0.

### Monitoring the cron

Railway's run history for the cron service shows a red run on any failed tick
(nonzero exit). If ticks stop happening *silently*, the daily full-scope
audit (`data-audit.yml`) catches it: `betting.fetch_stale` FAILs when the
upcoming slate's newest `fetched_at_utc` is older than 6 hours, which opens a
`pipeline-alert` issue. `/api/status` (merged with the real-time status
layer, PR #41) shows the same freshness live on the site.

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
networking) for the web service **and** the betting cron service — both use
the same `${{Postgres.DATABASE_URL}}` reference variable — and stored as a
GitHub Actions secret (public URL) for the Actions jobs. No database
credentials are stored in the repository.

### Schema changes & rollout order

The serving schema (`src/wnba_pipeline/schema.sql`) is self-applying: every
publisher calls `bootstrap_schema` before writing (so each in-season Railway
cron tick re-applies it), the manual **Scrape + Publish** dispatch runs
`wnba-pipeline db-init` as its first step, and `db-init` can be run by hand.
Every statement is additive and idempotent (`CREATE TABLE IF NOT EXISTS` /
`CREATE INDEX IF NOT EXISTS`), so re-applying on every tick is safe and there
is no separate migration system.

Rollout order for a schema change (AGENTS.md law 6):

1. **Merge** the `schema.sql` change — additive only, guarded by
   `IF NOT EXISTS`; never a destructive rewrite of live tables.
2. **Apply** — the next publishing tick (in season, the Railway betting cron)
   bootstraps the schema against live Postgres. To apply immediately, dispatch
   **Scrape + Publish** manually or run
   `wnba-pipeline db-init --database-url "<public url>"` yourself.
3. **Then rely on it.** Merge to `main` also deploys the web service, so code
   that reads a new column can go live before a tick has created it — until
   `db-init` runs, those queries fail and the site serves its empty
   state / 503s. Land the schema change first (or dispatch `db-init` right
   after merging), then ship the code that needs it.

### Scrapers

- **betting**: the Railway cron service (see *The betting cron service*
  above) — every 30 minutes on the tick, year-round schedule with the
  May–October gate in code — publishing `betting_games` (VSIN splits + Circa
  sharp line + Action Network lines). Manual backup: dispatch **Scrape +
  Publish** (`scrape.yml`), which runs the same publish + betting-scope gate
  from a GitHub-hosted runner over the public proxy URL.
- **team-stats**: runs daily in `extract.yml` (10:30 UTC, May–October) from
  ESPN's public APIs — datacenter-reachable, so GitHub-hosted runners work.
  `wnba-pipeline espn-team-stats` extracts BOTH real splits (`last7` from
  per-event boxes, `ytd` from season statistics + record) and publishes them,
  gated by full-scope `validate-data`. Manual fallback:
  `wnba-pipeline espn-team-stats --database-url "<public url>"` (publishing is
  the default; `--no-publish` skips it). The fixture-seed path in `scrape.yml`
  remains a manual break-glass only.
- Pause everything: repository variable `PIPELINE_ENABLED=false` (Actions
  jobs) **and** service variable `PIPELINE_ENABLED=false` on the Railway cron
  service (its ticks then log `pipeline_disabled` and exit 0).

## Web service & custom domain (offdutylocks.com)

The public site is the Railway service that reads `railway.toml` (its default
config), so it starts gunicorn automatically:

| Service | Config | Start command | Networking |
|---|---|---|---|
| Web (site) | `railway.toml` (default) | `gunicorn --config /app/gunicorn.conf.py wnba_pipeline.web:app` | public + domain |

The bind port is read from `os.environ["PORT"]` inside `gunicorn.conf.py`
(default 3000) — **not** from a `$PORT` token in the start command. Railway runs
the start command without shell interpolation, so a literal `$PORT` reaches
gunicorn unexpanded and fails with `'$PORT' is not a valid port number`; reading
the env var in Python avoids that. `railway.web.json` carries the identical
command and is kept as an alias for any service explicitly pinned to it.

> **The port must agree in three places:** the fallback in `gunicorn.conf.py`,
> `EXPOSE` in the `Dockerfile`, and the **target port** on the domain under
> Railway → Settings → Networking. It is currently **3000** in all three.
>
> A mismatch fails in a uniquely misleading way, because Railway uses two
> independent mechanisms. The **healthcheck** auto-detects whatever port the
> container is listening on, so it passes no matter what. The **domain** routes
> only to its configured target port. Listening on 8080 while the domain targets
> 3000 therefore produces a deploy log that reads:
>
> ```
> Listening at: http://[::]:8080
> "GET /healthz HTTP/1.1" 200 ... "RailwayHealthCheck/1.0"
> [1/1] Healthcheck succeeded!
> ```
>
> while every public request returns 502 with `x-railway-fallback: true` — the
> edge has nothing listening at the port it was told to route to. If you change
> the target port in the dashboard, change the other two to match, or set a
> `PORT` service variable (which overrides the `gunicorn.conf.py` default).

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
- **A bad accepted snapshot:** see *LKG rollback* in `docs/runbook.md`. Bad
  published team-stats rows are corrected by the next successful `extract`
  run (upsert per team) or removed with `wnba-pipeline repair-data` when a
  split is provably duplicated.
- **Stop all collection immediately:** set repository variable
  `PIPELINE_ENABLED=false` (gates **Extract** and the manual **Scrape +
  Publish**) *and* service variable `PIPELINE_ENABLED=false` on the Railway
  betting cron service (its ticks log `pipeline_disabled` and exit 0), or
  disable the workflows in the Actions tab (`docs/runbook.md`,
  *Disable / re-enable*).

## Running everything offline

The entire pipeline runs without network using recorded fixtures — this is how
CI validates it and how you reproduce issues locally:

```bash
wnba-pipeline run \
  --fixture fixtures/sanitized/leaguedashteamstats_2026_lastn7.json \
  --data-root ./data
wnba-pipeline status --data-root ./data
```
