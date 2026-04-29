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
        comp_id = mdx_file.stem
        content = mdx_file.read_text(errors="ignore")
        fm = _parse_mdx_frontmatter(content)
        img = _parse_first_image(content)
        metadata[comp_id] = {
            "title": fm.get("title", ""),
            "description": fm.get("description", ""),
            "image_file": img or "",
            "category": "",
        }

    # Category subdirectories
    for cat_dir in sorted(components_dir.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name == "images":
            continue
        cat_name = cat_dir.name
        mdx_files = list(cat_dir.glob("*.mdx"))
        if mdx_files:
            print(f"  {cat_name}: {len(mdx_files)} docs")
        for mdx_file in mdx_files:
            # `<dir>/index.mdx` documents the platform-component itself
            # (e.g. ota/index.mdx → ota). Use the directory name in that
            # case so we can resolve docs for unified entries like ota/time.
            comp_id = cat_name if mdx_file.stem == "index" else mdx_file.stem
            content = mdx_file.read_text(errors="ignore")
            fm = _parse_mdx_frontmatter(content)
            img = _parse_first_image(content)
            metadata[comp_id] = {
                "title": fm.get("title", ""),
                "description": fm.get("description", ""),
                "image_file": img or "",
                "category": cat_name,
            }

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

# Schema keys to skip (internal/inherited from base platform schemas)
SKIP_KEYS: set[str] = {
    "id",
    "mqtt_id",
    "web_server",
    "setup_priority",
    "type_id",
    # Base entity keys (inherited from platform type)
    "name",
    "internal",
    "disabled_by_default",
    "entity_category",
    "device_class",
    "state_class",
    "unit_of_measurement",
    "accuracy_decimals",
    "force_update",
    "expire_after",
    "filters",
    "icon",
    # MQTT inherited keys
    "device_id",
    "qos",
    "retain",
    "discovery",
    "subscribe_qos",
    "state_topic",
    "command_topic",
    "availability",
    # Zigbee inherited keys
    "zigbee_id",
    "zigbee_sensor",
}

# Schema keys that are automation triggers (skip)
AUTOMATION_KEY_PREFIXES = ("on_",)

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

    entry: dict[str, Any] = {
        "key": key_name,
        "type": entry_type,
        "label": _key_to_label(key_name),
        "required": required,
        "default_value": default if not callable(default) else None,
        "options": info.get("options"),
        "range": range_val,
        "advanced": not required,
        "translation_key": f"component.config.{key_name}",
    }

    if info.get("templatable"):
        entry["templatable"] = True

    return entry


def _unwrap_schema(schema: Any) -> dict | None:
    """Find the dict schema buried inside vol.All / vol.Schema wrappers.

    ESPHome wraps many CONFIG_SCHEMAs in `cv.All(...)` for chained validation
    (e.g. version checks, post-processing). The actual key-validator mapping
    lives inside one of the wrapped validators.
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


def _parse_schema(schema: Any, component_id: str) -> tuple[list[dict], list[dict]]:
    """Parse a CONFIG_SCHEMA into config entries and sub-entries.

    Returns (config_entries, sub_entries).
    """
    entries: list[dict] = []
    sub_entries: list[dict] = []

    schema_dict = _unwrap_schema(schema)
    if schema_dict is None:
        return entries, sub_entries

    for key, validator in schema_dict.items():
        key_name = _key_name(key)

        # Skip internal/inherited keys
        if key_name in SKIP_KEYS:
            continue
        if any(key_name.startswith(p) for p in AUTOMATION_KEY_PREFIXES):
            continue
        # Skip GenerateID
        if hasattr(key, "schema") and callable(getattr(key.schema, "__func__", None)):
            continue

        # Sub-entry (e.g. DHT's temperature/humidity readings)
        if _is_sub_entity_schema(validator):
            sub_entries.append(_build_sub_entry(key_name, validator))
            continue

        entry = _build_entry(key, validator)
        if entry is not None:
            entries.append(entry)

    return entries, sub_entries


def _build_sub_entry(key_name: str, validator: Any) -> dict:
    """Build a sub-entry dict from a sub-schema validator."""
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
        entry = _build_entry(sk, sv)
        if entry is not None:
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


def _sync_component(
    component_id: str,
    platform_types: set[str],
    docs_meta: dict[str, dict[str, str]] | None = None,
) -> dict | None:
    """Sync a single component. Returns a dict or None on failure."""
    try:
        manifest = get_component(component_id)
    except Exception as exc:
        _LOGGER.warning("Failed to load component %s: %s", component_id, exc)
        return None

    if manifest is None:
        return None

    # Skip platform-component aggregators (sensor, binary_sensor, switch, ...)
    # — these are the parents of platform-providing components, not user-facing
    # entries themselves. Target platforms (esp32, esp8266, ...) ARE included
    # because users configure them directly.
    if manifest.is_platform_component:
        return None

    # Find which platforms this component provides
    platforms = _get_component_platforms(component_id, platform_types)
    category = _determine_category(component_id, platforms)

    # Get docs metadata — try exact match first, then strip bus suffixes
    all_docs = docs_meta or {}
    comp_docs = all_docs.get(component_id, {})
    if not comp_docs:
        # Try stripping _i2c, _spi, _uart, _base suffixes
        for suffix in ("_i2c", "_spi", "_uart", "_base"):
            if component_id.endswith(suffix):
                base_id = component_id.removesuffix(suffix)
                comp_docs = all_docs.get(base_id, {})
                if comp_docs:
                    break
    name = _generate_name(component_id, category, comp_docs)
    description = _clean_description(comp_docs.get("description", ""))

    # Build image URL from docs image file
    image_url = ""
    image_file = comp_docs.get("image_file", "")
    if image_file:
        doc_cat = comp_docs.get("category") or category
        if doc_cat and doc_cat not in ("core", "bus", "automation", "misc"):
            image_url = f"https://esphome.io/components/{doc_cat}/images/{image_file}"
        else:
            image_url = f"https://esphome.io/components/images/{image_file}"

    # Build docs URL
    if category not in ("core", "bus", "automation", "misc"):
        docs_url = f"https://esphome.io/components/{category}/{component_id}"
    else:
        docs_url = f"https://esphome.io/components/{component_id}"

    # Parse config schema
    config_entries: list[dict] = []
    sub_entries: list[dict] = []

    if platforms:
        primary_platform = platforms[0]
        for pref in ("sensor", "binary_sensor", "switch", "light", "fan", "cover"):
            if pref in platforms:
                primary_platform = pref
                break
        try:
            platform_manifest = get_platform(primary_platform, component_id)
            if platform_manifest and platform_manifest.config_schema:
                config_entries, sub_entries = _parse_schema(
                    platform_manifest.config_schema, component_id
                )
        except Exception as exc:
            _LOGGER.warning(
                "Failed to parse schema for %s/%s: %s", primary_platform, component_id, exc
            )
    elif manifest.config_schema:
        try:
            config_entries, sub_entries = _parse_schema(manifest.config_schema, component_id)
        except Exception as exc:
            _LOGGER.warning("Failed to parse schema for %s: %s", component_id, exc)

    dependencies = list(manifest.dependencies) if manifest.dependencies else []

    # Determine platform compatibility from dependencies. If a component
    # depends on a target platform, it only works on that platform.
    target_platforms = {
        "esp32",
        "esp8266",
        "rp2040",
        "bk72xx",
        "rtl87xx",
        "ln882x",
        "nrf52",
        "host",
    }
    supported_platforms = [d for d in dependencies if str(d) in target_platforms]
    # The target-platform components themselves only support themselves.
    if manifest.is_target_platform:
        supported_platforms = [component_id]

    return {
        "id": component_id,
        "name": name,
        "description": description,
        "category": category,
        "docs_url": docs_url,
        "image_url": image_url,
        "dependencies": dependencies,
        "multi_conf": bool(manifest.multi_conf),
        "supported_platforms": supported_platforms,
        "config_entries": config_entries,
        "sub_entries": sub_entries,
    }


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
        result = _sync_component(comp_id, PLATFORM_TYPES, docs_meta)
        if result is None:
            continue
        components.append(result)

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
