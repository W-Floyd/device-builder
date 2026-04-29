#!/usr/bin/env python3
"""Sync component definitions from ESPHome's installed package.

Introspects ESPHome's component modules and CONFIG_SCHEMAs to generate
a structured component catalog at definitions/components.json.

Requires ESPHome to be installed in the active Python environment.

Usage:
    python script/sync_components.py [--dry-run]
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import re as re_module
from pathlib import Path
from typing import Any

import voluptuous as vol

# Set up ESPHome environment before imports
os.environ.setdefault("ESPHOME_STORAGE_DIR", "/tmp/esphome_sync")

from esphome import config_validation as cv
from esphome import const
from esphome.core import CORE
from esphome.loader import get_component, get_platform

# Initialize CORE with a dummy ESP32 target
CORE.data = {const.KEY_CORE: {const.KEY_TARGET_PLATFORM: const.PLATFORM_ESP32}}

logging.basicConfig(level=logging.WARNING)
_LOGGER = logging.getLogger(__name__)

OUTPUT_FILE = (
    Path(__file__).resolve().parent.parent
    / "esphome_device_builder"
    / "definitions"
    / "components.json"
)

# ---------------------------------------------------------------------------
# Docs metadata fetching
# ---------------------------------------------------------------------------

_DOCS_CLONE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "esphome-docs"


def _parse_mdx_frontmatter(mdx_content: str) -> dict[str, str]:
    """Extract title and description from MDX frontmatter."""
    match = re_module.match(r"^---\s*\n(.*?)\n---", mdx_content, re_module.DOTALL)
    if not match:
        return {}
    result = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


# Markdown / MDX line patterns we strip during body extraction.
_BODY_SKIP_LINE = re_module.compile(
    r"^\s*("
    r"import\s"  # ES module imports — `import { x } from`, `import x from`
    r"|export\s"  # ES module re-exports
    r"|<[A-Za-z]"  # MDX/HTML tags <Image .../>, <span ...>, <Figure ...>
    r"|#{1,6}\s"  # markdown headings
    r"|```"  # fenced code blocks
    r"|:::"  # directive blocks (warning, info, ...)
    r"|\$"  # dollar-style template lines
    r")"
)
_MD_LINK = re_module.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_INLINE_CODE = re_module.compile(r"`([^`]+)`")
_MD_BOLD_ITALIC = re_module.compile(r"(\*\*|__|\*|_)([^*_\n]+)\1")
_HTML_TAG = re_module.compile(r"<[^>]+>")


def _build_doc_meta(mdx_file: Path, category: str) -> dict[str, Any]:
    """
    Read a single MDX file and produce its docs-metadata entry.

    Combines frontmatter (title, description) with a body-prose
    fallback when the frontmatter description is empty or just
    boilerplate, and a ``field_descriptions`` map extracted from the
    ``## Configuration variables`` section.
    """
    content = mdx_file.read_text(errors="ignore")
    fm = _parse_mdx_frontmatter(content)
    image = _parse_first_image(content) or ""

    description = fm.get("description", "").strip()
    body_intro = _extract_body_intro(content)
    if not description or description.lower().startswith("instructions for "):
        description = body_intro or description

    return {
        "title": fm.get("title", ""),
        "description": description,
        "image_file": image,
        "category": category,
        "field_descriptions": _extract_field_descriptions(content),
    }


_MAX_INTRO_CHARS = 400


def _extract_body_intro(mdx_content: str) -> str:
    """
    Extract the first prose paragraph from an MDX file body.

    Skips imports, MDX components, headings, code blocks and directive
    blocks. Markdown links and emphasis are flattened. When the first
    paragraph ends mid-sentence (typical when followed by a bullet
    list) we concatenate subsequent prose paragraphs until a sentence
    terminator is found or the result exceeds ~400 chars.

    Returns an empty string when no prose paragraph is found.
    """
    body = re_module.sub(
        r"^---\s*\n.*?\n---\s*\n", "", mdx_content, count=1, flags=re_module.DOTALL
    )

    paragraphs: list[list[str]] = [[]]
    in_code = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if not stripped:
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        # Bullet list items break the paragraph but we don't include them
        # in the prose — they typically enumerate supported parts/devices.
        if stripped.startswith(("-", "*", "+")) and len(stripped) > 1 and stripped[1] == " ":
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        if _BODY_SKIP_LINE.match(line):
            continue
        paragraphs[-1].append(stripped)

    collected = ""
    for lines in paragraphs:
        if not lines:
            continue
        chunk = _flatten_markdown(" ".join(lines))
        if not chunk:
            continue
        if not collected:
            collected = chunk
        else:
            collected = f"{collected} {chunk}"
        if len(collected) >= _MAX_INTRO_CHARS or collected.rstrip()[-1:] in ".!?":
            break

    if len(collected) >= 30:
        return collected[:_MAX_INTRO_CHARS].rstrip()
    return ""


def _flatten_markdown(text: str) -> str:
    """Strip markdown links / emphasis / inline code / HTML to plain prose."""
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_INLINE_CODE.sub(r"\1", text)
    text = _MD_BOLD_ITALIC.sub(r"\2", text)
    text = _HTML_TAG.sub("", text)
    return re_module.sub(r"\s+", " ", text).strip()


# Top-level bullet introducing a config variable:
#   - **field_name** (Required, type): Description goes here
_CONFIG_VAR_LINE = re_module.compile(
    r"^- \*\*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\*\*[^:\n]*?:\s*(?P<desc>.*)$"
)


def _extract_field_descriptions(mdx_content: str) -> dict[str, str]:
    """
    Parse the ``## Configuration variables`` section into ``{key: description}``.

    The section uses a markdown bullet list where each top-level
    bullet starts with ``- **field_name** (...): description``. We
    capture the description text — including continuation lines from
    indented prose, but excluding nested sub-bullets (those describe
    sub-fields and would clutter the tooltip).
    """
    body = re_module.sub(
        r"^---\s*\n.*?\n---\s*\n", "", mdx_content, count=1, flags=re_module.DOTALL
    )

    # Two header styles in the wild:
    #   - "## Configuration variables" (most component pages)
    #   - "Configuration variables:" as plain prose (some base / platform pages)
    section_re = re_module.compile(
        r"^(?:##\s+Configuration variables\s*|Configuration variables:\s*)\n"
        r"(.*?)(?=^##\s|\Z)",
        re_module.MULTILINE | re_module.DOTALL,
    )
    match = section_re.search(body)
    if not match:
        return {}

    descriptions: dict[str, str] = {}
    current_key: str | None = None
    current_parts: list[str] = []
    section = match.group(1)

    def _commit() -> None:
        if current_key is None:
            return
        text = " ".join(p for p in current_parts if p)
        text = _flatten_markdown(text).rstrip(" .,:") + (
            "." if text and text[-1] not in ".!?" else ""
        )
        if text:
            descriptions[current_key] = text

    for raw_line in section.splitlines():
        line = raw_line.rstrip()
        # New top-level bullet → commit previous, start new
        m = _CONFIG_VAR_LINE.match(line)
        if m:
            _commit()
            current_key = m.group("name")
            initial = m.group("desc").strip()
            current_parts = [initial] if initial else []
            continue
        if current_key is None:
            continue
        stripped = line.strip()
        # Block-quotes / GitHub alerts (`> [!NOTE]`, `> ...`) end the
        # description for the current field — they're side notes, not
        # part of the field's own help text.
        if stripped.startswith(">"):
            _commit()
            current_key = None
            current_parts = []
            continue
        # Sub-bullets describe sub-fields and would clutter the tooltip.
        if stripped.startswith(("- ", "* ", "+ ")):
            continue
        if stripped:
            current_parts.append(stripped)

    _commit()
    return descriptions


def _attach_description(entry: dict, field_descriptions: dict[str, str]) -> None:
    """Set ``entry['description']`` from the docs map when the key is known."""
    desc = field_descriptions.get(entry["key"])
    if desc:
        entry["description"] = desc


def _merge_field_descriptions(
    docs: dict[str, dict[str, Any]],
    base_platform: str,
    overrides: dict[str, str],
) -> dict[str, str]:
    """
    Combine the base platform's field descriptions with platform-specific overrides.

    ``sensor/index.mdx`` documents the common entity fields (name, id,
    device_class, state_class, ...). Platform-specific docs typically
    only re-document the platform's own fields. Merging the two means
    a ``sensor.template`` form has tooltips for both ``name`` (from
    sensor base) and the template-specific fields (from template).
    Platform-specific entries win on conflict.
    """
    base_docs = docs.get(base_platform, {})
    base_descriptions: dict[str, str] = base_docs.get("field_descriptions") or {}
    merged = dict(base_descriptions)
    merged.update(overrides)
    return merged


def _parse_first_image(mdx_content: str) -> str | None:
    """Extract the first image filename from MDX content."""
    # Pattern 1: ES module import — import x from './images/foo.jpg';
    match = re_module.search(r"from\s+['\"]\.\/images\/([^'\"]+)['\"]", mdx_content)
    if match:
        return match.group(1)
    # Pattern 2: inline reference — images/foo.jpg (markdown or JSX)
    match = re_module.search(r"images/([a-zA-Z0-9_-]+\.\w+)", mdx_content)
    if match:
        return match.group(1)
    return None


def _ensure_docs_repo() -> Path | None:
    """Clone the esphome-docs repo locally (shallow, once). Returns components dir."""
    import subprocess

    components_dir = _DOCS_CLONE_DIR / "src" / "content" / "docs" / "components"
    if components_dir.exists():
        print("Updating esphome-docs repo...")
        subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=_DOCS_CLONE_DIR,
            capture_output=True,
            timeout=30,
            check=False,
        )
        return components_dir

    print("Cloning esphome-docs repo (first time)...")
    _DOCS_CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "git",
                "clone",
                "--depth=1",
                "--single-branch",
                "--branch=current",
                "https://github.com/esphome/esphome-docs.git",
                str(_DOCS_CLONE_DIR),
            ],
            capture_output=True,
            timeout=120,
            check=True,
        )
    except Exception as exc:
        print(f"  WARNING: Could not clone docs repo: {exc}")
        return None

    return components_dir


def fetch_docs_metadata() -> dict[str, dict[str, str]]:
    """Parse metadata from ESPHome docs (cloned locally).

    Returns {component_id: {title, description, image_file, category}}.
    """
    components_dir = _ensure_docs_repo()
    if not components_dir or not components_dir.exists():
        print("  WARNING: Docs repo not available — skipping enrichment")
        return {}

    print("Parsing component docs metadata...")
    metadata: dict[str, dict[str, str]] = {}

    # Top-level .mdx files (core components)
    for mdx_file in components_dir.glob("*.mdx"):
        metadata[mdx_file.stem] = _build_doc_meta(mdx_file, "")

    # Pass 1: every `<dir>/index.mdx` documents the directory's own
    # platform-component (sensor/index.mdx → "Sensor Component",
    # ota/index.mdx → "Over-the-Air Updates"). These claim the
    # unqualified key for their directory before any other per-category
    # file gets a chance to.
    for cat_dir in sorted(components_dir.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name == "images":
            continue
        index_mdx = cat_dir / "index.mdx"
        if not index_mdx.exists():
            continue
        doc_meta = _build_doc_meta(index_mdx, cat_dir.name)
        metadata[cat_dir.name] = doc_meta
        metadata[f"{cat_dir.name}.{cat_dir.name}"] = doc_meta

    # Pass 2: regular per-category .mdx files. Always store under the
    # qualified key `<domain>.<id>` (so multi-platform components like
    # ``template`` get distinct docs per domain). Also populate the
    # unqualified key `<id>` for the FIRST encountered entry — for
    # components that exist in only one domain the short key keeps
    # lookups simple. Top-level docs and `<dir>/index.mdx` always win.
    for cat_dir in sorted(components_dir.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name == "images":
            continue
        cat_name = cat_dir.name
        mdx_files = [m for m in cat_dir.glob("*.mdx") if m.stem != "index"]
        if mdx_files:
            print(f"  {cat_name}: {len(mdx_files)} docs")
        for mdx_file in mdx_files:
            stem = mdx_file.stem
            doc_meta = _build_doc_meta(mdx_file, cat_name)
            qualified_key = f"{cat_name}.{stem}"
            metadata[qualified_key] = doc_meta
            if stem not in metadata:
                metadata[stem] = doc_meta

    print(f"  Total: {len(metadata)} component docs found")

    # Also parse the index page for image mappings (most complete source)
    index_file = components_dir / "index.mdx"
    if index_file.exists():
        index_content = index_file.read_text(errors="ignore")
        # Match: ["Name", "/components/category/comp/", "image.ext", ...]
        img_entries = re_module.findall(
            r'\["([^"]+)",\s*"(/components/[^"]+)",\s*"([^"]+)"',
            index_content,
        )
        enriched = 0
        for entry_name, entry_path, entry_img in img_entries:
            # Extract component ID from path: /components/sensor/dht/ -> dht
            parts = entry_path.strip("/").split("/")
            if len(parts) < 2:
                continue
            comp_id = parts[-1] if parts[-1] else parts[-2]
            cat = parts[1] if len(parts) >= 3 else ""

            if comp_id not in metadata:
                metadata[comp_id] = {
                    "title": "",
                    "description": "",
                    "image_file": "",
                    "category": cat,
                }
            m = metadata[comp_id]
            if not m.get("image_file"):
                m["image_file"] = entry_img
                enriched += 1
            if not m.get("title") and entry_name:
                m["title"] = entry_name
            if not m.get("category") and cat:
                m["category"] = cat
        print(f"  Index page: {len(img_entries)} entries, {enriched} new images added")

    return metadata


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Platform component types — these become categories, not catalog entries
PLATFORM_TYPES: set[str] = set()

# Category overrides for non-platform components
CATEGORY_OVERRIDES: dict[str, str] = {
    "esphome": "core",
    "wifi": "core",
    "api": "core",
    "ota": "core",
    "logger": "core",
    "mqtt": "core",
    "web_server": "core",
    "captive_portal": "core",
    "safe_mode": "core",
    "time": "core",
    "network": "core",
    "i2c": "bus",
    "spi": "bus",
    "uart": "bus",
    "one_wire": "bus",
    "modbus": "bus",
    "canbus": "bus",
    "script": "automation",
    "interval": "automation",
    "globals": "automation",
}

# Schema keys that are pure plumbing — never user-facing.
SKIP_KEYS: set[str] = {
    "mqtt_id",
    "web_server",
    "setup_priority",
    "type_id",
    "device_id",  # MQTT internal
    "zigbee_id",  # Zigbee internal
}

# Schema keys that are automation triggers (skip)
AUTOMATION_KEY_PREFIXES = ("on_",)

# Base entity / framework fields that are valid but rarely tweaked.
# Surfaced in forms but collapsed under "Advanced" by default.
ADVANCED_BASE_KEYS: set[str] = {
    "internal",
    "disabled_by_default",
    "entity_category",
    "state_class",
    "accuracy_decimals",
    "force_update",
    "expire_after",
    "filters",
    "interlock",
    "interlock_wait_time",
    # MQTT entity options
    "qos",
    "retain",
    "discovery",
    "subscribe_qos",
    "state_topic",
    "command_topic",
    "availability",
    # Zigbee entity options
    "zigbee_sensor",
    "zigbee_switch",
    "zigbee_binary_sensor",
    "zigbee_button",
    "zigbee_cover",
    "zigbee_climate",
    "zigbee_fan",
    "zigbee_light",
    "zigbee_lock",
    "zigbee_number",
    "zigbee_select",
    "zigbee_text",
    "zigbee_text_sensor",
}

# Fields important enough to always show without an "Advanced" toggle
# even when they happen to be optional in the schema.
IMPORTANT_KEYS: set[str] = {
    "pin",
    "id",
    "name",
    "platform",
    "restore_mode",
    "device_class",
    "unit_of_measurement",
    "icon",
    "address",
    "i2c_id",
    "spi_id",
    "uart_id",
    "update_interval",
}

# Friendly names for well-known components
COMPONENT_NAMES: dict[str, str] = {
    "adc": "ADC Analog-to-Digital Converter",
    "ags10": "AGS10 VOC Gas Sensor",
    "bh1750": "BH1750 Ambient Light Sensor",
    "bme280_i2c": "BME280 I2C Temperature/Humidity/Pressure Sensor",
    "bme280_spi": "BME280 SPI Temperature/Humidity/Pressure Sensor",
    "bme680_i2c": "BME680 I2C Environmental Sensor",
    "bmp280_i2c": "BMP280 I2C Pressure/Temperature Sensor",
    "bmp280_spi": "BMP280 SPI Pressure/Temperature Sensor",
    "dallas_temp": "Dallas 1-Wire Temperature Sensor",
    "dht": "DHT Temperature & Humidity Sensor",
    "ds18b20": "DS18B20 1-Wire Temperature Sensor",
    "gpio": "GPIO Pin",
    "hdc1080": "HDC1080 Temperature & Humidity Sensor",
    "hlw8012": "HLW8012 Power Sensor",
    "htu21d": "HTU21D Temperature & Humidity Sensor",
    "hx711": "HX711 Load Cell Amplifier",
    "ina219": "INA219 Current/Power Sensor",
    "ina226": "INA226 Current/Power Sensor",
    "max6675": "MAX6675 Thermocouple Sensor",
    "mhz19": "MH-Z19 CO2 Sensor",
    "neopixelbus": "NeoPixel LED Strip",
    "pca9685": "PCA9685 PWM Driver",
    "pmsx003": "PMSX003 Particulate Matter Sensor",
    "rotary_encoder": "Rotary Encoder",
    "scd30": "SCD30 CO2 Sensor",
    "scd4x": "SCD4x CO2 Sensor",
    "sgp30": "SGP30 Air Quality Sensor",
    "sht3xd": "SHT3x-D Temperature & Humidity Sensor",
    "ssd1306_i2c": "SSD1306 I2C OLED Display",
    "ssd1306_spi": "SSD1306 SPI OLED Display",
    "template": "Template (Virtual)",
    "tsl2561": "TSL2561 Light Sensor",
    "ultrasonic": "Ultrasonic Distance Sensor",
    "veml7700": "VEML7700 Ambient Light Sensor",
    "vl53l0x": "VL53L0X Laser Distance Sensor",
}


# ---------------------------------------------------------------------------
# Schema parsing
# ---------------------------------------------------------------------------


# Leading boilerplate phrases stripped from descriptions. These add no
# value to a UI tooltip — the rest of the sentence is the actual content.
_DESC_LEAD_PHRASES = (
    "instructions for setting up the ",
    "instructions for setting up ",
    "instructions for using the ",
    "instructions for using ",
)

# Phrases that signal the rest of the text is not user-facing prose
# (config-variable lists, ref links, etc.). When found we cut here.
_DESC_STOP_PHRASES = (
    "configuration variables:",
    "configuration variables ",
    "see the configuration variables",
    "see :ref:",
)


def _clean_description(raw: str) -> str:
    """
    Trim ESPHome doc descriptions to the user-facing intro paragraph.

    Strips leading boilerplate ("Instructions for setting up the ...")
    and capitalises the new first word so the sentence still reads
    naturally. Any trailing config-variables list or sphinx :ref:
    directive is cut.
    """
    if not raw:
        return ""

    cleaned = raw.strip()
    lower = cleaned.lower()
    for phrase in _DESC_LEAD_PHRASES:
        if lower.startswith(phrase):
            cleaned = cleaned[len(phrase) :]
            cleaned = cleaned[:1].upper() + cleaned[1:] if cleaned else cleaned
            lower = cleaned.lower()
            break

    cut = len(cleaned)
    for phrase in _DESC_STOP_PHRASES:
        idx = lower.find(phrase)
        if idx != -1 and idx < cut:
            cut = idx
    cleaned = cleaned[:cut].strip().rstrip(".:- \n\t")
    if cleaned and cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def _key_name(key: Any) -> str:
    """Extract the string name from a voluptuous key."""
    if isinstance(key, str):
        return key
    if hasattr(key, "schema"):
        return str(key.schema)
    return str(key)


def _key_to_label(key: str) -> str:
    """Convert a config key to a human label."""
    return key.replace("_", " ").title()


def _is_required(key: Any) -> bool:
    """Check if a voluptuous key is Required."""
    return isinstance(key, vol.Required)


def _get_default(key: Any) -> Any:
    """
    Return the default value of a voluptuous key.

    Returns None when there is no default or accessing it raises. Some
    ESPHome defaults are descriptors that look up CORE.data, which
    isn't always populated during sync — KeyError is the common
    failure mode there.
    """
    try:
        default = getattr(key, "default", vol.UNDEFINED)
    except Exception:
        return None
    if default is vol.UNDEFINED:
        return None
    if callable(default):
        try:
            default = default()
        except Exception:
            return None
    if hasattr(default, "total_seconds"):
        return f"{int(default.total_seconds())}s"
    if hasattr(default, "total_milliseconds"):
        return f"{int(default.total_milliseconds)}ms"
    return default


# Lambda-only validators captured once at module load — re-resolved every
# call would be wasteful and `set` construction trips on unhashable items.
_LAMBDA_VALIDATORS: tuple[Any, ...] = tuple(
    v for v in (getattr(cv, n, None) for n in ("returning_lambda", "lambda_")) if v is not None
)

# Identity-mapping of cv-singletons to the type they represent. Tuples
# group validators that share a type. Order doesn't matter — the first
# match wins.
_CV_TYPE_BY_IDENTITY: tuple[tuple[tuple[Any, ...], dict[str, Any]], ...] = tuple(
    (tuple(filter(None, validators)), result)
    for validators, result in (
        ((getattr(cv, "boolean", None),), {"type": "boolean"}),
        ((getattr(cv, "string", None), getattr(cv, "string_strict", None)), {"type": "string"}),
        (
            (
                getattr(cv, "int_", None),
                getattr(cv, "positive_int", None),
                getattr(cv, "positive_not_null_int", None),
            ),
            {"type": "integer"},
        ),
        ((getattr(cv, "float_", None), getattr(cv, "positive_float", None)), {"type": "float"}),
        ((getattr(cv, "icon", None),), {"type": "icon"}),
        ((getattr(cv, "port", None),), {"type": "integer", "range_min": 1, "range_max": 65535}),
        ((getattr(cv, "mac_address", None),), {"type": "mac_address"}),
        ((getattr(cv, "hex_int", None),), {"type": "integer"}),
    )
)

# Validator-name → type fallback table. Used after identity checks fail —
# many ESPHome custom validators are recognisable by name alone.
_NAME_TYPE_MAP: tuple[tuple[tuple[str, ...], dict[str, Any]], ...] = (
    # Secure strings — passwords, encryption keys, OTA passwords
    (("validate_password", "password", "passcode", "encryption_key"), {"type": "secure_string"}),
    # Generic string-shaped validators
    (
        (
            "ssid",
            "domain_name",
            "hostname",
            "validate_area_config",
            "validate_includes",
            "include",
            "string_strict",
        ),
        {"type": "string"},
    ),
    # Byte-count / numeric helpers
    (("validate_bytes", "validate_buffer_size"), {"type": "integer"}),
    (("frequency",), {"type": "float"}),
    # Hardware references
    (("use_id", "declare_id"), {"type": "id"}),
    # Lambda variants
    (("returning_lambda", "lambda_"), {"type": "lambda"}),
    # Color helpers
    (("rgb_color", "color"), {"type": "color"}),
)


def _identify_validator(validator: Any) -> dict[str, Any]:
    """
    Map a voluptuous validator to a config-entry type description.

    The returned dict always carries a ``type`` key plus optional
    ``options``, ``range_min``, ``range_max`` and ``templatable``
    keys. Falls back to ``{"type": "unknown"}`` when no rule matches.
    """
    name = getattr(validator, "__name__", "") or ""
    qualname = getattr(validator, "__qualname__", "") or ""
    name_lower = name.lower()
    vmod = getattr(validator, "__module__", "") or ""

    # cv.templatable wraps another validator and accepts !lambda OR a literal.
    # The wrapped function is named `validator` but its qualname carries
    # `templatable.<locals>.validator` — that's the reliable signature.
    if "templatable" in qualname.lower() and getattr(validator, "__closure__", None):
        return _unwrap_templatable(validator)

    if any(validator is v for v in _LAMBDA_VALIDATORS):
        return {"type": "lambda"}

    for validators, result in _CV_TYPE_BY_IDENTITY:
        if any(validator is v for v in validators):
            return dict(result)

    # esphome.pins.<anything> validators all describe GPIO pins
    if vmod == "esphome.pins" or ("pin" in name_lower and "spin" not in name_lower):
        return {"type": "pin"}

    # Time periods (cv.time_period_*, cv.update_interval, ...)
    if "time_period" in name_lower or name == "update_interval":
        return {"type": "time_period"}

    # Name-based lookup table
    for names, result in _NAME_TYPE_MAP:
        if name_lower in names or name in names:
            return dict(result)

    # vol.Coerce(int|float)
    if isinstance(validator, vol.Coerce):
        if validator.type is int:
            return {"type": "integer"}
        if validator.type is float:
            return {"type": "float"}

    # vol.Range — bare range constraint, default to float
    if isinstance(validator, vol.Range):
        return {"type": "float", "range_min": validator.min, "range_max": validator.max}

    # vol.Any — union of validators; first identifiable wins
    if isinstance(validator, vol.Any):
        for inner in validator.validators:
            inner_result = _identify_validator(inner)
            if inner_result["type"] != "unknown":
                return inner_result

    # vol.All — chained validation; gather range constraints and
    # identify the primary type from inner validators (last to first).
    if isinstance(validator, vol.All):
        return _identify_vol_all(validator)

    # Closure-based detection: enum mappings, int/float ranges
    closure_result = _identify_from_closure(validator, name_lower)
    if closure_result is not None:
        return closure_result

    # Sub-schemas (anything carrying a dict .schema attribute that doesn't
    # look like a primitive) — caller decides whether to recurse into them.
    if hasattr(validator, "schema") and isinstance(validator.schema, dict):
        return {"type": "sub_schema", "schema": validator}

    # Last-resort name hints for fields whose validator name still
    # carries a type clue.
    if "mac_address" in name_lower or "bind_key" in name_lower:
        return {"type": "mac_address" if "mac" in name_lower else "string"}
    if "string" in name_lower:
        return {"type": "string"}
    if "hex" in name_lower:
        return {"type": "integer"}
    if "address" in name_lower:
        return {"type": "integer"}

    # Fallback: any function defined in esphome.config_validation that
    # we couldn't otherwise classify is overwhelmingly a string-shaped
    # validator (custom string formats — domain names, area names, ...).
    if vmod == "esphome.config_validation" and callable(validator):
        return {"type": "string"}

    return {"type": "unknown"}


def _unwrap_templatable(validator: Any) -> dict[str, Any]:
    """
    Identify a ``cv.templatable``-wrapped validator.

    Returns the identified inner type with ``templatable=True`` attached.
    Falls back to a templatable string if the inner validator can't be
    identified.
    """
    for cell in validator.__closure__:
        try:
            inner = cell.cell_contents
        except (ValueError, TypeError):
            continue
        if callable(inner) and inner is not validator:
            inner_result = _identify_validator(inner)
            inner_result["templatable"] = True
            return inner_result
    return {"type": "string", "templatable": True}


def _identify_vol_all(validator: vol.All) -> dict[str, Any]:
    """
    Identify the effective type of a ``vol.All`` chain.

    Range bounds from ``vol.Range`` are merged onto whichever inner
    validator wins type identification. When no validator matches but
    a Range is present, the range alone is returned.
    """
    range_info: dict[str, Any] = {}
    for inner in validator.validators:
        if isinstance(inner, vol.Range):
            range_info = {"range_min": inner.min, "range_max": inner.max}
        elif isinstance(inner, vol.Coerce):
            if inner.type is int:
                range_info.setdefault("type", "integer")
            elif inner.type is float:
                range_info.setdefault("type", "float")

    for inner in reversed(validator.validators):
        inner_result = _identify_validator(inner)
        if inner_result["type"] != "unknown":
            inner_result.update(
                {
                    k: v
                    for k, v in range_info.items()
                    if k not in inner_result or k.startswith("range")
                }
            )
            return inner_result

    if range_info.get("type"):
        return range_info
    return {"type": "unknown"}


def _identify_from_closure(validator: Any, name_lower: str) -> dict[str, Any] | None:
    """
    Identify a validator from its closure cells.

    Looks for an enum dict (becomes ``options``) or a numeric range
    (int_range / float_range). Returns None when the closure carries
    no recognisable signal.
    """
    closure = getattr(validator, "__closure__", None)
    if not closure:
        return None

    # Enum mapping {label: value} → drop-down with primitive value type
    for cell in closure:
        try:
            val = cell.cell_contents
        except (ValueError, TypeError):
            continue
        if isinstance(val, dict) and val and all(isinstance(k, str) for k in val):
            return {"type": "string", "options": list(val.keys())}

    # Numeric range
    if "int_range" in name_lower or "float_range" in name_lower:
        range_min = None
        range_max = None
        for cell in closure:
            try:
                val = cell.cell_contents
            except (ValueError, TypeError):
                continue
            if isinstance(val, (int, float)):
                if range_min is None:
                    range_min = val
                else:
                    range_max = val
        base_type = "integer" if "int" in name_lower else "float"
        return {"type": base_type, "range_min": range_min, "range_max": range_max}

    return None


def _is_sub_entity_schema(validator: Any) -> bool:
    """Check if a validator is a platform entity sub-schema (like sensor_schema)."""
    if not hasattr(validator, "schema") or not isinstance(validator.schema, dict):
        return False
    # Sub-entity schemas have platform base keys like name, device_class, state_class
    schema_keys = {_key_name(k) for k in validator.schema}
    entity_keys = {
        "name",
        "device_class",
        "state_class",
        "unit_of_measurement",
        "accuracy_decimals",
    }
    return len(schema_keys & entity_keys) >= 2


# Key-name fragments that imply the value is sensitive — when the validator
# resolves to a generic STRING we upgrade these to SECURE_STRING so the
# frontend masks them.
_SECRET_KEY_FRAGMENTS = ("password", "passcode", "secret", "token", "api_key", "apikey")

# Inherited base-entity fields whose presence on the device only
# matters when a specific transport / gateway component is also
# configured. Frontend hides these unless the named component is
# present in the device's YAML, so a switch on a Wi-Fi-only device
# doesn't show qos/retain/state_topic etc. (which are MQTT-only).
_FIELD_COMPONENT_DEPENDENCY: dict[str, str] = {
    # MQTT entity options
    "qos": "mqtt",
    "retain": "mqtt",
    "discovery": "mqtt",
    "subscribe_qos": "mqtt",
    "state_topic": "mqtt",
    "command_topic": "mqtt",
    "command_retain": "mqtt",
    "availability": "mqtt",
    # Zigbee entity options
    "zigbee_sensor": "zigbee",
    "zigbee_switch": "zigbee",
    "zigbee_binary_sensor": "zigbee",
    "zigbee_button": "zigbee",
    "zigbee_cover": "zigbee",
    "zigbee_climate": "zigbee",
    "zigbee_fan": "zigbee",
    "zigbee_light": "zigbee",
    "zigbee_lock": "zigbee",
    "zigbee_number": "zigbee",
    "zigbee_select": "zigbee",
    "zigbee_text": "zigbee",
    "zigbee_text_sensor": "zigbee",
}


def _is_generate_id(key: Any) -> bool:
    """
    Detect a ``cv.GenerateID`` (or ``cv.declare_id``) voluptuous key.

    Matches by class name to avoid importing the private symbol.
    """
    return type(key).__name__ in ("GenerateID", "DeclareID")


def _build_id_entry(key_name: str, key: Any) -> dict:
    """Build the config entry for an auto-generated component ID."""
    return {
        "key": key_name,
        "type": "id",
        "label": _key_to_label(key_name),
        "required": False,
        "default_value": None,
        "options": None,
        "range": None,
        "advanced": False,  # important: users may want to set this
        "translation_key": f"component.config.{key_name}",
    }


def _build_entry(key: Any, validator: Any) -> dict | None:
    """
    Build a single config-entry dict.

    Returns None when the validator is unrecognised or describes a
    nested schema — the caller decides how to handle those cases.
    """
    info = _identify_validator(validator)
    if info["type"] in ("unknown", "sub_schema"):
        return None

    key_name = _key_name(key)
    required = _is_required(key)
    default = _get_default(key)

    # Promote generic strings to secure_string for fields whose key names
    # imply credentials. Catches cases where the validator is e.g. a
    # deprecated `cv.invalid(...)` wrapper that doesn't carry "password"
    # in its function name but the YAML key clearly does.
    entry_type = info["type"]
    if entry_type == "string" and any(frag in key_name.lower() for frag in _SECRET_KEY_FRAGMENTS):
        entry_type = "secure_string"

    range_val: list[Any] | None = None
    if info.get("range_min") is not None or info.get("range_max") is not None:
        range_val = [info.get("range_min"), info.get("range_max")]

    advanced = _classify_advanced(key_name, required)

    entry: dict[str, Any] = {
        "key": key_name,
        "type": entry_type,
        "label": _key_to_label(key_name),
        "required": required,
        "default_value": default if not callable(default) else None,
        "options": info.get("options"),
        "range": range_val,
        "advanced": advanced,
        "translation_key": f"component.config.{key_name}",
    }

    if info.get("templatable"):
        entry["templatable"] = True

    component_dependency = _FIELD_COMPONENT_DEPENDENCY.get(key_name)
    if component_dependency:
        entry["depends_on_component"] = component_dependency

    return entry


def _classify_advanced(key_name: str, required: bool) -> bool:
    """
    Decide whether a config entry should be hidden under "Advanced".

    Required fields and those in IMPORTANT_KEYS always render at the
    top level. ADVANCED_BASE_KEYS always render as advanced. Anything
    else falls back to "advanced when optional".
    """
    if required:
        return False
    if key_name in IMPORTANT_KEYS:
        return False
    if key_name in ADVANCED_BASE_KEYS:
        return True
    return True


def _unwrap_schema(schema: Any) -> dict | None:
    """
    Find the dict schema buried inside vol.All / vol.Schema wrappers.

    ESPHome wraps many CONFIG_SCHEMAs in ``cv.All(...)`` for chained
    validation (version checks, post-processing). The actual
    key-validator mapping lives inside one of the wrapped validators.
    """
    if isinstance(schema, dict):
        return schema
    inner = getattr(schema, "schema", None)
    if isinstance(inner, dict):
        return inner
    if isinstance(schema, vol.All):
        for v in schema.validators:
            unwrapped = _unwrap_schema(v)
            if unwrapped is not None:
                return unwrapped
    return None


def _unwrap_typed_schema(validator: Any) -> dict[str, Any] | None:
    """
    Find a ``cv.typed_schema`` dict inside *validator*.

    Components like ``output.template``, ``datetime.template`` and many
    BLE-client platforms use ``cv.typed_schema({type_value: sub_schema,
    ...})`` to discriminate on a ``type:`` field. The wrapper function
    has ``typed_schema`` in its ``__qualname__`` and stores the dict in
    its closure. Returns the discriminator dict or None when not found.
    """
    candidates: list[Any] = []
    if isinstance(validator, vol.All):
        candidates.extend(validator.validators)
    else:
        candidates.append(validator)

    for candidate in candidates:
        qualname = getattr(candidate, "__qualname__", "") or ""
        if "typed_schema" not in qualname:
            continue
        closure = getattr(candidate, "__closure__", None)
        if not closure:
            continue
        for cell in closure:
            try:
                value = cell.cell_contents
            except (ValueError, TypeError):
                continue
            if (
                isinstance(value, dict)
                and value
                and all(_unwrap_schema(v) is not None for v in value.values())
            ):
                return value
    return None


def _parse_schema(
    schema: Any,
    component_id: str,
    field_descriptions: dict[str, str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Parse a CONFIG_SCHEMA into config entries and sub-entries.

    *field_descriptions* maps key-names to per-field help text pulled
    from the component docs. When given, it populates the
    ``description`` of each entry so the frontend can render an info
    tooltip per field.
    """
    field_descriptions = field_descriptions or {}

    typed_dict = _unwrap_typed_schema(schema)
    if typed_dict is not None:
        return _parse_typed_schema(typed_dict, component_id, field_descriptions), []

    entries: list[dict] = []
    sub_entries: list[dict] = []

    schema_dict = _unwrap_schema(schema)
    if schema_dict is None:
        return entries, sub_entries

    for key, validator in schema_dict.items():
        key_name = _key_name(key)

        if key_name in SKIP_KEYS:
            continue
        if any(key_name.startswith(p) for p in AUTOMATION_KEY_PREFIXES):
            continue

        # Sub-entry (e.g. DHT's temperature/humidity readings)
        if _is_sub_entity_schema(validator):
            sub_entries.append(_build_sub_entry(key_name, validator, field_descriptions))
            continue

        if _is_generate_id(key):
            entry = _build_id_entry(key_name, key)
            _attach_description(entry, field_descriptions)
            entries.append(entry)
            continue

        entry = _build_entry(key, validator)
        if entry is not None:
            _attach_description(entry, field_descriptions)
            entries.append(entry)

    return entries, sub_entries


