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
