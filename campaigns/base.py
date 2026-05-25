"""Shared dataclasses and protocols for the campaign framework."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

ItemStatus = Literal["pending", "in_progress", "completed", "skipped", "failed"]


# --- Items & dependencies --------------------------------------------------
#
# An item can have multiple ordered "phases" representing milestones in its
# lifecycle (e.g. ("created", "merged") for a PR-opening handler). The
# default is a single implicit phase named "done", which preserves the old
# single-status behaviour for items that don't care.
#
# ``depends_on`` is metadata for the roadmap diagram (the framework does not
# enforce ordering at dispatch time). Two forms are accepted:
#
#   ``("a", "b:created")``                        -- shorthand tuple
#   ``{"merged": ("a", "b:created"), "created": ("c",)}``  -- dict keyed by
#                                                            this item's phase
#
# Entries inside each tuple may be ``"src_item"`` (shorthand for the
# dependency's *last* phase) or ``"src_item:src_phase"`` for a specific
# milestone. The tuple shorthand binds all entries to *this* item's *first*
# phase, which is the right semantics for "blocks any work on this item".


_DependsTuple = tuple[str, ...]
_DependsDict = dict[str, _DependsTuple]


@dataclass
class Item:
    """One row on the plan checklist.

    ``handler`` is the registered handler name (see ``registry.HANDLERS``).
    ``None`` means the item is manual and the framework never touches it.
    """
    id: str
    title: str
    handler: str | None = None
    params: dict = field(default_factory=dict)
    repo: str | None = None
    phases: tuple[str, ...] = ("done",)
    depends_on: _DependsTuple | _DependsDict = ()

    def __post_init__(self) -> None:
        if not self.phases:
            raise ValueError(f"Item {self.id!r}: phases must be non-empty")

    @property
    def final_phase(self) -> str:
        return self.phases[-1]

    @property
    def first_phase(self) -> str:
        return self.phases[0]

    def depends_on_by_phase(self) -> _DependsDict:
        """Return ``depends_on`` normalised to ``{phase: tuple-of-specs}``.

        Tuple shorthand binds to ``first_phase``.
        """
        if isinstance(self.depends_on, dict):
            return dict(self.depends_on)
        if self.depends_on:
            return {self.first_phase: tuple(self.depends_on)}
        return {}


@dataclass
class ItemState:
    """Per-item progress, persisted in the issue body's JSON state block.

    ``phase`` is the latest phase the item is known to have reached (an
    empty string means "no phase reached yet"). ``status`` describes the
    item's current health -- for multi-phase items, ``status == "completed"``
    + ``phase == item.final_phase`` means the item is fully done.
    """
    status: ItemStatus = "pending"
    pr: int | None = None
    notes: str = ""
    phase: str = ""

    def to_json(self) -> dict:
        d: dict = {"status": self.status}
        if self.pr is not None:
            d["pr"] = self.pr
        if self.notes:
            d["notes"] = self.notes
        if self.phase:
            d["phase"] = self.phase
        return d

    @classmethod
    def from_json(cls, data: dict) -> "ItemState":
        return cls(
            status=data.get("status", "pending"),
            pr=data.get("pr"),
            notes=data.get("notes", ""),
            phase=data.get("phase", ""),
        )


@dataclass
class HandlerResult:
    """Outcome a handler reports back to the framework.

    Multi-phase handlers should set ``phase`` to indicate which phase the
    ``status`` applies to (e.g. ``phase="created"`` when a PR has just been
    opened, ``phase="merged"`` when the item is fully done). For
    single-phase items, leaving ``phase=""`` is fine -- the framework
    defaults it to the item's only phase.
    """
    status: ItemStatus
    pr: int | None = None
    notes: str = ""
    phase: str = ""


@dataclass
class HandlerContext:
    """Passed to every handler invocation."""
    item: Item
    repo: str
    issue_number: int
    params: dict
    force_recreate: bool
    dry_run: bool


class Campaign(Protocol):
    id: str
    issue_repo: str  # default repo where the plan issue lives

    def build(self, params: dict) -> "CampaignPlan | None":
        """Resolve dynamic data (e.g. detect a target version) and return
        the static plan, or ``None`` if there's nothing to do (e.g. no
        upgrade needed)."""
        ...


@dataclass
class CampaignPlan:
    title: str
    intro: str
    items: list[Item]
    placeholders: dict = field(default_factory=dict)


# --- Phase status resolution ----------------------------------------------

def phase_status(item: Item, state: ItemState, phase: str) -> ItemStatus:
    """Return the effective status for a *specific phase* of an item.

    - phases strictly before ``state.phase`` are ``completed``;
    - the phase equal to ``state.phase`` carries ``state.status`` directly;
    - phases strictly after ``state.phase`` are ``pending``;
    - if ``state.phase == ""``:
        * ``state.status == "completed"`` is interpreted as the *first*
          phase being completed (back-compat for legacy single-phase state),
        * any other status applies to the first phase; later phases pending.
    """
    phases = item.phases
    if phase not in phases:
        return "pending"

    if state.phase == "":
        if state.status == "completed":
            return "completed" if phase == phases[0] else "pending"
        return state.status if phase == phases[0] else "pending"

    if state.phase not in phases:
        return "pending"

    state_idx = phases.index(state.phase)
    phase_idx = phases.index(phase)
    if phase_idx < state_idx:
        return "completed"
    if phase_idx == state_idx:
        return state.status
    return "pending"


def is_fully_completed(item: Item, state: ItemState) -> bool:
    """True iff the item has reached its final phase with completed status."""
    if state.status != "completed":
        return False
    if state.phase == "":
        return len(item.phases) == 1
    return state.phase == item.final_phase
