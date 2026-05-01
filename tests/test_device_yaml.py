"""Unit tests for ``helpers/device_yaml.py``.

Focused on the parsers consumed by the devices controller, where
hand-rolled text scanning makes regression risk meaningful.
"""

from __future__ import annotations

from esphome_device_builder.helpers.device_yaml import parse_esphome_meta


def test_parse_meta_plain_values() -> None:
    """No substitutions block: literal values are returned as-is."""
    yaml_content = """
esphome:
  name: my-device
  friendly_name: My Device
  comment: A useful little box
"""
    assert parse_esphome_meta(yaml_content) == ("my-device", "My Device", "A useful little box")


def test_parse_meta_missing_keys_return_none() -> None:
    """Absent fields return ``None`` so callers can fall back to storage."""
    yaml_content = """
esphome:
  name: my-device
"""
    assert parse_esphome_meta(yaml_content) == ("my-device", None, None)


def test_parse_meta_resolves_dollar_substitution() -> None:
    """``$friendly_name`` resolves against the ``substitutions:`` block."""
    yaml_content = """
substitutions:
  friendly_name: "Living Room Lamp"
esphome:
  name: living-room-lamp
  friendly_name: $friendly_name
"""
    _, friendly_name, _ = parse_esphome_meta(yaml_content)
    assert friendly_name == "Living Room Lamp"


def test_parse_meta_resolves_brace_substitution() -> None:
    """``${friendly_name}`` brace syntax also resolves."""
    yaml_content = """
substitutions:
  friendly_name: Kitchen
esphome:
  name: kitchen
  friendly_name: ${friendly_name}
"""
    _, friendly_name, _ = parse_esphome_meta(yaml_content)
    assert friendly_name == "Kitchen"


def test_parse_meta_resolves_substitution_inside_string() -> None:
    """References that are part of a larger string are interpolated in place."""
    yaml_content = """
substitutions:
  room: Bedroom
esphome:
  friendly_name: "${room} Lamp"
"""
    _, friendly_name, _ = parse_esphome_meta(yaml_content)
    assert friendly_name == "Bedroom Lamp"


def test_parse_meta_substitutions_block_after_esphome() -> None:
    """Block order in the file does not matter (single pass + post-resolve)."""
    yaml_content = """
esphome:
  friendly_name: $friendly_name
substitutions:
  friendly_name: "Office"
"""
    _, friendly_name, _ = parse_esphome_meta(yaml_content)
    assert friendly_name == "Office"


def test_parse_meta_unknown_reference_left_untouched() -> None:
    """Unknown substitution names stay as the raw ``$token`` in the output."""
    yaml_content = """
substitutions:
  device_name: foo
esphome:
  friendly_name: $missing
"""
    _, friendly_name, _ = parse_esphome_meta(yaml_content)
    assert friendly_name == "$missing"
