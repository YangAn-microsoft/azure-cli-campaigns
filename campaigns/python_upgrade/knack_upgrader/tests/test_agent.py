"""Tests for the knack_upgrader pipeline state machine.

Stage 3 (clone/branch/push/PR) is not exercised here — those steps shell out
to git/gh. We test that stages 1 and 2 short-circuit correctly and that
``_apply_edits_in_tree`` integrates the deterministic edits.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from campaigns.python_upgrade.knack_upgrader import agent, pypi


class _Status:
    def __init__(self, supports: bool, latest: str | None):
        self.supports = supports
        self.latest_version = latest


def test_completed_when_pypi_already_supports(monkeypatch):
    monkeypatch.setattr(
        pypi, "check_pypi",
        lambda *a, **k: pypi.PyPIStatus("knack", "3.14", True, "0.14.0"),
    )
    result = agent.run_pipeline(repo="owner/knack", new_minor="3.14", dry_run=True)
    assert result.status == "completed"
    assert "0.14.0" in result.notes
    assert result.pr is None


def test_in_progress_when_open_pr_exists(monkeypatch):
    monkeypatch.setattr(
        pypi, "check_pypi",
        lambda *a, **k: pypi.PyPIStatus("knack", "3.14", False, "0.13.0"),
    )
    monkeypatch.setattr(agent, "find_open_pr", lambda repo, branch: 42)
    result = agent.run_pipeline(repo="owner/knack", new_minor="3.14", dry_run=True)
    assert result.status == "in_progress"
    assert result.pr == 42
    assert "awaiting" in result.notes.lower()


def test_dry_run_does_not_clone(monkeypatch):
    monkeypatch.setattr(
        pypi, "check_pypi",
        lambda *a, **k: pypi.PyPIStatus("knack", "3.14", False, "0.13.0"),
    )
    monkeypatch.setattr(agent, "find_open_pr", lambda repo, branch: None)
    # If the pipeline tried to clone in dry-run, this would raise.
    monkeypatch.setattr(agent, "run", lambda *a, **k: pytest.fail("should not shell out"))

    result = agent.run_pipeline(repo="owner/knack", new_minor="3.14", dry_run=True)
    assert result.status == "in_progress"
    assert result.pr is None
    assert "dry-run" in result.notes.lower()


def test_force_recreate_ignores_existing_pr(monkeypatch, tmp_path):
    """When force_recreate=True with an existing PR, dry_run still suppresses
    the actual clone/push so we just verify we don't short-circuit early."""
    monkeypatch.setattr(
        pypi, "check_pypi",
        lambda *a, **k: pypi.PyPIStatus("knack", "3.14", False, "0.13.0"),
    )
    monkeypatch.setattr(agent, "find_open_pr", lambda repo, branch: 99)

    result = agent.run_pipeline(
        repo="owner/knack", new_minor="3.14",
        dry_run=True, force_recreate=True,
    )
    # With force_recreate we bypass stage 2 and fall through to dry-run note.
    assert result.status == "in_progress"
    assert result.pr is None
    assert "dry-run" in result.notes.lower()


# ----- _apply_edits_in_tree -----

def _seed_repo(root: Path) -> None:
    """Drop a minimal setup.py + tox.ini + azure-pipeline.yml in ``root``."""
    (root / "setup.py").write_text(
        "setup(\n    classifiers=[\n"
        "        'Programming Language :: Python :: 3.13',\n"
        "        'License :: OSI Approved :: MIT License',\n"
        "    ],\n)\n",
        encoding="utf-8",
    )
    (root / "tox.ini").write_text("[tox]\nenvlist = py313\n", encoding="utf-8")
    (root / "azure-pipeline.yml").write_text(
        "    strategy:\n      matrix:\n        Python313:\n"
        "          python.version: '3.13'\n          tox_env: 'py313'\n",
        encoding="utf-8",
    )


def test_apply_edits_in_tree_modifies_all_three_files(tmp_path):
    _seed_repo(tmp_path)
    changed = agent._apply_edits_in_tree(tmp_path, "3.14")
    assert changed is True
    assert "Python :: 3.14" in (tmp_path / "setup.py").read_text(encoding="utf-8")
    assert "py314" in (tmp_path / "tox.ini").read_text(encoding="utf-8")
    assert "Python314" in (tmp_path / "azure-pipeline.yml").read_text(encoding="utf-8")


def test_apply_edits_in_tree_idempotent(tmp_path):
    _seed_repo(tmp_path)
    agent._apply_edits_in_tree(tmp_path, "3.14")
    # Second invocation must report "no change".
    changed = agent._apply_edits_in_tree(tmp_path, "3.14")
    assert changed is False


def test_apply_edits_in_tree_tolerates_missing_files(tmp_path):
    # Only setup.py exists.
    (tmp_path / "setup.py").write_text(
        "setup(\n    classifiers=[\n"
        "        'Programming Language :: Python :: 3.13',\n"
        "        'License :: OSI Approved :: MIT License',\n"
        "    ],\n)\n",
        encoding="utf-8",
    )
    changed = agent._apply_edits_in_tree(tmp_path, "3.14")
    assert changed is True


def test_render_pr_body_with_tracking():
    body = agent._render_pr_body(
        new_minor="3.14",
        tracking_issue=12,
        tracking_repo="YangAn-microsoft/azure-cli",
    )
    assert "Python 3.14" in body
    assert "YangAn-microsoft/azure-cli#12" in body


def test_render_pr_body_without_tracking():
    body = agent._render_pr_body(new_minor="3.14", tracking_issue=None, tracking_repo=None)
    assert "Python 3.14" in body
    assert "#" not in body  # no tracking link
