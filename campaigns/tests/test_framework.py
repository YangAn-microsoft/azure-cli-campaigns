"""Tests for the campaign framework runner.

Network/GH I/O is monkeypatched; we exercise render_body and the dispatch
loop's idempotency and error-handling semantics.
"""
from __future__ import annotations

import pytest

from campaigns import framework, state as state_mod
from campaigns.base import (
    Campaign,
    CampaignPlan,
    HandlerContext,
    HandlerResult,
    Item,
    ItemState,
)


class _FakeCampaign:
    id = "fake"
    issue_repo = "owner/repo"

    def __init__(self, items: list[Item]):
        self._items = items

    def build(self, params: dict) -> CampaignPlan:
        return CampaignPlan(title="Plan", intro="intro text", items=self._items)


def _patch_gh(monkeypatch, *, existing_issue: int | None = None,
              existing_body: str = "") -> dict:
    """Patch all gh calls to in-memory fakes. Returns a dict capturing state."""
    captured = {"issue_body": existing_body, "created": False, "edits": [],
                "comments": [], "assignees": []}

    def fake_find(repo, title):
        return existing_issue

    def fake_create(repo, title, body):
        captured["created"] = True
        captured["issue_body"] = body
        return 999

    def fake_fetch(repo, number):
        return captured["issue_body"]

    def fake_update(repo, number, body):
        captured["edits"].append(body)
        captured["issue_body"] = body

    def fake_comment(repo, number, body):
        captured["comments"].append(body)

    def fake_assign(repo, number, users):
        captured["assignees"].extend(users)

    monkeypatch.setattr(framework, "find_open_issue_by_title", fake_find)
    monkeypatch.setattr(framework, "create_issue", fake_create)
    monkeypatch.setattr(framework, "fetch_issue_body", fake_fetch)
    monkeypatch.setattr(framework, "update_issue_body", fake_update)
    monkeypatch.setattr(framework, "post_issue_comment", fake_comment)
    monkeypatch.setattr(framework, "add_issue_assignees", fake_assign)
    return captured


def test_render_body_contains_state_and_checklist():
    plan = CampaignPlan(title="t", intro="hello", items=[
        Item(id="a", title="Do thing A"),
        Item(id="b", title="Do thing B", handler="h"),
    ])
    body = framework.render_body(plan, {
        "a": ItemState(status="completed", pr=12),
        "b": ItemState(status="pending"),
    })
    assert "campaign-state" in body
    assert "hello" in body
    assert "[x] **a**" in body
    assert "PR #12" in body
    assert "[ ] **b**" in body


def test_run_creates_issue_when_absent(monkeypatch):
    items = [Item(id="manual", title="Do it")]
    cap = _patch_gh(monkeypatch, existing_issue=None)
    n = framework.run(_FakeCampaign(items), params={}, handlers={})
    assert n == 999
    assert cap["created"] is True
    assert "**manual**" in cap["issue_body"]


def test_run_dispatches_handler_and_records_pr(monkeypatch):
    items = [Item(id="x", title="Open PR", handler="h")]
    cap = _patch_gh(monkeypatch, existing_issue=None)

    def handler(ctx: HandlerContext) -> HandlerResult:
        return HandlerResult(status="completed", pr=77, notes="")

    framework.run(_FakeCampaign(items), params={}, handlers={"h": handler})
    parsed = state_mod.parse(cap["issue_body"])
    assert parsed["x"].status == "completed"
    assert parsed["x"].pr == 77


def test_run_skips_completed_item_on_rerun(monkeypatch):
    items = [Item(id="x", title="Open PR", handler="h")]
    prior_state = {"x": ItemState(status="completed", pr=55)}
    initial_body = state_mod.serialize(prior_state) + "\n"
    cap = _patch_gh(monkeypatch, existing_issue=42, existing_body=initial_body)

    calls = {"n": 0}

    def handler(ctx: HandlerContext) -> HandlerResult:
        calls["n"] += 1
        return HandlerResult(status="completed", pr=99)

    framework.run(_FakeCampaign(items), params={}, handlers={"h": handler})
    assert calls["n"] == 0
    parsed = state_mod.parse(cap["issue_body"])
    assert parsed["x"].pr == 55  # unchanged


