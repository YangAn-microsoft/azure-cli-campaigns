"""Tests for the python-upgrade campaign definition.

Network and filesystem detection are monkeypatched; we exercise plan shape,
item IDs, and the handler shim's parameter mapping.
"""
from __future__ import annotations

import pytest

from campaigns import python_upgrade as pu
from campaigns.base import HandlerContext, Item
from campaigns.python_upgrade.azure_cli_upgrader import detect


def _patch_detection(monkeypatch, current_full: str, new_full: str) -> None:
    def fake_current(_root):
        return pu._parse_full(current_full)

    def fake_releases(*_, **__):
        # Provide a release for current.minor+1 = new.
        new = pu._parse_full(new_full)
        return {(new.major, new.minor): new}

    monkeypatch.setattr(detect, "read_current_version", fake_current)
    monkeypatch.setattr(detect, "fetch_python_releases", fake_releases)


def test_build_with_forced_versions_returns_plan():
    camp = pu.PythonUpgradeCampaign(issue_repo="owner/repo")
    plan = camp.build({"current_full": "3.13.13", "new_full": "3.14.5"})
    assert plan is not None
    assert plan.title == "Support Python 3.14"
    ids = [it.id for it in plan.items]
    assert ids == [
        "azure-cli-bump",
        "knack-bump",
        "azure-cli-knack-pin",
        "aaz-dev-bump",
        "azdev-bump",
        "azure-cli-extensions-bump",
    ]


def test_build_via_detection(monkeypatch):
    _patch_detection(monkeypatch, "3.13.13", "3.14.5")
    camp = pu.PythonUpgradeCampaign()
    plan = camp.build({"repo_root": "."})
    assert plan is not None
    assert plan.title == "Support Python 3.14"


def test_build_returns_none_when_no_upgrade(monkeypatch):
    def fake_current(_root):
        return detect.Version(3, 14, 5)

    def fake_releases(*_, **__):
        return {(3, 14): detect.Version(3, 14, 5)}  # no 3.15 yet

    monkeypatch.setattr(detect, "read_current_version", fake_current)
    monkeypatch.setattr(detect, "fetch_python_releases", fake_releases)

    camp = pu.PythonUpgradeCampaign()
    assert camp.build({"repo_root": "."}) is None


def test_build_returns_none_when_feed_unreachable(monkeypatch):
    monkeypatch.setattr(detect, "read_current_version",
                        lambda _root: detect.Version(3, 13, 0))
    def boom(*_, **__):
        raise RuntimeError("network down")
    monkeypatch.setattr(detect, "fetch_python_releases", boom)

    assert pu.PythonUpgradeCampaign().build({"repo_root": "."}) is None


def test_handler_passes_tracking_issue_through(monkeypatch):
    captured: dict = {}

    def fake_run_pipeline(**kwargs):
        captured.update(kwargs)
        from campaigns.python_upgrade.azure_cli_upgrader.agent import PipelineResult
        return PipelineResult(status="completed", pr=123)

    monkeypatch.setattr(pu.agent, "run_pipeline", fake_run_pipeline)

    item = Item(id="azure-cli-bump", title="bump",
                handler="python_upgrade_agent",
                params={"current_minor": "3.13", "new_minor": "3.14",
                        "new_full": "3.14.5"},
                repo="owner/repo")
    ctx = HandlerContext(item=item, repo="owner/repo", issue_number=42,
                         params=item.params, force_recreate=False, dry_run=False)
    result = pu.python_upgrade_handler(ctx)

    assert result.status == "completed"
    assert result.pr == 123
    assert captured["tracking_issue"] == 42
    assert captured["force_current_minor"] == "3.13"
    assert captured["force_new_minor"] == "3.14"
    assert captured["force_new_patch"] == "3.14.5"
    assert captured["repo"] == "owner/repo"
