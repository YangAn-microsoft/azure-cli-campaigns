"""Deterministic file edits for declaring a new Python minor in knack.

All functions are pure (string in, string out) and idempotent — calling them
again with the same target is a no-op.
"""
from __future__ import annotations

import re


def add_python_to_setup_py(content: str, new_minor: str) -> str:
    """Insert ``Programming Language :: Python :: X.Y`` into the classifiers
    list, immediately after the highest existing ``Python :: 3.x`` line.

    Returns the content unchanged if the classifier is already present.
    Raises ``ValueError`` if no existing ``Python :: 3.x`` classifier is
    found to anchor against (handler should mark item failed in that case).
    """
    target = f"'Programming Language :: Python :: {new_minor}'"
    if target in content:
        return content

    # Find every 'Programming Language :: Python :: 3.x' line; insert after the
    # last one, preserving its indentation and trailing comma.
    pattern = re.compile(
        r"^(?P<indent>[ \t]*)'Programming Language :: Python :: 3\.(?P<minor>\d+)',?\s*$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(content))
    if not matches:
        raise ValueError(
            "no 'Programming Language :: Python :: 3.x' classifier found in setup.py"
        )
    last = matches[-1]
    indent = last.group("indent")
    insertion = f"{indent}{target},\n"
    # Insert immediately after the end of the matched line (including its newline).
    end = last.end()
    # Advance past the newline if not already consumed by the regex.
    if end < len(content) and content[end] == "\n":
        end += 1
    return content[:end] + insertion + content[end:]


def add_python_to_tox_ini(content: str, new_minor: str) -> str:
    """Append ``pyXY`` to the ``envlist =`` line. Idempotent."""
    env = "py" + new_minor.replace(".", "")
    pattern = re.compile(r"^(?P<prefix>envlist\s*=\s*)(?P<envs>.+)$", re.MULTILINE)
    m = pattern.search(content)
    if not m:
        raise ValueError("no 'envlist =' line found in tox.ini")
    existing = [e.strip() for e in m.group("envs").split(",")]
    if env in existing:
        return content
    existing.append(env)
    new_line = m.group("prefix") + ",".join(existing)
    return content[: m.start()] + new_line + content[m.end():]


def add_python_to_azure_pipeline(content: str, new_minor: str) -> str:
    """Add a ``PythonXYZ`` matrix entry in ``azure-pipeline.yml``.

    Locates the last existing ``PythonNNN:`` block in the ``matrix:`` section,
    duplicates it, and rewrites version + tox_env. Idempotent.
    """
    key = "Python" + new_minor.replace(".", "")
    if re.search(rf"^\s*{re.escape(key)}\s*:", content, re.MULTILINE):
        return content

    # Find all matrix entry headers like '        Python313:'.
    entry_pattern = re.compile(
        r"^(?P<indent>[ \t]+)Python(?P<minor>\d{3,4}):\s*\n"
        r"(?P=indent)[ \t]+python\.version:\s*'(?P<ver>3\.\d+)'\s*\n"
        r"(?P=indent)[ \t]+tox_env:\s*'py(?P<env>\d+)'\s*\n",
        re.MULTILINE,
    )
    matches = list(entry_pattern.finditer(content))
    if not matches:
        raise ValueError("no PythonNNN matrix entry found in azure-pipeline.yml")
    last = matches[-1]
    indent = last.group("indent")
    minor_compact = new_minor.replace(".", "")
    block = (
        f"{indent}Python{minor_compact}:\n"
        f"{indent}  python.version: '{new_minor}'\n"
        f"{indent}  tox_env: 'py{minor_compact}'\n"
    )
    end = last.end()
    return content[:end] + block + content[end:]
