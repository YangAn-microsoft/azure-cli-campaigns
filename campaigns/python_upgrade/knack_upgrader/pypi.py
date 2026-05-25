"""PyPI release detection for the knack project.

Checks whether the latest released version of a PyPI package declares
support for a given Python minor via its ``Programming Language :: Python ::
X.Y`` classifier.

stdlib only; uses ``urllib`` so the campaign repo stays dependency-free.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from urllib.request import Request, urlopen


PYPI_URL_TEMPLATE = "https://pypi.org/pypi/{project}/json"


@dataclass(frozen=True)
class PyPIStatus:
    """Result of a PyPI compatibility lookup.

    ``supports`` is True iff the latest non-yanked release of ``project``
    declares the ``Programming Language :: Python :: <py_minor>`` classifier.
    ``latest_version`` is the latest non-yanked version on PyPI regardless
    of compatibility (useful for diagnostic notes).
    """
    project: str
    py_minor: str
    supports: bool
    latest_version: str | None


# Injection point for tests. Production code calls ``_default_fetch`` which
# uses urllib; tests pass a fake fetcher.
Fetcher = Callable[[str], dict]


def _default_fetch(url: str) -> dict:
    req = Request(url, headers={"User-Agent": "azure-cli-campaigns/knack_upgrader"})
    with urlopen(req, timeout=30) as resp:  # noqa: S310 (https only)
        return json.loads(resp.read().decode("utf-8"))


def check_pypi(
    project: str,
    py_minor: str,
    *,
    fetcher: Fetcher | None = None,
) -> PyPIStatus:
    """Query PyPI for ``project`` and report whether the latest release
    supports Python ``py_minor`` (e.g. ``"3.14"``)."""
    fetch = fetcher or _default_fetch
    url = PYPI_URL_TEMPLATE.format(project=project)
    data = fetch(url)

    info = data.get("info") or {}
    latest = info.get("version")
    classifiers = info.get("classifiers") or []
    target = f"Programming Language :: Python :: {py_minor}"
    return PyPIStatus(
        project=project,
        py_minor=py_minor,
        supports=target in classifiers,
        latest_version=latest,
    )
