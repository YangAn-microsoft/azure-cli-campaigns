"""Lightweight validator handlers.

These handlers don't *do* work in another repo -- they only *check* a
condition and flip the item to ``completed`` when the condition is met.
They're suitable for companion items where the actual upgrade work happens
in another repo (manually or via another team), and the campaign just
needs to know when it's done.

Two flavours:

* ``pypi_classifier_validator``: looks at PyPI metadata for a package and
  checks for the ``Programming Language :: Python :: X.Y`` classifier on
  the latest non-yanked release. Use for projects that ship to PyPI.

* ``repo_file_validator``: fetches a file from a GitHub repo (via the
  ``gh api`` JSON contents endpoint) and looks for a substring. Use for
  projects whose Python-support signal lives in a CI matrix file or
  similar.
"""
from __future__ import annotations

import base64
import json
import subprocess

from ..knack_upgrader import pypi
from ...base import HandlerContext, HandlerResult


def pypi_classifier_validator_handler(ctx: HandlerContext) -> HandlerResult:
    """Validate that ``package`` on PyPI declares Python ``new_minor``.

    Required params: ``package`` (PyPI project name), ``new_minor``
    (``"3.14"``). Falls back to ``ctx.item.id`` for ``package`` when not
    given, which is convenient when the item id matches the PyPI name.
    """
    package = ctx.params.get("package") or ctx.item.id
    new_minor = ctx.params.get("new_minor", "")
    if not new_minor:
        return HandlerResult(
            status="failed",
            notes="missing 'new_minor' param",
        )
    try:
        status = pypi.check_pypi(package, new_minor)
    except Exception as exc:  # noqa: BLE001 -- network or PyPI hiccup
        return HandlerResult(
            status="pending",
            notes=f"PyPI lookup for {package!r} failed: {exc}",
        )

    if status.supports:
        return HandlerResult(
            status="completed",
            notes=f"{package} {status.latest_version} on PyPI declares Python {new_minor}",
        )
    latest = status.latest_version or "?"
    return HandlerResult(
        status="pending",
        notes=f"{package} {latest} on PyPI does not yet declare Python {new_minor}",
    )


def _fetch_repo_file(repo: str, path: str, ref: str = "") -> str | None:
    """Return the decoded text of ``path`` in ``repo`` via ``gh api``.

    Returns ``None`` if the file does not exist or the call fails.
    ``ref`` is appended as a query-string parameter (using ``-f`` would
    promote the request to POST and 404).
    """
    endpoint = f"/repos/{repo}/contents/{path}"
    if ref:
        endpoint += f"?ref={ref}"
    try:
        result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True, text=True, check=True, encoding="utf-8",
        )
    except subprocess.CalledProcessError:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if data.get("encoding") != "base64":
        return data.get("content")
    raw = data.get("content") or ""
    try:
        return base64.b64decode(raw).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None


def repo_file_validator_handler(ctx: HandlerContext) -> HandlerResult:
    """Validate that a file in ``ctx.repo`` contains a needle.

    Params:
      * ``path``    -- relative path inside the repo (e.g. ``azure-pipelines.yml``)
      * ``needle``  -- substring required for the condition to hold.
                       ``{new_minor}`` is substituted from params.
      * ``new_minor`` -- target Python minor (used to format ``needle`` if
                         it contains ``{new_minor}``).
    """
    path = ctx.params.get("path", "")
    needle = ctx.params.get("needle", "")
    new_minor = ctx.params.get("new_minor", "")
    if not path or not needle:
        return HandlerResult(
            status="failed",
            notes="repo_file_validator requires 'path' and 'needle' params",
        )
    try:
        needle = needle.format(new_minor=new_minor)
    except (KeyError, IndexError):
        pass
    content = _fetch_repo_file(ctx.repo, path)
    if content is None:
        return HandlerResult(
            status="pending",
            notes=f"could not fetch {ctx.repo}:{path}",
        )
    if needle in content:
        return HandlerResult(
            status="completed",
            notes=f"{ctx.repo}:{path} contains {needle!r}",
        )
    return HandlerResult(
        status="pending",
        notes=f"{ctx.repo}:{path} does not yet contain {needle!r}",
    )
