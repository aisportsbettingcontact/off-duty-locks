# QM — multiplayer agent orchestration (yc-software/qm)

[QM](https://github.com/yc-software/qm) is the org-level layer above this
repo's agent stack: a multiplayer harness for work — Slack + web workspaces
where every person and room gets scoped memory, files, keychain view, crons,
permissions, and a durable sandbox. A central core drives interchangeable
harnesses (Pi, Claude Code, OpenCode, Codex) over Postgres, with three
security postures (Strict / Auto / Dangerous) and a predeclared command
policy. Reference clone: `~/src/qm`.

## How this repo plugs into QM

### 1. Skill pack (the corpus, importable)

`qm.pack.json` at the repo root is the canonical config:

    url:        https://github.com/aisportsbettingcontact/off-duty-locks
    skillGlobs: [".pi/skills/**"]   (no excludes — the whole corpus ships)

The repo is public — the pack import needs no credential. QM matches globs
against the skill DIRECTORY, requires non-empty `name` + `description`
frontmatter (else the skill is silently skipped as `malformed`), and flags
name `collision`s at ingest. `qa/qm_pack_verify.py` (a lockstep port of
qm `src/skills/ingest.ts` semantics) verifies all of this offline, plus the
**intertwine law**: pack selection == `.pi/skills` census — one corpus, two
consumers (pi's loader and QM's pack). It runs as a layer of
`qa/pi_harness_audit.py`, so CI enforces it on every PR.

### 2. Sandbox (repo work inside a scope)

QM harnesses run SDK-embedded in isolated sandboxes — they do not inherit a
checkout's context. Clone this repo in the scope's durable sandbox and the
full in-repo wiring applies: AGENTS.md, `.pi/settings.json`, odl-guard, odl
theme, prompts. The trust flow (`/trust`, `-a` headless) activates project
resources and auto-installs the declared pi packages (`.pi/git/`).

## Laws that carry into QM (AGENTS.md/LLM.md apply unchanged)

- **Compliance**: no ad-hoc *.wnba.com requests from QM sandboxes; Live
  Smoke remains the only sanctioned live path.
- **Models**: current generation only — claude-fable-5 / claude-opus-5 /
  gpt-5.6-sol.
- **Credits**: NEVER wire any API key into QM's keychain, org config, or
  harness credentials. This repo has zero API-billed automation; QM-side
  model spend is its own owner-approved decision with its own key.
- **Security posture**: run **Auto** (default) or stricter. QM's command
  policy complements odl-guard; it does not replace repo law.
- LKG, honest-gates, fixture, and deploy law govern any off-duty-locks work
  done from a QM sandbox exactly as they do locally.

## Deployment (owner-gated — not executed by agents)

QM deploys from a **deployment directory**, not a source checkout:

    npm exec --yes --package=@yc-software/qm@latest -- \
      qm init . --org <slug> --target <docker|fly|aws>

Owner decisions, in order: hosting target (Railway is NOT a supported QM
target), org slug, sign-in (auth broker: admin email + verified sender +
Resend/SMTP, or external IdP), optional Slack workspace install, and the
model-provider API key — a deployment secret and therefore an explicit
owner billing decision (never this repo's key; per LLM.md there is none to
give). Until an owner runs that flow, QM integration is exactly the two
seams above.
