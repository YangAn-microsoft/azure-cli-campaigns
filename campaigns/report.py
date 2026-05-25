"""Pure functions that build campaign run-report and creation-announcement
comment bodies. No I/O; the framework module is responsible for posting.

Skip-rule for run reports: emit a comment only if there is something
worth notifying about. Otherwise the daily cron would post a "nothing
changed" comment every day, which is exactly the noise we want to avoid.
"""
from __future__ import annotations

from .base import CampaignPlan, Item, ItemState


def build_creation_announcement(
    *,
    plan: CampaignPlan,
    notify_users: list[str] | None = None,
    run_url: str = "",
) -> str:
    """One-time comment posted when the tracking issue is created.

    Issue creation itself doesn't notify non-watchers, so this comment
    guarantees an at-mention reaches the configured developers.
    """
    lines: list[str] = [f"Campaign **{plan.title}** started."]
    if run_url:
        lines.append(f"Run log: {run_url}")

    handler_items = [it for it in plan.items if it.handler]
    manual_items = [it for it in plan.items if it.handler is None]

    if handler_items:
        lines.append("")
        lines.append("**Automated steps**")
        for it in handler_items:
            lines.append(f"- {it.id} — {it.title}")
    if manual_items:
        lines.append("")
        lines.append("**Manual steps** (need your attention)")
        for it in manual_items:
            lines.append(f"- {it.id} — {it.title}")

    if notify_users:
        mentions = " ".join(f"@{u.lstrip('@')}" for u in notify_users)
        lines.append("")
        lines.append(f"cc {mentions}")
    return "\n".join(lines) + "\n"


def _state(d: dict[str, ItemState], key: str) -> ItemState:
    return d.get(key, ItemState())


def build_run_report(
    *,
    plan: CampaignPlan,
    before: dict[str, ItemState],
    after: dict[str, ItemState],
    run_url: str = "",
) -> str | None:
    """Return a markdown comment body, or ``None`` if nothing is worth
    reporting this run.

    Posted when at least one of the following is true:
    - some item transitioned to a new status / PR / notes,
    - some item is currently ``failed``,
    - some item with a handler is currently ``pending`` (waiting on an
      external prerequisite — worth surfacing).

    When posted, manual (no-handler) items not yet completed are always
    listed as a reminder.
    """
    transitions: list[tuple[Item, ItemState, ItemState]] = []
    for item in plan.items:
        b = _state(before, item.id)
        a = _state(after, item.id)
        if (b.status, b.phase, b.pr, b.notes) != (a.status, a.phase, a.pr, a.notes):
            transitions.append((item, b, a))

    failed = [it for it in plan.items
              if _state(after, it.id).status == "failed"]
    pending_external = [
        it for it in plan.items
        if it.handler and _state(after, it.id).status == "pending"
    ]

    if not transitions and not failed and not pending_external:
        return None

    lines: list[str] = ["## Run report"]
    if run_url:
        lines.append(f"_[Run log]({run_url})_")
    lines.append("")

    if transitions:
        lines.append(f"### Changes ({len(transitions)})")
        for item, b, a in transitions:
            # For multi-phase items, include phase so readers know which
            # milestone moved (e.g. "created → merged").
            if len(item.phases) > 1:
                b_label = f"`{b.status}@{b.phase or '—'}`"
                a_label = f"`{a.status}@{a.phase or '—'}`"
            else:
                b_label = f"`{b.status}`"
                a_label = f"`{a.status}`"
            parts = [f"{b_label} \u2192 {a_label}"]
            if a.pr is not None and a.pr != b.pr:
                parts.append(f"PR #{a.pr}")
            if a.notes:
                parts.append(a.notes)
            lines.append(f"- **{item.id}** \u2014 {' \u00b7 '.join(parts)}")
        lines.append("")

    if failed:
        lines.append(f"### Blockers ({len(failed)})")
        for it in failed:
            st = _state(after, it.id)
            suffix = f" — {st.notes}" if st.notes else ""
            lines.append(f"- **{it.id}**{suffix}")
        lines.append("")

    if pending_external:
        lines.append(f"### Waiting on external ({len(pending_external)})")
        for it in pending_external:
            st = _state(after, it.id)
            suffix = f" — {st.notes}" if st.notes else ""
            lines.append(f"- **{it.id}**{suffix}")
        lines.append("")

    manual = [
        it for it in plan.items
        if it.handler is None
        and _state(after, it.id).status != "completed"
    ]
    if manual:
        lines.append(f"### Manual items — reminder ({len(manual)})")
        for it in manual:
            lines.append(f"- **{it.id}** — {it.title}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
