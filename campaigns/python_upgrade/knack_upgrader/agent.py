"""Knack upgrader pipeline.

State machine (idempotent, runs on every campaign invocation):

    on each run:
      1. PyPI has knack release w/ Python X.Y classifier?
            yes → completed (note: "knack <ver> on PyPI ...")
            no  → continue
      2. open PR exists in knack repo on our branch?
            yes → in_progress (pr=#, "awaiting merge + release")
            no  → continue
      3. clone knack → branch → apply deterministic edits → push → open PR
            → in_progress (pr=new#, "PR opened")

The handler never blocks ``azure-cli-bump`` and never touches azure-cli.
The follow-up "bump knack pin in azure-cli" work is a separate item
(``azure-cli-knack-pin``) that owns its own state.
"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..azure_cli_upgrader.github_ops import (
    commit_all,
    create_branch,
    find_open_pr,
    open_draft_pr,
    push_branch,
    run,
)
from . import edits, pypi


BRANCH_PREFIX = "campaign/python-"
PR_TITLE_TEMPLATE = "Support Python {minor}"


@dataclass(frozen=True)
class KnackResult:
    status: str  # ItemStatus literal: pending | in_progress | completed | failed
    pr: int | None = None
    notes: str = ""


def run_pipeline(
    *,
    repo: str,
    new_minor: str,
    work_dir: Path | None = None,
    tracking_issue: int | None = None,
    tracking_repo: str | None = None,
    base_branch: str = "dev",
    dry_run: bool = False,
    force_recreate: bool = False,
) -> KnackResult:
    """Run the knack upgrader. See module docstring for the state machine.

    Parameters
    ----------
    repo: ``owner/name`` of the knack repository to PR against.
    new_minor: target Python minor like ``"3.14"``.
    work_dir: where to clone (defaults to a tempdir).
    tracking_issue / tracking_repo: campaign issue to link back to in the PR
        body, e.g. ``YangAn-microsoft/azure-cli#12``.
    """
    # Stage 1: already shipped on PyPI.
    status = pypi.check_pypi("knack", new_minor)
    if status.supports:
        return KnackResult(
            status="completed",
            notes=f"knack {status.latest_version} on PyPI declares Python {new_minor}",
        )

    branch = f"{BRANCH_PREFIX}{new_minor}"

    # Stage 2: existing open PR (idempotency).
    existing = find_open_pr(repo, branch)
    if existing is not None and not force_recreate:
        return KnackResult(
            status="in_progress",
            pr=existing,
            notes="knack PR open; awaiting merge + PyPI release",
        )

    if dry_run:
        return KnackResult(
            status="in_progress",
            notes=f"[dry-run] would open knack PR on {repo} for Python {new_minor}",
        )

    # Stage 3: clone → edit → branch → push → PR.
    return _open_pr(
        repo=repo,
        new_minor=new_minor,
        branch=branch,
        base_branch=base_branch,
        work_dir=work_dir,
        tracking_issue=tracking_issue,
        tracking_repo=tracking_repo,
    )


def _open_pr(
    *,
    repo: str,
    new_minor: str,
    branch: str,
    base_branch: str,
    work_dir: Path | None,
    tracking_issue: int | None,
    tracking_repo: str | None,
) -> KnackResult:
    work = work_dir or Path(tempfile.mkdtemp(prefix="knack_upgrader_"))
    work.mkdir(parents=True, exist_ok=True)
    clone_dir = work / "knack"
    if clone_dir.exists():
        shutil.rmtree(clone_dir)

    # Clone (gh handles auth via current token).
    run(["gh", "repo", "clone", repo, str(clone_dir)])
    create_branch(clone_dir, branch, base=base_branch)

    # Apply deterministic edits to the three files. Tolerate missing files
    # (e.g. tox.ini absent on a fork) but require setup.py to exist.
    changed = _apply_edits_in_tree(clone_dir, new_minor)
    if not changed:
        return KnackResult(
            status="completed",
            notes=f"knack at {repo} already declares Python {new_minor} (no diff)",
        )

    commit_all(clone_dir, f"Mark Python {new_minor} support")
    push_branch(clone_dir, branch)

    body = _render_pr_body(
        new_minor=new_minor,
        tracking_issue=tracking_issue,
        tracking_repo=tracking_repo,
    )
    pr_number = open_draft_pr(
        repo=repo,
        title=PR_TITLE_TEMPLATE.format(minor=new_minor),
        body=body,
        head=branch,
        base=base_branch,
    )
    return KnackResult(
        status="in_progress",
        pr=pr_number if pr_number > 0 else None,
        notes="PR opened; awaiting maintainer review + PyPI release",
    )


def _apply_edits_in_tree(root: Path, new_minor: str) -> bool:
    """Apply the three deterministic edits if their files exist.

    Returns True iff any file was modified.
    """
    targets = [
        ("setup.py", edits.add_python_to_setup_py),
        ("tox.ini", edits.add_python_to_tox_ini),
        ("azure-pipeline.yml", edits.add_python_to_azure_pipeline),
    ]
    any_change = False
    for name, fn in targets:
        path = root / name
        if not path.exists():
            continue
        before = path.read_text(encoding="utf-8")
        after = fn(before, new_minor)
        if after != before:
            path.write_text(after, encoding="utf-8")
            any_change = True
    return any_change


def _render_pr_body(
    *,
    new_minor: str,
    tracking_issue: int | None,
    tracking_repo: str | None,
) -> str:
    lines = [
        f"Declares Python {new_minor} support across `setup.py`, `tox.ini`, and `azure-pipeline.yml`.",
        "",
        "Generated by the azure-cli-campaigns python-upgrade automation.",
    ]
    if tracking_issue is not None and tracking_repo:
        lines.append("")
        lines.append(f"Tracking: {tracking_repo}#{tracking_issue}")
    return "\n".join(lines) + "\n"
