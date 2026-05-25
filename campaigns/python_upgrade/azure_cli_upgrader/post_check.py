"""Deterministic post-check: catch current-minor references the LLM forgot.

After the LLM emits a (edits, skipped) plan, this module simulates applying
the edits to each candidate file in memory and re-scans for the current minor
using the same regex `discover.py` used to surface candidates. Any remaining
hit that isn't explicitly listed in `skipped` is flagged as a forgotten edit.

The check is deliberately read-only and non-fatal: it produces a warning list
that the caller decides what to do with (log, append to PR body, fail the run).
We do this instead of hard-failing because some forgotten hits are genuinely
benign (e.g. a snippet in a docstring the LLM judged uneditable but didn't
bother to enumerate); the human reviewer is the final arbiter.
"""
from __future__ import annotations

import re
from pathlib import Path

from .discover import Candidate, _build_pattern
from .validate import ValidatedEdit


def _apply_edits_to_text(text: str, edits_for_path: list[ValidatedEdit]) -> str:
    """Apply edits in order using str.replace semantics matching github_ops."""
    for e in edits_for_path:
        if e.replace_all:
            text = text.replace(e.old_string, e.new_string)
        else:
            text = text.replace(e.old_string, e.new_string, 1)
    return text


def _is_expected_leftover(line: str, current_minor: str) -> bool:
    """Lines we deterministically know SHOULD remain after a correct plan.

    setup.py / setup.cfg / pyproject.toml classifier lists are additive
    (rule 11): the current-minor `Programming Language :: Python :: X.Y`
    classifier is preserved alongside the new-minor one, so it is expected
    to still match the current-minor regex post-edit.
    """
    stripped = line.strip()
    return f"Programming Language :: Python :: {current_minor}" in stripped


def find_forgotten_hits(
    repo_root: Path,
    current_minor: str,
    candidates: list[Candidate],
    edits: list[ValidatedEdit],
    skipped: list[dict],
) -> list[str]:
    """Return human-readable lines for each forgotten current-minor reference.

    A "forgotten" hit is a line in a candidate file that, after the planned
    edits are applied in memory, still matches the current-minor regex AND
    does not appear in the LLM's `skipped` list (matched loosely by path +
    snippet substring).
    """
    pattern = re.compile(_build_pattern(current_minor))

    edits_by_path: dict[str, list[ValidatedEdit]] = {}
    for e in edits:
        edits_by_path.setdefault(e.path, []).append(e)

    skipped_by_path: dict[str, list[dict]] = {}
    for s in skipped:
        p = s.get("path", "")
        if p:
            skipped_by_path.setdefault(p, []).append(s)

    paths_with_candidates: dict[str, list[Candidate]] = {}
    for c in candidates:
        paths_with_candidates.setdefault(c.path, []).append(c)

    warnings: list[str] = []
    for path in paths_with_candidates:
        full_path = repo_root / path
        try:
            original = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        post = _apply_edits_to_text(original, edits_by_path.get(path, []))

        skipped_entries = skipped_by_path.get(path, [])
        for line_no, line in enumerate(post.splitlines(), start=1):
            if not pattern.search(line):
                continue
            if _is_expected_leftover(line, current_minor):
                continue
            line_stripped = line.strip()
            consciously_skipped = False
            for s in skipped_entries:
                snip = (s.get("snippet") or "").strip()
                if snip and (snip in line or line_stripped in snip):
                    consciously_skipped = True
                    break
            if not consciously_skipped:
                warnings.append(f"{path}:{line_no}: {line_stripped}")

    return warnings
