#!/usr/bin/env python3
"""One-time fix: replace marketing/nonsense descriptions with clean auto-generated ones.

Finds boards whose descriptions look like website intros rather than board
descriptions, and replaces them with a factual description built from the
board's hardware metadata.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

DEFINITIONS_DIR = (
    Path(__file__).resolve().parent.parent / "esphome_device_builder" / "definitions" / "boards"
)

# Strings that indicate the description is marketing copy, not a board description
BAD_SIGNALS = [
    "we ",
    "our ",
    "you ",
    "your ",
    "click",
    "shop now",
    "buy ",
    "welcome to",
    "cookie",
    "privacy",
    "newsletter",
    "subscribe",
    "what's",
    "give your",
    "folks love",
    "home is where",
    "like missy",
    "we've got",
    "one of our star",
    "can uncover your deepest",
    "put our",
    "dive into",
    "unleash",
    "unlock the full",
    "aww yeah",
    "seller of electronic",
    "followers on linkedin",
    "note: you might like",
    "amazon qualified",
    "unphone yourself",
    "welcome to the unexpected",
    "access official documentation",
    "build smarter iot fast",
]

VARIANT_NAMES = {
    "esp32": "ESP32",
    "esp32s2": "ESP32-S2",
    "esp32s3": "ESP32-S3",
    "esp32c2": "ESP32-C2",
    "esp32c3": "ESP32-C3",
    "esp32c5": "ESP32-C5",
    "esp32c6": "ESP32-C6",
    "esp32c61": "ESP32-C61",
    "esp32h2": "ESP32-H2",
    "esp32p4": "ESP32-P4",
    "esp8266": "ESP8266",
    "rp2040": "RP2040",
    "rp2350": "RP2350",
}

CONN_NAMES = {
    "wifi": "Wi-Fi",
    "bluetooth": "Bluetooth",
    "ethernet": "Ethernet",
    "can": "CAN bus",
    "zigbee": "Zigbee",
    "thread": "Thread",
    "openthread": "Thread",
}


def build_description(data: dict) -> str:
    """Build a factual description from board metadata."""
    esphome = data.get("esphome", {})
    hardware = data.get("hardware", {})
    name = data.get("name", "")
    manufacturer = data.get("manufacturer", "")

    variant = esphome.get("variant") or esphome.get("platform", "")
    chip = VARIANT_NAMES.get(variant, variant).strip()

    flash = hardware.get("flash_size", "")
    freq = hardware.get("cpu_frequency", "")
    connectivity = hardware.get("connectivity", [])

    parts = []

    # Chip
    if chip:
        parts.append(f"{chip}-based")

    # Vendor (skip if already in board name)
    if manufacturer and manufacturer.lower() not in name.lower():
        parts.append(f"board by {manufacturer}")
    else:
        parts.append("board")

    # Specs
    specs = []
    if flash:
        specs.append(f"{flash} flash")
    if freq:
        specs.append(freq)
    if specs:
        parts.append(f"with {', '.join(specs)}")

    # Connectivity
    conn_labels = [CONN_NAMES.get(c, c) for c in connectivity]
    if conn_labels:
        if specs:
            parts.append(f"featuring {', '.join(conn_labels)}")
        else:
            parts.append(f"with {', '.join(conn_labels)}")

    result = " ".join(parts)
    return result[0].upper() + result[1:] + "."


def is_bad_description(desc: str) -> bool:
    """Check if a description looks like marketing copy."""
    lower = desc.lower()
    return any(signal in lower for signal in BAD_SIGNALS)


def fix() -> None:
    """Fix bad descriptions."""
    fixed = 0
    for manifest in sorted(DEFINITIONS_DIR.glob("*/manifest.yaml")):
        data = yaml.safe_load(manifest.read_text())
        desc = data.get("description", "")

        if not is_bad_description(desc):
            continue

        board_id = manifest.parent.name
        new_desc = build_description(data)

        # Update via text replacement to preserve formatting
        text = manifest.read_text()
        # Match the description line(s) — could be single or multiline
        # Single line: description: "..."
        # Multiline: description: |  or description: >
        if re.search(r"^description:\s*[|>]", text, re.MULTILINE):
            # Multiline — find the block and replace
            lines = text.split("\n")
            new_lines = []
            skip_indent = False
            for line in lines:
                if line.startswith("description:"):
                    new_lines.append(f'description: "{new_desc}"')
                    skip_indent = True
                    continue
                if skip_indent:
                    if line and (line[0] == " " or line[0] == "\t"):
                        continue  # skip continuation lines
                    skip_indent = False
                new_lines.append(line)
            text = "\n".join(new_lines)
        else:
            # Single line
            old_line = re.search(r"^description:.*$", text, re.MULTILINE)
            if old_line:
                replacement = f'description: "{new_desc}"'
                text = text[: old_line.start()] + replacement + text[old_line.end() :]

        manifest.write_text(text)
        print(f"  {board_id:45s} -> {new_desc[:80]}")
        fixed += 1

    print(f"\nFixed {fixed} descriptions")


if __name__ == "__main__":
    fix()