def test_run_force_recreate_reruns_completed(monkeypatch):
    items = [Item(id="x", title="Open PR", handler="h")]
    prior_state = {"x": ItemState(status="completed", pr=55)}
    initial_body = state_mod.serialize(prior_state) + "\n"
    cap = _patch_gh(monkeypatch, existing_issue=42, existing_body=initial_body)

    def handler(ctx: HandlerContext) -> HandlerResult:
        return HandlerResult(status="completed", pr=99)

    framework.run(_FakeCampaign(items), params={}, handlers={"h": handler},
                  force_recreate=True)
    parsed = state_mod.parse(cap["issue_body"])
    assert parsed["x"].pr == 99


def test_run_records_handler_failure(monkeypatch):
    items = [Item(id="x", title="Open PR", handler="h")]
    cap = _patch_gh(monkeypatch, existing_issue=None)

    def handler(ctx: HandlerContext) -> HandlerResult:
        raise RuntimeError("boom")

    framework.run(_FakeCampaign(items), params={}, handlers={"h": handler})
    parsed = state_mod.parse(cap["issue_body"])
    assert parsed["x"].status == "failed"
    assert "boom" in parsed["x"].notes


def test_run_unknown_handler_marks_failed(monkeypatch):
    items = [Item(id="x", title="Open PR", handler="nope")]
    cap = _patch_gh(monkeypatch, existing_issue=None)
    framework.run(_FakeCampaign(items), params={}, handlers={})
    parsed = state_mod.parse(cap["issue_body"])
    assert parsed["x"].status == "failed"
    assert "unknown handler" in parsed["x"].notes


def test_run_posts_creation_announcement_with_notify_users(monkeypatch):
    items = [Item(id="x", title="Open PR", handler="h")]
    cap = _patch_gh(monkeypatch, existing_issue=None)
    framework.run(
        _FakeCampaign(items),
        params={"notify_users": ["yangan"], "run_url": "https://x/y"},
        handlers={"h": lambda ctx: HandlerResult(status="completed", pr=1)},
    )
    # Creation announcement + run report (transition happened).
    assert len(cap["comments"]) == 2
    assert "Campaign **Plan**" in cap["comments"][0]
    assert "@yangan" in cap["comments"][0]
    assert "https://x/y" in cap["comments"][0]
    assert "Run report" in cap["comments"][1]
    assert "PR #1" in cap["comments"][1]
    assert cap["assignees"] == ["yangan"]


def test_run_skips_run_report_when_nothing_changed(monkeypatch):
    items = [Item(id="x", title="Open PR", handler="h")]
    prior = {"x": ItemState(status="completed", pr=5)}
    cap = _patch_gh(monkeypatch, existing_issue=42,
                    existing_body=state_mod.serialize(prior) + "\n")
    framework.run(
        _FakeCampaign(items), params={},
        handlers={"h": lambda ctx: HandlerResult(status="completed", pr=5)},
    )
    # No creation announcement (issue already existed) and no run report
    # because the only handler-driven item was completed and skipped.
    assert cap["comments"] == []


def test_run_posts_run_report_for_blocker_only(monkeypatch):
    items = [Item(id="x", title="Open PR", handler="h")]
    prior = {"x": ItemState(status="failed", notes="boom")}
    cap = _patch_gh(monkeypatch, existing_issue=42,
                    existing_body=state_mod.serialize(prior) + "\n")
    # Handler returns failed again with same notes — no transition, but
    # the blocker is still present and should be surfaced.
    framework.run(
        _FakeCampaign(items), params={},
        handlers={"h": lambda ctx: HandlerResult(status="failed", notes="boom")},
    )
    assert len(cap["comments"]) == 1
    assert "Blockers (1)" in cap["comments"][0]


