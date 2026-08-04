# CLAUDE.md

Off Duty Locks — WNBA team-statistics pipeline + read-only web service.
**Repo law lives in AGENTS.md — read it first and follow it.** This file adds
Claude Code-specific pointers only.

| Concern | Authority |
|---|---|
| Repo law (compliance, LKG, gates, fixtures, git, deploy) | `AGENTS.md` |
| Agent harnesses and their wiring | `HARNESS.md` |
| Model policy + auth + API-credit law | `LLM.md` |
| Skill inventory | `SKILLS.md` |
| Brand law (all UI work) | `design-system/off-duty-locks/MASTER.md` |
| pi runbook | `docs/pi-harness.md` |
| QM integration (pack + sandbox seams) | `docs/qm-harness.md` |
| QM pack contract | `qm.pack.json` (verified by `qa/qm_pack_verify.py`) |
| Pipeline runbook / triage | `docs/runbook.md` |
| Acceptance gates | `qa/acceptance-gates.md` |

Repo-local skills (also exposed to pi): `.pi/skills/` — pipeline-verify,
fixture-provenance, railway-ops, qm-harness. Read the relevant one before
pipeline verification, fixture edits, Railway work, or QM integration.

Verification before claiming done: `python -m pytest -q` ·
`python3 qa/verify.py --repo-root .` · `python3 qa/pi_harness_audit.py --repo-root .`
— real output only.

Sports-betting product: the responsible-gaming footer was removed by owner
directive 2026-08-02 (MASTER.md standing copy law); do not re-add it without
owner say-so.
