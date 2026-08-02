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
