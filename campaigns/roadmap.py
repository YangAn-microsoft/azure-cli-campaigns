"""Mermaid flowchart rendering for campaign progress.

GitHub renders Mermaid in a sandboxed iframe. ``click NODE "url"``
directives produce clickable hyperlinks (opening in a new tab); JS
``callback`` directives are stripped. So the diagram is interactive only
insofar as nodes work as links to PRs or the tracking issue.

Status → visual style:
    completed   → green fill
    in_progress → amber fill
    failed      → red fill
    pending +
      has handler → grey, dashed (waiting on external)
    pending +
      no handler  → default styling (manual, not yet started)
"""
from __future__ import annotations

from .base import CampaignPlan, Item, ItemState


_STATUS_GLYPH = {
    "completed": "\u2705",     # ✅
    "in_progress": "\U0001F501",  # 🔁
    "failed": "\u274C",        # ❌
    "pending": "\u23F3",       # ⏳
    "skipped": "\u23ED\uFE0F",  # ⏭
}

_STATUS_CLASS = {
    "completed": "done",
    "in_progress": "active",
    "failed": "blocked",
    "pending": "waiting",  # only applied to handler-driven items
}

_CLASSDEFS = [
    "classDef done fill:#1f6f3a,stroke:#0d4a25,color:#fff",
    "classDef active fill:#d29922,stroke:#9e6a00,color:#000",
    "classDef blocked fill:#cf222e,stroke:#82071e,color:#fff",
    "classDef waiting stroke:#6e7781,stroke-dasharray:4 2,color:#57606a",
]


def _node_id(item_id: str) -> str:
    """Mermaid node ids must be alphanumeric/underscore. Slug hyphens."""
    return item_id.replace("-", "_").replace(".", "_")


def _label(item: Item, st: ItemState) -> str:
    glyph = _STATUS_GLYPH.get(st.status, "")
    parts: list[str] = []
    if glyph:
        parts.append(glyph)
    if st.pr is not None:
        parts.append(f"PR #{st.pr}")
    suffix = " ".join(parts).strip()
    return f"{item.id}<br/>{suffix}" if suffix else item.id


def _url_for(
    item: Item, st: ItemState, *, issue_repo: str, issue_number: int | None,
) -> str | None:
    """Best link for the node.

    - PR known          → the PR (in item.repo or, by default, issue_repo)
    - otherwise         → the tracking issue (when issue_number is known)
    """
    if st.pr is not None:
        repo = item.repo or issue_repo
        return f"https://github.com/{repo}/pull/{st.pr}"
    if issue_number is not None:
        return f"https://github.com/{issue_repo}/issues/{issue_number}"
    return None


def build_roadmap_mermaid(
    plan: CampaignPlan,
    item_state: dict[str, ItemState],
    *,
    issue_repo: str,
    issue_number: int | None = None,
) -> str:
    """Return a fenced ```mermaid block ready to embed in an issue body."""
    lines: list[str] = ["```mermaid", "flowchart LR"]

    # Nodes
    for item in plan.items:
        st = item_state.get(item.id, ItemState())
        lines.append(f'    {_node_id(item.id)}["{_label(item, st)}"]')

    # Edges (declared dependencies; missing targets silently ignored)
    known = {it.id for it in plan.items}
    for item in plan.items:
        for dep in item.depends_on:
            if dep in known:
                lines.append(f"    {_node_id(dep)} --> {_node_id(item.id)}")

    # Class defs (always emit; harmless if a class is unused)
    lines.append("")
    for cd in _CLASSDEFS:
        lines.append(f"    {cd}")

    # Class assignments
    for item in plan.items:
        st = item_state.get(item.id, ItemState())
        # Manual items at the default pending state stay in default styling;
        # they're baseline expectations, not "stuck".
        if st.status == "pending" and item.handler is None:
            continue
        cls = _STATUS_CLASS.get(st.status)
        if cls:
            lines.append(f"    class {_node_id(item.id)} {cls}")

    # Hyperlinks
    for item in plan.items:
        st = item_state.get(item.id, ItemState())
        url = _url_for(
            item, st, issue_repo=issue_repo, issue_number=issue_number,
        )
        if url:
            lines.append(f'    click {_node_id(item.id)} "{url}" _blank')

    lines.append("```")
    return "\n".join(lines)
