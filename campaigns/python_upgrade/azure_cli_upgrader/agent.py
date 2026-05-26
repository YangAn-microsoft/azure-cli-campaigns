"""Entry point: orchestrate the Python upgrade workflow.

Usage:
    python -m python_upgrade_agent.agent
    python -m python_upgrade_agent.agent --dry-run
    python -m python_upgrade_agent.agent \\
        --force-current-minor 3.13 \\
        --force-new-minor 3.14 \\
        --force-new-patch 3.14.5 \\
        --dry-run

In dry-run mode, the agent reads files, calls the LLM, validates, and prints
the proposed edits, but does not touch git or open a PR.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from . import ai, detect, discover, github_ops, post_check, validate

PR_TITLE_TMPL = "{{Packaging}} Support Python {new_minor}"
BRANCH_TMPL = "python-{new_minor}-upgrade"
COMMIT_MSG_TMPL = "[ai-upgrade] Bump Python {current_minor} -> {new_minor}"


PipelineStatus = Literal["completed", "skipped", "failed"]


@dataclass
class PipelineResult:
    """Outcome of one agent invocation, suitable for both CLI exit codes
    and the campaign framework's HandlerResult."""
    status: PipelineStatus
    pr: int | None = None
    notes: str = ""
    phase: str = ""


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Python Upgrade Agent")
    p.add_argument("--repo-root", type=Path, default=Path.cwd(),
                   help="Path to the azure-cli checkout (default: CWD).")
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                   help="owner/repo for GitHub API calls (idempotency check, PR creation).")
    p.add_argument("--reference-repo",
                   default=os.environ.get("REFERENCE_REPO", "Azure/azure-cli"),
                   help="owner/repo to query for few-shot reference PRs. "
                        "Defaults to Azure/azure-cli so a fork can still learn "
                        "from upstream history.")
    p.add_argument("--base-branch", default="dev",
                   help="Base branch for the PR (default: dev).")
    p.add_argument("--force-current-minor", default="",
                   help="Override detected current minor (e.g. 3.13).")
    p.add_argument("--force-new-minor", default="",
                   help="Override detected new minor (e.g. 3.14).")
    p.add_argument("--force-new-patch", default="",
                   help="Override detected new patch (e.g. 3.14.5).")
    p.add_argument("--exclude-pr", action="append", type=int, default=[],
                   help="PR numbers to exclude from few-shot references "
                        "(useful when benchmarking).")
    p.add_argument("--model", default=ai.DEFAULT_MODEL, help="Model id.")
    p.add_argument("--dry-run", action="store_true",
                   help="Do not push, commit, or open a PR.")
    p.add_argument("--force-recreate", action="store_true",
                   help="Bypass the closed-PR idempotency check and open a new "
                        "PR even if a previous one for this minor was closed. "
                        "Use for testing / re-runs after a closed test PR.")
    p.add_argument("--verbose", action="store_true",
                   help="Write a detailed log (prompt, LLM raw output, timings) "
                        "to python_upgrade_agent.log in the repo root. "
                        "Auto-enabled by --dry-run.")
    p.add_argument("--log-file", type=Path, default=None,
                   help="Override path for the verbose log file.")
    return p.parse_args(argv)


class _VerboseLog:
    """Append-only log file plus stderr breadcrumb. No-op when disabled."""

    def __init__(self, path: Path | None):
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                f.write(f"# python_upgrade_agent verbose log — {datetime.now(timezone.utc).isoformat()}\n\n")

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def section(self, title: str) -> None:
        if not self.enabled:
            return
        with self.path.open("a", encoding="utf-8") as f:
            f.write(f"\n## {title}\n\n")

    def write(self, text: str) -> None:
        if not self.enabled:
            return
        with self.path.open("a", encoding="utf-8") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")


def _parse_version_str(s: str, parts: int) -> tuple[int, ...]:
    pieces = s.split(".")
    if len(pieces) != parts:
        raise ValueError(f"Expected {parts}-part version, got {s!r}")
    return tuple(int(x) for x in pieces)


def _apply_force_overrides(
    detected_current: detect.Version,
    detected_target: detect.Version | None,
    args: argparse.Namespace,
) -> tuple[detect.Version, detect.Version]:
    """Apply --force-* CLI overrides on top of detection results."""
    current = detected_current
    target = detected_target

    if args.force_current_minor:
        maj, minr = _parse_version_str(args.force_current_minor, 2)
        # Keep the detected patch if we can; else default to 0.
        patch = detected_current.patch if (maj, minr) == (detected_current.major, detected_current.minor) else 0
        current = detect.Version(maj, minr, patch)

    if args.force_new_patch:
        maj, minr, patch = _parse_version_str(args.force_new_patch, 3)
        target = detect.Version(maj, minr, patch)
    elif args.force_new_minor:
        maj, minr = _parse_version_str(args.force_new_minor, 2)
        # Need a patch. Prefer detected target if its minor matches.
        if target and (target.major, target.minor) == (maj, minr):
            patch = target.patch
        else:
            patch = 0
        target = detect.Version(maj, minr, patch)

    if target is None:
        raise SystemExit("No new Python minor detected; use --force-new-minor to override.")
    return current, target