def _parse_typed_schema(
    typed_dict: dict[str, Any],
    component_id: str,
    field_descriptions: dict[str, str],
) -> list[dict]:
    """
    Expand a ``cv.typed_schema`` into a flat list with a ``type`` discriminator.

    The first entry is a SELECT-style ``type`` field with one option
    per type-key in the typed_schema. Fields shared across every
    type-key are emitted unconditionally; type-specific fields are
    gated with ``depends_on=type, depends_on_value=<key>``.
    """
    type_options = sorted(typed_dict.keys())
    entries: list[dict] = [
        {
            "key": "type",
            "type": "string",
            "label": "Type",
            "required": True,
            "default_value": None,
            "options": type_options,
            "range": None,
            "advanced": False,
            "translation_key": "component.config.type",
            "description": field_descriptions.get("type"),
        }
    ]

    # Walk each sub-schema once, capturing key→entry per type
    per_type: dict[str, dict[str, dict]] = {}
    for type_value, sub_schema in typed_dict.items():
        sub_dict = _unwrap_schema(sub_schema)
        if sub_dict is None:
            per_type[type_value] = {}
            continue
        per_type[type_value] = _parse_subdict_to_map(sub_dict)

    # A field is "common" when present in every type with the same
    # serialised representation — emit those unconditionally.
    common: dict[str, dict] = {}
    if per_type:
        all_keys = (
            set.intersection(*(set(m.keys()) for m in per_type.values())) if per_type else set()
        )
        for key_name in all_keys:
            sample = next(iter(per_type.values()))[key_name]
            if all(per_type[t].get(key_name) == sample for t in per_type):
                common[key_name] = sample
    for entry in common.values():
        e = dict(entry)
        _attach_description(e, field_descriptions)
        entries.append(e)

    # Type-specific fields gated by depends_on
    for type_value, by_key in per_type.items():
        for key_name, entry in by_key.items():
            if key_name in common:
                continue
            e = dict(entry)
            e["depends_on"] = "type"
            e["depends_on_value"] = type_value
            _attach_description(e, field_descriptions)
            entries.append(e)

    return entries


