"""Mermaid flowchart rendering for campaign progress.

Items with a single phase render as a plain node. Items with multiple
phases render as a ``subgraph`` containing one node per phase, joined by
internal arrows reflecting the phase order. Phase-scoped dependencies (see
``Item.depends_on_by_phase``) target the specific phase node, so the
picture literally shows which milestone unblocks which.

GitHub renders Mermaid in a sandboxed iframe. ``click NODE "url"``
directives produce clickable hyperlinks (opening in a new tab); JS
``callback`` directives are stripped. So the diagram is interactive only
insofar as nodes work as links to PRs or the tracking issue.

Status -> visual style (applied per phase node):
    completed   -> green fill
    in_progress -> amber fill
    failed      -> red fill
    pending +
      has handler -> grey, dashed (waiting on external)
    pending +
      no handler  -> default styling (manual, not yet started)
"""
from __future__ import annotations

from .base import (
    CampaignPlan,
    Item,
    ItemState,
    phase_status,
)


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


def _slug(s: str) -> str:
    """Mermaid identifiers must be alphanumeric/underscore."""
    return s.replace("-", "_").replace(".", "_").replace(":", "_")


def _node_id(item_id: str, phase: str | None = None) -> str:
    """Node id used inside the diagram.

    For multi-phase items, the node id is per-phase: ``"<item>_<phase>"``.
    For single-phase items we keep the original id (no suffix), which is
    convenient for tests and backwards compatibility.
    """
    base = _slug(item_id)
    if phase is None:
        return base
    return f"{base}__{_slug(phase)}"


def _subgraph_id(item_id: str) -> str:
    return f"sg_{_slug(item_id)}"


def _phase_label(item: Item, state: ItemState, phase: str) -> str:
    """Label for a single phase node inside a multi-phase subgraph."""
    st = phase_status(item, state, phase)
    glyph = _STATUS_GLYPH.get(st, "")
    parts: list[str] = [glyph, phase] if glyph else [phase]
    label = " ".join(p for p in parts if p)
    # Surface PR number only on the phase whose status currently matches
    # the stored ItemState (i.e. the active milestone).
    if state.pr is not None and (state.phase == phase or
                                 (state.phase == "" and phase == item.first_phase)):
        label = f"{label}<br/>PR #{state.pr}"
    return label


def _single_label(item: Item, state: ItemState) -> str:
    """Label for a single-phase item (plain node)."""
    st = phase_status(item, state, item.first_phase)
    glyph = _STATUS_GLYPH.get(st, "")
    parts: list[str] = []
    if glyph:
        parts.append(glyph)
    if state.pr is not None:
        parts.append(f"PR #{state.pr}")
    suffix = " ".join(parts).strip()
    return f"{item.id}<br/>{suffix}" if suffix else item.id


def _url_for(
    item: Item, state: ItemState, *,
    issue_repo: str, issue_number: int | None,
) -> str | None:
    if state.pr is not None:
        repo = item.repo or issue_repo
        return f"https://github.com/{repo}/pull/{state.pr}"
    if issue_number is not None:
        return f"https://github.com/{issue_repo}/issues/{issue_number}"
    return None


def _parse_dep_spec(spec: str, items_by_id: dict[str, Item]) -> tuple[str, str] | None:
    """Parse a dep spec ``"item"`` or ``"item:phase"`` into ``(item_id, phase)``.

    Returns ``None`` if the referenced item is unknown. When no phase is
    given, defaults to the dependency's *final* phase.
    """
    if ":" in spec:
        src_id, src_phase = spec.split(":", 1)
    else:
        src_id, src_phase = spec, ""
    src_id = src_id.strip()
    src_phase = src_phase.strip()
    target = items_by_id.get(src_id)
    if target is None:
        return None
    if not src_phase:
        src_phase = target.final_phase
    if src_phase not in target.phases:
        return None
    return src_id, src_phase


def _resolve_node(item: Item, phase: str) -> str:
    """Resolve which Mermaid node represents (item, phase).

    Single-phase items use the bare node id (so test assertions like
    ``"a --> b"`` keep working). Multi-phase items use per-phase nodes.
    """
    if len(item.phases) == 1:
        return _node_id(item.id)
    return _node_id(item.id, phase)


def build_roadmap_mermaid(
    plan: CampaignPlan,
    item_state: dict[str, ItemState],
    *,
    issue_repo: str,
    issue_number: int | None = None,
) -> str:
    """Return a fenced ```mermaid block ready to embed in an issue body."""
    items_by_id = {it.id: it for it in plan.items}
    lines: list[str] = ["```mermaid", "flowchart LR"]

    # --- Nodes ---
    for item in plan.items:
        state = item_state.get(item.id, ItemState())
        if len(item.phases) == 1:
            lines.append(f'    {_node_id(item.id)}["{_single_label(item, state)}"]')
        else:
            lines.append(f"    subgraph {_subgraph_id(item.id)} [{item.id}]")
            for phase in item.phases:
                lines.append(
                    f'        {_node_id(item.id, phase)}'
                    f'["{_phase_label(item, state, phase)}"]'
                )
            # Internal arrows linking consecutive phases.
            for i in range(len(item.phases) - 1):
                lines.append(
                    f"        {_node_id(item.id, item.phases[i])} -->"
                    f" {_node_id(item.id, item.phases[i + 1])}"
                )
            lines.append("    end")

    # --- Cross-item edges ---
    for item in plan.items:
        for target_phase, specs in item.depends_on_by_phase().items():
            if target_phase not in item.phases:
                continue
            target_node = _resolve_node(item, target_phase)
            for spec in specs:
                parsed = _parse_dep_spec(spec, items_by_id)
                if parsed is None:
                    continue
                src_id, src_phase = parsed
                src_item = items_by_id[src_id]
                src_node = _resolve_node(src_item, src_phase)
                lines.append(f"    {src_node} --> {target_node}")

    # --- Class defs ---
    lines.append("")
    for cd in _CLASSDEFS:
        lines.append(f"    {cd}")

    # --- Class assignments per phase node ---
    for item in plan.items:
        state = item_state.get(item.id, ItemState())
        for phase in item.phases:
            st = phase_status(item, state, phase)
            # Manual items at default pending stay in default styling.
            if st == "pending" and item.handler is None:
                continue
            cls = _STATUS_CLASS.get(st)
            if cls:
                lines.append(f"    class {_resolve_node(item, phase)} {cls}")

    # --- Hyperlinks ---
    for item in plan.items:
        state = item_state.get(item.id, ItemState())
        url = _url_for(item, state, issue_repo=issue_repo, issue_number=issue_number)
        if url is None:
            continue
        # For multi-phase items, point every phase node at the same URL
        # (the PR for both "created" and "merged" is the same PR).
        for phase in item.phases:
            lines.append(f'    click {_resolve_node(item, phase)} "{url}" _blank')

    lines.append("```")
    return "\n".join(lines)