def test_run_dry_run_skips_io(monkeypatch):
    items = [Item(id="x", title="Open PR", handler="h")]
    monkeypatch.setattr(framework, "find_open_issue_by_title",
                        lambda *a, **k: pytest.fail("should not be called"))
    n = framework.run(_FakeCampaign(items), params={}, handlers={},
                      dry_run=True)
    assert n == -1


# --- Multi-phase items --------------------------------------------------

def test_multiphase_handler_default_phase_is_first(monkeypatch):
    items = [Item(id="x", title="Open PR", handler="h",
                  phases=("created", "merged"))]
    cap = _patch_gh(monkeypatch, existing_issue=None)
    framework.run(
        _FakeCampaign(items), params={},
        handlers={"h": lambda ctx: HandlerResult(status="completed", pr=1)},
    )
    parsed = state_mod.parse(cap["issue_body"])
    # Handler omitted phase => framework defaults to first phase.
    assert parsed["x"].phase == "created"
    assert parsed["x"].status == "completed"


def test_multiphase_explicit_phase_honoured(monkeypatch):
    items = [Item(id="x", title="x", handler="h",
                  phases=("created", "merged"))]
    cap = _patch_gh(monkeypatch, existing_issue=None)
    framework.run(
        _FakeCampaign(items), params={},
        handlers={"h": lambda ctx: HandlerResult(
            status="completed", phase="merged", notes="nothing to do",
        )},
    )
    parsed = state_mod.parse(cap["issue_body"])
    assert parsed["x"].phase == "merged"


def test_multiphase_redispatched_when_not_fully_completed(monkeypatch):
    items = [Item(id="x", title="x", handler="h",
                  phases=("created", "merged"))]
    prior = {"x": ItemState(status="completed", phase="created", pr=5)}
    cap = _patch_gh(monkeypatch, existing_issue=42,
                    existing_body=state_mod.serialize(prior) + "\n")
    calls = {"n": 0}

    def handler(ctx):
        calls["n"] += 1
        return HandlerResult(status="completed", phase="created", pr=5)

    framework.run(_FakeCampaign(items), params={}, handlers={"h": handler})
    # Even though status == completed, phase != final_phase, so the
    # handler is re-dispatched (idempotent by design).
    assert calls["n"] == 1


def test_multiphase_skipped_when_fully_completed(monkeypatch):
    items = [Item(id="x", title="x", handler="h",
                  phases=("created", "merged"))]
    prior = {"x": ItemState(status="completed", phase="merged", pr=5)}
    cap = _patch_gh(monkeypatch, existing_issue=42,
                    existing_body=state_mod.serialize(prior) + "\n")
    calls = {"n": 0}

    def handler(ctx):
        calls["n"] += 1
        return HandlerResult(status="completed", phase="merged")

    framework.run(_FakeCampaign(items), params={}, handlers={"h": handler})
    assert calls["n"] == 0


def test_multiphase_checklist_shows_phase_pips():
    plan = CampaignPlan(title="t", intro="", items=[
        Item(id="bump", title="B", handler="h",
             phases=("created", "merged")),
    ])
    body = framework.render_body(plan, {
        "bump": ItemState(status="completed", phase="created", pr=10),
    })
    # Plan checklist line shows both phases with status pips.
    assert "created" in body
    assert "merged" in body
    # Checkbox unchecked because we haven't reached the final phase.
    assert "[ ] **bump**" in body


def test_multiphase_checklist_checked_when_final_phase_completed():
    plan = CampaignPlan(title="t", intro="", items=[
        Item(id="bump", title="B", handler="h",
             phases=("created", "merged")),
    ])
    body = framework.render_body(plan, {
        "bump": ItemState(status="completed", phase="merged", pr=10),
    })
    assert "[x] **bump**" in body
