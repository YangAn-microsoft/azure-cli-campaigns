"""Python upgrade campaign definition.

Composes the work items for a Python minor bump across azure-cli and its
dependencies. Detection runs at plan-build time so item titles carry concrete
version strings; the resolved versions are forwarded to the handler via
``Item.params`` so both stages agree on the target without re-detecting.
"""
from __future__ import annotations

from pathlib import Path

from python_upgrade_agent import agent, detect

from .base import Campaign, CampaignPlan, HandlerContext, HandlerResult, Item


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
                             handler_repo=params.get("handler_repo"))
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
) -> list[Item]:
    new = target.minor_str
    return [
        # Real prerequisite: knack must release a compatible version first
        # because azure-cli pins it as a runtime dependency.
        Item(
            id="prereq-knack",
            title=f"`knack` supports Python {new}",
        ),
        # Handler-driven: bumps the embedded interpreter in azure-cli.
        Item(
            id="azure-cli-bump",
            title=f"Support Python {new} "
                  f"(bump embedded interpreter: {current.full_str} → {target.full_str})",
            handler="python_upgrade_agent",
            params={
                "current_minor": current.minor_str,
                "new_minor": target.minor_str,
                "new_full": target.full_str,
            },
            repo=handler_repo,
        ),
        # Companion repos: upgraded alongside azure-cli, not blocking prereqs.
        Item(
            id="aaz-dev",
            title=f"`aaz-dev` supports Python {new}",
        ),
        Item(
            id="azdev",
            title=f"`azdev` supports Python {new}",
        ),
        Item(
            id="azure-cli-extensions",
            title=f"`azure-cli-extensions` supports Python {new}",
        ),
    ]


def _render_intro(*, current: detect.Version, target: detect.Version) -> str:
    raw = (Path(__file__).parent / "python_upgrade_intro.md").read_text(encoding="utf-8")
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

def python_upgrade_handler(ctx: HandlerContext) -> HandlerResult:
    """Adapt the framework's HandlerContext to ``agent.run_pipeline`` and map
    the returned PipelineResult back into a HandlerResult."""
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
    return HandlerResult(status=result.status, pr=result.pr, notes=result.notes)
