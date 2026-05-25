"""Round-trip and resilience tests for the hidden state block."""
from __future__ import annotations

from campaigns import state as state_mod
from campaigns.base import ItemState


def test_roundtrip_preserves_fields():
    original = {
        "a": ItemState(status="completed", pr=42, notes="ok"),
        "b": ItemState(status="pending"),
    }
    block = state_mod.serialize(original)
    body = block + "\n\n## Plan\n- [x] a\n- [ ] b\n"
    parsed = state_mod.parse(body)
    assert parsed["a"].status == "completed"
    assert parsed["a"].pr == 42
    assert parsed["a"].notes == "ok"
    assert parsed["b"].status == "pending"
    assert parsed["b"].pr is None


def test_parse_empty_body_returns_empty_dict():
    assert state_mod.parse("") == {}
    assert state_mod.parse("just markdown, no block") == {}


def test_parse_malformed_json_returns_empty_dict():
    body = "<!-- campaign-state\n{not json\n-->"
    assert state_mod.parse(body) == {}


def test_strip_removes_block():
    block = state_mod.serialize({"a": ItemState(status="completed")})
    body = block + "\n\nhuman content"
    stripped = state_mod.strip(body)
    assert "campaign-state" not in stripped
    assert "human content" in stripped


def test_parse_ignores_non_dict_item_values():
    body = '<!-- campaign-state\n{"items": {"a": "garbage", "b": {"status": "completed"}}}\n-->'
    parsed = state_mod.parse(body)
    assert "a" not in parsed
    assert parsed["b"].status == "completed"


def test_phase_roundtrips():
    original = {
        "x": ItemState(status="completed", phase="created", pr=10),
        "y": ItemState(status="pending"),  # no phase -> omitted
    }
    parsed = state_mod.parse(state_mod.serialize(original))
    assert parsed["x"].phase == "created"
    assert parsed["x"].status == "completed"
    assert parsed["y"].phase == ""


def test_phase_field_omitted_when_empty():
    block = state_mod.serialize({"y": ItemState(status="pending")})
    assert '"phase"' not in block