def _parse_subdict_to_map(schema_dict: dict) -> dict[str, dict]:
    """
    Build a ``{key_name: entry_dict}`` map from a raw schema dict.

    Used by typed-schema expansion to compare per-type field shapes.
    Skipped keys / automation prefixes / unrecognised validators are
    omitted so the comparison only sees real, surfaced fields.
    """
    result: dict[str, dict] = {}
    for key, validator in schema_dict.items():
        key_name = _key_name(key)
        if key_name in SKIP_KEYS:
            continue
        if any(key_name.startswith(p) for p in AUTOMATION_KEY_PREFIXES):
            continue
        if _is_generate_id(key):
            result[key_name] = _build_id_entry(key_name, key)
            continue
        entry = _build_entry(key, validator)
        if entry is not None:
            result[key_name] = entry
    return result


def _build_sub_entry(
    key_name: str,
    validator: Any,
    field_descriptions: dict[str, str] | None = None,
) -> dict:
    """Build a sub-entry dict from a sub-schema validator."""
    field_descriptions = field_descriptions or {}
    schema_keys = {_key_name(k) for k in validator.schema}
    platform_type = "sensor"
    if "brightness" in schema_keys or "color_mode" in schema_keys:
        platform_type = "light"
    elif "device_class" in schema_keys:
        vmod = getattr(validator, "__module__", "")
        for pt in ("sensor", "binary_sensor", "text_sensor", "number", "switch"):
            if pt in vmod:
                platform_type = pt
                break

    inner_entries: list[dict] = []
    for sk, sv in validator.schema.items():
        sk_name = _key_name(sk)
        if sk_name in SKIP_KEYS:
            continue
        if any(sk_name.startswith(p) for p in AUTOMATION_KEY_PREFIXES):
            continue
        if _is_generate_id(sk):
            entry = _build_id_entry(sk_name, sk)
            _attach_description(entry, field_descriptions)
            inner_entries.append(entry)
            continue
        entry = _build_entry(sk, sv)
        if entry is not None:
            _attach_description(entry, field_descriptions)
            inner_entries.append(entry)

    return {
        "key": key_name,
        "platform_type": platform_type,
        "config_entries": inner_entries,
    }