def _render_pr_body(
    *,
    current: detect.Version,
    target: detect.Version,
    edits: list[validate.ValidatedEdit],
    skipped: list[dict],
    model: str,
    run_url: str,
    references: list[ai.ReferencePR],
    notes: str,
    forgotten: list[str],
    tracking_issue: int | None = None,
) -> str:
    template = (Path(__file__).parent / "pr_template.md").read_text(encoding="utf-8")
    if skipped:
        skipped_lines = []
        for s in skipped:
            loc = s.get("location", "?")
            snippet = (s.get("snippet") or "").strip()
            why = s.get("why", "")
            path = s.get("path", "?")
            skipped_lines.append(f"- [ ] `{path}` @ {loc} — {why}\n      `{snippet}`")
        skipped_list = "\n".join(skipped_lines)
    else:
        skipped_list = "_(none — the agent edited everything it found relevant.)_"
    refs = ", ".join(f"#{r.number}" for r in references) or "(none)"
    if forgotten:
        forgotten_list = "\n".join(f"- `{w}`" for w in forgotten)
    else:
        forgotten_list = "_(none — post-check found no leftover references.)_"
    body = template.format(
        current_minor=current.minor_str,
        new_minor=target.minor_str,
        current_full=current.full_str,
        new_full=target.full_str,
        skipped_list=skipped_list,
        forgotten_list=forgotten_list,
        model=model,
        run_url=run_url or "(local run)",
        reference_prs=refs,
        notes=notes or "(none)",
        tracking_issue=(f"#{tracking_issue}" if tracking_issue else "(none — running outside campaign framework)"),
    )
    return body


