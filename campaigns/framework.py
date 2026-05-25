"""Generic campaign runner: ensures the plan issue exists, dispatches
handlers, and keeps the issue body in sync with structured state."""
from __future__ import annotations

import copy
import json
import os
import subprocess
from typing import Callable

from . import report as report_mod
from . import state as state_mod
from .base import (
    Campaign,
    CampaignPlan,
    HandlerContext,
    HandlerResult,
    Item,
    ItemState,
)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, check=True, encoding="utf-8",
    )


def find_open_issue_by_title(repo: str, title: str) -> int | None:
    """Return the number of an open issue whose title exactly matches.

    Uses ``gh issue list`` without ``--search`` so the lookup hits the REST
    list endpoint (strongly consistent) instead of the search API (eventually
    consistent, ~30s-2min indexing lag). This prevents duplicate campaign
    issues when runs happen close together.
    """
    result = _run([
        "gh", "issue", "list",
        "--repo", repo,
        "--state", "open",
        "--json", "number,title",
        "--limit", "100",
    ])
    rows = json.loads(result.stdout or "[]")
    for row in rows:
        if row.get("title") == title:
            return int(row["number"])
    return None


def create_issue(repo: str, title: str, body: str) -> int:
    """Create an issue and return its number."""
    result = _run([
        "gh", "issue", "create",
        "--repo", repo,
        "--title", title,
        "--body", body,
    ])
    url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    return int(url.rsplit("/", 1)[-1])


def post_issue_comment(repo: str, number: int, body: str) -> None:
    """Post a comment on an existing issue."""
    _run([
        "gh", "issue", "comment", str(number),
        "--repo", repo,
        "--body", body,
    ])


def add_issue_assignees(repo: str, number: int, users: list[str]) -> None:
    """Best-effort: add assignees. Logs and continues on failure (e.g. user
    is outside the org or doesn't exist) so the campaign isn't blocked."""
    if not users:
        return
    cleaned = [u.lstrip("@") for u in users if u.strip()]
    if not cleaned:
        return
    try:
        _run([
            "gh", "issue", "edit", str(number),
            "--repo", repo,
            "--add-assignee", ",".join(cleaned),
        ])
    except subprocess.CalledProcessError as exc:
        print(f"campaign: could not assign {cleaned} to #{number}: "
              f"{(exc.stderr or '').strip()}")