# ---------------------------------------------------------------------------
# Component discovery
# ---------------------------------------------------------------------------


def _discover_platform_types() -> set[str]:
    """Find all platform component types in ESPHome."""
    components_dir = Path(const.__file__).parent / "components"
    platform_types = set()
    for comp_dir in components_dir.iterdir():
        if not comp_dir.is_dir() or comp_dir.name.startswith("_"):
            continue
        try:
            manifest = get_component(comp_dir.name)
            if manifest and manifest.is_platform_component:
                platform_types.add(comp_dir.name)
        except Exception:
            pass
    return platform_types


def _get_component_platforms(component_id: str, platform_types: set[str]) -> list[str]:
    """Find which platform types a component provides."""
    platforms = []
    for pt in platform_types:
        try:
            manifest = get_platform(pt, component_id)
            if manifest and manifest.config_schema:
                platforms.append(pt)
        except Exception:
            pass
    return platforms


def _determine_category(component_id: str, platforms: list[str]) -> str:
    """Determine the category for a component."""
    if component_id in CATEGORY_OVERRIDES:
        return CATEGORY_OVERRIDES[component_id]
    if platforms:
        # Prefer sensor > binary_sensor > others
        for preferred in ("sensor", "binary_sensor", "switch", "light", "fan", "cover"):
            if preferred in platforms:
                return preferred
        return platforms[0]
    return "misc"


