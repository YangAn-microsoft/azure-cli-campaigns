"""Golden-file tests for knack_upgrader.edits.

For each (file, edit_fn) pair we assert:
  - applying the edit to ``fixtures/before/<file>`` produces ``fixtures/after/<file>``
  - applying the edit again is a no-op (idempotent)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from campaigns.python_upgrade.knack_upgrader import edits

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "filename, edit_fn",
    [
        ("setup.py", edits.add_python_to_setup_py),
        ("tox.ini", edits.add_python_to_tox_ini),
        ("azure-pipeline.yml", edits.add_python_to_azure_pipeline),
    ],
)
def test_edit_produces_expected_after_state(filename, edit_fn):
    before = (FIXTURES / "before" / filename).read_text(encoding="utf-8")
    expected = (FIXTURES / "after" / filename).read_text(encoding="utf-8")
    actual = edit_fn(before, "3.14")
    assert actual == expected


@pytest.mark.parametrize(
    "filename, edit_fn",
    [
        ("setup.py", edits.add_python_to_setup_py),
        ("tox.ini", edits.add_python_to_tox_ini),
        ("azure-pipeline.yml", edits.add_python_to_azure_pipeline),
    ],
)
def test_edit_is_idempotent(filename, edit_fn):
    after = (FIXTURES / "after" / filename).read_text(encoding="utf-8")
    assert edit_fn(after, "3.14") == after


def test_setup_py_raises_when_no_anchor():
    with pytest.raises(ValueError):
        edits.add_python_to_setup_py("classifiers=[\n    'License :: OSI'\n]\n", "3.14")


def test_tox_ini_raises_when_no_envlist():
    with pytest.raises(ValueError):
        edits.add_python_to_tox_ini("[tox]\nminversion = 4\n", "3.14")


def test_azure_pipeline_raises_when_no_matrix():
    with pytest.raises(ValueError):
        edits.add_python_to_azure_pipeline("jobs: []\n", "3.14")
