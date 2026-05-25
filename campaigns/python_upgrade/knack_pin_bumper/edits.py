"""Deterministic edits for bumping the knack pin in azure-cli-core/setup.py."""
from __future__ import annotations

import re
from dataclasses import dataclass


PIN_RE = re.compile(
    r"""^(?P<lead>\s*['"]knack)(?P<op>~=|>=|==)(?P<ver>[\d.]+)(?P<tail>['"],?)\s*$""",
    re.MULTILINE,
)


@dataclass(frozen=True)
class PinInfo:
    operator: str   # ~= | >= | ==
    version: str    # e.g. 0.14.0


def find_pin(content: str) -> PinInfo | None:
    """Return the current knack pin (operator + version), or None if not found."""
    m = PIN_RE.search(content)
    if not m:
        return None
    return PinInfo(operator=m.group("op"), version=m.group("ver"))


def bump_pin(content: str, new_version: str) -> str:
    """Replace the knack version in the pin, preserving the comparator.

    Idempotent: returns the input unchanged if the pin is already at
    ``new_version``. Raises ``ValueError`` if no knack pin is found.
    """
    current = find_pin(content)
    if current is None:
        raise ValueError("no 'knack<op>VERSION' pin found")
    if current.version == new_version:
        return content
    return PIN_RE.sub(
        rf"\g<lead>\g<op>{new_version}\g<tail>",
        content,
        count=1,
    )
