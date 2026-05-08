"""Unit tests for ``_FIELD_PLATFORM_OVERRIDES`` in ``script/sync_components.py``.

The schema bundle doesn't reliably capture per-field platform
constraints — the upstream debug component's ``psram`` /
``fragmentation`` sub-sensors live behind ``CORE.is_esp32`` /
``CORE.is_esp8266`` branches at codegen time that don't surface
in the static schema dump. Issue #417 walked through the foot-gun:
the form happily lets a user fill in PSRAM stats on an ESP8266
board, then compile fails three minutes later with a
"doesn't apply on this platform" error.

The fix is a small explicit override list in the sync script
that stamps ``ConfigEntry.supported_platforms`` onto the affected
fields. The frontend's form renderer reads that and hides the
entry on incompatible boards.

Pin the override shape here so a future sync-script edit can't
quietly regress to "no platform gate" and reintroduce the
foot-gun.
"""

from __future__ import annotations

from script.sync_components import (  # type: ignore[import-not-found]
    _FIELD_PLATFORM_OVERRIDES,
    _apply_field_platform_overrides,
)


def test_psram_is_gated_to_esp32() -> None:
    """``sensor.debug.psram`` is ESP32-only.

    PSRAM stats only exist on ESP32 variants with PSRAM enabled —
    the upstream component refuses on ESP8266 / RP2040 / etc. via
    a ``CORE.is_esp32`` guard at codegen. The static schema
    doesn't capture this, so we override here.
    """
    assert _FIELD_PLATFORM_OVERRIDES[("sensor.debug", "psram")] == ["esp32"]


def test_fragmentation_is_gated_to_esp8266() -> None:
    """``sensor.debug.fragmentation`` is ESP8266-only.

    Heap fragmentation reporting is ESP8266-specific in upstream
    debug. The schema bundle doesn't carry the gate; we do.
    """
    assert _FIELD_PLATFORM_OVERRIDES[("sensor.debug", "fragmentation")] == ["esp8266"]


def test_apply_stamps_supported_platforms_on_top_level_field() -> None:
    """The walker stamps the override onto the matching entry."""
    entries = [
        {"key": "free", "config_entries": []},
        {"key": "psram", "config_entries": []},
        {"key": "loop_time", "config_entries": []},
    ]
    _apply_field_platform_overrides("sensor.debug", entries)
    by_key = {e["key"]: e for e in entries}
    # ``psram`` gets the override.
    assert by_key["psram"]["supported_platforms"] == ["esp32"]
    # Sibling fields without overrides stay untouched (no
    # ``supported_platforms`` key set, which the model defaults to
    # an empty list — meaning "all platforms").
    assert "supported_platforms" not in by_key["free"]
    assert "supported_platforms" not in by_key["loop_time"]


def test_apply_walks_nested_paths() -> None:
    """Nested entries are reachable via the ``(component, *path)`` key.

    Today's overrides only target top-level entries, but the
    walker supports nested paths so future entries can target
    deeper fields without restructuring the override map. Pin a
    synthetic nested case so a refactor that flattened the walk
    fails loudly.
    """
    # Synthetic component-side hook for the test only.
    _FIELD_PLATFORM_OVERRIDES[("test.synthetic", "outer", "inner")] = ["rp2040"]
    try:
        entries = [
            {
                "key": "outer",
                "config_entries": [
                    {"key": "inner", "config_entries": []},
                    {"key": "sibling", "config_entries": []},
                ],
            },
        ]
        _apply_field_platform_overrides("test.synthetic", entries)
        inner = entries[0]["config_entries"][0]
        sibling = entries[0]["config_entries"][1]
        assert inner["supported_platforms"] == ["rp2040"]
        assert "supported_platforms" not in sibling
    finally:
        del _FIELD_PLATFORM_OVERRIDES[("test.synthetic", "outer", "inner")]


def test_apply_is_a_no_op_on_unrelated_component() -> None:
    """Walking a component without overrides leaves entries unchanged."""
    entries = [
        {"key": "ssid", "config_entries": []},
        {"key": "password", "config_entries": []},
    ]
    before = [dict(e) for e in entries]
    _apply_field_platform_overrides("wifi", entries)
    assert entries == before


def test_apply_preserves_existing_entry_state() -> None:
    """Stamping ``supported_platforms`` doesn't disturb other fields."""
    entries = [
        {
            "key": "psram",
            "type": "nested",
            "label": "PSRAM",
            "config_entries": [{"key": "name"}],
        },
    ]
    _apply_field_platform_overrides("sensor.debug", entries)
    assert entries[0]["supported_platforms"] == ["esp32"]
    # Other keys preserved.
    assert entries[0]["type"] == "nested"
    assert entries[0]["label"] == "PSRAM"
    assert entries[0]["config_entries"] == [{"key": "name"}]
