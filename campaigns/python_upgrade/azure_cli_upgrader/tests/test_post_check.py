"""Tests for the deterministic post-check sweep."""
from __future__ import annotations

from pathlib import Path

import pytest

from campaigns.python_upgrade.azure_cli_upgrader.discover import Candidate
from campaigns.python_upgrade.azure_cli_upgrader.post_check import find_forgotten_hits
from campaigns.python_upgrade.azure_cli_upgrader.validate import ValidatedEdit


def _cand(path: str, lineno: int, text: str) -> Candidate:
    return Candidate(path=path, line_number=lineno, line_text=text,
                     context_before=[], context_after=[])


def test_no_forgotten_when_all_edits_applied(tmp_path: Path):
    f = tmp_path / "azure-pipelines.yml"
    f.write_text("versionSpec: '3.13'\n", encoding="utf-8")
    edits = [ValidatedEdit(path="azure-pipelines.yml",
                           old_string="versionSpec: '3.13'",
                           new_string="versionSpec: '3.14'",
                           reason="bump", replace_all=False)]
    warnings = find_forgotten_hits(
        repo_root=tmp_path, current_minor="3.13",
        candidates=[_cand("azure-pipelines.yml", 1, "versionSpec: '3.13'")],
        edits=edits, skipped=[],
    )
    assert warnings == []


def test_forgotten_hit_when_edit_missed(tmp_path: Path):
    f = tmp_path / "a.yml"
    f.write_text("foo: '3.13'\nbar: '3.13'\n", encoding="utf-8")
    # LLM edited only the first occurrence without replace_all.
    edits = [ValidatedEdit(path="a.yml",
                           old_string="foo: '3.13'",
                           new_string="foo: '3.14'",
                           reason="bump", replace_all=False)]
    warnings = find_forgotten_hits(
        repo_root=tmp_path, current_minor="3.13",
        candidates=[
            _cand("a.yml", 1, "foo: '3.13'"),
            _cand("a.yml", 2, "bar: '3.13'"),
        ],
        edits=edits, skipped=[],
    )
    assert warnings == ["a.yml:2: bar: '3.13'"]


def test_skipped_entry_suppresses_warning(tmp_path: Path):
    f = tmp_path / "test_extensions.sh"
    f.write_text("# not compatible with 3.13 https://github.com/foo/pull/1\n",
                 encoding="utf-8")
    skipped = [{
        "path": "test_extensions.sh",
        "location": "line 1",
        "snippet": "# not compatible with 3.13 https://github.com/foo/pull/1",
        "why": "historical",
    }]
    warnings = find_forgotten_hits(
        repo_root=tmp_path, current_minor="3.13",
        candidates=[_cand("test_extensions.sh", 1,
                          "# not compatible with 3.13 https://github.com/foo/pull/1")],
        edits=[], skipped=skipped,
    )
    assert warnings == []


def test_replace_all_handled(tmp_path: Path):
    f = tmp_path / "pipe.yml"
    f.write_text("a: 3.13\nb: 3.13\nc: 3.13\n", encoding="utf-8")
    edits = [ValidatedEdit(path="pipe.yml",
                           old_string="3.13", new_string="3.14",
                           reason="bump", replace_all=True)]
    warnings = find_forgotten_hits(
        repo_root=tmp_path, current_minor="3.13",
        candidates=[_cand("pipe.yml", i, f"{c}: 3.13")
                    for i, c in enumerate("abc", 1)],
        edits=edits, skipped=[],
    )
    assert warnings == []


def test_missing_file_does_not_crash(tmp_path: Path):
    warnings = find_forgotten_hits(
        repo_root=tmp_path, current_minor="3.13",
        candidates=[_cand("nope.yml", 1, "x: 3.13")],
        edits=[], skipped=[],
    )
    assert warnings == []


def test_additive_classifier_line_is_expected_leftover(tmp_path: Path):
    # setup.py classifier list is additive per rule 11: the current-minor
    # classifier stays alongside the new one, so post-check must not warn.
    f = tmp_path / "setup.py"
    f.write_text(
        "classifiers = [\n"
        "    'Programming Language :: Python :: 3.13',\n"
        "    'Programming Language :: Python :: 3.14',\n"
        "]\n",
        encoding="utf-8",
    )
    # No edit applied (the LLM produced an edit on a different snippet); the
    # 3.13 classifier line still exists post-edit and should be ignored.
    warnings = find_forgotten_hits(
        repo_root=tmp_path, current_minor="3.13",
        candidates=[_cand("setup.py", 2,
                          "    'Programming Language :: Python :: 3.13',")],
        edits=[], skipped=[],
    )
    assert warnings == []
