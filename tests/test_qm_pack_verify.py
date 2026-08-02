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
