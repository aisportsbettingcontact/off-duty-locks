# QM Harness Integration + pi↔QM Intertwine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire off-duty-locks into QM as a git skill pack and intertwine pi + QM around one verified skill corpus (`.pi/skills/`), enforced by a Python port of QM's ingest semantics running inside the existing harness audit.

**Architecture:** `qm.pack.json` at repo root is the pack contract; `qa/qm_pack_verify.py` ports QM's glob/frontmatter/collision semantics from `yc-software/qm src/skills/ingest.ts` and adds a pi↔QM corpus-consistency check; `qa/pi_harness_audit.py` gains a qm-pack layer so the existing CI step covers both harnesses. A `qm-harness` skill and `docs/qm-harness.md` runbook carry operational knowledge; context files gain QM rows and the keychain credit law.

**Tech Stack:** Python 3.11 + pytest (verifier, TDD). No new dependencies, no Node in-repo, no new CI workflow.

**Spec:** `docs/superpowers/specs/2026-08-02-qm-harness-integration-design.md` (approved).

## Global Constraints

- Branch: `feat/qm-harness-integration`; single PR to `main`; production-behavior neutral (no `src/`, Dockerfile, or Railway config changes).
- **Zero API-billed automation**; QM deployment is owner-gated and never executed; never wire any API key into QM's keychain/org config.
- Pack URL exactly `https://github.com/aisportsbettingcontact/off-duty-locks`; skillGlobs exactly `[".pi/skills/**"]`; no excludes.
- QM glob semantics ported verbatim: `**` → `.*`, `*` → `[^/]*`, patterns match the skill DIRECTORY path, not the SKILL.md path.
- Verification: `python -m pytest -q` (via `.venv/bin/python`), `.venv/bin/python qa/verify.py --repo-root .`, `.venv/bin/python qa/pi_harness_audit.py --repo-root .` all green after every task.
- All work in `~/src/off-duty-locks`.

---

### Task 1: Pack contract — `qm.pack.json` + dockerignore

**Files:**
- Create: `qm.pack.json`
- Modify: `.dockerignore` (append one line to the agent-harness block)

**Interfaces:**
- Produces: `qm.pack.json` with `url` and `config.skillGlobs` — Task 2's verifier asserts `url == "https://github.com/aisportsbettingcontact/off-duty-locks"` (trailing `/` or `.git` tolerated) and non-empty string-list `skillGlobs`.

- [ ] **Step 1: Write `qm.pack.json`**

```json
{
  "$comment": "Canonical QM skill-pack configuration for this repository (docs/qm-harness.md). QM admins import the repo URL with exactly this config; qa/qm_pack_verify.py validates the corpus against it — using QM's own glob semantics (patterns match the skill DIRECTORY, not the SKILL.md path; ported from qm src/skills/ingest.ts planIngest) — inside qa/pi_harness_audit.py and CI.",
  "url": "https://github.com/aisportsbettingcontact/off-duty-locks",
  "config": {
    "skillGlobs": [".pi/skills/**"]
  }
}
```

- [ ] **Step 2: Append to `.dockerignore`** — add `qm.pack.json` at the end of the existing "Agent-harness files" block (after the `SKILLS.md` line):

```
qm.pack.json
```

- [ ] **Step 3: Verify JSON parses**

Run: `python3 -m json.tool qm.pack.json > /dev/null && echo JSON-OK`
Expected: `JSON-OK`

- [ ] **Step 4: Commit**

```bash
git add qm.pack.json .dockerignore
git commit -m "feat(qm): canonical skill-pack contract — .pi/skills/** as a QM-importable pack"
```

---

### Task 2: Verifier — `qa/qm_pack_verify.py` (TDD)

**Files:**
- Test: `tests/test_qm_pack_verify.py`
- Create: `qa/qm_pack_verify.py`

**Interfaces:**
- Consumes: `qm.pack.json` from Task 1.
- Produces: module-level `audit(root: Path) -> list[str]` (empty = pass), `REPO_URL` constant, and CLI `python3 qa/qm_pack_verify.py --repo-root .` exiting 0/1. Task 3 imports the module by file path and calls `audit(root)`.

- [ ] **Step 1: Write the failing tests** — `tests/test_qm_pack_verify.py`

