"""Tests for the run-report and creation-announcement builders."""
from __future__ import annotations

from campaigns import report
from campaigns.base import CampaignPlan, Item, ItemState


def _plan(items: list[Item]) -> CampaignPlan:
    return CampaignPlan(title="Support Python 3.14", intro="", items=items)


# ---------- creation announcement ----------

def test_creation_announcement_contains_title_and_items():
    plan = _plan([
        Item(id="azure-cli-bump", title="bump azure-cli", handler="h1"),
        Item(id="aaz-dev", title="manual aaz-dev"),
    ])
    body = report.build_creation_announcement(plan=plan)
    assert "Support Python 3.14" in body
    assert "Automated steps" in body
    assert "azure-cli-bump" in body
    assert "Manual steps" in body
    assert "aaz-dev" in body


def test_creation_announcement_mentions_notify_users():
    plan = _plan([Item(id="x", title="x", handler="h")])
    body = report.build_creation_announcement(
        plan=plan, notify_users=["yangan", "@otheruser"], run_url="https://x/y",
    )
    assert "@yangan" in body
    assert "@otheruser" in body
    assert "https://x/y" in body


# ---------- run report ----------

def test_run_report_returns_none_when_nothing_changed():
    items = [Item(id="a", title="A", handler="h")]
    before = {"a": ItemState(status="completed", pr=1)}
    after = {"a": ItemState(status="completed", pr=1)}
    assert report.build_run_report(plan=_plan(items), before=before, after=after) is None


def test_run_report_reports_transition_with_pr():
    items = [Item(id="a", title="A", handler="h")]
    before = {"a": ItemState()}
    after = {"a": ItemState(status="completed", pr=42, notes="done")}
    body = report.build_run_report(plan=_plan(items), before=before, after=after)
    assert body is not None
    assert "Changes (1)" in body
    assert "`pending` → `completed`" in body
    assert "PR #42" in body
    assert "done" in body


def test_run_report_lists_blockers_even_when_unchanged():
    items = [Item(id="a", title="A", handler="h")]
    failed = ItemState(status="failed", notes="no anchor")
    before = {"a": failed}
    after = {"a": failed}
    body = report.build_run_report(plan=_plan(items), before=before, after=after)
    assert body is not None
    assert "Blockers (1)" in body
    assert "no anchor" in body
    # No "Changes" section because nothing transitioned.
    assert "Changes (" not in body


def test_run_report_lists_pending_with_handler():
    items = [Item(id="a", title="A", handler="h")]
    pending = ItemState(status="pending", notes="awaiting knack")
    before = {"a": pending}
    after = {"a": pending}
    body = report.build_run_report(plan=_plan(items), before=before, after=after)
    assert body is not None
    assert "Waiting on external (1)" in body
    assert "awaiting knack" in body


def test_run_report_ignores_pending_manual_items_when_quiet():
    """Manual items always start as pending. That alone should NOT
    trigger a daily comment — otherwise every cron run would spam."""
    items = [Item(id="m", title="manual")]  # handler=None
    before = {"m": ItemState()}
    after = {"m": ItemState()}
    assert report.build_run_report(plan=_plan(items), before=before, after=after) is None


def test_run_report_includes_manual_reminder_when_already_warranted():
    items = [
        Item(id="auto", title="auto step", handler="h"),
        Item(id="manual1", title="manual A"),
        Item(id="manual2", title="manual B"),
    ]
    before = {
        "auto": ItemState(),
        "manual1": ItemState(),
        "manual2": ItemState(status="completed"),
    }
    after = {
        "auto": ItemState(status="completed", pr=7),
        "manual1": ItemState(),
        "manual2": ItemState(status="completed"),  # already done; not in reminder
    }
    body = report.build_run_report(plan=_plan(items), before=before, after=after)
    assert body is not None
    assert "Manual items \u2014 reminder (1)" in body
    assert "manual1" in body
    # Completed manual item should not appear in the reminder section.
    reminder = body.split("Manual items", 1)[1]
    assert "manual2" not in reminder


def test_run_report_includes_run_url_when_provided():
    items = [Item(id="a", title="A", handler="h")]
    body = report.build_run_report(
        plan=_plan(items),
        before={"a": ItemState()},
        after={"a": ItemState(status="completed", pr=1)},
        run_url="https://github.com/o/r/actions/runs/123",
    )
    assert body is not None
    assert "https://github.com/o/r/actions/runs/123" in body
