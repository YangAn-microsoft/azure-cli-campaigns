"""Git + GitHub operations via `git` and `gh` CLIs (pre-installed in Actions).

Kept thin: each function maps to one operation, no orchestration logic.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .validate import ValidatedEdit


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        # Surface stderr in the exception message so CI logs show the real cause.
        raise subprocess.CalledProcessError(
            result.returncode, cmd, output=result.stdout,
            stderr=(result.stderr or "") + f"\n[cmd: {' '.join(cmd)}]",
        )
    return result


def branch_exists_remote(repo: str, branch: str) -> bool:
    """Check if a branch exists on the remote via gh api."""
    try:
        run(["gh", "api", f"repos/{repo}/branches/{branch}"])
        return True
    except subprocess.CalledProcessError:
        return False


def find_open_pr(repo: str, head_branch: str) -> int | None:
    """Return PR number if an open PR exists from head_branch, else None."""
    try:
        result = run([
            "gh", "pr", "list",
            "--repo", repo,
            "--head", head_branch,
            "--state", "open",
            "--json", "number",
            "--limit", "1",
        ])
        rows = json.loads(result.stdout or "[]")
        if rows:
            return int(rows[0]["number"])
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, ValueError):
        pass
    return None


def find_any_pr(repo: str, head_branch: str) -> dict | None:
    """Return {number, state, merged_at} for the most recent PR from head_branch."""
    try:
        result = run([
            "gh", "pr", "list",
            "--repo", repo,
            "--head", head_branch,
            "--state", "all",
            "--json", "number,state,mergedAt",
            "--limit", "1",
        ])
        rows = json.loads(result.stdout or "[]")
        return rows[0] if rows else None
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def create_branch(repo_root: Path, branch: str, base: str = "dev") -> None:
    run(["git", "checkout", base], cwd=repo_root)
    run(["git", "checkout", "-b", branch], cwd=repo_root)


def apply_edits(repo_root: Path, edits: list[ValidatedEdit]) -> None:
    """Apply edits in-place. Caller must have validated them first."""
    for e in edits:
        path = repo_root / e.path
        text = path.read_text(encoding="utf-8")
        occurrences = text.count(e.old_string)
        if occurrences == 0:
            raise RuntimeError(
                f"apply_edits: old_string vanished from {e.path} "
                "(file changed between validation and apply?)"
            )
        if occurrences > 1 and not e.replace_all:
            raise RuntimeError(
                f"apply_edits: old_string no longer unique in {e.path} "
                "(file changed between validation and apply?)"
            )
        path.write_text(text.replace(e.old_string, e.new_string), encoding="utf-8")


def commit_all(repo_root: Path, message: str) -> None:
    run(["git", "add", "-A"], cwd=repo_root)
    run(["git", "commit", "-m", message], cwd=repo_root)


def push_branch(repo_root: Path, branch: str) -> None:
    # Force-with-lease: agent owns this branch; safe to overwrite on retries.
    run(["git", "push", "--force-with-lease", "-u", "origin", branch], cwd=repo_root)


def open_draft_pr(
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str = "dev",
) -> int:
    """Open a draft PR and return its number."""
    result = run([
        "gh", "pr", "create",
        "--repo", repo,
        "--head", head,
        "--base", base,
        "--title", title,
        "--body", body,
        "--draft",
    ])
    # gh prints the PR URL on success; last path segment is the number.
    url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    try:
        return int(url.rsplit("/", 1)[-1])
    except ValueError:
        return -1


def comment_on_pr(repo: str, number: int, body: str) -> None:
    run([
        "gh", "pr", "comment", str(number),
        "--repo", repo,
        "--body", body,
    ])
