# Off Duty Locks — pi Harness Foundation + Brand Foundation (Design)

**Date:** 2026-08-01
**Status:** Approved (all four sections user-approved in brainstorming)
**Origin:** Port of the Dime AI pi harness foundation (ai-sports-betting-dime-ai
PR #299, `feat/pi-harness-foundation`, merged 2026-08-01) — same architecture,
every law-carrying file rewritten for this repo, and an Off Duty Locks-native
brand foundation replacing Dime's.

## Goal

Give off-duty-locks the same agent-harness foundation dime-ai has — pi CLI
wiring, mechanical repo-law enforcement, a context-file suite for every
harness (pi, Codex, Claude Code), and a locked brand law — without touching
production behavior. The Railway web service, pipeline, and workflows are
unchanged; harness files are excluded from the Docker image.

**Non-goals:** no embedded agent runtime (nothing serves chat here), no
API-billed CI (owner credit policy: zero unattended model calls), no dashboard
UI build (follow-up spec that must obey the brand law shipped here).

## 1. Harness architecture

```
off-duty-locks/
├── .pi/
│   ├── settings.json          # theme "odl"; defaultModel claude-fable-5;
│   │                          # enabledModels: claude-fable-5, claude-opus-5, gpt-5.6-sol;
│   │                          # skills: ["skills"]; prompts: ["prompts"];
│   │                          # packages: git:github.com/badlogic/pi-skills,
│   │                          #           git:github.com/anthropics/skills
│   ├── APPEND_SYSTEM.md       # execution law injected into every pi session (§4)
│   ├── extensions/odl-guard.ts
│   ├── themes/odl.json        # dark terminal theme, signal-orange accent (§3)
│   ├── skills/
│   │   ├── pipeline-verify/SKILL.md      # run pytest + qa/verify.py, read manifests,
│   │   │                                 # interpret exit codes / freshnessState
│   │   ├── fixture-provenance/SKILL.md   # fixture law: _provenance, synthetic flag,
│   │   │                                 # no secrets; regen via qa/gen_adversarial_fixtures.py
│   │   └── railway-ops/SKILL.md          # deploy topology, railway.toml/web.json,
│   │                                     # scripts/railway_ops.py, deploy-verify workflow
│   └── prompts/
│       ├── verify.md          # /verify — full offline verification pass
│       ├── triage.md          # /triage — runbook-driven manifest/failure triage
│       └── ship.md            # /ship — merge + deploy-verify gate sequence
├── AGENTS.md                  # universal harness context: repo laws inline
│                              # (pi/Codex load AGENTS.md INSTEAD of CLAUDE.md)
├── CLAUDE.md                  # Claude Code entry: arsenal map + cross-refs to laws
├── HARNESS.md                 # how to run pi (pi / pi -p / --mode rpc), entry points
├── LLM.md                     # auth + model + API-credit law (§4)
├── SKILLS.md                  # skill inventory; how packages auto-install
├── CODEX.md                   # Codex-specific notes
├── design-system/off-duty-locks/MASTER.md   # brand law (§3)
├── docs/pi-harness.md         # runbook (docs/ is this repo's runbook home)
└── qa/pi_harness_audit.py     # model-free harness audit (§4)
```

Decisions:

- **No `package.json`.** pi is invoked directly; commands documented in
  HARNESS.md. The repo stays pure Python.
- **`.pi/git/` is gitignored** (skill packages auto-install there on trust).
- **`.dockerignore` gains** `.pi/`, the context-file suite, `design-system/`,
  and `docs/superpowers/` so the Railway image is byte-identical in behavior.
- Skill/prompt paths in `settings.json` are repo-relative (`"skills"`,
  `"prompts"`) — no `../` cross-repo references; the harness is fully
  self-contained and portable.

## 2. Guard extension — `.pi/extensions/odl-guard.ts`

Mechanical enforcement at the tool-call layer, active in every pi mode;
in headless (`-p`/json/rpc) blocks are unconditional. Ported structure from
dime-guard; rules rewritten for this repo's laws.

**Blocks (hard):**

| Rule | Rationale |
|---|---|
| Destructive git: force push (any variant incl. `--force-with-lease`, `+refspec`), `reset --hard`, `clean -f*`, `checkout .` / `restore .`, `commit --no-verify` | Same repo law as dime-ai |
| Writes to `.env*` / envrc files | Secrets are managed by hand, never by agents |
| Bash HTTP clients (`curl`, `wget`, `http`/`httpie`, `python -c` requests one-liners) targeting `*.wnba.com` | `docs/compliance.md` budgets every live-source request; agents never probe the source ad hoc. The Live Smoke workflow is the only sanctioned live path |

**Warnings (notify in UI; annotate in headless):**

| Rule | Reminder |
|---|---|
| Writes under `fixtures/` | Provenance law: `_provenance` object required, `synthetic` flag must be honest, no secrets ever (fixtures/README.md) |
| Edits to `src/wnba_pipeline/http_client.py` or `extractor.py` | Retry counts, backoff, spacing, and headers are compliance commitments — never loosen them (docs/compliance.md §2) |
| Writes to `src/wnba_pipeline/schema.sql` | Postgres serving-layer change — needs deliberate rollout against the live DB |

## 3. Brand foundation

Two artifacts: `design-system/off-duty-locks/MASTER.md` (product brand law,
authoritative for all future UI work) and `.pi/themes/odl.json` (the same
identity applied to the pi terminal theme).

Direction source: the approved reference — dark sports-terminal dashboard
(dense data grids, signal-color semantics, one hot accent). Dark-first; a
light mode is not part of this spec.

**Locked tokens (MASTER.md):**

- Surfaces: base `#0B0B0D`, panel `#141417`, hairline borders `#26262B`
- **One accent — signal orange `#FF5C1C`**: active nav, primary buttons,
  selected states, line-move emphasis. No gradients. No second accent.
- Signal semantics: green `#22C55E` sharp-money/positive · blue `#3B82F6`
  model edge · yellow `#EAB308` public-heavy/caution · orange (accent)
  reverse-line-move · red `#EF4444` warning/conflict · gray `#6B7280` neutral
- Rating rings: green ≥ 7.5 · yellow 5.0–7.4 · orange < 5.0
- Typography: **Barlow Condensed 700** (uppercase, tracked) for
  display/headers/nav; **Inter** with `tabular-nums` for body and every data
  grid
- Motion: minimal and fast, ~140 ms; no decorative animation
- **Responsible-gaming law:** every product surface carries "All data provided
  for informational purposes only. Please wager responsibly." plus 21+ and
  1-800-GAMBLER on marketing surfaces

`.pi/themes/odl.json` mirrors: near-black background, signal-orange accent,
signal-green/red/yellow for diff/status colors.

## 4. Execution law, context files, audit, delivery

**`APPEND_SYSTEM.md` (injected into every pi session) + `AGENTS.md` carry:**

- Skill-triggering rule: if any available skill plausibly applies — even 1% —
  read and follow it before acting; process skills before domain skills
- Model policy: current-generation only (claude-fable-5 default, claude-opus-5,
  gpt-5.6-sol); never switch to older models
- **Verification law:** `python -m pytest` (full offline suite) and
  `python qa/verify.py` must pass before claiming done; report real command
  output, never assumed success
- **LKG law:** failed runs never touch last-known-good; never hand-edit
  `data/`; quarantine is append-only evidence
- **Honest-gates law:** a BLOCKED gate is never reported as PASS
  (qa/acceptance-gates.md)
- **Compliance law:** never add retry aggression, header spoofing, or
  bot-evasion; live-source requests are budgeted and go through sanctioned
  workflows only
- **Auth law (LLM.md):** subscription-first — interactive work runs on Claude
  subscription auth; API credits are never spent by automation in this repo;
  **no API-billed CI workflows exist or may be added** without explicit owner
  approval

**Context-file suite:** AGENTS.md is the universal carrier (pi/Codex read it
instead of CLAUDE.md, so laws live inline there). CLAUDE.md addresses Claude
Code and cross-references AGENTS.md/HARNESS.md/LLM.md/SKILLS.md rather than
duplicating. docs/pi-harness.md is the operational runbook (install, trust
flow, modes, troubleshooting).

**Audit — `qa/pi_harness_audit.py`:** model-free; validates settings.json
parses and every referenced path exists, each SKILL.md has name+description
frontmatter, prompts exist, theme JSON is valid, context files present.
Wired as a step in `.github/workflows/ci.yml` (no model calls, no secrets).

**Delivery:** working clone at `~/src/off-duty-locks`, branch
`feat/pi-harness-foundation`, single PR to `main`. Production-behavior
neutral: web service, pipeline, and scheduled workflows untouched;
`.dockerignore` keeps harness files out of the image. Verification before PR:
existing pytest suite still green, `qa/pi_harness_audit.py` passes, pi loads
the harness locally (skills/prompts/extension/theme resolve).

## Follow-ups (out of scope here)

1. **Dashboard build** — WNBA Alpha-style product UI on the Flask/Postgres
   serving layer, governed by `design-system/off-duty-locks/MASTER.md`
   (own brainstorm → spec → plan cycle)
2. Optional shared-harness extraction if a third repo ever adopts this
   foundation
