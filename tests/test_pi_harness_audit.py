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