```python
"""Tests for the QM skill-pack verifier (qa/qm_pack_verify.py)."""

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "qm_pack_verify", REPO_ROOT / "qa" / "qm_pack_verify.py"
)
qm_pack_verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qm_pack_verify)


def _write_minimal_pack(root: Path) -> None:
    """Minimal tree the verifier should accept: one skill, matching pack."""
    skill = root / ".pi" / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: a demo skill\n---\nbody\n"
    )
    (root / "qm.pack.json").write_text(json.dumps({
        "url": "https://github.com/aisportsbettingcontact/off-duty-locks",
        "config": {"skillGlobs": [".pi/skills/**"]},
    }))


def test_real_repo_passes():
    errors = qm_pack_verify.audit(REPO_ROOT)
    assert errors == [], f"pack verifier errors in real repo: {errors}"


def test_minimal_tree_passes(tmp_path):
    _write_minimal_pack(tmp_path)
    assert qm_pack_verify.audit(tmp_path) == []


def test_missing_pack_fails(tmp_path):
    _write_minimal_pack(tmp_path)
    (tmp_path / "qm.pack.json").unlink()
    assert any("qm.pack.json" in e for e in qm_pack_verify.audit(tmp_path))


def test_wrong_url_fails(tmp_path):
    _write_minimal_pack(tmp_path)
    pack = json.loads((tmp_path / "qm.pack.json").read_text())
    pack["url"] = "https://github.com/someone-else/other-repo"
    (tmp_path / "qm.pack.json").write_text(json.dumps(pack))
    assert any("url" in e for e in qm_pack_verify.audit(tmp_path))


def test_url_git_suffix_tolerated(tmp_path):
    _write_minimal_pack(tmp_path)
    pack = json.loads((tmp_path / "qm.pack.json").read_text())
    pack["url"] = "https://github.com/aisportsbettingcontact/off-duty-locks.git"
    (tmp_path / "qm.pack.json").write_text(json.dumps(pack))
    assert qm_pack_verify.audit(tmp_path) == []


def test_single_star_does_not_cross_slash():
    # QM glob semantics: * -> [^/]* so ".pi/*" must NOT match ".pi/skills/demo"
    assert not qm_pack_verify.qm_matches_any(".pi/skills/demo", [".pi/*"])
    assert qm_pack_verify.qm_matches_any(".pi/skills/demo", [".pi/skills/**"])
    assert qm_pack_verify.qm_matches_any(".pi/skills/demo", [".pi/skills/*"])


def test_malformed_frontmatter_fails(tmp_path):
    _write_minimal_pack(tmp_path)
    bad = tmp_path / ".pi" / "skills" / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter\n")
    errors = qm_pack_verify.audit(tmp_path)
    assert any("malformed" in e for e in errors)


def test_name_collision_fails(tmp_path):
    _write_minimal_pack(tmp_path)
    dupe = tmp_path / ".pi" / "skills" / "dupe"
    dupe.mkdir()
    (dupe / "SKILL.md").write_text(
        "---\nname: demo\ndescription: same name as demo\n---\nbody\n"
    )
    errors = qm_pack_verify.audit(tmp_path)
    assert any("collision" in e for e in errors)


def test_corpus_drift_fails(tmp_path):
    # A skill in .pi/skills that the globs do NOT select = pi-only drift.
    _write_minimal_pack(tmp_path)
    pack = json.loads((tmp_path / "qm.pack.json").read_text())
    pack["config"]["skillGlobs"] = [".pi/skills/demo"]
    (tmp_path / "qm.pack.json").write_text(json.dumps(pack))
    extra = tmp_path / ".pi" / "skills" / "extra"
    extra.mkdir()
    (extra / "SKILL.md").write_text(
        "---\nname: extra\ndescription: unselected skill\n---\nbody\n"
    )
    errors = qm_pack_verify.audit(tmp_path)
    assert any("drift" in e for e in errors)


def test_package_installs_are_not_scanned(tmp_path):
    # .pi/git/ holds auto-installed skill packages — QM ingests the FETCHED
    # (committed) repo, so local installs must never enter the pack census.
    _write_minimal_pack(tmp_path)
    pkg = tmp_path / ".pi" / "git" / "github.com" / "x" / "pack" / "sk"
    pkg.mkdir(parents=True)
    (pkg / "SKILL.md").write_text("---\nname: demo\ndescription: dupe\n---\n")
    assert qm_pack_verify.audit(tmp_path) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/src/off-duty-locks && .venv/bin/python -m pytest tests/test_qm_pack_verify.py -q`
Expected: collection ERROR — `FileNotFoundError` for `qa/qm_pack_verify.py`.

- [ ] **Step 3: Write `qa/qm_pack_verify.py`**

