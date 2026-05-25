"""Tests for the knack_pin_bumper state machine (stages 1-3 short-circuits).

Stage 4 (branch/commit/push/PR) shells out to git/gh and is not exercised here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from campaigns.python_upgrade.knack_pin_bumper import agent
from campaigns.python_upgrade.knack_upgrader import pypi


def _seed(repo_root: Path, pin: str = "'knack~=0.14.0'") -> None:
    (repo_root / "src" / "azure-cli-core").mkdir(parents=True, exist_ok=True)
    (repo_root / "src" / "azure-cli-core" / "setup.py").write_text(
        f"DEPENDENCIES = [\n    {pin},\n]\n",
        encoding="utf-8",
    )


def test_pending_when_knack_not_on_pypi(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pypi, "check_pypi",
        lambda *a, **k: pypi.PyPIStatus("knack", "3.14", False, "0.14.0"),
    )
    _seed(tmp_path)
    result = agent.run_pipeline(
        repo="owner/azure-cli", repo_root=tmp_path, new_minor="3.14", dry_run=True,
    )
    assert result.status == "pending"
    assert "awaiting knack" in result.notes.lower()


def test_completed_when_pin_already_at_target(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pypi, "check_pypi",
        lambda *a, **k: pypi.PyPIStatus("knack", "3.14", True, "0.14.0"),
    )
    _seed(tmp_path, pin="'knack~=0.14.0'")
    result = agent.run_pipeline(
        repo="owner/azure-cli", repo_root=tmp_path, new_minor="3.14", dry_run=True,
    )
    assert result.status == "completed"


def test_in_progress_when_existing_pr(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pypi, "check_pypi",
        lambda *a, **k: pypi.PyPIStatus("knack", "3.14", True, "0.15.0"),
    )
    monkeypatch.setattr(agent, "find_open_pr", lambda repo, branch: 77)
    _seed(tmp_path, pin="'knack~=0.14.0'")
    result = agent.run_pipeline(
        repo="owner/azure-cli", repo_root=tmp_path, new_minor="3.14", dry_run=True,
    )
    assert result.status == "in_progress"
    assert result.pr == 77
    assert "0.15.0" in result.notes


def test_dry_run_skips_push(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pypi, "check_pypi",
        lambda *a, **k: pypi.PyPIStatus("knack", "3.14", True, "0.15.0"),
    )
    monkeypatch.setattr(agent, "find_open_pr", lambda repo, branch: None)
    monkeypatch.setattr(
        agent, "create_branch",
        lambda *a, **k: pytest.fail("must not branch in dry-run"),
    )
    _seed(tmp_path)
    result = agent.run_pipeline(
        repo="owner/azure-cli", repo_root=tmp_path, new_minor="3.14", dry_run=True,
    )
    assert result.status == "in_progress"
    assert "dry-run" in result.notes.lower()
    assert "0.14.0" in result.notes  # current
    assert "0.15.0" in result.notes  # target


def test_failed_when_setup_py_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pypi, "check_pypi",
        lambda *a, **k: pypi.PyPIStatus("knack", "3.14", True, "0.15.0"),
    )
    result = agent.run_pipeline(
        repo="owner/azure-cli", repo_root=tmp_path, new_minor="3.14", dry_run=True,
    )
    assert result.status == "failed"
    assert "not found" in result.notes.lower()


def test_failed_when_pin_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pypi, "check_pypi",
        lambda *a, **k: pypi.PyPIStatus("knack", "3.14", True, "0.15.0"),
    )
    (tmp_path / "src" / "azure-cli-core").mkdir(parents=True)
    (tmp_path / "src" / "azure-cli-core" / "setup.py").write_text(
        "DEPENDENCIES = ['jmespath']\n", encoding="utf-8",
    )
    result = agent.run_pipeline(
        repo="owner/azure-cli", repo_root=tmp_path, new_minor="3.14", dry_run=True,
    )
    assert result.status == "failed"
    assert "no knack pin" in result.notes.lower()
