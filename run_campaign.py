"""Generic entry point: select a campaign by id and run it.

Usage::

    python -m run_campaign --campaign python-upgrade \\
        --params '{"repo_root": ".", "handler_repo": "Azure/azure-cli"}' \\
        [--issue-repo OWNER/REPO] [--force-recreate] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

from campaigns import framework
from campaigns.registry import CAMPAIGNS, HANDLERS


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a campaign")
    p.add_argument("--campaign", required=True, choices=sorted(CAMPAIGNS.keys()),
                   help="Campaign id (see scripts/campaigns/registry.py).")
    p.add_argument("--params", default="{}",
                   help="JSON object of campaign params.")
    p.add_argument("--issue-repo", default="",
                   help="Override the campaign's default issue host repo "
                        "(useful for testing against a fork).")
    p.add_argument("--force-recreate", action="store_true",
                   help="Re-dispatch handlers even for items already marked "
                        "completed in the issue state.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the plan; do not touch GitHub or the working tree.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    campaign = CAMPAIGNS[args.campaign]
    if args.issue_repo:
        # All current campaigns expose issue_repo as a mutable attribute.
        campaign.issue_repo = args.issue_repo  # type: ignore[attr-defined]

    try:
        params: dict[str, Any] = json.loads(args.params)
    except json.JSONDecodeError as exc:
        print(f"run_campaign: --params is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(params, dict):
        print("run_campaign: --params must decode to a JSON object.", file=sys.stderr)
        return 2

    params.setdefault("run_url", os.environ.get("AGENT_RUN_URL", ""))

    n = framework.run(
        campaign,
        params=params,
        handlers=HANDLERS,
        force_recreate=args.force_recreate,
        dry_run=args.dry_run,
    )
    if n == -1 and not args.dry_run:
        # No-op (e.g. detection said no upgrade).
        return 0
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(
            f"\nrun_campaign: subprocess failed (exit {exc.returncode}):\n"
            f"  cmd: {exc.cmd}\n"
            f"  stdout: {exc.stdout or '<empty>'}\n"
            f"  stderr: {exc.stderr or '<empty>'}\n"
        )
        raise SystemExit(exc.returncode)