def _generate_name(component_id: str, category: str, docs_meta: dict | None = None) -> str:
    """Generate a human-readable name for a component."""
    # Prefer docs title
    if docs_meta and docs_meta.get("title"):
        return docs_meta["title"]
    if component_id in COMPONENT_NAMES:
        return COMPONENT_NAMES[component_id]
    name = component_id.replace("_", " ").replace("-", " ").title()
    return name


# Platform components whose sub-platforms should be folded into a single
# multi-conf entry with a `platform` discriminator. The user-facing model
# in YAML for these is `<id>: [- platform: X, ...]` — they belong together
# in the catalog rather than being scattered across their providers.
_UNIFIED_PLATFORM_COMPONENTS: tuple[str, ...] = ("ota", "time", "audio_dac", "audio_adc")


def _build_unified_platform_component(
    platform_id: str,
    component_dirs: list[str],
    docs_meta: dict[str, dict[str, str]] | None,
) -> dict | None:
    """
    Build a unified catalog entry for a platform component.

    Discovers all components that register a sub-platform under
    ``platform_id``, gathers each sub-platform's CONFIG_SCHEMA, and
    folds them into a single entry with:

      - a ``platform`` SELECT field listing every available sub-platform
      - the parent's ``BASE_<ID>_SCHEMA`` fields (common to all platforms)
      - per-platform fields gated by ``depends_on=platform`` so the form
        only shows fields relevant to the chosen platform
    """
    providers: list[tuple[str, Any]] = []
    for cid in component_dirs:
        try:
            pm = get_platform(platform_id, cid)
        except Exception:  # noqa: S112 — many components don't provide this platform
            continue
        if pm and pm.config_schema:
            providers.append((cid, pm.config_schema))

    if not providers:
        return None

    # Common base schema (BASE_OTA_SCHEMA, BASE_TIME_SCHEMA, ...)
    common_entries: list[dict] = []
    seen_keys: set[str] = set()
    try:
        parent_module = importlib.import_module(f"esphome.components.{platform_id}")
    except Exception:
        parent_module = None
    if parent_module is not None:
        base_schema = getattr(parent_module, f"BASE_{platform_id.upper()}_SCHEMA", None)
        if base_schema is not None:
            base_dict = _unwrap_schema(base_schema)
            if base_dict:
                for key, validator in base_dict.items():
                    key_name = _key_name(key)
                    if key_name in SKIP_KEYS or any(
                        key_name.startswith(p) for p in AUTOMATION_KEY_PREFIXES
                    ):
                        continue
                    if _is_generate_id(key):
                        common_entries.append(_build_id_entry(key_name, key))
                        seen_keys.add(key_name)
                        continue
                    entry = _build_entry(key, validator)
                    if entry is not None:
                        common_entries.append(entry)
                        seen_keys.add(key_name)

    platform_options = sorted(p for p, _ in providers)
    config_entries: list[dict] = [
        {
            "key": "platform",
            "type": "string",
            "label": "Platform",
            "required": True,
            "default_value": None,
            "options": platform_options,
            "range": None,
            "advanced": False,
            "translation_key": "component.config.platform",
        }
    ]
    config_entries.extend(common_entries)

    # Per-platform fields, gated by the discriminator
    for platform_name, schema in providers:
        platform_dict = _unwrap_schema(schema)
        if not platform_dict:
            continue
        for key, validator in platform_dict.items():
            key_name = _key_name(key)
            if key_name in SKIP_KEYS or any(
                key_name.startswith(p) for p in AUTOMATION_KEY_PREFIXES
            ):
                continue
            if key_name in seen_keys:
                continue  # already covered by base schema or platform itself
            if _is_generate_id(key):
                id_entry = _build_id_entry(key_name, key)
                id_entry["depends_on"] = "platform"
                id_entry["depends_on_value"] = platform_name
                config_entries.append(id_entry)
                continue
            entry = _build_entry(key, validator)
            if entry is None:
                continue
            entry["depends_on"] = "platform"
            entry["depends_on_value"] = platform_name
            config_entries.append(entry)

    docs = (docs_meta or {}).get(platform_id, {})
    name = docs.get("title") or _generate_name(platform_id, "core", docs)
    description = _clean_description(docs.get("description", ""))

    return {
        "id": platform_id,
        "name": name,
        "description": description,
        "category": "core",
        "docs_url": f"https://esphome.io/components/{platform_id}",
        "image_url": "",
        "dependencies": [],
        "multi_conf": True,
        "supported_platforms": [],
        "config_entries": config_entries,
        "sub_entries": [],
    }