```python
#!/usr/bin/env python3
"""QM skill-pack verifier — model-free.

One corpus, two consumers: pi serves .pi/skills/ through its resource loader;
QM imports the same directory as a git skill pack (qm.pack.json). This
verifier makes the QM side as bulletproof as the harness audit makes the pi
side, deterministically and offline:

  1. qm.pack.json parses, points at this repo, and skillGlobs is well-formed;
  2. every SKILL.md the pack selects carries frontmatter with non-empty
     name and description (QM's normalize step silently skips malformed
     skills at ingest — the quiet loss this catches first);
  3. no two selected skills share a name (QM flags `collision` at ingest;
     we fail here so the pack always imports whole);
  4. intertwine: the pack's selection equals the .pi/skills census, so pi
     and QM can never drift apart silently.

Glob semantics are ported verbatim from yc-software/qm
src/skills/ingest.ts (globToRegExp / matchesAny / planIngest): patterns
match the skill DIRECTORY path, not the SKILL.md path; `**` -> `.*`,
`*` -> `[^/]*`. Keep in lockstep with upstream.

Usage: python3 qa/qm_pack_verify.py --repo-root .
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_URL = "https://github.com/aisportsbettingcontact/off-duty-locks"

# Local-only trees that are not part of the fetched (committed) repo QM
# ingests: package auto-installs, virtualenvs, git internals, runtime data.
SKIP_DIRS = {".git", ".venv", "node_modules", "data"}
SKIP_PREFIXES = (".pi/git/", ".pi/npm/")

FRONTMATTER_RE = re.compile(r"^---\r?\n([\s\S]*?)\r?\n---")


def qm_glob_to_regexp(glob: str) -> re.Pattern[str]:
    pattern = ".*".join(
        "[^/]*".join(re.escape(part) for part in segment.split("*"))
        for segment in glob.split("**")
    )
    return re.compile(f"^{pattern}$")


def qm_matches_any(dir_path: str, globs: list[str] | None) -> bool:
    return bool(globs) and any(qm_glob_to_regexp(g).match(dir_path) for g in globs)


def parse_frontmatter(raw: str) -> dict[str, str] | None:
    match = FRONTMATTER_RE.match(raw)
    if not match:
        return None
    attrs: dict[str, str] = {}
    for line in match.group(1).splitlines():
        kv = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
        if kv:
            attrs[kv.group(1)] = kv.group(2).strip()
    return attrs


def _skill_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("SKILL.md"):
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        if parts[0] in SKIP_DIRS:
            continue
        if any(rel.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        out.append(path)
    return sorted(out)


def audit(root: Path) -> list[str]:
    errors: list[str] = []

    pack_path = root / "qm.pack.json"
    if not pack_path.is_file():
        return [f"missing qm.pack.json under {root}"]
    try:
        pack = json.loads(pack_path.read_text())
    except json.JSONDecodeError as exc:
        return [f"qm.pack.json is not valid JSON: {exc}"]

    url = str(pack.get("url", ""))
    normalized = url.rstrip("/")
    normalized = normalized[:-4] if normalized.endswith(".git") else normalized
    if normalized != REPO_URL:
        errors.append(f"qm.pack.json url must be {REPO_URL}, got {url!r}")

    config = pack.get("config") or {}
    globs = config.get("skillGlobs")
    if not (
        isinstance(globs, list)
        and globs
        and all(isinstance(g, str) for g in globs)
    ):
        errors.append("qm.pack.json config.skillGlobs must be a non-empty list of strings")
        return errors
    exclude = config.get("exclude")

    selected_dirs: set[str] = set()
    names_seen: dict[str, str] = {}
    for skill_md in _skill_files(root):
        rel = skill_md.relative_to(root).as_posix()
        skill_dir = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if not qm_matches_any(skill_dir, globs):
            continue
        if qm_matches_any(skill_dir, exclude):
            continue
        attrs = parse_frontmatter(skill_md.read_text())
        if not attrs or not attrs.get("name") or not attrs.get("description"):
            errors.append(
                f"{rel}: malformed frontmatter — QM ingest would silently skip it "
                "(needs non-empty name and description)"
            )
            continue
        name = attrs["name"]
        if name in names_seen:
            errors.append(
                f"skill name collision: {name!r} in {names_seen[name]} and {rel} "
                "(QM flags this at ingest)"
            )
        else:
            names_seen[name] = rel
        selected_dirs.add(skill_dir)

    if not selected_dirs and not errors:
        errors.append("pack selects zero skills — skillGlobs match nothing")

    census = {
        p.parent.relative_to(root).as_posix()
        for p in (root / ".pi" / "skills").glob("*/SKILL.md")
    }
    if selected_dirs != census:
        pack_only = sorted(selected_dirs - census)
        pi_only = sorted(census - selected_dirs)
        errors.append(
            f"pi<->QM corpus drift: pack-only={pack_only} pi-only={pi_only} "
            "(one corpus, two consumers — selection must equal .pi/skills census)"
        )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)
    errors = audit(Path(args.repo_root).resolve())
    if errors:
        print("qm pack verify: FAIL")
        for err in errors:
            print(f"  - {err}")
        return 1
    print("qm pack verify: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/src/off-duty-locks && .venv/bin/python -m pytest tests/test_qm_pack_verify.py -v`
