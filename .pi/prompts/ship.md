---
description: Release gate sequence — verify CI, merge, confirm Railway deploy
---

Ship the change described below through the release gates, in order:

1. **CI green**: the PR's required check (job literally named "CI") must pass —
   `gh pr checks <PR#>`. No merge on red or pending.
2. **Harness + QA clean locally**: `python -m pytest -q`,
   `python3 qa/verify.py --repo-root .`, `python3 qa/pi_harness_audit.py --repo-root .`.
3. **Merge**: squash-merge via `gh pr merge <PR#> --squash`. Merge to main deploys
   the Railway web service.
4. **Deploy verification**: run the deploy-verify workflow
   (`gh workflow run deploy-verify.yml`) and/or `python3 scripts/railway_ops.py status`;
   confirm the service is healthy before claiming shipped.
5. Report each gate with real command output. A skipped gate is reported as skipped,
   never as passed.

Ship: $ARGUMENTS