_TARGET_PLATFORMS = frozenset(
    {"esp32", "esp8266", "rp2040", "bk72xx", "rtl87xx", "ln882x", "nrf52", "host"}
)


def _sync_component(
    component_id: str,
    platform_types: set[str],
    docs_meta: dict[str, dict[str, str]] | None = None,
) -> list[dict]:
    """
    Sync a single component directory.

    Returns a list of catalog entries — most components produce a
    single entry, but components that provide multiple platforms (like
    ``template``, ``gpio``, ``ble_client``) yield one entry per
    platform with a domain-qualified id (``<domain>.<component_id>``).
    Hub-style components that ALSO have their own top-level schema
    (``ble_client``, ``daly_bms``, ...) produce both the parent entry
    and the per-platform entries.
    """
    try:
        manifest = get_component(component_id)
    except Exception as exc:
        _LOGGER.warning("Failed to load component %s: %s", component_id, exc)
        return []

    if manifest is None or manifest.is_platform_component:
        # Platform-component aggregators (sensor, binary_sensor, ...) are
        # surfaced as unified entries elsewhere; target platforms
        # (esp32, esp8266, ...) ARE included because users configure
        # them directly and they pass the is_platform_component check.
        return []

    platforms = _get_component_platforms(component_id, platform_types)
    docs = docs_meta or {}
    dependencies = list(manifest.dependencies) if manifest.dependencies else []
    supported = [d for d in dependencies if str(d) in _TARGET_PLATFORMS]
    if manifest.is_target_platform:
        supported = [component_id]

    entries: list[dict] = []

    # Generate the parent / hub entry under the unqualified id when
    # there's something meaningful to put in it:
    #   - has own schema (any number of platforms — includes hubs like
    #     ble_client, daly_bms, and core components like wifi)
    #   - exactly one platform and no own schema (single-platform
    #     providers like dht — keeps the short id `dht`)
    # Components with 2+ platforms and no own schema (template, gpio,
    # copy, ...) skip the parent and produce only per-platform entries.
    if manifest.config_schema is not None or len(platforms) < 2:
        entries.append(
            _build_component_entry(
                component_id=component_id,
                manifest=manifest,
                platforms=platforms,
                schema=manifest.config_schema,
                docs=docs,
                dependencies=dependencies,
                supported_platforms=supported,
            )
        )

    # Per-platform entries when the component provides multiple
    # platforms (template/gpio/copy/ble_client/...). Single-platform
    # components without their own schema already got their entry
    # above with the platform schema.
    if len(platforms) >= 2:
        for platform_name in platforms:
            entry = _build_platform_entry(
                component_id=component_id,
                platform_name=platform_name,
                manifest=manifest,
                docs=docs,
                dependencies=dependencies,
                supported_platforms=supported,
            )
            if entry is not None:
                entries.append(entry)

    return entries


