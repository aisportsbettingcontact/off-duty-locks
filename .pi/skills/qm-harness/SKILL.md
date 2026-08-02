---
name: qm-harness
description: Use when working with or from QM (yc-software/qm) — importing this repo's skill pack, working in a QM scope sandbox, or reasoning about the pi↔QM skill corpus contract
---

# QM harness — how this repo plugs in

QM is the org-level multiplayer agent layer (Slack + web; drives pi, Claude
Code, OpenCode, Codex over Postgres). This repo integrates through two seams.
Full runbook: docs/qm-harness.md.

## Seam 1 — skill pack (one corpus, two consumers)

`qm.pack.json` is the canonical pack config: this repo's URL +
`skillGlobs: [".pi/skills/**"]`. A QM admin imports it under
Skills → packs; the repo is public, so no repo credential is needed. QM scans
for Agent Skills–standard SKILL.md files, matches globs against the skill
DIRECTORY, normalizes frontmatter (non-empty name + description or the skill
is silently skipped as `malformed`), and flags name `collision`s.

`qa/qm_pack_verify.py` enforces all of that offline plus the intertwine law:
the pack's selection must equal the `.pi/skills/*/SKILL.md` census, so pi's
loader and QM's pack can never drift. It runs inside
`qa/pi_harness_audit.py` and CI. After adding/renaming any skill, run:
`python3 qa/pi_harness_audit.py --repo-root .`

## Seam 2 — sandbox (repo work inside a QM scope)

QM harnesses run in isolated sandboxes — they do NOT inherit this checkout.
Clone the repo in the scope's durable sandbox; then the full wiring applies:
AGENTS.md context, `.pi/settings.json`, odl-guard, prompts, odl theme. Treat
it like any new machine: the trust flow (`/trust`, or `-a` headless)
activates project resources and auto-installs the declared pi packages.

## Laws that carry into QM unchanged

- Compliance: no ad-hoc *.wnba.com requests from QM sandboxes; the guard
  blocks them under pi, and the law binds under every other harness.
- Models: current generation only (LLM.md).
- Credits: NEVER wire any API key into QM's keychain, org config, or harness
  credentials. QM model spend is a separate owner-approved decision.
- Security posture: Auto or stricter; QM's command policy complements
  odl-guard, never replaces repo law.