Expected: 10 passed.

- [ ] **Step 5: Run the CLI against the repo**

Run: `.venv/bin/python qa/qm_pack_verify.py --repo-root .`
Expected: `qm pack verify: PASS`, exit 0.

- [ ] **Step 6: Commit**

```bash
git add tests/test_qm_pack_verify.py qa/qm_pack_verify.py
git commit -m "feat(qm): pack verifier — QM ingest semantics ported, pi↔QM corpus intertwine enforced (TDD)"
```

---

### Task 3: qm-pack layer inside the harness audit

**Files:**
- Modify: `qa/pi_harness_audit.py` (add `_qm_pack_errors` + call it in `audit`)
- Modify: `tests/test_pi_harness_audit.py` (minimal-harness helper gains a valid pack; new drift test)

**Interfaces:**
- Consumes: `qm_pack_verify.audit(root) -> list[str]` (loaded by file path **relative to the audit module**, so tmp-path trees are verified with the real verifier code).
- Produces: `pi_harness_audit.audit(root)` now also returns qm-pack errors prefixed `"qm-pack: "`.

- [ ] **Step 1: Extend the minimal-harness test helper** — in `tests/test_pi_harness_audit.py`, add a valid pack to `_write_minimal_harness`. Append at the end of the function body:

```python
    (root / "qm.pack.json").write_text(json.dumps({
        "url": "https://github.com/aisportsbettingcontact/off-duty-locks",
        "config": {"skillGlobs": ["skills/**", ".pi/skills/**"]},
    }))
```

(The tmp trees keep their skills under `.pi/skills/`, so `.pi/skills/**` selects them; `skills/**` is harmless.)

- [ ] **Step 2: Add the failing drift test** — append to `tests/test_pi_harness_audit.py`:

```python
def test_audit_includes_qm_pack_layer(tmp_path):
    _write_minimal_harness(tmp_path)
    # Break the pack: select nothing -> qm layer must surface the failure.
    (tmp_path / "qm.pack.json").write_text(json.dumps({
        "url": "https://github.com/aisportsbettingcontact/off-duty-locks",
        "config": {"skillGlobs": ["nonexistent/**"]},
    }))
    errors = pi_harness_audit.audit(tmp_path)
    assert any(e.startswith("qm-pack: ") for e in errors)
```

- [ ] **Step 3: Run to verify the new test fails**

Run: `.venv/bin/python -m pytest tests/test_pi_harness_audit.py::test_audit_includes_qm_pack_layer -q`
Expected: FAIL — no `qm-pack: ` errors yet.

- [ ] **Step 4: Wire the layer** — in `qa/pi_harness_audit.py`, add below the imports:

```python
def _qm_pack_errors(root: Path) -> list[str]:
    """Run the QM pack verifier (qa/qm_pack_verify.py, loaded from this
    module's own directory so tmp-path trees are checked with real code)."""
    import importlib.util

    verifier_path = Path(__file__).resolve().parent / "qm_pack_verify.py"
    if not verifier_path.is_file():
        return ["qm-pack: missing qa/qm_pack_verify.py"]
    spec = importlib.util.spec_from_file_location("qm_pack_verify", verifier_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [f"qm-pack: {err}" for err in module.audit(root)]
```

and at the end of `audit()`, immediately before `return errors`:

```python
    errors.extend(_qm_pack_errors(root))
```

- [ ] **Step 5: Full audit test file green**

Run: `.venv/bin/python -m pytest tests/test_pi_harness_audit.py tests/test_qm_pack_verify.py -q`
Expected: 18 passed (8 audit + 10 pack).

