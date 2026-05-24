"""Hidden JSON state block embedded in the plan issue body.

The framework's source of truth for per-item status is the JSON inside an
HTML comment at the very top of the issue body. Humans can edit the visible
checklist below freely without confusing the framework.

Format::

    <!-- campaign-state
    {"version": 1, "items": {"<item-id>": {"status": "...", "pr": N}}}
    -->
"""
from __future__ import annotations

import json
import re

from .base import ItemState

_BLOCK_RE = re.compile(
    r"<!--\s*campaign-state\s*(\{.*?\})\s*-->",
    re.DOTALL,
)

STATE_VERSION = 1


def parse(body: str) -> dict[str, ItemState]:
    """Extract per-item state from an issue body. Missing/invalid -> empty."""
    if not body:
        return {}
    m = _BLOCK_RE.search(body)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    items = data.get("items", {})
    return {k: ItemState.from_json(v) for k, v in items.items() if isinstance(v, dict)}


def serialize(state: dict[str, ItemState]) -> str:
    """Render the HTML-comment-wrapped JSON block (no surrounding newlines)."""
    payload = {
        "version": STATE_VERSION,
        "items": {k: v.to_json() for k, v in state.items()},
    }
    return "<!-- campaign-state\n" + json.dumps(payload, indent=2, sort_keys=True) + "\n-->"


def strip(body: str) -> str:
    """Return the body with any existing state block removed."""
    if not body:
        return ""
    return _BLOCK_RE.sub("", body, count=1).lstrip("\n")