def _write_step_summary(body: str) -> None:
    """Append to GitHub Actions step summary if running in CI."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(body)
            if not body.endswith("\n"):
                fh.write("\n")
    except OSError as exc:
        print(f"campaign: could not write step summary: {exc}")


def update_issue_body(repo: str, number: int, body: str) -> None:
    # Use REST PATCH (not `gh issue edit`, which goes through the GraphQL
    # `updateIssue` mutation and trips a stricter permission check for App
    # tokens on forked repos).
    _run([
        "gh", "api", "--method", "PATCH",
        f"/repos/{repo}/issues/{number}",
        "-f", f"body={body}",
    ])


def fetch_issue_body(repo: str, number: int) -> str:
    result = _run([
        "gh", "issue", "view", str(number),
        "--repo", repo,
        "--json", "body",
        "--jq", ".body",
    ])
    return result.stdout


def render_body(plan: CampaignPlan, item_state: dict[str, ItemState]) -> str:
    """Compose the full issue body: state block + intro + checklist."""
    lines: list[str] = [state_mod.serialize(item_state), ""]
    if plan.intro.strip():
        lines.append(plan.intro.strip())
        lines.append("")
    lines.append("## Plan")
    lines.append("")
    for item in plan.items:
        st = item_state.get(item.id, ItemState())
        check = "x" if st.status == "completed" else " "
        suffix_parts: list[str] = []
        if st.pr is not None:
            suffix_parts.append(f"PR #{st.pr}")
        if st.status in {"skipped", "failed", "in_progress"}:
            suffix_parts.append(f"_{st.status}_")
        if st.notes:
            suffix_parts.append(st.notes)
        suffix = f" — {' · '.join(suffix_parts)}" if suffix_parts else ""
        lines.append(f"- [{check}] **{item.id}** — {item.title}{suffix}")
    lines.append("")
    return "\n".join(lines)


def run(
    campaign: Campaign,
    params: dict,
    handlers: dict[str, Callable[[HandlerContext], HandlerResult]],
    force_recreate: bool = False,
    dry_run: bool = False,
) -> int:
    """Execute the campaign end-to-end. Returns the plan issue number
    (or ``-1`` in dry-run / when the campaign chose no-op)."""
    plan = campaign.build(params)
    if plan is None:
        print(f"campaign[{campaign.id}]: build() returned no plan; nothing to do.")
        return -1

    if dry_run:
        print(f"campaign[{campaign.id}]: dry-run; would open issue '{plan.title}'")
        for item in plan.items:
            print(f"  - {item.id}: {item.title} (handler={item.handler})")
        return -1

    # 1. Find or create the plan issue.
    run_url = params.get("run_url", "")
    notify_users = params.get("notify_users") or []
    issue_number = find_open_issue_by_title(campaign.issue_repo, plan.title)
    newly_created = False
    if issue_number is None:
        seed_state: dict[str, ItemState] = {item.id: ItemState() for item in plan.items}
        issue_number = create_issue(
            campaign.issue_repo, plan.title, render_body(plan, seed_state),
        )
        newly_created = True
        print(f"campaign[{campaign.id}]: created issue #{issue_number}")
        # Notify configured developers about the new campaign. Both steps
        # are best-effort: assignment may fail for non-org users, the
        # announcement comment still guarantees an at-mention notification.
        if notify_users:
            add_issue_assignees(campaign.issue_repo, issue_number, notify_users)
        post_issue_comment(
            campaign.issue_repo, issue_number,
            report_mod.build_creation_announcement(
                plan=plan, notify_users=notify_users, run_url=run_url,
            ),
        )
    else:
        print(f"campaign[{campaign.id}]: reusing issue #{issue_number}")

    # 2. Load current state.
    body = fetch_issue_body(campaign.issue_repo, issue_number)
    item_state = state_mod.parse(body)
    for item in plan.items:
        item_state.setdefault(item.id, ItemState())
    before_state = copy.deepcopy(item_state)

    # 3. Dispatch handlers.
    for item in plan.items:
        if item.handler is None:
            continue
        st = item_state[item.id]
        if st.status == "completed" and not force_recreate:
            print(f"campaign[{campaign.id}]: item '{item.id}' already completed, skipping")
            continue
        handler = handlers.get(item.handler)
        if handler is None:
            print(f"campaign[{campaign.id}]: no handler '{item.handler}' registered for item '{item.id}'")
            item_state[item.id] = ItemState(status="failed", notes=f"unknown handler {item.handler}")
            continue
        item_state[item.id] = ItemState(status="in_progress")
        # Merge campaign-level params (e.g. repo_root, reference_repo) with
        # item-level params; item wins on conflict.
        merged_params = {**params, **item.params}
        try:
            result = handler(HandlerContext(
                item=item,
                repo=item.repo or campaign.issue_repo,
                issue_number=issue_number,
                params=merged_params,
                force_recreate=force_recreate,
                dry_run=dry_run,
            ))
        except Exception as exc:  # noqa: BLE001 — surface to issue, keep loop going
            item_state[item.id] = ItemState(status="failed", notes=str(exc)[:200])
            print(f"campaign[{campaign.id}]: handler '{item.handler}' failed: {exc}")
            continue
        item_state[item.id] = ItemState(
            status=result.status, pr=result.pr, notes=result.notes,
        )

    # 4. Re-render and update issue body.
    update_issue_body(
        campaign.issue_repo, issue_number, render_body(plan, item_state),
    )

    # 5. Post run report if anything's worth notifying about.
    report = report_mod.build_run_report(
        plan=plan, before=before_state, after=item_state, run_url=run_url,
    )
    if report is not None:
        post_issue_comment(campaign.issue_repo, issue_number, report)
        _write_step_summary(report)
    elif newly_created:
        # Mirror the creation announcement into the workflow run summary
        # even when nothing changed beyond the initial seed.
        _write_step_summary(
            f"Created tracking issue [#{issue_number}]"
            f"(https://github.com/{campaign.issue_repo}/issues/{issue_number}) "
            f"for **{plan.title}**.\n"
        )
    return issue_number