def _build_component_entry(
    *,
    component_id: str,
    manifest: Any,
    platforms: list[str],
    schema: Any,
    docs: dict[str, dict[str, str]],
    dependencies: list[str],
    supported_platforms: list[str],
) -> dict:
    """Build the parent / hub entry for a component."""
    category = _determine_category(component_id, platforms)
    comp_docs = _resolve_docs(component_id, docs)
    name = _generate_name(component_id, category, comp_docs)
    description = _clean_description(comp_docs.get("description", ""))
    field_descriptions = dict(comp_docs.get("field_descriptions") or {})

    config_entries: list[dict] = []
    sub_entries: list[dict] = []
    if schema is not None:
        try:
            config_entries, sub_entries = _parse_schema(schema, component_id, field_descriptions)
        except Exception as exc:
            _LOGGER.warning("Failed to parse schema for %s: %s", component_id, exc)
    elif platforms:
        # Single-platform component with no own schema — surface its
        # platform schema under the short id (e.g. dht as `dht`).
        primary = _primary_platform(platforms)
        # Inherit base-platform field descriptions so common entity
        # fields (name, id, device_class, ...) carry tooltips.
        merged = _merge_field_descriptions(docs, primary, field_descriptions)
        try:
            platform_manifest = get_platform(primary, component_id)
            if platform_manifest and platform_manifest.config_schema:
                config_entries, sub_entries = _parse_schema(
                    platform_manifest.config_schema, component_id, merged
                )
        except Exception as exc:
            _LOGGER.warning("Failed to parse schema for %s/%s: %s", primary, component_id, exc)

    return {
        "id": component_id,
        "name": name,
        "description": description,
        "category": category,
        "docs_url": _build_docs_url(component_id, category),
        "image_url": _build_image_url(comp_docs, category),
        "dependencies": dependencies,
        "multi_conf": bool(manifest.multi_conf),
        "supported_platforms": supported_platforms,
        "config_entries": config_entries,
        "sub_entries": sub_entries,
    }


