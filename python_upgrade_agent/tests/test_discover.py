"""Tests for discover.py using a temp mini-repo."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from python_upgrade_agent.discover import (
    _build_pattern,
    candidate_paths,
    git_grep,
)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def mini_repo(tmp_path: Path) -> Path:
    """Create a tiny git repo with seeded files."""
    _git(["init", "-q", "-b", "main"], tmp_path)
    _git(["config", "user.email", "t@t"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)

    (tmp_path / "build_scripts" / "windows" / "scripts").mkdir(parents=True)
    (tmp_path / "build_scripts" / "windows" / "scripts" / "build.cmd").write_text(
        "set PYTHON_VERSION=3.13.13\n", encoding="utf-8",
    )
    (tmp_path / "azure-pipelines.yml").write_text(
        "jobs:\n  - task: UsePythonVersion@0\n    inputs:\n      versionSpec: '3.13'\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "setup.py").write_text(
        "classifiers = ['Programming Language :: Python :: 3.13']\n",
        encoding="utf-8",
    )
    # File that should be excluded by deny-list:
    (tmp_path / "LICENSE").write_text("(c) 3.13 example\n", encoding="utf-8")
    # File that should be excluded by pathspec:
    recordings = tmp_path / "src" / "recordings"
    recordings.mkdir()
    (recordings / "test_x.yaml").write_text("python: 3.13\n", encoding="utf-8")

    _git(["add", "-A"], tmp_path)
    _git(["commit", "-q", "-m", "init"], tmp_path)
    return tmp_path


def test_build_pattern_matches_minor_and_patch():
    import re
    pat = re.compile(_build_pattern("3.13"))
    assert pat.search("versionSpec: 3.13")
    assert pat.search("PYTHON_VERSION=3.13.13")
    assert pat.search("'3.13.7'")
    # boundary checks
    assert not pat.search("3.130")
    assert not pat.search("3.13a")
    assert not pat.search("13.13")


def test_build_pattern_matches_dotless_identifiers():
    import re
    pat = re.compile(_build_pattern("3.13"))
    # dotless identifier forms used in CI matrix keys and job IDs
    assert pat.search("Python313:")
    assert pat.search("- job: AutomationFullTestPython313ProfileLatest")
    assert pat.search("py313")
    assert pat.search("python313-env")
    # must not match prefixes of unrelated higher patches
    assert not pat.search("Python3130")
    assert not pat.search("py3131")
    # must not match a different minor
    assert not pat.search("Python314")


def test_git_grep_finds_expected_files(mini_repo: Path):
    cands = git_grep("3.13", mini_repo)
    paths = candidate_paths(cands)
    assert "build_scripts/windows/scripts/build.cmd" in paths
    assert "azure-pipelines.yml" in paths
    assert "src/setup.py" in paths


def test_git_grep_excludes_recordings(mini_repo: Path):
    cands = git_grep("3.13", mini_repo)
    paths = candidate_paths(cands)
    assert not any("recordings" in p for p in paths)


def test_git_grep_excludes_deny_files(mini_repo: Path):
    cands = git_grep("3.13", mini_repo)
    paths = candidate_paths(cands)
    assert "LICENSE" not in paths


def test_git_grep_includes_context(mini_repo: Path):
    cands = git_grep("3.13", mini_repo)
    # build.cmd has just one line in our fixture; context should be empty but
    # the matched line text must include the version.
    bc = [c for c in cands if c.path.endswith("build.cmd")]
    assert bc
    assert "3.13.13" in bc[0].line_text
