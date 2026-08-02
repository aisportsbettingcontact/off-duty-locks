# Off Duty Locks — QM Harness Integration + pi↔QM Intertwine (Design)

**Date:** 2026-08-02
**Status:** Approved (Approach A, repo-side wiring; user-approved in brainstorming)
**Origin:** Mirror of dime-ai's QM integration (qm.pack.json contract +
qm-pack-verify + references/qm-harness.md), adapted to this repo's pi harness
(PR #25) and Python toolchain. QM source of truth: yc-software/qm (reference
clone `~/src/qm`), glob/ingest semantics read directly from
`src/skills/ingest.ts` (`planIngest`, `globToRegExp`/`matchesAny`,
`normalizeSkill`).

## Goal

Wire off-duty-locks into QM (the org-level multiplayer agent harness — Slack +
web, drives pi/Claude Code/OpenCode/Codex over Postgres) at the same depth as
the pi foundation, and intertwine the two harnesses: **one skill corpus, two
consumers**. pi serves `.pi/skills/` through its resource loader; QM imports
the same directory as a git skill pack. A deterministic verifier proves both
consumers always see an identical, collision-free, well-formed corpus.

**Non-goals:** deploying a QM org instance (owner-gated: hosting target
Docker/Fly/AWS — Railway is NOT a supported QM target — org slug, sign-in
email, model-provider API key = billing), Slack app install, forking qm.
Deployment is documented as the owner-gated next step, never executed by
agents.

## 1. Pack contract — `qm.pack.json` (repo root)

```json
{
  "$comment": "Canonical QM skill-pack configuration for this repository (docs/qm-harness.md). QM admins import the repo URL with exactly this config; qa/qm_pack_verify.py validates the corpus against it — using QM's own glob semantics (patterns match the skill DIRECTORY, not the SKILL.md path; ported from qm src/skills/ingest.ts planIngest) — inside qa/pi_harness_audit.py and CI.",
  "url": "https://github.com/aisportsbettingcontact/off-duty-locks",
  "config": {
    "skillGlobs": [".pi/skills/**"]
  }
}
```

No excludes — the whole repo-local corpus ships. The repo is public, so QM
pack import needs no repo credential (unlike dime-ai's private-repo pack).
`qm.pack.json` is added to `.dockerignore` (production image unchanged).

## 2. Verifier — `qa/qm_pack_verify.py` (TDD)

Python port of QM's ingest semantics, kept in lockstep with
`yc-software/qm src/skills/ingest.ts`:

- **Glob semantics ported verbatim:** `**` → `.*`, `*` → `[^/]*`, other chars
  regex-escaped; the pattern matches the **skill directory path** (e.g.
  `.pi/skills/pipeline-verify`), not the SKILL.md file path.
- **Checks (exposed as `audit(root: Path) -> list[str]`, plus a CLI
  `python3 qa/qm_pack_verify.py --repo-root .` exiting 0/1):**
  1. `qm.pack.json` parses; `url` points at this repo; `skillGlobs` is a
     non-empty list of strings.
  2. Every SKILL.md selected by the pack globs carries parseable frontmatter
     with non-empty `name` and `description` — QM's normalize step silently
     skips malformed skills at ingest ("malformed" count), which is exactly
     the quiet loss this catches ahead of time.
  3. No two selected skills share a name (QM flags `collision` at ingest; we
     fail first so the pack always imports whole).
  4. **Intertwine check:** the set of skill directories selected by the pack
     config equals the census of `.pi/skills/*/SKILL.md` — pi's loader and
     QM's pack can never silently drift apart.
- `qa/pi_harness_audit.py` gains a **qm-pack layer**: it imports
  `qm_pack_verify` by path and appends its errors to the audit result. The
  existing CI step, `/verify` template, and every future PR therefore
  validate both harnesses with zero new workflow surface.

## 3. qm-harness skill + runbook

**`.pi/skills/qm-harness/SKILL.md`** (joins both corpora — pack-selected and
pi-loaded): use when working with or from QM — pack import steps for admins,
what QM ingest does (glob → normalize → collision/scope checks), and sandbox
law: repo work inside a QM scope happens by cloning this repo into the
scope's durable sandbox, where the full in-repo wiring applies — AGENTS.md
context, trust flow (`/trust`, `-a` headless) activates odl-guard, the odl
theme, prompts, and auto-installs pi packages. Compliance law (no ad-hoc
*.wnba.com requests) binds inside QM sandboxes exactly as locally.

**`docs/qm-harness.md`** (runbook, mirrors dime's references/qm-harness.md):
what QM is, the two integration seams (pack + sandbox), the pack contract and
how to import it, laws that carry unchanged (current-gen models only;
**never wire any API key into QM's keychain, org config, or harness
credentials** — QM model spend is a separate owner-approved decision; zero
API-billed automation in this repo), security posture (Auto or stricter),
and the owner-gated deployment path (`qm init . --org <slug> --target
<fly|aws|docker>`; Railway not supported; decisions enumerated, not made).

## 4. Context files, law, delivery

- `HARNESS.md`: +QM row (context: pack-imported skills + sandbox clone;
  config: `qm.pack.json`, `docs/qm-harness.md`) and the two-seam note.
- `SKILLS.md`: +"QM pack contract" section — one corpus, two consumers;
  `qm.pack.json` is the contract, `qa/qm_pack_verify.py` the enforcement;
  qm-harness added to the repo-local skill table.
- `AGENTS.md`: repo law gains one line — never wire API keys into QM
  keychain/org config; QM sandboxes obey all repo law. Verification section
  unchanged (`pi_harness_audit` now covers the pack).
- `CLAUDE.md`: authority table +2 rows (QM integration → `docs/qm-harness.md`;
  pack contract → `qm.pack.json`).
- `LLM.md`: API-credit law gains the QM clause (keychain prohibition).
- `.dockerignore`: +`qm.pack.json`.

**Delivery:** branch `feat/qm-harness-integration`, TDD for the verifier,
single PR to `main`, CI green (existing "CI" job; audit step now includes the
qm-pack layer), production-behavior neutral (no `src/`, Dockerfile, or
Railway config changes). Verification before PR: `python -m pytest -q`,
`qa/verify.py`, `qa/pi_harness_audit.py` (now including qm-pack), plus the
pi loader census still ALL PASS (the new qm-harness skill must load in pi
too — corpus grows 3 → 4 repo-local skills).

## Follow-ups (out of scope)

1. Owner-gated QM deployment (hosting/billing/sign-in decisions).
2. Off Duty Locks dashboard build (separate spec; brand law already shipped).
