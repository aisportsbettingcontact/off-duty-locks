# pi Harness + Brand Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the dime-ai pi harness foundation into off-duty-locks — `.pi/` CLI wiring, mechanical repo-law guard, context-file suite, Off Duty Locks brand law, and a model-free harness audit wired into CI.

**Architecture:** Self-contained `.pi/` project config (settings, theme, guard extension, repo-local skills, prompt templates) + a context-file suite at repo root (AGENTS.md is the universal law carrier; pi/Codex load it instead of CLAUDE.md). A Python audit script (`qa/pi_harness_audit.py`) structurally validates the harness and runs as a CI step. Production-behavior neutral: `.dockerignore` keeps every harness file out of the Railway image.

**Tech Stack:** pi coding agent (`@earendil-works/pi-coding-agent`, global install), TypeScript (pi extension only — executed by pi's own runtime, no Node added to the repo), Python 3.11 + pytest (audit + tests).

**Spec:** `docs/superpowers/specs/2026-08-01-pi-harness-foundation-design.md` (approved).

## Global Constraints

- Branch: `feat/pi-harness-foundation`; single PR to `main`. Merge to `main` deploys the Railway web service — this PR must be production-behavior neutral.
- Models: `claude-fable-5` default; enabled set exactly `["claude-fable-5", "claude-opus-5", "gpt-5.6-sol"]`. Never reference older models.
- **Zero API-billed automation.** No CI step, workflow, or script may call a model. The audit is structural only. Do not add a pi-review workflow.
- Accent: signal orange `#FF5C1C`, single accent, no gradients. Surfaces `#0B0B0D` / `#141417` / borders `#26262B`. Signal colors: green `#22C55E`, blue `#3B82F6`, yellow `#EAB308`, red `#EF4444`, gray `#6B7280`.
- Verification commands for this repo: `python -m pytest -q` and `python3 qa/verify.py --repo-root .` — both must stay green after every task.
- Responsible-gaming copy (exact): "All data provided for informational purposes only. Please wager responsibly." plus "21+" and "1-800-GAMBLER" on marketing surfaces.
- All work happens in `~/src/off-duty-locks`.

---

### Task 1: `.pi/` scaffolding — settings, theme, ignore rules

**Files:**
- Create: `.pi/settings.json`
- Create: `.pi/themes/odl.json`
- Modify: `.gitignore` (append at end)
- Modify: `.dockerignore` (append at end)

**Interfaces:**
- Produces: `.pi/settings.json` with keys `theme, defaultProvider, defaultModel, enabledModels, skills, prompts, packages` — Task 8's audit asserts exactly these keys and `defaultModel == "claude-fable-5"`. Skill dir is `.pi/skills` (Task 4), prompts dir `.pi/prompts` (Task 3), theme name `odl`.

- [ ] **Step 1: Write `.pi/settings.json`**

```json
{
  "theme": "odl",
  "defaultProvider": "anthropic",
  "defaultModel": "claude-fable-5",
  "enabledModels": [
    "claude-fable-5",
    "claude-opus-5",
    "gpt-5.6-sol"
  ],
  "skills": [
    "skills"
  ],
  "prompts": [
    "prompts"
  ],
  "packages": [
    "git:github.com/badlogic/pi-skills",
    "git:github.com/anthropics/skills"
  ]
}
```

(`skills` and `prompts` are relative to `.pi/`, so they resolve to `.pi/skills` and `.pi/prompts`. Packages auto-install to `.pi/git/` on trust — gitignored in Step 3.)

- [ ] **Step 2: Write `.pi/themes/odl.json`** — dime theme structure, Off Duty Locks tokens (signal orange accent, graphite surfaces, signal green/red/yellow):

```json
{
  "$schema": "https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/src/modes/interactive/theme/theme-schema.json",
  "name": "odl",
  "vars": {
    "cyan": "#FF5C1C",
    "blue": "#26262B",
    "green": "#22C55E",
    "red": "#EF4444",
    "yellow": "#EAB308",
    "text": "#D4D4D4",
    "gray": "#6B7280",
    "dimGray": "#52525B",
    "darkGray": "#3F3F46",
    "accent": "#FF5C1C",
    "selectedBg": "#26262B",
    "userMsgBg": "#141417",
    "toolPendingBg": "#18181B",
    "toolSuccessBg": "#121A14",
    "toolErrorBg": "#1F1214",
    "customMsgBg": "#1A1512"
  },
  "colors": {
    "accent": "accent",
    "border": "blue",
    "borderAccent": "cyan",
    "borderMuted": "darkGray",
    "success": "green",
    "error": "red",
    "warning": "yellow",
    "muted": "gray",
    "dim": "dimGray",
    "text": "text",
    "thinkingText": "gray",
    "selectedBg": "selectedBg",
    "userMessageBg": "userMsgBg",
    "userMessageText": "text",
    "customMessageBg": "customMsgBg",
    "customMessageText": "text",
    "customMessageLabel": "accent",
    "toolPendingBg": "toolPendingBg",
    "toolSuccessBg": "toolSuccessBg",
    "toolErrorBg": "toolErrorBg",
    "toolTitle": "text",
    "toolOutput": "gray",
    "mdHeading": "text",
    "mdLink": "accent",
    "mdLinkUrl": "dimGray",
    "mdCode": "accent",
    "mdCodeBlock": "green",
    "mdCodeBlockBorder": "gray",
    "mdQuote": "gray",
    "mdQuoteBorder": "gray",
    "mdHr": "gray",
    "mdListBullet": "accent",
    "toolDiffAdded": "green",
    "toolDiffRemoved": "red",
    "toolDiffContext": "gray",
    "syntaxComment": "#6A9955",
    "syntaxKeyword": "#569CD6",
    "syntaxFunction": "#DCDCAA",
    "syntaxVariable": "#9CDCFE",
    "syntaxString": "#CE9178",
    "syntaxNumber": "#B5CEA8",
    "syntaxType": "#4EC9B0",
    "syntaxOperator": "#D4D4D4",
    "syntaxPunctuation": "#D4D4D4",
    "thinkingOff": "darkGray",
    "thinkingMinimal": "#6E6E6E",
    "thinkingLow": "#5F87AF",
    "thinkingMedium": "#81A2BE",
    "thinkingHigh": "#B294BB",
    "thinkingXhigh": "#D183E8",
    "thinkingMax": "#FF5FFF",
    "bashMode": "green"
  },
  "export": {
    "pageBg": "#0B0B0D",
    "cardBg": "#141417",
    "infoBg": "#1A1512"
  }
}
```

- [ ] **Step 3: Append to `.gitignore`**

```gitignore

# pi coding agent (.pi/ project config is committed; local package installs are not)
.pi/npm/
.pi/git/
```

- [ ] **Step 4: Append to `.dockerignore`** (harness files never enter the Railway image)

```
# Agent-harness files — never part of the runtime image
.pi
design-system
AGENTS.md
CLAUDE.md
CODEX.md
HARNESS.md
LLM.md
SKILLS.md
```

(`docs/` is already excluded, which covers `docs/superpowers/` and `docs/pi-harness.md`.)

- [ ] **Step 5: Verify both JSON files parse**

Run: `python3 -m json.tool .pi/settings.json > /dev/null && python3 -m json.tool .pi/themes/odl.json > /dev/null && echo JSON-OK`
Expected: `JSON-OK`

- [ ] **Step 6: Commit**

```bash
git add .pi/settings.json .pi/themes/odl.json .gitignore .dockerignore
git commit -m "feat(pi): settings + odl theme scaffolding, ignore rules"
```

---

### Task 2: Guard extension — `.pi/extensions/odl-guard.ts`

**Files:**
- Create: `.pi/extensions/odl-guard.ts`

**Interfaces:**
- Consumes: nothing from other tasks (pi auto-loads `.pi/extensions/*.ts` on trust).
- Produces: file whose existence Task 8's audit asserts at exactly `.pi/extensions/odl-guard.ts`.

- [ ] **Step 1: Write `.pi/extensions/odl-guard.ts`**

```typescript
/**
 * odl-guard — repo-law enforcement at the tool-call layer.
 *
 * Enforces AGENTS.md laws mechanically, regardless of what the model decides:
 *  - blocks destructive git (force push, reset --hard, clean -f, checkout .,
 *    --no-verify)
 *  - blocks writes to env/secret files
 *  - blocks ad-hoc bash requests to *.wnba.com — every live-source request is
 *    budgeted (docs/compliance.md); the Live Smoke workflow is the only
 *    sanctioned live path
 *  - warns on fixtures/ writes (provenance law), http_client/extractor edits
 *    (compliance commitments), and schema.sql writes (live Postgres layer)
 *
 * Runs in every pi mode; in headless (-p/json/rpc) blocks are unconditional
 * since no one can confirm.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const DESTRUCTIVE_GIT = [
  // --force-with-lease is blocked too: intentionally strict — no agent
  // force-pushes of any variant in this repo (AGENTS.md).
  /git\s+push\b[^\n]*(--force\b|-f\b)/,
  /git\s+push\b[^\n]*\s\+\S+/, // refspec force-push: git push origin +main
  /git\s+reset\s+--hard\b/,
  /git\s+clean\s+-[a-z]*f/,
  /git\s+checkout\s+(--\s+)?\.(\s|$)/,
  /git\s+restore\b(?![^\n]*--staged)[^\n]*\s\.(\s|$)/, // git restore [--source=...] .
  /git\s+commit\b[^\n]*--no-verify\b/,
];

// HTTP clients aimed at the live source. Deliberately requires BOTH a client
// invocation AND the wnba.com host in the same command, so `grep wnba.com src/`
// and other read-only mentions stay allowed.
const LIVE_SOURCE = [
  /\b(curl|wget|httpie|xh|aria2c)\b[^\n]*\bwnba\.com/i,
  /\bhttp\b\s[^\n]*\bwnba\.com/i, // httpie's `http` binary
  /\bpython3?\b[^\n]*\b(requests|urllib|httpx|aiohttp)\b[^\n]*\bwnba\.com/i,
];

const PROTECTED_WRITE_PATTERNS = [
  {
    re: /(^|\/)\.env(rc)?(\.|$)|\.env$/,
    why: "environment/secret files are managed by hand, never by agents",
  },
];

const FIXTURE_RE = /(^|\/)fixtures\//;
const COMPLIANCE_RE = /(^|\/)src\/wnba_pipeline\/(http_client|extractor)\.py$/;
const SCHEMA_RE = /(^|\/)src\/wnba_pipeline\/schema\.sql$/;

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName === "bash") {
      const command = String((event.input as { command?: unknown }).command ?? "");
      for (const re of DESTRUCTIVE_GIT) {
        if (re.test(command)) {
          return {
            block: true,
            reason: `odl-guard: "${command.slice(0, 80)}" is a destructive git operation banned by repo law (AGENTS.md). Not allowed in any mode.`,
          };
        }
      }
      for (const re of LIVE_SOURCE) {
        if (re.test(command)) {
          return {
            block: true,
            reason:
              "odl-guard: ad-hoc requests to *.wnba.com are banned — every live-source request is budgeted (docs/compliance.md). Work offline against fixtures/; the Live Smoke GitHub workflow is the only sanctioned live path.",
          };
        }
      }
      return;
    }

    if (event.toolName === "write" || event.toolName === "edit") {
      const input = event.input as { path?: unknown; file_path?: unknown };
      const path = String(input.path ?? input.file_path ?? "");
      for (const { re, why } of PROTECTED_WRITE_PATTERNS) {
        if (re.test(path)) {
          return { block: true, reason: `odl-guard: writes to "${path}" are blocked — ${why}.` };
        }
      }
      const warn = (msg: string) => {
        if (ctx.hasUI) ctx.ui.notify(msg, "warning");
      };
      if (FIXTURE_RE.test(path)) {
        warn(
          "odl-guard: fixtures law — every JSON fixture needs a _provenance object, the synthetic flag must be honest, and no secrets ever (fixtures/README.md).",
        );
      }
      if (COMPLIANCE_RE.test(path)) {
        warn(
          "odl-guard: http_client/extractor edits touch compliance commitments — retry counts, backoff, spacing, and headers are hard limits (docs/compliance.md §2). Never loosen them.",
        );
      }
      if (SCHEMA_RE.test(path)) {
        warn(
          "odl-guard: schema.sql is the live Postgres serving layer — plan the rollout (docs/deployment.md) before changing it.",
        );
      }
    }
  });
}
```

- [ ] **Step 2: Sanity-check the regexes offline** (no pi needed — Node one-liner mirrors of the live-source rule set)

Run:
```bash
node -e '
const live = [/\b(curl|wget|httpie|xh|aria2c)\b[^\n]*\bwnba\.com/i, /\bhttp\b\s[^\n]*\bwnba\.com/i, /\bpython3?\b[^\n]*\b(requests|urllib|httpx|aiohttp)\b[^\n]*\bwnba\.com/i];
const hit = (s) => live.some((r) => r.test(s));
console.assert(hit("curl https://stats.wnba.com/stats/x"), "curl should block");
console.assert(hit("python3 -c \"import requests; requests.get('https://stats.wnba.com')\""), "python requests should block");
console.assert(!hit("grep -r wnba.com src/"), "grep must NOT block");
console.assert(!hit("python -m pytest -q"), "pytest must NOT block");
console.log("guard-regex-OK");'
```
Expected: `guard-regex-OK` with no assertion failures.

- [ ] **Step 3: Commit**

```bash
git add .pi/extensions/odl-guard.ts
git commit -m "feat(pi): odl-guard — destructive git, env writes, and ad-hoc wnba.com requests blocked; fixtures/compliance/schema warnings"
```

---

### Task 3: Execution law + prompt templates

**Files:**
- Create: `.pi/APPEND_SYSTEM.md`
- Create: `.pi/prompts/verify.md`
- Create: `.pi/prompts/triage.md`
- Create: `.pi/prompts/ship.md`

**Interfaces:**
- Produces: three prompt files in `.pi/prompts/` (Task 8's audit asserts the dir exists and contains ≥1 `.md`); `APPEND_SYSTEM.md` (audit asserts it exists).

- [ ] **Step 1: Write `.pi/APPEND_SYSTEM.md`**

```markdown
Execution rules for this repo (appended to every pi session):

- Skills: if any entry in <available_skills> plausibly applies to the task — even 1% —
  read and follow it before acting. Process skills (brainstorming, systematic-debugging,
  test-driven-development, verification-before-completion) come before domain skills.
- Models: current-generation only per LLM.md (claude-fable-5 default, claude-opus-5,
  gpt-5.6-sol). Never switch to older models.
- Verification before claiming done: `python -m pytest -q` and
  `python3 qa/verify.py --repo-root .` must pass; report real command output, never
  assumed success.
- LKG law: failed runs never touch last-known-good. Never hand-edit `data/`;
  quarantine is append-only evidence.
- Honest gates: a BLOCKED gate is never reported as PASS (qa/acceptance-gates.md).
- Compliance: never add retry aggression, header spoofing, or bot evasion. Live-source
  requests are budgeted (docs/compliance.md); the Live Smoke workflow is the only
  sanctioned live path — never probe *.wnba.com ad hoc.
- Shipping: merge to main deploys the Railway web service. Use the /ship template;
  it encodes the gates.
```

- [ ] **Step 2: Write `.pi/prompts/verify.md`**

```markdown
---
description: Full offline verification pass — unit suite, QA harness, harness audit
---

Run the complete offline verification for this repo and report real output:

1. `python -m pytest -q` — full unit + integration suite (offline; must be green)
2. `python3 qa/verify.py --repo-root .` — independent QA harness (all offline sections must pass)
3. `python3 qa/pi_harness_audit.py --repo-root .` — pi harness structural audit

If anything fails, stop and triage with the systematic-debugging skill before
touching code. Never report a blocked or skipped check as passed.
```

- [ ] **Step 3: Write `.pi/prompts/triage.md`**

```markdown
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
```

- [ ] **Step 4: Write `.pi/prompts/ship.md`**

```markdown
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
```

- [ ] **Step 5: Verify prompt frontmatter parses** (each file starts with `---` and has a `description:` line)

Run: `for f in .pi/prompts/*.md; do head -1 "$f" | grep -q -- '---' && grep -q '^description:' "$f" && echo "OK $f" || echo "BAD $f"; done`
Expected: three `OK` lines, zero `BAD`.

- [ ] **Step 6: Commit**

```bash
git add .pi/APPEND_SYSTEM.md .pi/prompts/
git commit -m "feat(pi): execution law (APPEND_SYSTEM) + /verify /triage /ship templates"
```

---

### Task 4: Repo-local skills

**Files:**
- Create: `.pi/skills/pipeline-verify/SKILL.md`
- Create: `.pi/skills/fixture-provenance/SKILL.md`
- Create: `.pi/skills/railway-ops/SKILL.md`

**Interfaces:**
- Produces: three `SKILL.md` files, each with YAML frontmatter containing non-empty `name:` and `description:` — Task 8's audit validates this exact shape (`---` fence, `name:`, `description:`).

- [ ] **Step 1: Write `.pi/skills/pipeline-verify/SKILL.md`**

```markdown
---
name: pipeline-verify
description: Use when verifying pipeline changes, interpreting run manifests, or checking exit codes / freshness — runs the offline suite + QA harness and reads RunManifest evidence correctly
---

# Pipeline verification

## Commands (all offline, deterministic)

1. `python -m pytest -q` — full unit + integration suite. Must be green.
2. `python3 qa/verify.py --repo-root .` — independent QA harness (secret sweep,
   idempotency, LKG protection, doc-command parsing). All offline sections must pass.
3. End-to-end fixture run (what CI does):
   `wnba-pipeline run --fixture fixtures/sanitized/leaguedashteamstats_2026_lastn7.json --data-root "$(mktemp -d)"`
   — first run exits 0 with status SUCCESS; an identical rerun exits 0 with
   SUCCESS_UNCHANGED and writes no second snapshot.

## Reading a RunManifest

One JSON line on stdout, persisted to `data/manifests/<run_id>.json`. Start from
`status` and `failureReason`. Exit codes: 0 SUCCESS / SUCCESS_UNCHANGED ·
2 CONFIG_ERROR · 3 UPSTREAM_UNAVAILABLE · 4 VALIDATION_FAILED · 5 LOCK_HELD ·
6 STORAGE_ERROR · 7 INTERNAL_ERROR.

`freshnessState` describes the stored last-known-good, never a failed candidate:
FRESH (≤36h default) · STALE · MISSING · INVALID · UPSTREAM_UNAVAILABLE.

## Laws

- A failed run must leave LKG byte-identical. If LKG changed on a failed run,
  that is a severity-1 bug — stop and report.
- A BLOCKED gate (network-dependent, e.g. Live Smoke) is never reported as PASS.
- Report real command output. Never assume success.
```

- [ ] **Step 2: Write `.pi/skills/fixture-provenance/SKILL.md`**

```markdown
---
name: fixture-provenance
description: Use when creating, editing, or regenerating anything under fixtures/ — provenance object rules, the synthetic flag, secret hygiene, and the adversarial-fixture generator
---

# Fixture provenance law

Rules for every fixture (fixtures/README.md is authoritative):

1. **No secrets, ever.** No cookies, Authorization headers, tokens, full
   request-header dumps, or private infrastructure values. Sanitized captures
   keep only: URL, query parameters, response body, response status, and the
   response Date header.
2. **Provenance is mandatory.** Every JSON fixture carries a top-level
   `_provenance` object: `{"synthetic": true|false, "capturedAtUtc": ...,
   "describedBy": "docs/source-contract.md", "notes": ...}`. Schema-accurate
   fixtures that were NOT captured live MUST say `"synthetic": true` — the flag
   is an honesty contract, not metadata.
3. Tree layout: `sanitized/` source-contract fixtures · `expected_teams/`
   versioned expected-team sets · `adversarial/` malformed/hostile payloads ·
   `betting/` betting-feed captures.

## Regeneration

- Adversarial payloads are generated, not hand-edited:
  `python3 qa/gen_adversarial_fixtures.py` (writes into `fixtures/adversarial/`).
- Never fabricate a "captured" fixture. If you cannot capture live (sandbox
  blocks *.wnba.com), build it synthetic and label it synthetic.
- After any fixture change run `python -m pytest -q` — the suite pins fixture
  shape — and `python3 qa/verify.py --repo-root .` (secret sweep covers fixtures).
```

- [ ] **Step 3: Write `.pi/skills/railway-ops/SKILL.md`**

```markdown
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
```

- [ ] **Step 4: Verify skill frontmatter shape** (same check the audit will run)

Run:
```bash
python3 - <<'EOF'
import re, pathlib
FRONTMATTER_RE = re.compile(r"^---\n(?=(?:.*\n)*?name:\s*\S)(?=(?:.*\n)*?description:\s*\S)(?:.*\n)*?---\n")
for p in sorted(pathlib.Path(".pi/skills").glob("*/SKILL.md")):
    print(("OK  " if FRONTMATTER_RE.match(p.read_text()) else "BAD ") + str(p))
EOF
```
Expected: three `OK` lines.

- [ ] **Step 5: Commit**

```bash
git add .pi/skills/
git commit -m "feat(pi): repo-local skills — pipeline-verify, fixture-provenance, railway-ops"
```

---

### Task 5: Brand law — `design-system/off-duty-locks/MASTER.md`

**Files:**
- Create: `design-system/off-duty-locks/MASTER.md`

**Interfaces:**
- Produces: brand-law file at exactly `design-system/off-duty-locks/MASTER.md` (Task 8's audit asserts the path; Task 6's context files cross-reference it).

- [ ] **Step 1: Write `design-system/off-duty-locks/MASTER.md`**

```markdown
# Off Duty Locks — Brand Law (MASTER)

Authoritative visual law for every Off Duty Locks product surface. Any UI work in
this repo — dashboard, web app, marketing — obeys this file. Generic
palette/font generator output never overrides these tokens.

Direction: dark sports-terminal. Dense data grids, signal-color semantics, one
hot accent. Dark-first; no light mode is specified yet (do not invent one).

## Surfaces

| Token | Value | Use |
|---|---|---|
| `--odl-bg` | `#0B0B0D` | Page background (near-black graphite) |
| `--odl-panel` | `#141417` | Cards, panels, table containers |
| `--odl-border` | `#26262B` | Hairline borders, dividers, table rules |
| `--odl-text` | `#E7E7EA` | Primary text |
| `--odl-text-muted` | `#9CA3AF` | Secondary text, column headers |

## Accent — ONE accent only

| Token | Value | Use |
|---|---|---|
| `--odl-accent` | `#FF5C1C` | Active nav, primary buttons, selected tabs, line-move emphasis, brand marks |

No gradients. No second accent. No purple, gold, or neon green as UI chrome.

## Signal semantics (data meaning, not decoration)

| Token | Value | Meaning |
|---|---|---|
| `--odl-signal-sharp` | `#22C55E` | Sharp money / positive edge / green rating ring |
| `--odl-signal-model` | `#3B82F6` | Model edge |
| `--odl-signal-public` | `#EAB308` | Public heavy / caution / mid rating ring |
| `--odl-signal-rlm` | `#FF5C1C` | Reverse line move (shares the accent) |
| `--odl-signal-warn` | `#EF4444` | Warning / conflict / negative |
| `--odl-signal-none` | `#6B7280` | No clear signal / neutral |

Rating rings: green ≥ 7.5 · yellow 5.0–7.4 · orange < 5.0.

Green/red in data cells always mean favorable/unfavorable values — never use
them decoratively, and never encode meaning by color alone (pair with text).

## Typography

| Role | Face | Rules |
|---|---|---|
| Display / headers / nav | **Barlow Condensed 700** | Uppercase, tracked (+2–4%), tight leading |
| Body + all data grids | **Inter** | `font-variant-numeric: tabular-nums` on every numeric cell; 13–14px grid text |

## Motion

Minimal and fast: ~140 ms ease-out for state changes. No decorative animation,
no parallax, no scroll-triggered effects on data surfaces.

## Standing copy law

Every product surface footer carries:
"All data provided for informational purposes only. Please wager responsibly."
Marketing surfaces additionally carry "21+" and "1-800-GAMBLER".

## Terminal theme

`.pi/themes/odl.json` mirrors this identity (accent `#FF5C1C`, surfaces
`#0B0B0D`/`#141417`, signal green/red/yellow). Keep them in sync when tokens
change.
```

- [ ] **Step 2: Commit**

```bash
git add design-system/off-duty-locks/MASTER.md
git commit -m "feat(brand): Off Duty Locks brand law — graphite surfaces, signal-orange accent, signal semantics"
```

---

### Task 6: Context-file suite

**Files:**
- Create: `AGENTS.md`
- Create: `CLAUDE.md`
- Create: `HARNESS.md`
- Create: `LLM.md`
- Create: `SKILLS.md`
- Create: `CODEX.md`

**Interfaces:**
- Consumes: brand law path from Task 5, skill names from Task 4, prompt names from Task 3, guard rules from Task 2.
- Produces: the six context files at repo root (Task 8's audit asserts each exists).

- [ ] **Step 1: Write `AGENTS.md`** (universal carrier — pi and Codex load this INSTEAD of CLAUDE.md, so laws live inline)

```markdown
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

## Verification (before claiming anything done)

- `python -m pytest -q` — full offline suite, must be green
- `python3 qa/verify.py --repo-root .` — independent QA harness
- `python3 qa/pi_harness_audit.py --repo-root .` — harness structural audit

## Models and auth

Current generation only: claude-fable-5 (default), claude-opus-5, gpt-5.6-sol.
Subscription-first auth; **zero API-billed automation in this repo** — no CI
step or workflow may call a model. Details: LLM.md.
```

- [ ] **Step 2: Write `CLAUDE.md`** (Claude Code entry — cross-references, does not duplicate)

```markdown
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
| Pipeline runbook / triage | `docs/runbook.md` |
| Acceptance gates | `qa/acceptance-gates.md` |

Repo-local skills (also exposed to pi): `.pi/skills/` — pipeline-verify,
fixture-provenance, railway-ops. Read the relevant one before pipeline
verification, fixture edits, or Railway work.

Verification before claiming done: `python -m pytest -q` ·
`python3 qa/verify.py --repo-root .` · `python3 qa/pi_harness_audit.py --repo-root .`
— real output only.

Sports-betting product: responsible-gaming language stays on product surfaces
(see brand law).
```

- [ ] **Step 3: Write `HARNESS.md`**

```markdown
# HARNESS.md — agent runtimes and their wiring

Every way an agent executes against this repo. Deep pi runbook:
`docs/pi-harness.md`.

| Harness | Runtime | Context it loads | Config |
|---|---|---|---|
| Claude Code | CLI / desktop / IDE | `CLAUDE.md` → `AGENTS.md`, `.pi/skills/` on demand | — (no .claude/ config in this repo) |
| pi (interactive) | global `@earendil-works/pi-coding-agent` | `AGENTS.md` (first match wins — not CLAUDE.md), `.pi/skills/` + packages, `.pi/prompts/` as `/` templates, `.pi/APPEND_SYSTEM.md` | `.pi/settings.json`, `.pi/extensions/odl-guard.ts`, `~/.pi/agent/trust.json` |
| pi (headless) | `pi -p` / `--mode json` / `--mode rpc` | same, with `-a`/`--approve` for project trust | same |
| Codex | OpenAI Codex CLI/cloud | `AGENTS.md` (native), `CODEX.md` | model `gpt-5.6-sol` per LLM.md |

## Entry points (no package.json — invoke pi directly)

- `pi` — interactive session (odl theme, guard active, skills + templates loaded)
- `pi -p "<prompt>"` — headless one-shot (add `-a` to approve project trust)
- `pi --mode rpc` — LF-delimited JSONL process integration
- `/verify`, `/triage`, `/ship` — prompt templates from `.pi/prompts/`

## Invariants across harnesses

Model policy per LLM.md (Fable 5 / Opus 5 / Codex 5.6 Sol only). Laws per
AGENTS.md. The same repo-local skills are exposed to every harness so behavior
stays consistent regardless of which agent runs.

## Trust

pi project resources (settings, skills, prompts, packages, extension) load only
after trust — `/trust` interactively, `-a` per headless run. Declared packages
auto-install on trust to `.pi/git/` (gitignored).
```

- [ ] **Step 4: Write `LLM.md`**

```markdown
# LLM.md — model policy and auth

Authoritative model policy for every agent runtime in this repo. Summarized in
AGENTS.md; this file wins on conflict.

## Approved models (current generation only)

| Provider | Model | Use |
|---|---|---|
| anthropic | `claude-fable-5` | Default everywhere |
| anthropic | `claude-opus-5` | Alternate when Fable is unavailable or a second opinion is wanted |
| openai-codex | `gpt-5.6-sol` | When using Codex (subscription auth) |

We do not use older models in execution — no opus-4.x, sonnet, haiku, or
gpt-4/5.x below 5.6. If a task seems to want a cheaper model, use Fable 5
anyway; consistency beats micro-savings.

Enforcement: `.pi/settings.json` `enabledModels` (Ctrl+P cycles only these);
Claude Code session model selection stays Fable 5 / Opus 5.

## Auth model: subscription-first (IMPORTANT)

Interactive execution runs on Claude subscription auth, not the Anthropic API.
Claude Code authenticates via the Claude subscription; the pi CLI does the same
(`/login` → Anthropic). No `ANTHROPIC_API_KEY` is required for any interactive
work, and an empty API balance must never block it.

## API credit law: zero API-billed automation (owner directive, 2026-08-01)

**This repo has no sanctioned API-billed surface.** No CI step, workflow,
script, or scheduled job may call a model. The harness audit
(`qa/pi_harness_audit.py`) is structural only. Do not add a pi-review or any
LLM-in-CI workflow without explicit owner approval. Before any action that
would bill an API key, stop — in this repo the answer is "don't".
```

- [ ] **Step 5: Write `SKILLS.md`**

```markdown
# SKILLS.md — skill inventory

## Repo-local (committed, `.pi/skills/`)

| Skill | Use when |
|---|---|
| `pipeline-verify` | Verifying pipeline changes, reading RunManifests, exit codes, freshness |
| `fixture-provenance` | Creating/editing/regenerating anything under `fixtures/` |
| `railway-ops` | Deploying, debugging, or checking the Railway web service |

## Packages (auto-installed on pi trust → `.pi/git/`, gitignored)

| Package | Contents |
|---|---|
| `git:github.com/badlogic/pi-skills` | General engineering skills (debugging, planning, verification) |
| `git:github.com/anthropics/skills` | Anthropic official skills |

Skill-triggering rule (from `.pi/APPEND_SYSTEM.md`): if any available skill
plausibly applies — even 1% — read and follow it before acting; process skills
before domain skills.

Claude Code sessions: the repo-local skills are plain markdown — read
`.pi/skills/<name>/SKILL.md` directly when the trigger matches.
```

- [ ] **Step 6: Write `CODEX.md`**

```markdown
# CODEX.md — Codex-specific notes

Codex loads `AGENTS.md` natively — all repo law applies unchanged.

- Model: `gpt-5.6-sol` only (LLM.md). Subscription/OAuth auth; no API keys in
  this repo.
- The pi guard extension does not run under Codex — the laws it enforces
  mechanically (no destructive git, no .env writes, no ad-hoc *.wnba.com
  requests) still bind and must be self-enforced.
- Verification commands and gates: AGENTS.md "Verification". Real output only.
- No API-billed automation may be added from a Codex session either (LLM.md).
```

- [ ] **Step 7: Commit**

```bash
git add AGENTS.md CLAUDE.md HARNESS.md LLM.md SKILLS.md CODEX.md
git commit -m "feat(harness): context-file suite — AGENTS/CLAUDE/HARNESS/LLM/SKILLS/CODEX"
```

---

### Task 7: pi runbook — `docs/pi-harness.md`

**Files:**
- Create: `docs/pi-harness.md`

**Interfaces:**
- Produces: runbook at exactly `docs/pi-harness.md` (Task 8's audit asserts the path).

- [ ] **Step 1: Write `docs/pi-harness.md`**

````markdown
# pi harness runbook

Operational guide for the pi coding agent in this repo. Wiring overview:
`HARNESS.md`. Law: `AGENTS.md`.

## Install

```bash
npm install -g @earendil-works/pi-coding-agent
pi --version
```

## First run and trust

From the repo root, run `pi`. Project resources (`.pi/settings.json`, skills,
prompts, the odl theme, the odl-guard extension) load only after you trust the
project: `/trust` in the session (persisted to `~/.pi/agent/trust.json`).
Trusting also auto-installs the two declared skill packages into `.pi/git/`
(gitignored — safe to delete and re-trust to reinstall).

Auth: `/login` → Anthropic (Claude subscription). No API key needed for
interactive work (LLM.md).

## Modes

| Mode | Command | Notes |
|---|---|---|
| Interactive | `pi` | odl theme, guard active, `/verify` `/triage` `/ship` templates |
| Headless one-shot | `pi -p "<prompt>" -a` | `-a` approves project trust for the run; guard blocks are unconditional |
| JSONL / RPC | `pi --mode rpc` | LF-delimited JSONL for process integration |

Model cycling: Ctrl+P cycles `claude-fable-5` → `claude-opus-5` → `gpt-5.6-sol`
only (`enabledModels`). The Codex entry activates after Codex login; until
then a startup warning `No models match pattern "gpt-5.6-sol"` is expected.

## What the guard does (odl-guard)

Blocks: destructive git (force push any variant, `reset --hard`, `clean -f`,
`checkout .`/`restore .`, `--no-verify`), writes to `.env*`, and any bash HTTP
client aimed at `*.wnba.com` (compliance budget — the Live Smoke workflow is
the only sanctioned live path). Warns on: `fixtures/` writes (provenance law),
`http_client.py`/`extractor.py` edits (compliance commitments),
`schema.sql` writes (live Postgres).

## Audit

`python3 qa/pi_harness_audit.py --repo-root .` — structural validation of the
whole harness (settings keys, theme JSON, skill frontmatter, prompts, context
files, ignore rules). Runs in CI; model-free.

## Troubleshooting

- **Skills/templates missing in session** → project not trusted; run `/trust`.
- **Packages absent under `.pi/git/`** → delete `.pi/git/` and re-trust.
- **Theme not applied** → `.pi/settings.json` `"theme": "odl"` must match
  `.pi/themes/odl.json` `"name"`.
- **Guard seems inactive** → extensions load from `.pi/extensions/` only after
  trust; check for extension load errors at session start.
````

- [ ] **Step 2: Commit**

```bash
git add docs/pi-harness.md
git commit -m "docs: pi harness runbook — install, trust, modes, guard, audit, troubleshooting"
```

---

### Task 8: Harness audit — `qa/pi_harness_audit.py` (TDD) + CI wiring

**Files:**
- Create: `tests/test_pi_harness_audit.py`
- Create: `qa/pi_harness_audit.py`
- Modify: `.github/workflows/ci.yml` (add one step after "Independent verification harness")

**Interfaces:**
- Consumes: every artifact from Tasks 1–7 (paths asserted).
- Produces: `audit(root: Path) -> list[str]` (empty list = pass) and CLI `python3 qa/pi_harness_audit.py --repo-root .` exiting 0 on pass / 1 on fail.

- [ ] **Step 1: Write the failing tests** — `tests/test_pi_harness_audit.py`

```python
"""Tests for the pi harness structural audit (qa/pi_harness_audit.py)."""

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# qa/ is not a package; load the module by path.
_spec = importlib.util.spec_from_file_location(
    "pi_harness_audit", REPO_ROOT / "qa" / "pi_harness_audit.py"
)
pi_harness_audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pi_harness_audit)


def _write_minimal_harness(root: Path) -> None:
    """A minimal tree that the audit should accept."""
    pi = root / ".pi"
    (pi / "themes").mkdir(parents=True)
    (pi / "extensions").mkdir()
    (pi / "prompts").mkdir()
    skill = pi / "skills" / "demo"
    skill.mkdir(parents=True)

    (pi / "settings.json").write_text(json.dumps({
        "theme": "odl",
        "defaultProvider": "anthropic",
        "defaultModel": "claude-fable-5",
        "enabledModels": ["claude-fable-5", "claude-opus-5", "gpt-5.6-sol"],
        "skills": ["skills"],
        "prompts": ["prompts"],
        "packages": ["git:github.com/badlogic/pi-skills"],
    }))
    (pi / "themes" / "odl.json").write_text(json.dumps({"name": "odl"}))
    (pi / "extensions" / "odl-guard.ts").write_text("export default function () {}\n")
    (pi / "prompts" / "verify.md").write_text("---\ndescription: x\n---\nbody\n")
    (pi / "APPEND_SYSTEM.md").write_text("rules\n")
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: a demo skill\n---\nbody\n")

    for name in ("AGENTS.md", "CLAUDE.md", "HARNESS.md", "LLM.md", "SKILLS.md", "CODEX.md"):
        (root / name).write_text(f"# {name}\n")
    (root / "design-system" / "off-duty-locks").mkdir(parents=True)
    (root / "design-system" / "off-duty-locks" / "MASTER.md").write_text("# brand\n")
    (root / "docs").mkdir()
    (root / "docs" / "pi-harness.md").write_text("# runbook\n")
    (root / ".gitignore").write_text(".pi/npm/\n.pi/git/\n")
    (root / ".dockerignore").write_text(".pi\ndesign-system\nAGENTS.md\n")


def test_real_repo_harness_passes():
    errors = pi_harness_audit.audit(REPO_ROOT)
    assert errors == [], f"audit errors in real repo: {errors}"


def test_minimal_tree_passes(tmp_path):
    _write_minimal_harness(tmp_path)
    assert pi_harness_audit.audit(tmp_path) == []


def test_missing_settings_fails(tmp_path):
    _write_minimal_harness(tmp_path)
    (tmp_path / ".pi" / "settings.json").unlink()
    errors = pi_harness_audit.audit(tmp_path)
    assert any("settings.json" in e for e in errors)


def test_wrong_default_model_fails(tmp_path):
    _write_minimal_harness(tmp_path)
    settings_path = tmp_path / ".pi" / "settings.json"
    settings = json.loads(settings_path.read_text())
    settings["defaultModel"] = "claude-3-opus"
    settings_path.write_text(json.dumps(settings))
    errors = pi_harness_audit.audit(tmp_path)
    assert any("defaultModel" in e for e in errors)


def test_skill_without_frontmatter_fails(tmp_path):
    _write_minimal_harness(tmp_path)
    bad = tmp_path / ".pi" / "skills" / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter here\n")
    errors = pi_harness_audit.audit(tmp_path)
    assert any("frontmatter" in e for e in errors)


def test_missing_context_file_fails(tmp_path):
    _write_minimal_harness(tmp_path)
    (tmp_path / "LLM.md").unlink()
    errors = pi_harness_audit.audit(tmp_path)
    assert any("LLM.md" in e for e in errors)


def test_gitignore_must_cover_pi_git(tmp_path):
    _write_minimal_harness(tmp_path)
    (tmp_path / ".gitignore").write_text("# nothing\n")
    errors = pi_harness_audit.audit(tmp_path)
    assert any(".pi/git/" in e for e in errors)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/src/off-duty-locks && python -m pytest tests/test_pi_harness_audit.py -v`
Expected: FAIL/ERROR at import time — `qa/pi_harness_audit.py` does not exist yet (`FileNotFoundError` from `spec_from_file_location`).

- [ ] **Step 3: Write `qa/pi_harness_audit.py`**

```python
#!/usr/bin/env python3
"""pi harness structural audit — model-free.

Validates that the committed pi harness is internally consistent: settings
parse and reference real paths, the theme is valid JSON with the right name,
every repo-local skill has honest frontmatter, prompts and the guard extension
exist, the context-file suite is present, and ignore rules cover the
package-install dir. Exit 0 on pass, 1 on fail. No network, no model calls.

Usage: python3 qa/pi_harness_audit.py --repo-root .
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_SETTINGS_KEYS = {
    "theme", "defaultProvider", "defaultModel", "enabledModels",
    "skills", "prompts", "packages",
}

CONTEXT_FILES = [
    "AGENTS.md", "CLAUDE.md", "HARNESS.md", "LLM.md", "SKILLS.md", "CODEX.md",
    "design-system/off-duty-locks/MASTER.md",
    "docs/pi-harness.md",
]

FRONTMATTER_RE = re.compile(
    r"^---\n(?=(?:.*\n)*?name:\s*\S)(?=(?:.*\n)*?description:\s*\S)(?:.*\n)*?---\n"
)


def audit(root: Path) -> list[str]:
    errors: list[str] = []
    pi = root / ".pi"

    settings_path = pi / "settings.json"
    if not settings_path.is_file():
        return [f"missing .pi/settings.json under {root}"]
    try:
        settings = json.loads(settings_path.read_text())
    except json.JSONDecodeError as exc:
        return [f".pi/settings.json is not valid JSON: {exc}"]

    missing_keys = REQUIRED_SETTINGS_KEYS - settings.keys()
    if missing_keys:
        errors.append(f".pi/settings.json missing keys: {sorted(missing_keys)}")
    if settings.get("defaultModel") != "claude-fable-5":
        errors.append(
            f"defaultModel must be claude-fable-5 per LLM.md, got {settings.get('defaultModel')!r}"
        )

    theme = settings.get("theme")
    theme_path = pi / "themes" / f"{theme}.json"
    if not theme_path.is_file():
        errors.append(f"theme {theme!r} declared but .pi/themes/{theme}.json is missing")
    else:
        try:
            theme_doc = json.loads(theme_path.read_text())
            if theme_doc.get("name") != theme:
                errors.append(
                    f"theme file name {theme_doc.get('name')!r} != settings theme {theme!r}"
                )
        except json.JSONDecodeError as exc:
            errors.append(f".pi/themes/{theme}.json is not valid JSON: {exc}")

    for entry in settings.get("skills", []):
        skills_dir = pi / entry
        if not skills_dir.is_dir():
            errors.append(f"skills dir .pi/{entry} declared but missing")
            continue
        for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
            if not FRONTMATTER_RE.match(skill_md.read_text()):
                errors.append(
                    f"{skill_md.relative_to(root)} lacks frontmatter with name: and description:"
                )

    for entry in settings.get("prompts", []):
        prompts_dir = pi / entry
        if not prompts_dir.is_dir():
            errors.append(f"prompts dir .pi/{entry} declared but missing")
        elif not list(prompts_dir.glob("*.md")):
            errors.append(f"prompts dir .pi/{entry} contains no .md templates")

    if not (pi / "extensions" / "odl-guard.ts").is_file():
        errors.append("missing .pi/extensions/odl-guard.ts (law enforcement extension)")
    if not (pi / "APPEND_SYSTEM.md").is_file():
        errors.append("missing .pi/APPEND_SYSTEM.md (execution law)")

    for rel in CONTEXT_FILES:
        if not (root / rel).is_file():
            errors.append(f"missing context file {rel}")

    gitignore = root / ".gitignore"
    if not gitignore.is_file() or ".pi/git/" not in gitignore.read_text():
        errors.append(".gitignore must cover .pi/git/ (package auto-installs)")

    dockerignore = root / ".dockerignore"
    if not dockerignore.is_file() or not re.search(
        r"^\.pi\s*$", dockerignore.read_text(), re.MULTILINE
    ):
        errors.append(".dockerignore must exclude .pi from the image")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)
    errors = audit(Path(args.repo_root).resolve())
    if errors:
        print("pi harness audit: FAIL")
        for err in errors:
            print(f"  - {err}")
        return 1
    print("pi harness audit: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/src/off-duty-locks && python -m pytest tests/test_pi_harness_audit.py -v`
Expected: 7 passed. If `test_real_repo_harness_passes` fails, the audit found a real gap from Tasks 1–7 — fix the harness file it names, not the test.

- [ ] **Step 5: Run the CLI against the repo**

Run: `python3 qa/pi_harness_audit.py --repo-root .`
Expected: `pi harness audit: PASS`, exit 0.

- [ ] **Step 6: Add the CI step** — in `.github/workflows/ci.yml`, insert after the "Independent verification harness" step (keep everything else byte-identical):

```yaml
      - name: pi harness audit (structural, model-free)
        run: python3 qa/pi_harness_audit.py --repo-root .
```

- [ ] **Step 7: Full suite green**

Run: `python -m pytest -q && python3 qa/verify.py --repo-root .`
Expected: all tests pass (163 existing + 7 new); verify.py all offline sections pass.

- [ ] **Step 8: Commit**

```bash
git add tests/test_pi_harness_audit.py qa/pi_harness_audit.py .github/workflows/ci.yml
git commit -m "feat(qa): pi harness structural audit + CI step (model-free)"
```

---

### Task 9: Final verification + PR

**Files:**
- No new files. Verification + push + PR.

**Interfaces:**
- Consumes: everything from Tasks 1–8.

- [ ] **Step 1: Full offline verification, real output**

Run:
```bash
cd ~/src/off-duty-locks
python -m pytest -q
python3 qa/verify.py --repo-root .
python3 qa/pi_harness_audit.py --repo-root .
```
Expected: pytest green (170 tests), verify.py offline sections pass, audit PASS. Paste real output into the PR body.

- [ ] **Step 2: Confirm production neutrality**

Run: `git diff main --stat -- src/ Dockerfile railway.toml railway.web.json gunicorn.conf.py`
Expected: empty (zero production files touched). `.github/workflows/ci.yml` is the only workflow change, and it adds a model-free step.

- [ ] **Step 3: Live guard check (only if pi is installed and logged in via subscription — otherwise record as PENDING in the PR, never as done)**

Run: `cd ~/src/off-duty-locks && pi` then in-session: `/trust`, then ask it to run `git reset --hard`.
Expected: odl-guard blocks with the repo-law message. Also confirm the odl theme renders and the `/verify` template appears. Exit without committing anything.

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin feat/pi-harness-foundation
gh pr create --title "feat(pi): pi harness + brand foundation — CLI wiring, odl-guard, context-file suite, brand law" --body "$(cat <<'EOF'
## Summary

Ports the dime-ai pi harness foundation (ai-sports-betting-dime-ai PR #299 architecture) into this repo, with every law-carrying file rewritten for off-duty-locks, plus the Off Duty Locks brand foundation. Spec: docs/superpowers/specs/2026-08-01-pi-harness-foundation-design.md (user-approved).

**Production-behavior neutral**: no src/, Dockerfile, or Railway config changes; .dockerignore keeps all harness files out of the image. Zero API-billed automation — the only CI addition is a structural, model-free audit step.

### CLI harness (.pi/)
- settings.json — claude-fable-5 default (opus-5 + gpt-5.6-sol enabled), repo-local skills + prompts, badlogic/pi-skills + anthropics/skills packages, odl theme
- extensions/odl-guard.ts — blocks destructive git, .env writes, and ad-hoc *.wnba.com requests (compliance budget); warns on fixtures/http_client/extractor/schema.sql
- APPEND_SYSTEM.md — verification, LKG, honest-gates, compliance, shipping law
- skills: pipeline-verify, fixture-provenance, railway-ops · prompts: /verify /triage /ship

### Brand foundation
- design-system/off-duty-locks/MASTER.md — dark graphite surfaces, single signal-orange accent #FF5C1C, signal-color semantics, Barlow Condensed + Inter tabular, responsible-gaming copy law
- .pi/themes/odl.json — same identity for the terminal

### Context-file suite
- AGENTS.md (universal law carrier — pi/Codex load it instead of CLAUDE.md), CLAUDE.md, HARNESS.md, LLM.md (subscription-first, zero API-billed automation), SKILLS.md, CODEX.md, docs/pi-harness.md runbook

### Audit
- qa/pi_harness_audit.py + 7 tests; wired into CI after the verification harness step

## Verification

(real command output pasted here from Task 9 Step 1; live guard check result or PENDING from Step 3)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Confirm CI green on the PR**

Run: `gh pr checks --watch`
Expected: the required "CI" check passes (now including the harness audit step).

---

## Self-Review (completed)

- **Spec coverage:** §1 architecture → Tasks 1, 3, 4, 6, 7; §2 guard → Task 2; §3 brand → Tasks 1 (theme) + 5 (MASTER.md); §4 law/audit/delivery → Tasks 3, 6, 8, 9. No `package.json` (Task 1 note), `.dockerignore` (Task 1), no API-billed CI (Global Constraints + LLM.md + PR body). No gaps found.
- **Placeholder scan:** no TBDs; every file's full content is inline; the one intentionally conditional step (live guard check) has an explicit honest-reporting fallback.
- **Type consistency:** `audit(root: Path) -> list[str]` used identically in Task 8 tests and implementation; paths (`.pi/extensions/odl-guard.ts`, `design-system/off-duty-locks/MASTER.md`, `docs/pi-harness.md`, prompt/skill names) match across Tasks 1–8; `defaultModel` string identical in settings, audit, and tests.
