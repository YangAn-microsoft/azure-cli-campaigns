"""Tests for ``knack_upgrader.pypi`` using injected fake fetcher."""
from __future__ import annotations

import pytest

from campaigns.python_upgrade.knack_upgrader import pypi


def _fake_pypi(version: str, classifiers: list[str]):
    """Build a fake fetcher matching PyPI's JSON shape."""
    def fetch(_url: str) -> dict:
        return {"info": {"version": version, "classifiers": classifiers}}
    return fetch


def test_supports_when_classifier_present():
    fetcher = _fake_pypi(
        "0.14.0",
        [
            "Programming Language :: Python :: 3.13",
            "Programming Language :: Python :: 3.14",
        ],
    )
    result = pypi.check_pypi("knack", "3.14", fetcher=fetcher)
    assert result.supports is True
    assert result.latest_version == "0.14.0"
    assert result.project == "knack"
    assert result.py_minor == "3.14"


def test_not_supports_when_classifier_missing():
    fetcher = _fake_pypi(
        "0.13.0",
        ["Programming Language :: Python :: 3.13"],
    )
    result = pypi.check_pypi("knack", "3.14", fetcher=fetcher)
    assert result.supports is False
    assert result.latest_version == "0.13.0"


def test_handles_empty_classifiers():
    fetcher = _fake_pypi("0.0.1", [])
    result = pypi.check_pypi("knack", "3.14", fetcher=fetcher)
    assert result.supports is False
    assert result.latest_version == "0.0.1"


def test_handles_missing_info():
    def fetch(_url: str) -> dict:
        return {}
    result = pypi.check_pypi("knack", "3.14", fetcher=fetch)
    assert result.supports is False
    assert result.latest_version is None


def test_url_includes_project_name():
    captured = {}
    def fetch(url: str) -> dict:
        captured["url"] = url
        return {"info": {"version": "1.0", "classifiers": []}}
    pypi.check_pypi("some-project", "3.14", fetcher=fetch)
    assert captured["url"] == "https://pypi.org/pypi/some-project/json"
