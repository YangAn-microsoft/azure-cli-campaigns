"""Discover candidate locations referencing the current Python minor.

Runs ``git grep`` for the current minor (e.g. "3.13") across the repo,
excluding paths that must never be touched (test recordings, agent's own
workflow file, etc.). Returns a list of candidates with file + line context
for the LLM to classify as edit/skip/uncertain.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Paths the agent must never propose edits for.
EXCLUDE_PATHSPECS = [
    ":!*recordings*",
    ":!*.lock",
    ":!.github/workflows/PythonUpgradeAgent.yml",
    ":!scripts/python_upgrade_agent/**",
    ":!CHANGELOG*",
    ":!HISTORY*",
    ":!**/*.svg",
    ":!**/*.png",
]

# Hard deny-list - even if grep matches, we drop these.
DENY_FILES = {
    "LICENSE",
    "NOTICE.txt",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
}


@dataclass(frozen=True)
class Candidate:
    path: str
    line_number: int
    line_text: str
    context_before: list[str]
    context_after: list[str]


def _build_pattern(current_minor: str) -> str:
    """Regex matching X.Y or X.Y.Z but not X.Y.Z prefixes of other versions,
    plus dotless identifier forms like ``Python313``, ``py313``.

    Examples for current_minor='3.13':
        matches: 3.13, 3.13.7, "3.13", 'Python3.13', PythonVersion=3.13,
                 Python313, py313, AutomationFullTestPython313ProfileLatest
        no match: 3.130, 3.13a, 13.13, Python3130, py3131
    """
    major, minor = current_minor.split(".")
    # Dotted form (with patch-version optional, blocking false neighbours).
    dotted = rf"(?<![0-9.]){re.escape(major)}\.{re.escape(minor)}(?:\.\d+)?\b"
    # Dotless identifier form: Python313 / py313 / Py313 / python313.
    # The (?![0-9]) prevents matching the prefix of Python3130, py3131, etc.
    dotless = rf"[Pp]y(?:thon)?{re.escape(major)}{re.escape(minor)}(?![0-9])"
    return f"{dotted}|{dotless}"


def git_grep(current_minor: str, repo_root: Path | None = None) -> list[Candidate]:
    """Run git grep for the current Python minor and return candidates with context."""
    root = repo_root or Path.cwd()
    pattern = _build_pattern(current_minor)
    cmd = [
        "git", "-C", str(root), "grep",
        "-n",                # line numbers
        "-I",                # skip binary
        "--no-color",
        "-P", pattern,       # PCRE: needed for lookbehind
        "--",
    ] + EXCLUDE_PATHSPECS
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False, encoding="utf-8",
        )
    except FileNotFoundError as e:
        raise RuntimeError("git is not available on PATH") from e

    # git grep exits 1 when nothing matches; that is not an error for us.
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"git grep failed (rc={result.returncode}): {result.stderr.strip()}"
        )

    # Group matches per file, then attach ±2 lines of context.
    # ±2 (not ±3) keeps the prompt small while still showing the immediate
    # surroundings — enough to disambiguate setup.py classifiers, YAML matrix
    # entries, and CI displayName/inputs pairs.
    per_file_lines: dict[str, list[tuple[int, str]]] = {}
    for raw in result.stdout.splitlines():
        # Format: path:line:content  (path may contain ':' on Windows? git uses forward slashes)
        try:
            path, lineno_s, content = raw.split(":", 2)
        except ValueError:
            continue
        if Path(path).name in DENY_FILES:
            continue
        try:
            lineno = int(lineno_s)
        except ValueError:
            continue
        per_file_lines.setdefault(path, []).append((lineno, content))

    candidates: list[Candidate] = []
    for path, hits in per_file_lines.items():
        file_lines = (root / path).read_text(encoding="utf-8", errors="replace").splitlines()
        for lineno, content in hits:
            i = lineno - 1
            before = file_lines[max(0, i - 2): i]
            after = file_lines[i + 1: i + 3]
            candidates.append(Candidate(
                path=path,
                line_number=lineno,
                line_text=content,
                context_before=before,
                context_after=after,
            ))
    return candidates


def candidates_summary(candidates: list[Candidate]) -> str:
    """Render candidates as a compact string for inclusion in the LLM prompt."""
    by_file: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_file.setdefault(c.path, []).append(c)
    parts: list[str] = []
    for path, items in sorted(by_file.items()):
        parts.append(f"### {path}")
        for c in sorted(items, key=lambda x: x.line_number):
            ctx_b = "\n".join(c.context_before)
            ctx_a = "\n".join(c.context_after)
            parts.append(
                f"--- line {c.line_number} ---\n"
                f"{ctx_b}\n"
                f">>> {c.line_text}\n"
                f"{ctx_a}"
            )
        parts.append("")
    return "\n".join(parts)


def candidate_paths(candidates: list[Candidate]) -> set[str]:
    return {c.path for c in candidates}
