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
