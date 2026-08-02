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
