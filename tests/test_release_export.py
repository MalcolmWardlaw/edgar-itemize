"""Tests for scripts/release/export_public.py (the public-tree export and its pattern gate).

The export tool is not itself exported, so in the public tree this module skips. Forbidden
strings planted below are assembled from fragments so this file never matches the gate.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "release" / "export_public.py"

if not TOOL.is_file():
    pytest.skip("export tool not present (public tree)", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("export_public", TOOL)
ep = importlib.util.module_from_spec(_spec)
sys.modules["export_public"] = ep
_spec.loader.exec_module(ep)  # type: ignore[union-attr]

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
       "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run([*GIT, "init", "-q"], cwd=repo, check=True)
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    subprocess.run([*GIT, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*GIT, "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


BASE = {
    "src/pkg/__init__.py": "x = 1\n",
    "src/pkg/data.bin": "",
    "README.md": "# demo\n",
    "docs/OUTPUT_CONTRACT.md": "contract\n",
    "docs/PRIVATE_NOTES.md": "not shipped\n",
    "scripts/run_diff.py": "print('diff')\n",
    "scripts/other.py": "print('lab')\n",
}
ALLOW = ("src/", "README.md", "docs/OUTPUT_CONTRACT.md", "scripts/run_diff.py")


def test_allowlist_export(tmp_path: Path) -> None:
    repo = _repo(tmp_path, BASE)
    dest = tmp_path / "out"
    res = ep.export(repo, "HEAD", dest, allowlist=ALLOW)
    got = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    assert got == sorted(["src/pkg/__init__.py", "src/pkg/data.bin", "README.md",
                          "docs/OUTPUT_CONTRACT.md", "scripts/run_diff.py"])
    assert res.files == got and res.missing == []
    assert not (dest / "docs/PRIVATE_NOTES.md").exists()
    assert not (dest / "scripts/other.py").exists()


def test_nonempty_dest_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path, BASE)
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "keep.txt").write_text("x")
    with pytest.raises(FileExistsError):
        ep.export(repo, "HEAD", dest, allowlist=ALLOW)
    ep.export(repo, "HEAD", dest, allowlist=ALLOW, force=True)
    assert (dest / "keep.txt").exists() and (dest / "README.md").exists()


def test_denylist_raises(tmp_path: Path) -> None:
    files = dict(BASE)
    files["src/pkg/viewer_config/runs.yaml"] = "a: 1\n"
    repo = _repo(tmp_path, files)
    dest = tmp_path / "out"
    with pytest.raises(ep.DenylistError, match="viewer_config"):
        ep.export(repo, "HEAD", dest, allowlist=ALLOW)
    assert not dest.exists()


@pytest.mark.parametrize("path,hit", [
    ("CLAUDE.md", True),
    ("src/CLAUDE.md", True),
    ("docs/BOX_ACCESS_NEXT.md", True),
    ("docs/onboarding/a.md", True),
    ("scripts/turn13/probe.py", True),
    ("runs/x.pid", True),
    ("a/.claude/settings.json", True),
    ("docs/OUTPUT_CONTRACT.md", False),
    ("scripts/run_diff.py", False),
    ("tests/test_turn13.py", False),
])
def test_denylist_match(path: str, hit: bool) -> None:
    assert (ep.denylist_match(path) is not None) == hit


def test_scan_planted_hit_and_clean(tmp_path: Path) -> None:
    host = "bad" + "-ideas"
    addr = "100" + ".107.2.74"
    files = dict(BASE)
    files["src/pkg/conf.py"] = f"a = 1\nurl = 'http://{addr}:8000'\nb = '{host}'\n"
    files["CITATION.cff"] = "email: malcolm." + "wardlaw" + "@uga.edu\n"
    repo = _repo(tmp_path, files)
    dest = tmp_path / "out"
    res = ep.export(repo, "HEAD", dest, allowlist=(*ALLOW, "CITATION.cff"))
    hits, n_text, n_bin = ep.scan_paths(dest, res.files)
    assert {(h.path, h.line, h.label) for h in hits} == {
        ("src/pkg/conf.py", 2, "tailnet address"),
        ("src/pkg/conf.py", 3, "host name"),
    }
    assert "src/pkg/conf.py:2: tailnet address" in str(hits[0])

    clean = _repo(tmp_path / "c", BASE)
    dest2 = tmp_path / "out2"
    res2 = ep.export(clean, "HEAD", dest2, allowlist=ALLOW)
    assert ep.scan_paths(dest2, res2.files)[0] == []


def test_scan_skips_binary_and_flags_other_email(tmp_path: Path) -> None:
    d = tmp_path
    (d / "b.bin").write_bytes(b"\0" + ("sie" + "nna").encode())
    (d / "t.txt").write_text("contact: someone." + "wardlaw" + "@example.org\n")
    hits, n_text, n_bin = ep.scan_paths(d, ["b.bin", "t.txt"])
    assert (n_text, n_bin) == (1, 1)
    assert [(h.path, h.label) for h in hits] == [("t.txt", "personal email")]


def test_missing_allowlisted_path_warns(tmp_path: Path) -> None:
    repo = _repo(tmp_path, BASE)
    dest = tmp_path / "out"
    res = ep.export(repo, "HEAD", dest, allowlist=(*ALLOW, "examples/", "CHANGELOG.md"))
    assert res.missing == ["examples/", "CHANGELOG.md"]
    assert (dest / "README.md").exists()


def test_this_repository_allowlist_is_clean() -> None:
    files, _ = ep.working_tree_files(ROOT)
    assert files
    assert ep.check_denylist(files) == []
    hits, _, _ = ep.scan_paths(ROOT, files)
    assert hits == [], "\n".join(map(str, hits[:50]))
