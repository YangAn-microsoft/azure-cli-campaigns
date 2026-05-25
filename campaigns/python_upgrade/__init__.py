"""Python upgrade campaign definition.

Composes the work items for a Python minor bump across azure-cli and its
dependencies. Detection runs at plan-build time so item titles carry concrete
version strings; the resolved versions are forwarded to the handler via
``Item.params`` so both stages agree on the target without re-detecting.
"""
from __future__ import annotations

from pathlib import Path

from .azure_cli_upgrader import agent, detect
from .knack_upgrader import agent as knack_agent
from .knack_pin_bumper import agent as knack_pin_agent
from .validators import (
    pypi_classifier_validator_handler,
    repo_file_validator_handler,
)

from ..base import Campaign, CampaignPlan, HandlerContext, HandlerResult, Item


class PythonUpgradeCampaign:
    id = "python-upgrade"

    def __init__(self, issue_repo: str = "Azure/azure-cli"):
        # Settable so tests / dev runs can target a fork.
        self.issue_repo = issue_repo

    # ----- Campaign protocol -----

    def build(self, params: dict) -> CampaignPlan | None:
        repo_root = Path(params.get("repo_root") or ".").resolve()

        # Resolve current + target. Params may override detection (useful for
        # forced runs / tests). All four overrides must be passed together.
        forced = all(k in params for k in ("current_full", "new_full"))
        if forced:
            current = _parse_full(params["current_full"])
            target = _parse_full(params["new_full"])
        else:
            current = detect.read_current_version(repo_root)
            try:
                releases = detect.fetch_python_releases()
            except Exception as exc:  # noqa: BLE001
                print(f"python-upgrade campaign: python.org feed unreachable: {exc}")
                return None
            decision = detect.decide_upgrade(current, releases)
            if decision is None:
                print(f"python-upgrade campaign: no upgrade needed "
                      f"(current {current.minor_str}).")
                return None
            target = decision.target

        title = f"Support Python {target.minor_str}"
        intro = _render_intro(current=current, target=target)
        items = _build_items(current=current, target=target,
                             handler_repo=params.get("handler_repo"),
                             knack_repo=params.get("knack_repo", "Azure/knack"))
        return CampaignPlan(
            title=title,
            intro=intro,
            items=items,
            placeholders={
                "current_minor": current.minor_str,
                "new_minor": target.minor_str,
                "current_full": current.full_str,
                "new_full": target.full_str,
            },
        )


def _parse_full(s: str) -> detect.Version:
    parts = [int(x) for x in s.split(".")]
    if len(parts) != 3:
        raise ValueError(f"expected MAJOR.MINOR.PATCH, got {s!r}")
    return detect.Version(*parts)


def _build_items(
    *,
    current: detect.Version,
    target: detect.Version,
    handler_repo: str | None,
    knack_repo: str | None,
) -> list[Item]:
    new = target.minor_str
    return [
        # Lead item: bumps the embedded interpreter in azure-cli. Two phases:
        #   created -- handler opens the PR (or detects an existing one).
        #   merged  -- the PR has been merged. Gated by all companion items
        #              (each must declare Python <new> support) plus the
        #              knack-pin bump landing.
        Item(
            id="azure-cli-bump",
            title=f"Support Python {new} "
                  f"(bump embedded interpreter: {current.full_str} \u2192 {target.full_str})",
            handler="azure_cli_upgrader",
            params={
                "current_minor": current.minor_str,
                "new_minor": target.minor_str,
                "new_full": target.full_str,
            },
            repo=handler_repo,
            phases=("created", "merged"),
            depends_on={
                "merged": (
                    "aaz-dev",
                    "azdev",
                    "azure-cli-extensions",
                    "azure-cli-knack-pin:merged",
                ),
            },
        ),
        # Parallel: opens a PR on the knack repo declaring new Python
        # support. Single "done" phase -- the handler short-circuits to
        # completed once knack ships on PyPI with the new classifier.
        Item(
            id="prereq-knack",
            title=f"`knack` supports Python {new}",
            handler="knack_upgrader",
            params={"new_minor": target.minor_str},
            repo=knack_repo,
        ),
        # Follow-up: after knack ships, bump the knack pin in azure-cli.
        # Multi-phase like the main bump: the PR is one milestone, the
        # merge is another. Its merged phase gates azure-cli-bump:merged.
        Item(
            id="azure-cli-knack-pin",
            title=f"Bump `knack` pin in azure-cli after Python {new} release",
            handler="knack_pin_bumper",
            params={"new_minor": target.minor_str},
            repo=handler_repo,
            phases=("created", "merged"),
            depends_on=("prereq-knack",),
        ),
        # Companion repos: each declares Python <new> support independently
        # of the bump PR. Each has a lightweight validator handler so the
        # campaign auto-detects completion. They feed into azure-cli-bump's
        # merged phase (declared above), not into its created phase --
        # opening the bump PR doesn't require any companion to be ready.
        Item(
            id="aaz-dev",
            title=f"`aaz-dev` supports Python {new}",
            handler="pypi_classifier_validator",
            params={"package": "aaz-dev", "new_minor": target.minor_str},
        ),
        Item(
            id="azdev",
            title=f"`azdev` supports Python {new}",
            handler="pypi_classifier_validator",
            params={"package": "azdev", "new_minor": target.minor_str},
        ),
        Item(
            id="azure-cli-extensions",
            title=f"`azure-cli-extensions` supports Python {new}",
            handler="repo_file_validator",
            repo="Azure/azure-cli-extensions",
            params={
                "path": "azure-pipelines.yml",
                "needle": "{new_minor}",
                "new_minor": target.minor_str,
            },
        ),
    ]