- [ ] **Step 6: CLI still passes on the repo**

Run: `.venv/bin/python qa/pi_harness_audit.py --repo-root .`
Expected: `pi harness audit: PASS`, exit 0 (qm-pack layer included).

- [ ] **Step 7: Commit**

```bash
git add qa/pi_harness_audit.py tests/test_pi_harness_audit.py
git commit -m "feat(qa): harness audit gains qm-pack layer — CI now enforces both harnesses"
```

---

### Task 4: `qm-harness` skill (corpus 3 → 4)

**Files:**
- Create: `.pi/skills/qm-harness/SKILL.md`

**Interfaces:**
- Produces: fourth repo-local skill; must satisfy both the audit frontmatter check and the pack verifier (it joins the pack selection and the pi census simultaneously — the intertwine check proves it).

- [ ] **Step 1: Write `.pi/skills/qm-harness/SKILL.md`**

```markdown
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
```

- [ ] **Step 2: Audit + pack verify pick it up (intertwine proves itself)**

Run: `.venv/bin/python qa/pi_harness_audit.py --repo-root . && .venv/bin/python qa/qm_pack_verify.py --repo-root .`
Expected: both PASS — the new skill is simultaneously in the pack selection and the pi census; any asymmetry would have failed the drift check.

- [ ] **Step 3: Commit**

```bash
git add .pi/skills/qm-harness/
git commit -m "feat(qm): qm-harness skill — pack import, sandbox law, corpus contract (3→4 repo-local skills)"
```

---

### Task 5: Runbook — `docs/qm-harness.md`

**Files:**
- Create: `docs/qm-harness.md`

**Interfaces:**
- Produces: runbook referenced by the skill (Task 4) and context files (Task 6).

- [ ] **Step 1: Write `docs/qm-harness.md`**

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add docs/qm-harness.md
git commit -m "docs: QM harness runbook — pack + sandbox seams, carried laws, owner-gated deployment"
```

---

### Task 6: Context files gain QM

**Files:**
- Modify: `HARNESS.md`, `SKILLS.md`, `AGENTS.md`, `CLAUDE.md`, `LLM.md`

**Interfaces:**
- Consumes: paths `qm.pack.json`, `docs/qm-harness.md`, skill `qm-harness` from Tasks 1/4/5.

- [ ] **Step 1: `HARNESS.md`** — add a QM row to the harness table. Replace the Codex row line:

```
| Codex | OpenAI Codex CLI/cloud | `AGENTS.md` (native), `CODEX.md` | model `gpt-5.6-sol` per LLM.md |
```

with:

```
| Codex | OpenAI Codex CLI/cloud | `AGENTS.md` (native), `CODEX.md` | model `gpt-5.6-sol` per LLM.md |
| QM (org layer) | yc-software/qm core driving pi/Claude Code/OpenCode/Codex in scope sandboxes | pack-imported `.pi/skills/` corpus; in-sandbox: repo clone + full pi wiring | `qm.pack.json` (pack contract), `docs/qm-harness.md` (runbook) |
```

- [ ] **Step 2: `SKILLS.md`** — (a) add the skill row after the `railway-ops` table line:

```
| `qm-harness` | Working with or from QM — pack imports, scope sandboxes, corpus contract |
```

(b) append a new section at the end of the file:

```markdown

## QM pack contract (one corpus, two consumers)

`qm.pack.json` publishes `.pi/skills/**` as a QM-importable git skill pack —
the same corpus pi serves locally. `qa/qm_pack_verify.py` (run inside
`qa/pi_harness_audit.py` and CI) enforces QM's ingest rules offline and the
intertwine law: pack selection must equal the `.pi/skills` census. Add or
rename a skill → the audit fails until both consumers agree. Runbook:
`docs/qm-harness.md`.
```

- [ ] **Step 3: `AGENTS.md`** — add law 9 after law 8 (the brand-law list item):

```markdown
9. **QM law.** This repo is a QM skill pack (`qm.pack.json`, verified in CI)
   and its laws bind inside QM scope sandboxes. Never wire any API key into
   QM's keychain, org config, or harness credentials; QM deployment is
   owner-gated (docs/qm-harness.md).
```

- [ ] **Step 4: `CLAUDE.md`** — in the authority table, after the "pi runbook" row, add:

```
| QM integration (pack + sandbox seams) | `docs/qm-harness.md` |
| QM pack contract | `qm.pack.json` (verified by `qa/qm_pack_verify.py`) |
```

- [ ] **Step 5: `LLM.md`** — append to the end of the "API credit law" section:

```markdown

