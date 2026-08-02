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
