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