The prohibition extends to QM (docs/qm-harness.md): never wire any API key
into QM's keychain, org config, or harness credentials. QM-side model spend
is a separate, owner-approved decision with its own key — never one
belonging to this repo or to Dime AI Chat.
```

- [ ] **Step 6: Audit still green (context files + pack + corpus)**

Run: `.venv/bin/python qa/pi_harness_audit.py --repo-root .`
Expected: `pi harness audit: PASS`.

- [ ] **Step 7: Commit**

```bash
git add HARNESS.md SKILLS.md AGENTS.md CLAUDE.md LLM.md
git commit -m "feat(harness): context files gain QM — harness row, pack contract, keychain credit law"
```

---

### Task 7: Final verification + PR

**Files:** none new.

- [ ] **Step 1: Full offline verification, real output**

Run:
```bash
cd ~/src/off-duty-locks
.venv/bin/python -m pytest -q
.venv/bin/python qa/verify.py --repo-root .
.venv/bin/python qa/pi_harness_audit.py --repo-root .
.venv/bin/python qa/qm_pack_verify.py --repo-root .
```
Expected: pytest ~198 passed (187 + 11 new); verify.py 9 pass exit 0; both audits PASS.

- [ ] **Step 2: pi loader census still ALL PASS with the fourth skill**

Run: `node /private/tmp/claude-501/-Users-danielwalker-src-ai-sports-betting-dime-ai/fd933b41-1ebc-4cc8-8f02-e22818fa295b/scratchpad/odl-loader-census.mjs`
(First update the script's `localNames` list to `["pipeline-verify", "fixture-provenance", "railway-ops", "qm-harness"]`.)
Expected: `LOADER CENSUS: ALL PASS` — qm-harness loads in pi too.

- [ ] **Step 3: Production neutrality**

Run: `git diff main --stat -- src/ Dockerfile railway.toml railway.web.json gunicorn.conf.py`
Expected: empty.

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin feat/qm-harness-integration
gh pr create --title "feat(qm): QM harness integration — skill-pack contract, ingest-semantics verifier, pi↔QM intertwine" --body "$(cat <<'EOF'
## Summary

Wires this repo into QM (yc-software/qm — org-level multiplayer agent harness) at the same depth as the pi foundation (PR #25), and intertwines the two: **one skill corpus, two consumers**. Spec: docs/superpowers/specs/2026-08-02-qm-harness-integration-design.md (approved).

**Production-behavior neutral**; zero API-billed automation; QM deployment stays owner-gated and documented, never executed.

### Pack contract
- qm.pack.json — this repo's URL + `skillGlobs [".pi/skills/**"]`; public repo, no import credential needed; dockerignored

### Verifier (TDD, 10 tests)
- qa/qm_pack_verify.py — lockstep port of qm src/skills/ingest.ts semantics (globs match the skill DIRECTORY; `**`→`.*`, `*`→`[^/]*`; frontmatter normalize contract; collision detection) plus the **intertwine law**: pack selection == .pi/skills census, so pi's loader and QM's pack can never silently drift
- qa/pi_harness_audit.py gains a qm-pack layer (+1 test) — the existing CI step now enforces both harnesses; no new workflow surface

### Corpus + knowledge
- .pi/skills/qm-harness/SKILL.md — fourth repo-local skill (pack import steps, sandbox law, corpus contract); joins both consumers, proving the intertwine check live
- docs/qm-harness.md — runbook: two seams, carried laws (keychain prohibition, compliance, models), owner-gated deployment path
- HARNESS/SKILLS/AGENTS/CLAUDE/LLM.md — QM rows + QM law (never wire API keys into QM keychain/org config)

## Verification

(real output pasted from Task 7 Steps 1–3)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: CI green**

Run: `gh pr checks --watch`
Expected: required "CI" check passes (audit step now includes qm-pack layer).

---

## Self-Review (completed)

- **Spec coverage:** spec §1 → Task 1; §2 → Tasks 2–3; §3 → Tasks 4–5; §4 → Tasks 6–7. Non-goals honored (no deployment, no new workflow).
- **Placeholder scan:** all file contents inline; no TBDs.
- **Type consistency:** `audit(root: Path) -> list[str]` identical across verifier, audit layer, and tests; `qm_matches_any` name matches between implementation and glob-semantics test; pack URL constant matches Task 1 JSON; skill dir names consistent (`qm-harness`).
