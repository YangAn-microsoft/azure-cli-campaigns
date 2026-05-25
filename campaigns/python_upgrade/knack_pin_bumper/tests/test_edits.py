"""Tests for knack_pin_bumper.edits."""
from __future__ import annotations

import pytest

from campaigns.python_upgrade.knack_pin_bumper import edits


REAL_SNIPPET = """\
DEPENDENCIES = [
    'humanfriendly~=10.0',
    'jmespath',
    'knack~=0.14.0',
    'msal==1.36.0',
    'packaging>=20.9',
]
"""


def test_find_pin_compatible_release():
    info = edits.find_pin(REAL_SNIPPET)
    assert info is not None
    assert info.operator == "~="
    assert info.version == "0.14.0"


def test_find_pin_exact_equality():
    info = edits.find_pin("    'knack==0.14.0',\n")
    assert info is not None
    assert info.operator == "=="


def test_find_pin_greater_or_equal():
    info = edits.find_pin("    'knack>=0.14.0',\n")
    assert info is not None
    assert info.operator == ">="


def test_find_pin_returns_none_when_missing():
    assert edits.find_pin("    'jmespath',\n") is None


def test_bump_pin_preserves_operator():
    out = edits.bump_pin(REAL_SNIPPET, "0.15.0")
    assert "'knack~=0.15.0'" in out
    assert "'knack~=0.14.0'" not in out
    # Surrounding lines untouched.
    assert "'msal==1.36.0'" in out
    assert "'jmespath'" in out


def test_bump_pin_idempotent():
    once = edits.bump_pin(REAL_SNIPPET, "0.15.0")
    twice = edits.bump_pin(once, "0.15.0")
    assert twice == once


def test_bump_pin_noop_when_already_at_target():
    out = edits.bump_pin(REAL_SNIPPET, "0.14.0")
    assert out == REAL_SNIPPET


def test_bump_pin_raises_when_no_pin():
    with pytest.raises(ValueError):
        edits.bump_pin("DEPENDENCIES = ['jmespath']\n", "0.15.0")
