from __future__ import annotations

from pathlib import Path

import pytest

from python_upgrade_agent.validate import ValidationError, validate


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("version: 3.13\nother: x\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("3.13\n3.13\n", encoding="utf-8")  # ambiguous target
    return tmp_path


def test_validate_happy_path(repo: Path):
    output = {
        "edits": [
            {"path": "a.txt", "old_string": "version: 3.13",
             "new_string": "version: 3.14", "reason": "bump"},
        ],
        "skipped": [],
    }
    edits = validate(output, {"a.txt"}, repo)
    assert len(edits) == 1
    assert edits[0].path == "a.txt"


def test_validate_rejects_path_outside_candidates(repo: Path):
    output = {
        "edits": [
            {"path": "b.txt", "old_string": "3.13",
             "new_string": "3.14", "reason": "x"},
        ]
    }
    with pytest.raises(ValidationError, match="not in the candidate list"):
        validate(output, {"a.txt"}, repo)


def test_validate_rejects_missing_old_string(repo: Path):
    output = {
        "edits": [
            {"path": "a.txt", "old_string": "not in file",
             "new_string": "x", "reason": ""},
        ]
    }
    with pytest.raises(ValidationError, match="old_string not found"):
        validate(output, {"a.txt"}, repo)


def test_validate_rejects_ambiguous_old_string(repo: Path):
    output = {
        "edits": [
            {"path": "b.txt", "old_string": "3.13",
             "new_string": "3.14", "reason": ""},
        ]
    }
    with pytest.raises(ValidationError, match="matches 2 times"):
        validate(output, {"b.txt"}, repo)


def test_validate_rejects_identical_strings(repo: Path):
    output = {
        "edits": [
            {"path": "a.txt", "old_string": "version: 3.13",
             "new_string": "version: 3.13", "reason": ""},
        ]
    }
    with pytest.raises(ValidationError, match="identical"):
        validate(output, {"a.txt"}, repo)


def test_validate_rejects_empty_edits(repo: Path):
    with pytest.raises(ValidationError, match="empty"):
        validate({"edits": [], "skipped": []}, {"a.txt"}, repo)


def test_validate_rejects_oversize(repo: Path, monkeypatch):
    import python_upgrade_agent.validate as v
    monkeypatch.setattr(v, "MAX_CHANGED_LINES", 0)
    output = {
        "edits": [
            {"path": "a.txt", "old_string": "version: 3.13",
             "new_string": "version: 3.14", "reason": ""},
        ]
    }
    with pytest.raises(ValidationError, match="Too many lines"):
        validate(output, {"a.txt"}, repo)


def test_validate_silently_skips_true_duplicate(tmp_path: Path):
    (tmp_path / "a.txt").write_text("foo: 3.13\n", encoding="utf-8")
    output = {
        "edits": [
            {"path": "a.txt", "old_string": "foo: 3.13",
             "new_string": "foo: 3.14", "reason": "bump"},
            {"path": "a.txt", "old_string": "foo: 3.13",
             "new_string": "foo: 3.14", "reason": "bump"},
        ]
    }
    edits = validate(output, {"a.txt"}, tmp_path)
    assert len(edits) == 1


def test_validate_rejects_conflicting_duplicate(tmp_path: Path):
    (tmp_path / "a.txt").write_text("foo: 3.13\n", encoding="utf-8")
    output = {
        "edits": [
            {"path": "a.txt", "old_string": "foo: 3.13",
             "new_string": "foo: 3.14", "reason": ""},
            {"path": "a.txt", "old_string": "foo: 3.13",
             "new_string": "foo: 4.00", "reason": ""},
        ]
    }
    with pytest.raises(ValidationError, match="conflicting duplicate"):
        validate(output, {"a.txt"}, tmp_path)


def test_validate_rejects_malformed_additive_classifier(tmp_path: Path):
    (tmp_path / "setup.py").write_text(
        "classifiers = [\n    'Programming Language :: Python :: 3.13',\n]\n",
        encoding="utf-8",
    )
    output = {
        "edits": [
            {
                "path": "setup.py",
                "old_string": "    'Programming Language :: Python :: 3.13',",
                "new_string":
                    "    'Programming Language :: Python :: Python :: 3.13',\n"
                    "    'Programming Language :: Python :: 3.14',",
                "reason": "additive",
            }
        ]
    }
    with pytest.raises(ValidationError, match="additive classifier"):
        validate(output, {"setup.py"}, tmp_path)


def test_validate_accepts_well_formed_additive_classifier(tmp_path: Path):
    (tmp_path / "setup.py").write_text(
        "classifiers = [\n    'Programming Language :: Python :: 3.13',\n]\n",
        encoding="utf-8",
    )
    output = {
        "edits": [
            {
                "path": "setup.py",
                "old_string": "    'Programming Language :: Python :: 3.13',",
                "new_string":
                    "    'Programming Language :: Python :: 3.13',\n"
                    "    'Programming Language :: Python :: 3.14',",
                "reason": "additive",
            }
        ]
    }
    edits = validate(output, {"setup.py"}, tmp_path)
    assert len(edits) == 1