def run_pipeline(
    *,
    repo_root: Path,
    repo: str,
    reference_repo: str = "Azure/azure-cli",
    base_branch: str = "dev",
    model: str = ai.DEFAULT_MODEL,
    exclude_prs: list[int] | None = None,
    force_current_minor: str = "",
    force_new_minor: str = "",
    force_new_patch: str = "",
    force_recreate: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
    log_file: Path | None = None,
    run_url: str = "",
    tracking_issue: int | None = None,
) -> PipelineResult:
    """Run the full upgrade pipeline. Pure function over its inputs; the only
    side effects are git/gh calls and the verbose log file.

    Returns a ``PipelineResult`` so callers (CLI ``main`` and the campaign
    framework handler shim) can render it into an exit code or HandlerResult.
    """
    # --verbose is implied by --dry-run; an explicit log_file implies --verbose.
    verbose_enabled = verbose or dry_run or log_file is not None
    if verbose_enabled:
        log_path = log_file or (repo_root / "python_upgrade_agent.log")
    else:
        log_path = None
    vlog = _VerboseLog(log_path)
    if vlog.enabled:
        print(f"agent: verbose log -> {log_path}")
        vlog.write(f"repo_root: {repo_root}")
        vlog.write(f"repo: {repo or '(unset)'}")
        vlog.write(f"model: {model}")
        vlog.write(f"dry_run: {dry_run}")
        vlog.write(f"tracking_issue: {tracking_issue or '(none)'}")

    # --- Step 1: detect ---
    detected_current = detect.read_current_version(repo_root)
    detected_target: detect.Version | None = None
    if not (force_new_minor or force_new_patch):
        try:
            releases = detect.fetch_python_releases()
        except Exception as exc:  # noqa: BLE001
            print(f"agent: python.org feed unreachable: {exc}", file=sys.stderr)
            return PipelineResult(status="skipped", notes=f"python.org unreachable: {exc}")
        decision = detect.decide_upgrade(detected_current, releases)
        if decision is None:
            print(f"agent: no upgrade needed (current {detected_current.minor_str}).")
            return PipelineResult(status="skipped", notes="no upgrade needed")
        detected_target = decision.target

    current, target = _apply_force_overrides_kwargs(
        detected_current, detected_target,
        force_current_minor=force_current_minor,
        force_new_minor=force_new_minor,
        force_new_patch=force_new_patch,
    )
    if (target.major, target.minor) <= (current.major, current.minor):
        print(f"agent: target {target.minor_str} not newer than current {current.minor_str}.")
        return PipelineResult(status="skipped", notes="target not newer than current")

    new_minor = target.minor_str
    branch = BRANCH_TMPL.format(new_minor=new_minor)
    print(f"agent: planning upgrade {current.full_str} -> {target.full_str} on branch {branch}")
    if vlog.enabled:
        vlog.section("Detection")
        vlog.write(f"current: {current.full_str}")
        vlog.write(f"target:  {target.full_str}")
        vlog.write(f"branch:  {branch}")

    # --- Step 2: idempotency check ---
    if repo and not dry_run:
        pr = github_ops.find_any_pr(repo, branch)
        if pr:
            state = pr.get("state", "?")
            number = pr.get("number")
            if state == "OPEN":
                print(f"agent: PR #{number} already open for {new_minor}; nothing to do.")
                return PipelineResult(status="completed", pr=number, notes="existing open PR")
            if state == "MERGED":
                print(f"agent: PR #{number} for {new_minor} is MERGED; advancing phase.")
                return PipelineResult(
                    status="completed", pr=number,
                    notes=f"PR #{number} merged", phase="merged",
                )
            if force_recreate:
                print(f"agent: PR #{number} for {new_minor} is {state}; --force-recreate set, proceeding.")
            else:
                print(f"agent: PR #{number} for {new_minor} is {state}; not auto-recreating.")
                return PipelineResult(status="skipped", notes=f"prior PR #{number} {state}; force_recreate=False")

    # --- Step 3: discover candidates ---
    candidates = discover.git_grep(current.minor_str, repo_root)
    if not candidates:
        print(f"agent: no candidate locations found for {current.minor_str}; nothing to do.")
        return PipelineResult(status="skipped", notes="no candidates found")
    paths = discover.candidate_paths(candidates)
    print(f"agent: discovered {len(candidates)} candidate locations across {len(paths)} files.")
    if vlog.enabled:
        vlog.section("Discovered candidates")
        for p in sorted(paths):
            vlog.write(f"- {p}")
        vlog.section("Candidate details (with context)")
        vlog.write(discover.candidates_summary(candidates))

    # --- Step 4: gather few-shot references ---
    ref_repo = reference_repo or repo
    if ref_repo:
        exclude = set(exclude_prs or [])
        references = ai.fetch_reference_prs(ref_repo, exclude=exclude, limit=3)
    else:
        references = []
    print(f"agent: using {len(references)} reference PR(s) from {ref_repo or '(none)'} as few-shot examples.")
    if vlog.enabled:
        vlog.section("Few-shot reference PRs")
        for r in references:
            vlog.write(f"- #{r.number} {r.title} (diff {len(r.diff)} chars)")

    # --- Step 5: call LLM with one retry on validation failure ---
    retry_error: str | None = None
    edits: list[validate.ValidatedEdit] = []
    output: dict = {}
    for attempt in (1, 2):
        user_msg = ai.build_user_message(current, target, candidates, references, retry_error)
        if vlog.enabled:
            vlog.section(f"LLM attempt {attempt} — user message")
            vlog.write(user_msg)
        try:
            t0 = time.perf_counter()
            raw = ai.call_model(ai.SYSTEM_PROMPT, user_msg, model=model)
            elapsed = time.perf_counter() - t0
            if vlog.enabled:
                vlog.section(f"LLM attempt {attempt} — raw response ({elapsed:.1f}s, {len(raw)} chars)")
                vlog.write(raw)
            output = ai.parse_model_output(raw)
            edits = validate.validate(output, paths, repo_root)
            break
        except (validate.ValidationError, ValueError) as exc:
            retry_error = str(exc)
            print(f"agent: attempt {attempt} validation error: {exc}", file=sys.stderr)
            if vlog.enabled:
                vlog.write(f"\nVALIDATION ERROR (attempt {attempt}): {exc}")
            if attempt == 2:
                print("agent: validation failed twice; aborting.", file=sys.stderr)
                return PipelineResult(status="failed", notes=f"validation failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"agent: LLM call failed: {exc}", file=sys.stderr)
            if vlog.enabled:
                vlog.write(f"\nLLM CALL FAILED: {exc}")
            return PipelineResult(status="failed", notes=f"LLM call failed: {exc}")

    skipped = output.get("skipped", []) if isinstance(output, dict) else []
    notes = output.get("notes", "") if isinstance(output, dict) else ""

    if vlog.enabled:
        vlog.section(f"Validated edits ({len(edits)})")
        for e in edits:
            vlog.write(f"\n*** {e.path} ***")
            if e.reason:
                vlog.write(f"  reason: {e.reason}")
            vlog.write(f"  - {e.old_string}")
            vlog.write(f"  + {e.new_string}")
        vlog.section(f"Skipped ({len(skipped)})")
        for s in skipped:
            vlog.write(f"- {s.get('path')} @ {s.get('location')}: {s.get('why')}")
        vlog.section("Notes")
        vlog.write(notes or "(none)")

    # --- Step 5b: deterministic post-check for forgotten edits ---
    forgotten = post_check.find_forgotten_hits(
        repo_root=repo_root,
        current_minor=current.minor_str,
        candidates=candidates,
        edits=edits,
        skipped=skipped,
    )
    if forgotten:
        print(f"agent: post-check found {len(forgotten)} forgotten reference(s):",
              file=sys.stderr)
        for w in forgotten:
            print(f"  - {w}", file=sys.stderr)
    if vlog.enabled:
        vlog.section(f"Post-check forgotten references ({len(forgotten)})")
        for w in forgotten:
            vlog.write(f"- {w}")

    # --- Step 6: apply, commit, push, open PR (or print in dry-run) ---
    if dry_run:
        print("--- DRY RUN: proposed edits ---")
        for e in edits:
            print(f"\n*** {e.path} ***")
            print(f"  reason: {e.reason}")
            print(f"  - {e.old_string}")
            print(f"  + {e.new_string}")
        print(f"\n--- DRY RUN: {len(skipped)} skipped ---")
        for s in skipped:
            print(f"  {s.get('path')} @ {s.get('location')}: {s.get('why')}")
        print(f"\n--- DRY RUN: {len(forgotten)} forgotten ---")
        for w in forgotten:
            print(f"  {w}")
        print(f"\nnotes: {notes}")
        return PipelineResult(status="completed", pr=None, notes="dry-run")

    if not repo:
        print("agent: --repo not set; cannot create PR. Use --dry-run for local testing.",
              file=sys.stderr)
        return PipelineResult(status="failed", notes="--repo not set")

    github_ops.create_branch(repo_root, branch, base=base_branch)
    github_ops.apply_edits(repo_root, edits)
    github_ops.commit_all(
        repo_root,
        COMMIT_MSG_TMPL.format(current_minor=current.minor_str, new_minor=new_minor),
    )
    github_ops.push_branch(repo_root, branch)

    body = _render_pr_body(
        current=current,
        target=target,
        edits=edits,
        skipped=skipped,
        model=model,
        run_url=run_url,
        references=references,
        notes=notes,
        forgotten=forgotten,
        tracking_issue=tracking_issue,
    )
    title = PR_TITLE_TMPL.format(new_minor=new_minor)
    number = github_ops.open_draft_pr(
        repo=repo, title=title, body=body, head=branch, base=base_branch,
    )
    print(f"agent: opened draft PR #{number} for Python {new_minor}.")
    return PipelineResult(status="completed", pr=number, notes=f"opened PR #{number}")


def _apply_force_overrides_kwargs(
    detected_current: detect.Version,
    detected_target: detect.Version | None,
    *,
    force_current_minor: str,
    force_new_minor: str,
    force_new_patch: str,
) -> tuple[detect.Version, detect.Version]:
    """Kwargs-based variant used by run_pipeline (the older argparse-based
    helper above is kept for any external callers that still build a
    Namespace)."""
    ns = argparse.Namespace(
        force_current_minor=force_current_minor,
        force_new_minor=force_new_minor,
        force_new_patch=force_new_patch,
    )
    return _apply_force_overrides(detected_current, detected_target, ns)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = run_pipeline(
        repo_root=args.repo_root.resolve(),
        repo=args.repo,
        reference_repo=args.reference_repo,
        base_branch=args.base_branch,
        model=args.model,
        exclude_prs=args.exclude_pr,
        force_current_minor=args.force_current_minor,
        force_new_minor=args.force_new_minor,
        force_new_patch=args.force_new_patch,
        force_recreate=args.force_recreate,
        dry_run=args.dry_run,
        verbose=args.verbose,
        log_file=args.log_file,
        run_url=os.environ.get("AGENT_RUN_URL", ""),
    )
    return 0 if result.status != "failed" else 2


if __name__ == "__main__":
    import subprocess as _sp
    import sys as _sys
    try:
        raise SystemExit(main())
    except _sp.CalledProcessError as e:
        # Make stderr from gh/git failures visible in CI logs.
        _sys.stderr.write(
            f"\nagent: subprocess failed (exit {e.returncode}):\n"
            f"  cmd: {e.cmd}\n"
            f"  stdout: {e.stdout or '<empty>'}\n"
            f"  stderr: {e.stderr or '<empty>'}\n"
        )
        raise SystemExit(e.returncode)
