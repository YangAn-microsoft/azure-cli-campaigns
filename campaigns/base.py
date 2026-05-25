"""Shared dataclasses and protocols for the campaign framework."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

ItemStatus = Literal["pending", "in_progress", "completed", "skipped", "failed"]


@dataclass(frozen=True)
class Item:
    """One row on the plan checklist.

    ``handler`` is the registered handler name (see ``registry.HANDLERS``).
    ``None`` means the item is manual and the framework never touches it.
    ``depends_on`` lists item ids this item logically depends on; used only
    for the roadmap diagram (the framework does not enforce ordering).
    """
    id: str
    title: str
    handler: str | None = None
    params: dict = field(default_factory=dict)
    repo: str | None = None
    depends_on: tuple[str, ...] = ()


@dataclass
class ItemState:
    status: ItemStatus = "pending"
    pr: int | None = None
    notes: str = ""

    def to_json(self) -> dict:
        d: dict = {"status": self.status}
        if self.pr is not None:
            d["pr"] = self.pr
        if self.notes:
            d["notes"] = self.notes
        return d

    @classmethod
    def from_json(cls, data: dict) -> "ItemState":
        return cls(
            status=data.get("status", "pending"),
            pr=data.get("pr"),
            notes=data.get("notes", ""),
        )


@dataclass
class HandlerResult:
    status: ItemStatus
    pr: int | None = None
    notes: str = ""


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