def _build_platform_entry(
    *,
    component_id: str,
    platform_name: str,
    manifest: Any,
    docs: dict[str, dict[str, str]],
    dependencies: list[str],
    supported_platforms: list[str],
) -> dict | None:
    """
    Build a per-platform entry for a multi-platform component.

    Used when a single component implements several platforms — e.g.
    ``template`` shows up as ``sensor.template``, ``switch.template``,
    ``binary_sensor.template`` etc. Each entry uses the qualified id
    so the catalog presents them as distinct user-facing options.
    """
    try:
        platform_manifest = get_platform(platform_name, component_id)
    except Exception:
        return None
    if platform_manifest is None or platform_manifest.config_schema is None:
        return None

    qualified_id = f"{platform_name}.{component_id}"
    qualified_docs = docs.get(qualified_id) or _resolve_docs(component_id, docs)
    field_descriptions = _merge_field_descriptions(
        docs, platform_name, qualified_docs.get("field_descriptions") or {}
    )
    domain_label = platform_name.replace("_", " ").title()
    title = qualified_docs.get("title", "")
    if title:
        name = title
    else:
        short_name = _generate_name(component_id, platform_name, qualified_docs)
        name = f"{short_name} {domain_label}"
    description = _clean_description(qualified_docs.get("description", ""))

    try:
        config_entries, sub_entries = _parse_schema(
            platform_manifest.config_schema, component_id, field_descriptions
        )
    except Exception as exc:
        _LOGGER.warning("Failed to parse %s.%s schema: %s", platform_name, component_id, exc)
        return None

    return {
        "id": qualified_id,
        "name": name,
        "description": description,
        "category": platform_name,
        "docs_url": f"https://esphome.io/components/{platform_name}/{component_id}",
        "image_url": _build_image_url(qualified_docs, platform_name),
        "dependencies": dependencies,
        "multi_conf": bool(manifest.multi_conf),
        "supported_platforms": supported_platforms,
        "config_entries": config_entries,
        "sub_entries": sub_entries,
    }


def _primary_platform(platforms: list[str]) -> str:
    """Pick the primary platform for category / schema selection."""
    for pref in ("sensor", "binary_sensor", "switch", "light", "fan", "cover"):
        if pref in platforms:
            return pref
    return platforms[0]


def _resolve_docs(component_id: str, docs: dict[str, dict[str, str]]) -> dict[str, str]:
    """Find docs metadata for *component_id*, trying common bus suffixes."""
    comp_docs = docs.get(component_id, {})
    if comp_docs:
        return comp_docs
    for suffix in ("_i2c", "_spi", "_uart", "_base"):
        if component_id.endswith(suffix):
            base_id = component_id.removesuffix(suffix)
            comp_docs = docs.get(base_id, {})
            if comp_docs:
                return comp_docs
    return {}


def _build_docs_url(component_id: str, category: str) -> str:
    """Build the canonical esphome.io docs URL for a component."""
    if category not in ("core", "bus", "automation", "misc"):
        return f"https://esphome.io/components/{category}/{component_id}"
    return f"https://esphome.io/components/{component_id}"


def _build_image_url(comp_docs: dict[str, str], category: str) -> str:
    """Build the docs-image URL for a component (empty when no image)."""
    image_file = comp_docs.get("image_file", "")
    if not image_file:
        return ""
    doc_cat = comp_docs.get("category") or category
    if doc_cat and doc_cat not in ("core", "bus", "automation", "misc"):
        return f"https://esphome.io/components/{doc_cat}/images/{image_file}"
    return f"https://esphome.io/components/images/{image_file}"


def sync(dry_run: bool = False) -> None:
    """Run the component sync."""
    global PLATFORM_TYPES

    # Fetch docs metadata first (titles, descriptions, images)
    docs_meta = fetch_docs_metadata()

    print("\nDiscovering platform types...")
    PLATFORM_TYPES = _discover_platform_types()
    print(
        f"Found {len(PLATFORM_TYPES)} platform types: {', '.join(sorted(PLATFORM_TYPES)[:10])}..."
    )

    # List all component directories
    components_dir = Path(const.__file__).parent / "components"
    component_dirs = sorted(
        d.name for d in components_dir.iterdir() if d.is_dir() and not d.name.startswith("_")
    )
    print(f"Found {len(component_dirs)} total component directories")

    components: list[dict] = []
    failed = 0

    for comp_id in component_dirs:
        components.extend(_sync_component(comp_id, PLATFORM_TYPES, docs_meta))

    # Synthesize unified entries for platform components (OTA, time, ...)
    # — these are aggregator components; the user-facing model is one
    # entry with a `platform` discriminator rather than separate per-
    # provider components.
    for platform_id in _UNIFIED_PLATFORM_COMPONENTS:
        if platform_id not in PLATFORM_TYPES:
            continue
        unified = _build_unified_platform_component(platform_id, component_dirs, docs_meta)
        if unified is not None:
            components.append(unified)

    # Sort: components with config entries first, then alphabetical
    components.sort(key=lambda c: (not c["config_entries"], c["name"].lower()))

    catalog = {
        "esphome_version": const.__version__,
        "components": components,
    }

    if dry_run:
        with_entries = sum(1 for c in components if c["config_entries"])
        with_subs = sum(1 for c in components if c["sub_entries"])
        total_entries = sum(len(c["config_entries"]) for c in components)
        print(f"\n[dry-run] Would write {len(components)} components to {OUTPUT_FILE.name}")
        print(f"  {with_entries} have config entries ({total_entries} total fields)")
        print(f"  {with_subs} have sub-entries")
        print("\nSample (first 10):")
        for c in components[:10]:
            entries = [e["key"] for e in c["config_entries"]]
            subs = [s["key"] for s in c["sub_entries"]]
            print(f"  {c['id']:30s} cat={c['category']:15s} fields={entries} subs={subs}")
    else:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps(catalog, indent=2, default=str))
        print(f"\nWritten {len(components)} components to {OUTPUT_FILE.name}")

    with_entries = sum(1 for c in components if c["config_entries"])
    print(
        f"Total: {len(components)} components, {with_entries} with config entries, {failed} failed"
    )


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sync(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
