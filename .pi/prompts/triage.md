---
description: Runbook-driven triage of a pipeline run manifest or pipeline-alert issue
---

Triage the failure described below using docs/runbook.md as the authority.

1. Identify the manifest: `data/manifests/<run_id>.json`, the Actions run summary,
   or the open `pipeline-alert` issue.
2. Map `status` + `failureReason` to the runbook table (exit codes: 0 SUCCESS /
   SUCCESS_UNCHANGED, 2 CONFIG_ERROR, 3 UPSTREAM_UNAVAILABLE, 4 VALIDATION_FAILED,
   5 LOCK_HELD, 6 STORAGE_ERROR, 7 INTERNAL_ERROR).
3. Confirm LKG state via `freshnessState` — a failed candidate never explains a
   changed LKG; if LKG changed on a failed run, that is a severity-1 bug.
4. Follow the runbook action for that failureReason. For `http_403_forbidden` or
   `http_429_rate_limited`: do NOT retry aggressively and do NOT add evasion —
   compliance law (docs/compliance.md) wins over uptime.
5. Report: root cause, evidence (manifest fields, log lines), action taken or
   recommended, and whether the pipeline-alert issue can be closed.

Failure to triage: $ARGUMENTS