def _render_intro(*, current: detect.Version, target: detect.Version) -> str:
    raw = (Path(__file__).parent / "intro.md").read_text(encoding="utf-8")
    substitutions = {
        "{{current_minor}}": current.minor_str,
        "{{new_minor}}": target.minor_str,
        "{{current_full}}": current.full_str,
        "{{new_full}}": target.full_str,
        "{{new_minor_dotless}}": target.minor_str.replace(".", ""),
    }
    out = raw
    for key, val in substitutions.items():
        out = out.replace(key, val)
    # Strip the leading HTML comment block (developer-facing notes).
    if out.lstrip().startswith("<!--"):
        out = out.lstrip()
        end = out.find("-->")
        if end != -1:
            out = out[end + 3:].lstrip()
    return out


# ----- Handler -----

def azure_cli_upgrader_handler(ctx: HandlerContext) -> HandlerResult:
    """Adapt the framework's HandlerContext to ``agent.run_pipeline`` and map
    the returned PipelineResult back into a HandlerResult.

    ``azure-cli-bump`` is multi-phase (``created``/``merged``). The agent
    can at best detect or open the PR -- it cannot merge it -- so any
    non-skipped result is reported as the ``created`` phase. The merged
    phase is set later by a (future) merge-watcher or manually.
    """
    repo_root = Path(ctx.params.get("repo_root") or ".").resolve()
    result = agent.run_pipeline(
        repo_root=repo_root,
        repo=ctx.repo,
        reference_repo=ctx.params.get("reference_repo", "Azure/azure-cli"),
        base_branch=ctx.params.get("base_branch", "dev"),
        model=ctx.params.get("model", agent.ai.DEFAULT_MODEL),
        exclude_prs=ctx.params.get("exclude_prs"),
        force_current_minor=ctx.params.get("current_minor", ""),
        force_new_minor=ctx.params.get("new_minor", ""),
        force_new_patch=ctx.params.get("new_full", ""),
        force_recreate=ctx.force_recreate,
        dry_run=ctx.dry_run,
        run_url=ctx.params.get("run_url", ""),
        tracking_issue=ctx.issue_number,
    )
    phase = "created" if result.status in ("completed", "in_progress", "failed") else ""
    return HandlerResult(
        status=result.status, pr=result.pr, notes=result.notes, phase=phase,
    )


# Backwards-compat alias for older registrations.
python_upgrade_handler = azure_cli_upgrader_handler


def knack_upgrader_handler(ctx: HandlerContext) -> HandlerResult:
    """Open / track a PR on the knack repo declaring new Python support."""
    work_dir = ctx.params.get("work_dir")
    result = knack_agent.run_pipeline(
        repo=ctx.repo,
        new_minor=ctx.params.get("new_minor", ""),
        work_dir=Path(work_dir).resolve() if work_dir else None,
        tracking_issue=ctx.issue_number,
        tracking_repo=ctx.params.get("tracking_repo"),
        base_branch=ctx.params.get("base_branch", "dev"),
        dry_run=ctx.dry_run,
        force_recreate=ctx.force_recreate,
    )
    return HandlerResult(status=result.status, pr=result.pr, notes=result.notes)


def knack_pin_bumper_handler(ctx: HandlerContext) -> HandlerResult:
    """Bump the knack pin in azure-cli's setup.py once knack ships on PyPI."""
    repo_root = Path(ctx.params.get("repo_root") or ".").resolve()
    result = knack_pin_agent.run_pipeline(
        repo=ctx.repo,
        repo_root=repo_root,
        new_minor=ctx.params.get("new_minor", ""),
        base_branch=ctx.params.get("base_branch", "dev"),
        tracking_issue=ctx.issue_number,
        tracking_repo=ctx.params.get("tracking_repo"),
        dry_run=ctx.dry_run,
        force_recreate=ctx.force_recreate,
    )
    return HandlerResult(
        status=result.status, pr=result.pr, notes=result.notes,
        phase=result.phase,
    )
