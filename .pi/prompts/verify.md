---
description: Full offline verification pass — unit suite, QA harness, harness audit
---

Run the complete offline verification for this repo and report real output:

1. `python -m pytest -q` — full unit + integration suite (offline; must be green)
2. `python3 qa/verify.py --repo-root .` — independent QA harness (all offline sections must pass)
3. `python3 qa/pi_harness_audit.py --repo-root .` — pi harness structural audit

If anything fails, stop and triage with the systematic-debugging skill before
touching code. Never report a blocked or skipped check as passed.
