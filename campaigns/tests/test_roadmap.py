"""Tests for the Mermaid roadmap builder."""
from __future__ import annotations

from campaigns import roadmap
from campaigns.base import CampaignPlan, Item, ItemState


def _plan(items: list[Item]) -> CampaignPlan:
    return CampaignPlan(title="t", intro="", items=items)


def test_emits_fenced_mermaid_block():
    plan = _plan([Item(id="a", title="A", handler="h")])
    out = roadmap.build_roadmap_mermaid(
        plan, {"a": ItemState()},
        issue_repo="o/r", issue_number=10,
    )
    assert out.startswith("```mermaid\n")
    assert out.endswith("\n```")
    assert "flowchart LR" in out


def test_node_ids_are_slugged():
    plan = _plan([Item(id="azure-cli-bump", title="bump", handler="h")])
    out = roadmap.build_roadmap_mermaid(
        plan, {"azure-cli-bump": ItemState()},
        issue_repo="o/r", issue_number=10,
    )
    assert "azure_cli_bump[" in out
    # Display label keeps the original id with the hyphen.
    assert '"azure-cli-bump' in out


def test_edges_render_for_known_dependencies():
    items = [
        Item(id="a", title="A", handler="h"),
        Item(id="b", title="B", handler="h", depends_on=("a",)),
    ]
    out = roadmap.build_roadmap_mermaid(
        _plan(items), {"a": ItemState(), "b": ItemState()},
        issue_repo="o/r", issue_number=10,
    )
    assert "a --> b" in out


def test_unknown_dependency_is_silently_ignored():
    items = [Item(id="a", title="A", handler="h", depends_on=("ghost",))]
    out = roadmap.build_roadmap_mermaid(
        _plan(items), {"a": ItemState()},
        issue_repo="o/r", issue_number=10,
    )
    assert "ghost" not in out


def test_status_classes_assigned():
    items = [
        Item(id="done", title="D", handler="h"),
        Item(id="run", title="R", handler="h"),
        Item(id="bad", title="B", handler="h"),
        Item(id="wait", title="W", handler="h"),
        Item(id="manual", title="M"),  # no handler, pending
    ]
    state = {
        "done": ItemState(status="completed", pr=1),
        "run": ItemState(status="in_progress"),
        "bad": ItemState(status="failed", notes="boom"),
        "wait": ItemState(status="pending", notes="awaiting"),
        "manual": ItemState(status="pending"),
    }
    out = roadmap.build_roadmap_mermaid(
        _plan(items), state, issue_repo="o/r", issue_number=10,
    )
    assert "class done done" in out
    assert "class run active" in out
    assert "class bad blocked" in out
    assert "class wait waiting" in out
    # Manual pending stays in default styling — no class assignment.
    assert "class manual " not in out


def test_node_label_shows_pr_and_glyph():
    items = [Item(id="a", title="A", handler="h")]
    out = roadmap.build_roadmap_mermaid(
        _plan(items),
        {"a": ItemState(status="completed", pr=42)},
        issue_repo="o/r", issue_number=10,
    )
    assert "PR #42" in out
    assert "\u2705" in out  # ✅


def test_click_directive_links_to_pr_when_known():
    items = [Item(id="a", title="A", handler="h", repo="ext/repo")]
    out = roadmap.build_roadmap_mermaid(
        _plan(items),
        {"a": ItemState(status="completed", pr=7)},
        issue_repo="o/r", issue_number=10,
    )
    assert 'click a "https://github.com/ext/repo/pull/7"' in out


def test_click_directive_falls_back_to_issue():
    items = [Item(id="a", title="A", handler="h")]
    out = roadmap.build_roadmap_mermaid(
        _plan(items), {"a": ItemState()},
        issue_repo="o/r", issue_number=10,
    )
    assert 'click a "https://github.com/o/r/issues/10"' in out


