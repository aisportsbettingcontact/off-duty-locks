#!/usr/bin/env python3
"""Render a run manifest (or any subset of its fields) as a Markdown summary.

Used by the Extract and Live Smoke workflows to append a human-readable run
summary to ``$GITHUB_STEP_SUMMARY`` without fragile inline heredocs. Reads a
manifest JSON file and writes Markdown bullet points to stdout. Never fails the
build: an unreadable/absent manifest prints a note and exits 0.
"""

from __future__ import annotations

import json
import sys

FIELDS = (
    "runId", "status", "freshnessState", "validationState",
    "rawRowCount", "validRowCount", "rejectedRowCount",
    "expectedTeamCount", "actualTeamCount",
    "requestCount", "retryCount", "durationSeconds",
    "sourceChecksum", "normalizedChecksum",
    "storageResult", "lastKnownGoodPreserved", "failureReason",
)


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else "manifest.json"
    try:
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except FileNotFoundError:
        print(f"- no manifest produced at `{path}`")
        return 0
    except (OSError, json.JSONDecodeError) as exc:
        print(f"- could not parse manifest `{path}`: {exc}")
        return 0

    for key in FIELDS:
        if key in manifest:
            print(f"- **{key}**: {manifest.get(key)}")
    failures = manifest.get("validationFailures") or []
    if failures:
        print(f"- **validationFailures**: {len(failures)}")
        for f in failures[:10]:
            print(f"  - `{f.get('code')}`: {f.get('message')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
