# AGENTS.md — operating context and repo law

WNBA team-statistics extraction pipeline + read-only web service
(offdutylocks.com). Python 3.11, requests, Flask/gunicorn, Postgres serving
layer, Railway hosting, GitHub Actions automation. Harness map: HARNESS.md.
Model policy: LLM.md. Skills: SKILLS.md. Brand: design-system/off-duty-locks/MASTER.md.

## Repo law (enforced mechanically by .pi/extensions/odl-guard.ts where possible)

1. **Compliance beats uptime.** Never add retry aggression, header spoofing, or
   bot evasion. Every live-source request is budgeted (docs/compliance.md); the
   Live Smoke workflow is the only sanctioned way to touch *.wnba.com. Ad-hoc
   curl/wget/python requests to wnba.com are blocked at the tool layer.
2. **LKG law.** Failed runs never touch last-known-good. Never hand-edit
   `data/`; quarantine is append-only evidence.
3. **Honest gates.** A BLOCKED gate is never reported as PASS
   (qa/acceptance-gates.md). Verification output is real output.
4. **Fixture law.** `_provenance` required, `synthetic` flag honest, no secrets
   ever (fixtures/README.md).
5. **No destructive git.** No force push (any variant), reset --hard, clean -f,
   checkout/restore ., or --no-verify commits.
6. **Deploy law.** Merge to `main` deploys the Railway web service. Schema
   (`src/wnba_pipeline/schema.sql`) changes hit live Postgres — plan rollout
   order first (docs/deployment.md).
7. **Secrets.** Never commit secrets; never write `.env*` files.
8. **Brand law.** All UI obeys design-system/off-duty-locks/MASTER.md — one
   signal-orange accent #FF5C1C, dark graphite surfaces, responsible-gaming
   copy on every product surface.
9. **QM law.** This repo is a QM skill pack (`qm.pack.json`, verified in CI)
   and its laws bind inside QM scope sandboxes. Never wire any API key into
   QM's keychain, org config, or harness credentials; QM deployment is
   owner-gated (docs/qm-harness.md).

## Verification (before claiming anything done)

- `python -m pytest -q` — full offline suite, must be green
- `python3 qa/verify.py --repo-root .` — independent QA harness
- `python3 qa/pi_harness_audit.py --repo-root .` — harness structural audit

## Models and auth

Current generation only: claude-fable-5 (default), claude-opus-5, gpt-5.6-sol.
Subscription-first auth; **zero API-billed automation in this repo** — no CI
step or workflow may call a model. Details: LLM.md.