def test_click_directive_omitted_when_no_issue_number():
    items = [Item(id="a", title="A", handler="h")]
    out = roadmap.build_roadmap_mermaid(
        _plan(items), {"a": ItemState()},
        issue_repo="o/r", issue_number=None,
    )
    assert "click a " not in out


# --- Multi-phase items --------------------------------------------------

def test_multiphase_item_renders_as_subgraph():
    items = [Item(id="bump", title="B", handler="h",
                  phases=("created", "merged"))]
    out = roadmap.build_roadmap_mermaid(
        _plan(items), {"bump": ItemState()},
        issue_repo="o/r", issue_number=10,
    )
    assert "subgraph sg_bump [bump]" in out
    assert "bump__created[" in out
    assert "bump__merged[" in out
    # Internal arrow linking consecutive phases.
    assert "bump__created --> bump__merged" in out
    assert "end" in out


def test_multiphase_phase_status_resolution():
    """state.phase="created" + completed => created node done, merged pending."""
    items = [Item(id="bump", title="B", handler="h",
                  phases=("created", "merged"))]
    state = {"bump": ItemState(status="completed", phase="created", pr=10)}
    out = roadmap.build_roadmap_mermaid(
        _plan(items), state, issue_repo="o/r", issue_number=12,
    )
    assert "class bump__created done" in out
    assert "class bump__merged waiting" in out


def test_multiphase_legacy_state_no_phase_completes_first_phase():
    """Old-style state (no phase, status=completed) maps to first-phase done."""
    items = [Item(id="bump", title="B", handler="h",
                  phases=("created", "merged"))]
    state = {"bump": ItemState(status="completed", pr=10)}
    out = roadmap.build_roadmap_mermaid(
        _plan(items), state, issue_repo="o/r", issue_number=12,
    )
    assert "class bump__created done" in out
    assert "class bump__merged waiting" in out


def test_dep_with_phase_targets_specific_phase_node():
    items = [
        Item(id="a", title="A", handler="h"),
        Item(id="b", title="B", handler="h",
             phases=("created", "merged"),
             depends_on={"merged": ("a",)}),
    ]
    out = roadmap.build_roadmap_mermaid(
        _plan(items), {"a": ItemState(), "b": ItemState()},
        issue_repo="o/r", issue_number=10,
    )
    # a is single-phase => bare node id; b:merged => per-phase node id.
    assert "a --> b__merged" in out
    # No edge into b__created from a.
    assert "a --> b__created" not in out


def test_dep_with_explicit_source_phase():
    items = [
        Item(id="src", title="S", handler="h",
             phases=("created", "merged")),
        Item(id="dst", title="D", handler="h",
             phases=("created", "merged"),
             depends_on={"merged": ("src:merged",)}),
    ]
    out = roadmap.build_roadmap_mermaid(
        _plan(items), {"src": ItemState(), "dst": ItemState()},
        issue_repo="o/r", issue_number=10,
    )
    assert "src__merged --> dst__merged" in out


def test_tuple_shorthand_binds_to_first_phase():
    items = [
        Item(id="a", title="A", handler="h"),
        Item(id="b", title="B", handler="h",
             phases=("created", "merged"),
             depends_on=("a",)),  # tuple shorthand
    ]
    out = roadmap.build_roadmap_mermaid(
        _plan(items), {"a": ItemState(), "b": ItemState()},
        issue_repo="o/r", issue_number=10,
    )
    assert "a --> b__created" in out


def test_click_url_emitted_for_each_phase_node():
    items = [Item(id="bump", title="B", handler="h",
                  phases=("created", "merged"))]
    state = {"bump": ItemState(status="completed", phase="created", pr=10)}
    out = roadmap.build_roadmap_mermaid(
        _plan(items), state, issue_repo="o/r", issue_number=12,
    )
    pr_url = "https://github.com/o/r/pull/10"
    assert f'click bump__created "{pr_url}"' in out
    assert f'click bump__merged "{pr_url}"' in out
