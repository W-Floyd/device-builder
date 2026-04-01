#!/usr/bin/env python3
"""Prefill pin definitions for boards that have pins: [].

For each board without pins, copies the pin map from the generic board
matching its variant/platform. Then applies ESPHome's named pin data
(LED, BUTTON, SDA, SCL etc.) as occupied_by overrides.

Usage:
    python script/prefill_pins.py [--dry-run]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

DEFINITIONS_DIR = (
    Path(__file__).resolve().parent.parent / "esphome_device_builder" / "definitions" / "boards"
)

# Named pins that indicate an onboard component
OCCUPIED_PIN_NAMES: dict[str, str] = {
    "LED": "Built-in LED",
    "LED_BUILTIN": "Built-in LED",
    "BUILTIN_LED": "Built-in LED",
    "NEOPIXEL": "NeoPixel RGB LED",
    "NEOPIXEL_POWER": "NeoPixel power",
    "RGB_LED": "RGB LED",
    "BUTTON": "Built-in button",
    "BOOT": "BOOT button",
    "TFT_CS": "TFT display",
    "TFT_DC": "TFT display",
    "TFT_RST": "TFT display",
    "TFT_BACKLIGHT": "TFT display",
    "SD_CS": "SD card slot",
}


def _load_generic_pins() -> dict[str, list[dict]]:
    """Load pin maps from generic boards, keyed by variant or platform."""
    lookup: dict[str, list[dict]] = {}
    for manifest in DEFINITIONS_DIR.glob("generic-*/manifest.yaml"):
        data = yaml.safe_load(manifest.read_text())
        if not data.get("is_generic") or not data.get("pins"):
            continue
        esphome = data.get("esphome", {})
        key = esphome.get("variant") or esphome.get("platform")
        if key:
            lookup[key] = data["pins"]
    return lookup


def _load_esphome_named_pins() -> dict[str, dict[str, dict[str, int]]]:
    """Load ESPHome's named pin maps. Returns {platform: {board_id: {name: gpio}}}."""
    result: dict[str, dict[str, dict[str, int]]] = {}

    try:
        from esphome.components.esp32.boards import ESP32_BASE_PINS, ESP32_BOARD_PINS

        boards: dict[str, dict[str, int]] = {}
        for board_id, pins in ESP32_BOARD_PINS.items():
            if isinstance(pins, str):
                pins = ESP32_BOARD_PINS.get(pins, {})
            if isinstance(pins, dict):
                boards[board_id] = {**ESP32_BASE_PINS, **pins}
        result["esp32"] = boards
    except ImportError:
        pass

    try:
        from esphome.components.esp8266.boards import ESP8266_BASE_PINS, ESP8266_BOARD_PINS

        boards = {}
        for board_id, pins in ESP8266_BOARD_PINS.items():
            if isinstance(pins, str):
                pins = ESP8266_BOARD_PINS.get(pins, {})
            if isinstance(pins, dict):
                boards[board_id] = {**ESP8266_BASE_PINS, **pins}
        result["esp8266"] = boards
    except ImportError:
        pass

    try:
        from esphome.components.rp2040.boards import RP2040_BASE_PINS, RP2040_BOARD_PINS

        boards = {}
        for board_id, pins in RP2040_BOARD_PINS.items():
            if isinstance(pins, str):
                pins = RP2040_BOARD_PINS.get(pins, {})
            if isinstance(pins, dict):
                boards[board_id] = {**RP2040_BASE_PINS, **pins}
        result["rp2040"] = boards
    except ImportError:
        pass

    return result


def _apply_named_pins(pins: list[dict], named_pins: dict[str, int]) -> int:
    """Apply ESPHome named pin data to a pin list. Returns number of changes."""
    gpio_to_pin: dict[int, dict] = {p["gpio"]: p for p in pins}
    changes = 0

    for pin_name, gpio_num in named_pins.items():
        if not isinstance(gpio_num, int):
            continue
        pin = gpio_to_pin.get(gpio_num)
        if pin is None:
            continue

        # Mark occupied pins
        if pin_name in OCCUPIED_PIN_NAMES and not pin.get("occupied_by"):
            pin["occupied_by"] = OCCUPIED_PIN_NAMES[pin_name]
            changes += 1

    return changes


def _write_pins_to_manifest(manifest: Path, pins: list[dict]) -> None:
    """Replace pins: [] in a manifest with the full pin list."""
    text = manifest.read_text()

    # Build YAML pin block
    pin_lines = ["pins:"]
    for pin in sorted(pins, key=lambda p: p["gpio"]):
        pin_lines.append(f"  - gpio: {pin['gpio']}")
        if pin.get("label"):
            pin_lines.append(f'    label: "{pin["label"]}"')
        features = pin.get("features", [])
        if features:
            pin_lines.append(f"    features: [{', '.join(features)}]")
        if pin.get("available") is not None:
            val = str(pin["available"]).lower()
            pin_lines.append(f"    available: {val}")
        elif "available" in pin and pin["available"] is None:
            pin_lines.append("    available: null")
        if pin.get("occupied_by"):
            pin_lines.append(f'    occupied_by: "{pin["occupied_by"]}"')
        if pin.get("notes"):
            pin_lines.append(f'    notes: "{pin["notes"]}"')

    pin_block = "\n".join(pin_lines) + "\n"

    # Replace pins: [] with the full block
    text = text.replace("pins: []\n", pin_block)
    manifest.write_text(text)


def prefill(dry_run: bool = False) -> None:
    """Prefill pins for all boards that have pins: []."""
    generic_pins = _load_generic_pins()
    print(f"Loaded generic pin maps for: {', '.join(sorted(generic_pins.keys()))}")

    esphome_named = _load_esphome_named_pins()
    named_count = sum(len(b) for b in esphome_named.values())
    print(f"Loaded ESPHome named pins for {named_count} boards")

    updated = 0
    enriched = 0

    for manifest in sorted(DEFINITIONS_DIR.glob("*/manifest.yaml")):
        data = yaml.safe_load(manifest.read_text())

        # Skip generic boards and boards that already have pins
        if data.get("is_generic") or data.get("pins"):
            continue

        esphome = data.get("esphome", {})
        variant_key = esphome.get("variant") or esphome.get("platform")
        pio_board = esphome.get("board", "")
        platform = esphome.get("platform", "")

        # Find the matching generic pin map
        base_pins = generic_pins.get(variant_key)
        if not base_pins:
            continue

        # Deep copy so we don't mutate the generic data
        import copy

        pins = copy.deepcopy(base_pins)

        # Apply ESPHome named pin overrides
        named = esphome_named.get(platform, {}).get(pio_board, {})
        pin_changes = _apply_named_pins(pins, named) if named else 0

        board_id = manifest.parent.name
        if dry_run:
            suffix = f" (+{pin_changes} named pin overrides)" if pin_changes else ""
            print(f"  [dry-run] {board_id}: {len(pins)} pins from {variant_key}{suffix}")
        else:
            _write_pins_to_manifest(manifest, pins)
            suffix = f" (+{pin_changes} named)" if pin_changes else ""
            print(f"  {board_id}: {len(pins)} pins{suffix}")

        updated += 1
        if pin_changes:
            enriched += 1

    print(f"\nDone: {updated} boards prefilled, {enriched} with named pin overrides")


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    prefill(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
