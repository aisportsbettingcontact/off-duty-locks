---
name: railway-ops
description: Use when deploying, debugging, or checking the Railway web service (offdutylocks.com) — service topology, config files, ops script, and deploy verification
---

# Railway operations

## Topology

One Railway web service runs the Flask read-only app (`src/wnba_pipeline/web.py`)
behind gunicorn (`gunicorn.conf.py`), serving team stats + betting data from
Postgres. Merge to `main` deploys it.

## Config files

- `railway.toml` — service build/deploy config (Dockerfile build)
- `railway.web.json` — web-service settings
- `Dockerfile` + `.dockerignore` — image contents; docs/tests/qa/scripts and all
  agent-harness files (.pi, context-file suite, design-system) are excluded, so
  harness changes never alter the image
- `gunicorn.conf.py` — WSGI server tuning

## Operations

- Status / logs / ops: `python3 scripts/railway_ops.py --help` for the supported
  subcommands, or the `railway-ops.yml` workflow for runner-side execution.
- Deploy verification: `.github/workflows/deploy-verify.yml`
  (`gh workflow run deploy-verify.yml`) checks the live service after a deploy.
- Never claim a deploy healthy without real evidence: deploy-verify output or a
  live status/log check.

## Laws

- Schema changes (`src/wnba_pipeline/schema.sql`) hit the live Postgres serving
  layer — plan rollout order before merging (docs/deployment.md).
- The pipeline's scheduled extraction workflows are separate from the web
  service; do not restart or redeploy them to "fix" web issues.
