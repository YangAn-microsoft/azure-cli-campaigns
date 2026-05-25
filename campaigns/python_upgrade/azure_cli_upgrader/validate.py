"""Validate the LLM's proposed edits before applying them.

All checks must pass; any failure produces a structured error fed back to the
LLM for one retry, after which the agent stops and posts a comment.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Per AI Safety Contract, override for the bump workflow:
MAX_FILES = 25
MAX_CHANGED_LINES = 500


@dataclass
class ValidatedEdit:
    path: str
    old_string: str
    new_string: str
    reason: str
    replace_all: bool = False


class ValidationError(Exception):
    """Raised with a human/LLM-readable message describing what's wrong."""


def _count_changed_lines(old: str, new: str) -> int:
    # Conservative: count max(lines added, lines removed)
    return max(old.count("\n") + 1, new.count("\n") + 1)


def validate(
    output: dict,
    candidate_paths: set[str],
    repo_root: Path,
) -> list[ValidatedEdit]:
    """Validate the LLM output. Returns parsed edits or raises ValidationError."""
    if not isinstance(output, dict):
        raise ValidationError("Output is not a JSON object.")

    edits_raw = output.get("edits")
    if not isinstance(edits_raw, list):
        raise ValidationError("`edits` must be a list.")
    if not edits_raw:
        raise ValidationError(
            "`edits` is empty. The agent should never produce zero edits when "
            "candidates exist; either propose edits or stop the run with a reason."
        )

    skipped_raw = output.get("skipped", [])
    if not isinstance(skipped_raw, list):
        raise ValidationError("`skipped` must be a list (may be empty).")

    seen_keys: set[tuple[str, str]] = set()
    edits: list[ValidatedEdit] = []
    total_lines = 0
    touched_files: set[str] = set()

    for i, e in enumerate(edits_raw):
        if not isinstance(e, dict):
            raise ValidationError(f"edits[{i}] is not an object.")
        for key in ("path", "old_string", "new_string"):
            if key not in e or not isinstance(e[key], str) or not e[key]:
                raise ValidationError(f"edits[{i}].{key} is missing or empty.")
        path = e["path"]
        old_s = e["old_string"]
        new_s = e["new_string"]
        reason = e.get("reason", "")
        replace_all = bool(e.get("replace_all", False))

        if path not in candidate_paths:
            raise ValidationError(
                f"edits[{i}]: path '{path}' is not in the candidate list "
                f"(only discovered files may be edited)."
            )

        file_path = repo_root / path
        if not file_path.exists():
            raise ValidationError(f"edits[{i}]: file '{path}' does not exist.")
        text = file_path.read_text(encoding="utf-8", errors="replace")
        occurrences = text.count(old_s)
        if occurrences == 0:
            raise ValidationError(
                f"edits[{i}]: old_string not found in '{path}'. "
                f"Snippet: {old_s!r}"
            )
        if occurrences > 1 and not replace_all:
            raise ValidationError(
                f"edits[{i}]: old_string matches {occurrences} times in '{path}'; "
                f"must be unique. Either add more surrounding context to make it "
                f"unique, or set \"replace_all\": true to replace every occurrence. "
                f"Snippet: {old_s!r}"
            )
        if old_s == new_s:
            raise ValidationError(
                f"edits[{i}]: old_string and new_string are identical."
            )

        # Additive-classifier invariant (rule 11): when the LLM bumps a
        # setup.py `Programming Language :: Python :: X.Y` classifier line,
        # the prior-minor line must be preserved verbatim and a new sibling
        # appended — so old_string MUST appear as a substring of new_string.
        # Without this check the LLM occasionally produces typos like
        # `Programming Language :: Python :: Python :: 3.13` while
        # "preserving" the original.
        if "Programming Language :: Python ::" in old_s:
            if old_s not in new_s:
                raise ValidationError(
                    f"edits[{i}]: additive classifier edit must preserve the "
                    f"original line verbatim inside new_string. The "
                    f"old_string was not found as a substring of new_string. "
                    f"old_string={old_s!r} new_string={new_s!r}"
                )

        # Duplicates: silently skip a true duplicate (same path + old + new +
        # replace_all) — the LLM occasionally emits the same edit twice and a
        # hard error here causes a retry that historically loses many other
        # valid edits. Only error on a real conflict where the same
        # (path, old_string) maps to a different new_string.
        key = (path, old_s)
        if key in seen_keys:
            prior = next((x for x in edits if x.path == path and x.old_string == old_s), None)
            if prior is not None and prior.new_string == new_s and prior.replace_all == replace_all:
                continue
            raise ValidationError(
                f"edits[{i}]: conflicting duplicate edit (same path+old_string "
                f"but different new_string or replace_all flag)."
            )
        seen_keys.add(key)

        total_lines += _count_changed_lines(old_s, new_s) * occurrences
        touched_files.add(path)
        edits.append(ValidatedEdit(
            path=path, old_string=old_s, new_string=new_s,
            reason=reason, replace_all=replace_all,
        ))

    if len(touched_files) > MAX_FILES:
        raise ValidationError(
            f"Too many files touched: {len(touched_files)} > {MAX_FILES}."
        )
    if total_lines > MAX_CHANGED_LINES:
        raise ValidationError(
            f"Too many lines changed: {total_lines} > {MAX_CHANGED_LINES}."
        )

    return edits
