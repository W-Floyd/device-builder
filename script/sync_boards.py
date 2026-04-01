#!/usr/bin/env python3
"""Sync board definitions from upstream PlatformIO board JSON files.

Fetches board metadata from the PlatformIO platform repos (the same source
ESPHome uses) and creates or updates manifest.yaml files in our definitions.

Boards that already have a curated manifest are SKIPPED — this only
bootstraps new boards that we don't have yet.

Usage:
    python script/sync_boards.py [--dry-run]

Sources:
    ESP32:   pioarduino/platform-espressif32
    ESP8266: platformio/platform-espressif8266
    RP2040:  maxgerhardt/platform-raspberrypi
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

import yaml

DEFINITIONS_DIR = (
    Path(__file__).resolve().parent.parent / "esphome_device_builder" / "definitions" / "boards"
)

# Upstream PlatformIO platform repos — GitHub API for directory listings,
# raw URLs for individual board JSON files.
SOURCES = [
    {
        "platform": "esp32",
        "repo": "pioarduino/platform-espressif32",
        "branch": "develop",
        "path": "boards",
        "docs_url": "https://esphome.io/components/esp32.html",
    },
    {
        "platform": "esp8266",
        "repo": "platformio/platform-espressif8266",
        "branch": "master",
        "path": "boards",
        "docs_url": "https://esphome.io/components/esp8266.html",
    },
    {
        "platform": "rp2040",
        "repo": "maxgerhardt/platform-raspberrypi",
        "branch": "develop",
        "path": "boards",
        "docs_url": "https://esphome.io/components/rp2040.html",
    },
]


def _fetch_json(url: str) -> dict | list:
    """Fetch a JSON URL."""
    req = urllib.request.Request(url, headers={"User-Agent": "esphome-device-builder"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _list_board_files(source: dict) -> list[str]:
    """List all .json board files from a GitHub repo directory."""
    url = (
        f"https://api.github.com/repos/{source['repo']}"
        f"/contents/{source['path']}?ref={source['branch']}"
    )
    entries = _fetch_json(url)
    return [e["name"] for e in entries if isinstance(e, dict) and e["name"].endswith(".json")]


def _fetch_board_json(source: dict, filename: str) -> dict:
    """Fetch a single board JSON from the raw GitHub URL."""
    url = (
        f"https://raw.githubusercontent.com/{source['repo']}"
        f"/{source['branch']}/{source['path']}/{filename}"
    )
    return _fetch_json(url)


def _hz_to_mhz(hz_str: str) -> str:
    """Convert '240000000L' to '240MHz'."""
    try:
        hz = int(hz_str.rstrip("L"))
        return f"{hz // 1_000_000}MHz"
    except (ValueError, TypeError):
        return ""


def _bytes_to_mb(size_bytes: int) -> str:
    """Convert bytes to human-readable MB string."""
    mb = size_bytes / (1024 * 1024)
    if mb == int(mb):
        return f"{int(mb)}MB"
    return f"{mb:.1f}MB"


def _get_variant(board_data: dict, platform: str) -> str | None:
    """Extract the ESPHome variant from board data."""
    mcu = board_data.get("build", {}).get("mcu", "")
    if platform == "esp32" and mcu:
        return mcu.lower()
    return None


def _get_connectivity(board_data: dict) -> list[str]:
    """Extract connectivity list."""
    return board_data.get("connectivity", [])


_VARIANT_NAMES: dict[str, str] = {
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


def _build_description(
    name: str,
    vendor: str,
    variant: str | None,
    connectivity: list[str],
    flash_size: str,
    cpu_freq: str,
) -> str:
    """Build a human-readable description from board metadata."""
    chip = _VARIANT_NAMES.get(variant or "", variant or "").strip()

    parts: list[str] = []

    # Chip
    if chip:
        parts.append(f"{chip}-based")

    # Vendor attribution (skip if already in the name)
    if vendor and vendor.lower() not in name.lower():
        parts.append(f"board by {vendor}")
    else:
        parts.append("board")

    # Specs
    specs: list[str] = []
    if flash_size:
        specs.append(f"{flash_size} flash")
    if cpu_freq:
        specs.append(f"{cpu_freq}")
    if specs:
        parts.append(f"with {', '.join(specs)}")

    # Connectivity
    conn_names = {
        "wifi": "Wi-Fi",
        "bluetooth": "Bluetooth",
        "ethernet": "Ethernet",
        "can": "CAN bus",
        "zigbee": "Zigbee",
        "thread": "Thread",
    }
    conn_labels = [conn_names.get(c, c) for c in connectivity]
    if conn_labels:
        if specs:
            parts.append(f"featuring {', '.join(conn_labels)}")
        else:
            parts.append(f"with {', '.join(conn_labels)}")

    result = " ".join(parts)
    return result[0].upper() + result[1:] + "."


def _board_to_manifest(board_id: str, board_data: dict, source: dict) -> str:
    """Generate manifest.yaml content from a PlatformIO board JSON."""
    build = board_data.get("build", {})
    upload = board_data.get("upload", {})

    name = board_data.get("name", board_id)
    vendor = board_data.get("vendor", "")
    url = board_data.get("url", "")
    platform = source["platform"]
    variant = _get_variant(board_data, platform)
    connectivity = _get_connectivity(board_data)

    # Flash size: prefer the human-readable string, fall back to bytes
    flash_size = upload.get("flash_size", "")
    if not flash_size and upload.get("maximum_size"):
        flash_size = _bytes_to_mb(upload["maximum_size"])

    cpu_freq = _hz_to_mhz(build.get("f_cpu", ""))
    ram_size = upload.get("maximum_ram_size")

    description = _build_description(name, vendor, variant, connectivity, flash_size, cpu_freq)

    lines = [
        f"id: {board_id}",
        f'name: "{name}"',
        f'description: "{description}"',
        f'manufacturer: "{vendor}"',
        "",
        "esphome:",
        f"  platform: {platform}",
        f"  board: {board_id}",
    ]
    if variant:
        lines.append(f"  variant: {variant}")

    lines.append("")

    # Hardware
    lines.append("hardware:")
    if flash_size:
        lines.append(f'  flash_size: "{flash_size}"')
    if ram_size:
        lines.append(f"  ram_size: {ram_size}")
    if cpu_freq:
        lines.append(f'  cpu_frequency: "{cpu_freq}"')
    if connectivity:
        lines.append(f"  connectivity: [{', '.join(connectivity)}]")

    lines.append("")

    lines.append("tags: []")
    lines.append(f'docs_url: "{source["docs_url"]}"')
    if url:
        lines.append(f'product_url: "{url}"')

    lines.append("")
    lines.append("pins: []")
    lines.append("")

    return "\n".join(lines)


def _get_existing_board_ids() -> set[str]:
    """Get all board IDs already defined in our manifests."""
    existing = set()
    for manifest in DEFINITIONS_DIR.glob("*/manifest.yaml"):
        # Use the folder name as the canonical ID
        existing.add(manifest.parent.name)
        # Also read the esphome.board field to catch duplicates by PlatformIO ID
        try:
            data = yaml.safe_load(manifest.read_text())
            pio_board = data.get("esphome", {}).get("board", "")
            if pio_board:
                existing.add(pio_board)
        except Exception:
            pass
    return existing


def sync(dry_run: bool = False) -> None:
    """Run the board sync."""
    existing = _get_existing_board_ids()
    print(f"Found {len(existing)} existing board IDs/PlatformIO boards to skip")

    total_synced = 0
    total_skipped = 0

    for source in SOURCES:
        platform = source["platform"]
        print(f"\n--- {platform} ({source['repo']}) ---")

        try:
            files = _list_board_files(source)
        except Exception as exc:
            print(f"  ERROR listing boards: {exc}")
            continue

        print(f"  Found {len(files)} boards upstream")

        for filename in sorted(files):
            board_id = filename.removesuffix(".json")

            if board_id in existing:
                total_skipped += 1
                continue

            try:
                board_data = _fetch_board_json(source, filename)
            except Exception as exc:
                print(f"  ERROR fetching {filename}: {exc}")
                continue

            manifest_content = _board_to_manifest(board_id, board_data, source)
            board_dir = DEFINITIONS_DIR / board_id

            if dry_run:
                print(f"  [dry-run] Would create {board_dir.name}/manifest.yaml")
            else:
                board_dir.mkdir(exist_ok=True)
                (board_dir / "manifest.yaml").write_text(manifest_content)
                print(f"  Created {board_dir.name}/manifest.yaml")

            total_synced += 1
            existing.add(board_id)

    print(f"\nDone: {total_synced} boards synced, {total_skipped} skipped (already exist)")


# ---------------------------------------------------------------------------
# Named pin sync from ESPHome's BOARD_PINS
# ---------------------------------------------------------------------------

# Named pins that indicate an onboard component occupies the GPIO.
_OCCUPIED_PIN_NAMES: dict[str, str] = {
    "LED": "Built-in LED",
    "LED_BUILTIN": "Built-in LED",
    "BUILTIN_LED": "Built-in LED",
    "NEOPIXEL": "NeoPixel RGB LED",
    "RGB_LED": "RGB LED",
    "NEOPIXEL_POWER": "NeoPixel power",
    "BUTTON": "Built-in button",
    "BOOT": "BOOT button",
    "TFT_CS": "TFT display",
    "TFT_DC": "TFT display",
    "TFT_RST": "TFT display",
    "TFT_BACKLIGHT": "TFT display",
    "SD_CS": "SD card slot",
}

# Named pins that map to features (enriches pin labels)
_FEATURE_PIN_NAMES: dict[str, str] = {
    "SDA": "i2c_sda",
    "SCL": "i2c_scl",
    "MOSI": "spi_mosi",
    "MISO": "spi_miso",
    "SCK": "spi_clk",
    "SS": "spi_cs",
    "TX": "uart_tx",
    "RX": "uart_rx",
}


def _load_esphome_board_pins() -> dict[str, dict[str, dict[str, int]]]:
    """Load ESPHome's BASE_PINS and BOARD_PINS for all platforms.

    Returns {platform: {board_id: {pin_name: gpio_num}}} with base pins
    already merged.
    """
    result: dict[str, dict[str, dict[str, int]]] = {}

    try:
        from esphome.components.esp32.boards import (
            ESP32_BASE_PINS,
            ESP32_BOARD_PINS,
        )

        esp32_boards: dict[str, dict[str, int]] = {}
        for board_id, pins in ESP32_BOARD_PINS.items():
            if isinstance(pins, str):
                # Alias — resolve it
                pins = ESP32_BOARD_PINS.get(pins, {})
            if isinstance(pins, dict):
                esp32_boards[board_id] = {**ESP32_BASE_PINS, **pins}
        result["esp32"] = esp32_boards
    except ImportError:
        pass

    try:
        from esphome.components.esp8266.boards import (
            ESP8266_BASE_PINS,
            ESP8266_BOARD_PINS,
        )

        esp8266_boards: dict[str, dict[str, int]] = {}
        for board_id, pins in ESP8266_BOARD_PINS.items():
            if isinstance(pins, str):
                pins = ESP8266_BOARD_PINS.get(pins, {})
            if isinstance(pins, dict):
                esp8266_boards[board_id] = {**ESP8266_BASE_PINS, **pins}
        result["esp8266"] = esp8266_boards
    except ImportError:
        pass

    try:
        from esphome.components.rp2040.boards import (
            RP2040_BASE_PINS,
            RP2040_BOARD_PINS,
        )

        rp2040_boards: dict[str, dict[str, int]] = {}
        for board_id, pins in RP2040_BOARD_PINS.items():
            if isinstance(pins, str):
                pins = RP2040_BOARD_PINS.get(pins, {})
            if isinstance(pins, dict):
                rp2040_boards[board_id] = {**RP2040_BASE_PINS, **pins}
        result["rp2040"] = rp2040_boards
    except ImportError:
        pass

    return result


def sync_pins(dry_run: bool = False) -> None:
    """Enrich board manifests with named pin data from ESPHome.

    For each board that has `pins: []`, if ESPHome knows about named pins
    for that board (LED, BUTTON, SDA, SCL, etc.), update the manifest's
    pin entries with `occupied_by` or feature labels.

    Only updates boards that already have pin data (inherited from generic
    boards at runtime). This writes the ESPHome-specific pin info into
    the manifest so it's persisted.
    """
    all_board_pins = _load_esphome_board_pins()
    if not all_board_pins:
        print("ESPHome not installed or no board pin data found.")
        return

    total_pins = sum(len(boards) for boards in all_board_pins.values())
    print(f"Loaded named pin data for {total_pins} boards from ESPHome")

    updated = 0
    for manifest in sorted(DEFINITIONS_DIR.glob("*/manifest.yaml")):
        data = yaml.safe_load(manifest.read_text())
        board_id = manifest.parent.name
        pio_board = data.get("esphome", {}).get("board", "")
        platform = data.get("esphome", {}).get("platform", "")

        # Skip generic boards and boards with curated pins
        if data.get("is_generic"):
            continue

        # Find ESPHome named pins for this board
        platform_pins = all_board_pins.get(platform, {})
        named_pins = platform_pins.get(pio_board, {})
        if not named_pins:
            continue

        # Get existing pins from manifest
        existing_pins = data.get("pins", [])
        if not isinstance(existing_pins, list):
            continue

        # Build GPIO -> existing pin entry lookup
        pin_by_gpio: dict[int, dict] = {}
        for pin in existing_pins:
            if isinstance(pin, dict) and "gpio" in pin:
                pin_by_gpio[pin["gpio"]] = pin

        # Apply named pin info
        changes = 0
        for pin_name, gpio_num in named_pins.items():
            if not isinstance(gpio_num, int):
                continue

            pin_entry = pin_by_gpio.get(gpio_num)
            if pin_entry is None:
                continue  # GPIO not in our pin list

            # Add occupied_by for known onboard components
            if pin_name in _OCCUPIED_PIN_NAMES and not pin_entry.get("occupied_by"):
                pin_entry["occupied_by"] = _OCCUPIED_PIN_NAMES[pin_name]
                changes += 1

            # Add feature labels (only if not already present)
            if pin_name in _FEATURE_PIN_NAMES:
                feature = _FEATURE_PIN_NAMES[pin_name]
                features = pin_entry.get("features", [])
                if feature not in features:
                    features.append(feature)
                    pin_entry["features"] = features
                    changes += 1

        if changes == 0:
            continue

        if dry_run:
            print(f"  [dry-run] {board_id}: {changes} pin enrichments")
        else:
            # Rewrite the manifest with updated pins
            data["pins"] = existing_pins
            with open(manifest, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            print(f"  {board_id}: {changes} pin enrichments applied")

        updated += 1

    print(f"\nDone: {updated} boards updated with named pin data")


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("sync", help="Sync board definitions from PlatformIO repos (default)")
    sub.add_parser("pins", help="Enrich boards with named pin data from ESPHome")
    sub.add_parser("all", help="Run both sync and pins")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without writing files",
    )

    args = parser.parse_args()
    command = args.command or "sync"

    if command in ("sync", "all"):
        sync(dry_run=args.dry_run)
    if command in ("pins", "all"):
        sync_pins(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
