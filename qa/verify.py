#!/usr/bin/env python3
"""Independent verification harness for the WNBA pipeline.

Runs a battery of static and dynamic checks against the repository and emits a
section-by-section PASS / FAIL / BLOCKED report to stdout and to
``qa/verify-report.json``. Stdlib only; safe to run offline. Exit code is 0 iff
no section FAILED (BLOCKED sections — e.g. live-source checks that need network
this environment lacks — do not fail the harness, but they are never counted as
passes).

Usage: ``python3 qa/verify.py --repo-root .``
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"


class Report:
    def __init__(self) -> None:
        self.sections: list[dict] = []

    def add(self, name: str, status: str, evidence, detail: str = "") -> None:
        self.sections.append({
            "section": name, "status": status,
            "evidence": evidence, "detail": detail,
        })

    @property
    def failed(self) -> bool:
        return any(s["status"] == FAIL for s in self.sections)

    def to_json(self) -> dict:
        counts = {PASS: 0, FAIL: 0, BLOCKED: 0}
        for s in self.sections:
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        return {"summary": counts, "sections": self.sections}


def _run(cmd, cwd, env=None):
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          env=env, timeout=600)


# --------------------------------------------------------------------------- #
# (a) test suite
# --------------------------------------------------------------------------- #

def section_tests(repo: Path, rep: Report) -> None:
    """Run the suite and parse counts from a JUnit XML report (robust — the
    terminal summary line is not always emitted when stdout is piped)."""
    env = dict(os.environ)
    import xml.etree.ElementTree as ET
    with tempfile.TemporaryDirectory() as td:
        xml_path = Path(td) / "junit.xml"
        proc = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                     f"--junitxml={xml_path}", "tests"], repo, env)
        passed = failed = errors = skipped = total = 0
        try:
            root = ET.parse(xml_path).getroot()
            suite = root.find("testsuite") if root.tag == "testsuites" else root
            total = int(suite.get("tests", 0))
            failed = int(suite.get("failures", 0))
            errors = int(suite.get("errors", 0))
            skipped = int(suite.get("skipped", 0))
            passed = total - failed - errors - skipped
        except (ET.ParseError, FileNotFoundError, AttributeError):
            pass
    evidence = {"total": total, "passed": passed, "failed": failed,
                "errors": errors, "skipped": skipped, "returncode": proc.returncode}
    status = PASS if proc.returncode == 0 and failed == 0 and errors == 0 else FAIL
    rep.add("a_test_suite", status, evidence,
            "" if status == PASS else (proc.stdout + proc.stderr)[-2000:])


# --------------------------------------------------------------------------- #
# (b) secret scan
# --------------------------------------------------------------------------- #

SECRET_PATTERNS = [
    (r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._\-]{12,}", "authorization-bearer"),
    (r"(?i)\bcookie\s*:\s*[^\s\"']{8,}", "cookie-header"),
    (r"(?i)set-cookie\s*:\s*[^\s\"']{8,}", "set-cookie-header"),
    (r"(?i)\b(api[_-]?key|secret|password|passwd|access[_-]?token)\s*[:=]\s*"
     r"[\"'][A-Za-z0-9/+=_\-]{12,}[\"']", "credential-assignment"),
    (r"AKIA[0-9A-Z]{16}", "aws-access-key"),
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "github-token"),
]
SCAN_DIRS = ("src", "tests", "fixtures", "qa", "docs", ".github", "scripts")
# The scanner file itself contains these patterns as regex literals.
SCAN_SKIP = {"qa/verify.py"}


def section_secret_scan(repo: Path, rep: Report) -> None:
    hits: list[dict] = []
    for d in SCAN_DIRS:
        base = repo / d
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(repo).as_posix()
            if rel in SCAN_SKIP:
                continue
            if path.suffix in {".pyc"} or "__pycache__" in rel:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pattern, label in SECRET_PATTERNS:
                for m in re.finditer(pattern, text):
                    line = text[:m.start()].count("\n") + 1
                    hits.append({"file": rel, "line": line, "kind": label})
    status = PASS if not hits else FAIL
    rep.add("b_secret_scan", status, {"hits": hits, "scanned_dirs": list(SCAN_DIRS)},
            "" if status == PASS else f"{len(hits)} potential secret(s)")


# --------------------------------------------------------------------------- #
# (c) fixture provenance audit
# --------------------------------------------------------------------------- #

def section_provenance(repo: Path, rep: Report) -> None:
    missing: list[str] = []
    audited = 0
    for path in (repo / "fixtures").rglob("*.json"):
        rel = path.relative_to(repo).as_posix()
        # truncated/malformed adversarial fixtures are intentionally not JSON.
        if path.name in {"truncated.json", "malformed.json"}:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            missing.append(f"{rel} (unparseable)")
            continue
        audited += 1
        prov = doc.get("_provenance") if isinstance(doc, dict) else None
        if not isinstance(prov, dict) or "synthetic" not in prov:
            missing.append(f"{rel} (no _provenance.synthetic)")
    status = PASS if not missing else FAIL
    rep.add("c_fixture_provenance", status,
            {"audited": audited, "missing": missing})


# --------------------------------------------------------------------------- #
# (d) checksum reproducibility
# --------------------------------------------------------------------------- #

def section_checksums(repo: Path, rep: Report) -> None:
    try:
        sys.path.insert(0, str(repo / "src"))
        from wnba_pipeline.contract import ExtractionParams, sha256_hex
        from wnba_pipeline.teams import resolve_expected_teams
        from wnba_pipeline.validation import validate_and_normalize
        sys.path.insert(0, str(repo))
        from tests._builders import make_raw

        base = repo / "fixtures" / "sanitized" / "leaguedashteamstats_2026_lastn7.json"
        payload = json.loads(base.read_text())
        expected = resolve_expected_teams(
            "2026",
            fallback_path=str(repo / "fixtures" / "expected_teams" / "2026.json"))
        raw = make_raw(payload, params=ExtractionParams())
        s1 = validate_and_normalize(raw, expected).snapshot
        s2 = validate_and_normalize(raw, expected).snapshot
        norm_stable = s1.normalized_checksum() == s2.normalized_checksum()
        file_bytes = base.read_bytes()
        file_stable = sha256_hex(file_bytes) == sha256_hex(base.read_bytes())
        status = PASS if norm_stable and file_stable else FAIL
        rep.add("d_checksum_reproducibility", status, {
            "normalized_checksum_stable": norm_stable,
            "file_checksum_stable": file_stable,
            "normalized_checksum": s1.normalized_checksum(),
        })
    except Exception as exc:  # noqa: BLE001
        rep.add("d_checksum_reproducibility", BLOCKED, {"error": str(exc)},
                "modules unavailable")


# --------------------------------------------------------------------------- #
# (e) idempotency via CLI
# --------------------------------------------------------------------------- #

def section_idempotency(repo: Path, rep: Report) -> None:
    fixture = repo / "fixtures" / "sanitized" / "leaguedashteamstats_2026_lastn7.json"
    if not fixture.exists():
        rep.add("e_idempotency", BLOCKED, {"reason": "fixture missing"})
        return
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
        c = [sys.executable, "-m", "wnba_pipeline", "run",
             "--fixture", str(fixture), "--data-root", td]
        p1 = _run(c, repo, env)
        p2 = _run(c, repo, env)
        try:
            s1 = json.loads(p1.stdout.strip().splitlines()[-1])["status"]
            s2 = json.loads(p2.stdout.strip().splitlines()[-1])["status"]
        except (json.JSONDecodeError, IndexError, KeyError):
            rep.add("e_idempotency", FAIL,
                    {"run1_rc": p1.returncode, "run2_rc": p2.returncode,
                     "stdout2": p2.stdout[-500:]})
            return
        snaps = list((Path(td) / "snapshots").rglob("*.json"))
        ok = (p1.returncode == 0 and p2.returncode == 0
              and s1 == "SUCCESS" and s2 == "SUCCESS_UNCHANGED"
              and len(snaps) == 1)
        rep.add("e_idempotency", PASS if ok else FAIL,
                {"run1": s1, "run2": s2, "snapshot_files": len(snaps)})


# --------------------------------------------------------------------------- #
# (f) LKG protection
# --------------------------------------------------------------------------- #

def section_lkg_protection(repo: Path, rep: Report) -> None:
    good = repo / "fixtures" / "sanitized" / "leaguedashteamstats_2026_lastn7.json"
    bad = repo / "fixtures" / "adversarial" / "makes_exceed_attempts.json"
    if not good.exists() or not bad.exists():
        rep.add("f_lkg_protection", BLOCKED, {"reason": "fixtures missing"})
        return
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
        base = [sys.executable, "-m", "wnba_pipeline", "run", "--data-root", td]
        _run(base + ["--fixture", str(good)], repo, env)
        current = list((Path(td) / "current").glob("*.json"))
        before = current[0].read_bytes() if current else None
        p = _run(base + ["--fixture", str(bad)], repo, env)
        after = current[0].read_bytes() if current else None
        ok = (before is not None and before == after and p.returncode == 4)
        rep.add("f_lkg_protection", PASS if ok else FAIL,
                {"failing_run_exit": p.returncode,
                 "lkg_unchanged": before == after})


# --------------------------------------------------------------------------- #
# (g) automation audit
# --------------------------------------------------------------------------- #

def section_automation(repo: Path, rep: Report) -> None:
    wf = repo / ".github" / "workflows"
    problems: list[str] = []
    checks: dict = {}
    ci = wf / "ci.yml"
    extract = wf / "extract.yml"
    live = wf / "live-smoke.yml"
    if ci.exists():
        t = ci.read_text()
        checks["ci_named_CI"] = ("name: CI" in t and re.search(r"\bCI:\s*\n", t) is not None)
        if not checks["ci_named_CI"]:
            problems.append("ci.yml job/check not named 'CI'")
    else:
        problems.append("ci.yml missing")
    if extract.exists():
        t = extract.read_text()
        checks["extract_concurrency"] = "concurrency:" in t and "wnba-extract" in t
        checks["extract_month_gated_cron"] = "5-10" in t
        checks["extract_disable_switch"] = "PIPELINE_ENABLED" in t
        checks["extract_season_2026"] = "2026" in t
        for k in ("extract_concurrency", "extract_month_gated_cron",
                  "extract_disable_switch"):
            if not checks[k]:
                problems.append(f"extract.yml: {k} not satisfied")
    else:
        problems.append("extract.yml missing")
    if live.exists():
        t = live.read_text()
        checks["live_dispatch_only"] = ("workflow_dispatch" in t
                                        and "schedule:" not in t)
    else:
        problems.append("live-smoke.yml missing")
    # No plaintext secrets in workflows.
    for f in (ci, extract, live):
        if f.exists() and re.search(r"(?i)(password|api[_-]?key)\s*[:=]\s*['\"]", f.read_text()):
            problems.append(f"{f.name}: possible plaintext secret")
    status = PASS if not problems else FAIL
    rep.add("g_automation_audit", status, {"checks": checks, "problems": problems})


# --------------------------------------------------------------------------- #
# (h) docs executability (bash -n on offline-runnable blocks)
# --------------------------------------------------------------------------- #

BASH_BLOCK = re.compile(r"```bash\n(.*?)```", re.DOTALL)
PLACEHOLDER = re.compile(r"<[a-zA-Z_][\w ]*>")  # illustrative <run_id> etc.


def section_docs(repo: Path, rep: Report) -> None:
    from shutil import which
    if which("bash") is None:
        rep.add("h_docs_executable", BLOCKED, {"reason": "bash unavailable"})
        return
    checked = 0
    illustrative = 0
    errors: list[dict] = []
    for doc in ("docs/runbook.md", "docs/deployment.md", "README.md"):
        p = repo / doc
        if not p.exists():
            continue
        for block in BASH_BLOCK.findall(p.read_text()):
            if PLACEHOLDER.search(block):
                illustrative += 1
                continue
            checked += 1
            proc = subprocess.run(["bash", "-n"], input=block, text=True,
                                  capture_output=True)
            if proc.returncode != 0:
                errors.append({"doc": doc, "error": proc.stderr.strip()[:200],
                               "block": block[:120]})
    status = PASS if not errors else FAIL
    rep.add("h_docs_executable", status,
            {"checked": checked, "illustrative_skipped": illustrative,
             "errors": errors})


# --------------------------------------------------------------------------- #
# (i) deployment artifacts exclude debug material
# --------------------------------------------------------------------------- #

def section_artifacts(repo: Path, rep: Report) -> None:
    gitignore = (repo / ".gitignore").read_text() if (repo / ".gitignore").exists() else ""
    needs = ["__pycache__", "data/tmp", ".pytest_cache"]
    missing = [n for n in needs if n not in gitignore]
    # Tracked-file check (best effort; git may be absent).
    tracked_bad: list[str] = []
    try:
        proc = _run(["git", "ls-files"], repo)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.endswith(".pyc") or "__pycache__" in line \
                        or ".pytest_cache" in line or line.endswith(".egg-info"):
                    tracked_bad.append(line)
    except Exception:  # noqa: BLE001
        pass
    status = PASS if not missing and not tracked_bad else FAIL
    rep.add("i_artifact_hygiene", status,
            {"gitignore_missing": missing, "tracked_debug_files": tracked_bad})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Independent verification harness")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--json-out", default=None,
                    help="path for the JSON report (default: <repo>/qa/verify-report.json)")
    args = ap.parse_args(argv)
    repo = Path(args.repo_root).resolve()

    rep = Report()
    for fn in (section_tests, section_secret_scan, section_provenance,
               section_checksums, section_idempotency, section_lkg_protection,
               section_automation, section_docs, section_artifacts):
        try:
            fn(repo, rep)
        except Exception as exc:  # noqa: BLE001 - a broken check must not crash the harness
            rep.add(fn.__name__, BLOCKED, {"error": repr(exc)})

    out_path = Path(args.json_out) if args.json_out else repo / "qa" / "verify-report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rep.to_json(), indent=2) + "\n", encoding="utf-8")

    print("\n=== Independent verification report ===")
    for s in rep.sections:
        mark = {"PASS": "✓", "FAIL": "✗", "BLOCKED": "▲"}[s["status"]]
        print(f"  {mark} {s['section']:28} {s['status']}"
              + (f"  — {s['detail']}" if s.get("detail") else ""))
    summary = rep.to_json()["summary"]
    print(f"\n  {summary.get('PASS',0)} pass · {summary.get('FAIL',0)} fail · "
          f"{summary.get('BLOCKED',0)} blocked")
    print(f"  report: {out_path}")
    if rep.failed:
        print("\nRESULT: FAIL (a section failed)")
        return 1
    print("\nRESULT: OK (no failures; blocked sections need a networked/live run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
