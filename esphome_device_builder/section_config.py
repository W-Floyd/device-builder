"""Section config definitions: rich ConfigEntry lists for each YAML section type.

Maps section keys (e.g. 'wifi', 'sensor', 'esphome') to their visual config entries
with current values parsed from the device YAML.
"""

from __future__ import annotations

import re
from typing import Any

from .models import ConfigEntry, ConfigValueOption, SectionConfigResponse


# ---------------------------------------------------------------------------
# Section config definitions
# ---------------------------------------------------------------------------

def _opt(label: str, value: str) -> ConfigValueOption:
    return ConfigValueOption(label=label, value=value)


def _options(values: list[str]) -> list[ConfigValueOption]:
    return [_opt(v, v) for v in values]


# Core config section entries
CORE_SECTION_ENTRIES: dict[str, dict[str, Any]] = {
    "esphome": {
        "title": "ESPHome Core",
        "description": "Core device identity and build settings.",
        "docs_url": "https://esphome.io/components/esphome.html",
        "icon": "chip",
        "entries": [
            ConfigEntry(key="name", type="string", label="Device Name", required=True, description="The name of your device. Used as the hostname."),
            ConfigEntry(key="friendly_name", type="string", label="Friendly Name", description="Human-readable name shown in Home Assistant."),
            ConfigEntry(key="comment", type="string", label="Comment", description="A comment to describe the device."),
            ConfigEntry(key="area", type="string", label="Area", description="Area where the device is located."),
            ConfigEntry(key="platform", type="string", label="Platform", hidden=True),
            ConfigEntry(key="compile_process_limit", type="integer", label="Compile Process Limit", description="Max parallel compile processes.", default_value=1),
        ],
    },
    "esp32": {
        "title": "ESP32 Platform",
        "description": "ESP32 chip and board configuration.",
        "docs_url": "https://esphome.io/components/esp32.html",
        "icon": "memory",
        "entries": [
            ConfigEntry(key="board", type="string", label="Board", required=True, description="The PlatformIO board ID."),
            ConfigEntry(key="variant", type="select", label="Variant", options=_options(["ESP32", "ESP32S2", "ESP32S3", "ESP32C3", "ESP32C6", "ESP32H2"]), description="ESP32 chip variant."),
            ConfigEntry(key="framework.type", type="select", label="Framework", options=_options(["arduino", "esp-idf"]), default_value="arduino", description="Build framework to use."),
        ],
    },
    "esp8266": {
        "title": "ESP8266 Platform",
        "description": "ESP8266 chip and board configuration.",
        "docs_url": "https://esphome.io/components/esp8266.html",
        "icon": "memory",
        "entries": [
            ConfigEntry(key="board", type="string", label="Board", required=True, description="The PlatformIO board ID."),
            ConfigEntry(key="framework.type", type="select", label="Framework", options=_options(["arduino"]), default_value="arduino"),
        ],
    },
    "rp2040": {
        "title": "RP2040 Platform",
        "description": "Raspberry Pi RP2040 board configuration.",
        "docs_url": "https://esphome.io/components/rp2040.html",
        "icon": "memory",
        "entries": [
            ConfigEntry(key="board", type="string", label="Board", required=True),
        ],
    },
    "wifi": {
        "title": "Wi-Fi",
        "description": "Connect the device to a Wi-Fi network.",
        "docs_url": "https://esphome.io/components/wifi.html",
        "icon": "wifi",
        "entries": [
            ConfigEntry(key="ssid", type="string", label="SSID", required=True, description="Wi-Fi network name."),
            ConfigEntry(key="password", type="secure_string", label="Password", required=True, description="Wi-Fi password."),
            ConfigEntry(key="fast_connect", type="boolean", label="Fast Connect", default_value=False, description="Skip scanning and connect directly."),
            ConfigEntry(key="power_save_mode", type="select", label="Power Save Mode", options=_options(["none", "light", "high"]), default_value="none", description="Wi-Fi power saving mode."),
            ConfigEntry(key="domain", type="string", label="Domain", default_value=".local", description="mDNS domain suffix."),
            ConfigEntry(key="reboot_timeout", type="string", label="Reboot Timeout", default_value="15min", description="Reboot if Wi-Fi not connected for this duration."),
        ],
    },
    "api": {
        "title": "Home Assistant API",
        "description": "Enable the native ESPHome API for Home Assistant.",
        "docs_url": "https://esphome.io/components/api.html",
        "icon": "home-assistant",
        "entries": [
            ConfigEntry(key="encryption.key", type="secure_string", label="Encryption Key", description="Noise encryption key for secure communication."),
            ConfigEntry(key="port", type="integer", label="Port", default_value=6053, description="TCP port for the API."),
            ConfigEntry(key="reboot_timeout", type="string", label="Reboot Timeout", default_value="15min", description="Reboot if no API client connects within this time."),
        ],
    },
    "ota": {
        "title": "OTA Updates",
        "description": "Allow over-the-air firmware updates.",
        "docs_url": "https://esphome.io/components/ota/esphome.html",
        "icon": "update",
        "entries": [
            ConfigEntry(key="platform", type="string", label="Platform", default_value="esphome", hidden=True),
            ConfigEntry(key="password", type="secure_string", label="OTA Password", description="Password required for OTA updates."),
            ConfigEntry(key="port", type="integer", label="Port", description="UDP port for OTA."),
        ],
    },
    "logger": {
        "title": "Logger",
        "description": "Configure serial logging level and output.",
        "docs_url": "https://esphome.io/components/logger.html",
        "icon": "text-box",
        "entries": [
            ConfigEntry(
                key="level", type="select", label="Log Level",
                options=_options(["NONE", "ERROR", "WARN", "INFO", "DEBUG", "VERBOSE", "VERY_VERBOSE"]),
                default_value="DEBUG", description="Minimum log level to output.",
            ),
            ConfigEntry(key="baud_rate", type="integer", label="Baud Rate", default_value=115200, description="Serial baud rate. Set to 0 to disable serial logging."),
            ConfigEntry(key="logs", type="label", label="Per-component log levels can be configured in YAML."),
        ],
    },
    "mqtt": {
        "title": "MQTT",
        "description": "Connect to an MQTT broker.",
        "docs_url": "https://esphome.io/components/mqtt.html",
        "icon": "mqtt",
        "entries": [
            ConfigEntry(key="broker", type="string", label="Broker Address", required=True, description="MQTT broker hostname or IP."),
            ConfigEntry(key="port", type="integer", label="Port", default_value=1883),
            ConfigEntry(key="username", type="string", label="Username"),
            ConfigEntry(key="password", type="secure_string", label="Password"),
            ConfigEntry(key="topic_prefix", type="string", label="Topic Prefix", description="Prefix for all MQTT topics. Defaults to device name."),
            ConfigEntry(key="discovery", type="boolean", label="Home Assistant Discovery", default_value=True),
        ],
    },
    "web_server": {
        "title": "Web Server",
        "description": "Enable the built-in HTTP web server on the device.",
        "docs_url": "https://esphome.io/components/web_server.html",
        "icon": "web",
        "entries": [
            ConfigEntry(key="port", type="integer", label="Port", default_value=80),
            ConfigEntry(key="auth.username", type="string", label="Username", description="HTTP basic auth username."),
            ConfigEntry(key="auth.password", type="secure_string", label="Password", description="HTTP basic auth password."),
        ],
    },
    "captive_portal": {
        "title": "Captive Portal",
        "description": "Fallback Wi-Fi hotspot with a web interface.",
        "docs_url": "https://esphome.io/components/captive_portal.html",
        "icon": "web",
        "entries": [
            ConfigEntry(key="_info", type="alert", label="The captive portal provides a fallback Wi-Fi AP when the device cannot connect to the configured network. No additional configuration needed."),
        ],
    },
    "time": {
        "title": "Time",
        "description": "Configure time synchronization.",
        "docs_url": "https://esphome.io/components/time/index.html",
        "icon": "clock",
        "entries": [
            ConfigEntry(key="platform", type="select", label="Platform", options=_options(["homeassistant", "sntp"]), required=True),
            ConfigEntry(key="timezone", type="string", label="Timezone", description="POSIX timezone string."),
        ],
    },
}

# Component section entries
COMPONENT_SECTION_ENTRIES: dict[str, dict[str, Any]] = {
    "binary_sensor": {
        "title": "Binary Sensor",
        "description": "Detects on/off states such as buttons, door contacts, and PIR sensors.",
        "docs_url": "https://esphome.io/components/binary_sensor/index.html",
        "icon": "electric-switch",
        "entries": [
            ConfigEntry(key="platform", type="string", label="Platform", required=True, description="The sensor platform (e.g. gpio, status)."),
            ConfigEntry(key="name", type="string", label="Name", required=True, description="Name exposed to Home Assistant."),
            ConfigEntry(key="pin", type="string", label="GPIO Pin", description="GPIO pin number."),
            ConfigEntry(key="device_class", type="select", label="Device Class", options=_options([
                "", "battery", "cold", "connectivity", "door", "garage_door", "gas",
                "heat", "light", "lock", "moisture", "motion", "moving", "occupancy",
                "opening", "plug", "power", "presence", "problem", "safety",
                "smoke", "sound", "vibration", "window",
            ])),
            ConfigEntry(key="icon", type="icon", label="Icon", description="MDI icon override."),
            ConfigEntry(key="inverted", type="boolean", label="Inverted", default_value=False, description="Invert the binary state."),
            ConfigEntry(key="filters", type="label", label="Filters can be configured in YAML (debounce, delayed_on, etc)."),
        ],
    },
    "sensor": {
        "title": "Sensor",
        "description": "Measure numeric values like temperature, humidity, voltage.",
        "docs_url": "https://esphome.io/components/sensor/index.html",
        "icon": "thermometer",
        "entries": [
            ConfigEntry(key="platform", type="string", label="Platform", required=True, description="The sensor platform (e.g. dht, adc, dallas)."),
            ConfigEntry(key="name", type="string", label="Name", required=True, description="Name exposed to Home Assistant."),
            ConfigEntry(key="pin", type="string", label="GPIO Pin", description="GPIO pin for the sensor."),
            ConfigEntry(key="update_interval", type="string", label="Update Interval", default_value="60s", description="How often to read the sensor."),
            ConfigEntry(key="unit_of_measurement", type="string", label="Unit of Measurement", description="Unit string (e.g. °C, %, V)."),
            ConfigEntry(key="accuracy_decimals", type="integer", label="Accuracy Decimals", description="Number of decimal places."),
            ConfigEntry(key="device_class", type="select", label="Device Class", options=_options([
                "", "apparent_power", "aqi", "atmospheric_pressure", "battery",
                "carbon_dioxide", "carbon_monoxide", "current", "distance",
                "energy", "frequency", "gas", "humidity", "illuminance",
                "moisture", "nitrogen_dioxide", "ozone", "pm1", "pm10", "pm25",
                "power", "power_factor", "pressure", "signal_strength",
                "speed", "sulphur_dioxide", "temperature", "volatile_organic_compounds",
                "voltage", "volume", "water", "weight", "wind_speed",
            ])),
            ConfigEntry(key="icon", type="icon", label="Icon"),
            ConfigEntry(key="filters", type="label", label="Filters can be configured in YAML (multiply, offset, sliding_window, etc)."),
        ],
    },
    "switch": {
        "title": "Switch",
        "description": "Control an output device such as a relay or LED.",
        "docs_url": "https://esphome.io/components/switch/index.html",
        "icon": "toggle-switch",
        "entries": [
            ConfigEntry(key="platform", type="string", label="Platform", required=True),
            ConfigEntry(key="name", type="string", label="Name", required=True),
            ConfigEntry(key="pin", type="string", label="GPIO Pin"),
            ConfigEntry(key="icon", type="icon", label="Icon"),
            ConfigEntry(key="inverted", type="boolean", label="Inverted", default_value=False),
            ConfigEntry(key="restore_mode", type="select", label="Restore Mode", options=_options([
                "RESTORE_DEFAULT_OFF", "RESTORE_DEFAULT_ON",
                "ALWAYS_OFF", "ALWAYS_ON", "RESTORE_INVERTED_DEFAULT_OFF", "RESTORE_INVERTED_DEFAULT_ON",
                "DISABLED",
            ]), description="How to restore state on boot."),
        ],
    },
    "light": {
        "title": "Light",
        "description": "Control lights including simple on/off, dimmable, and RGB.",
        "docs_url": "https://esphome.io/components/light/index.html",
        "icon": "lightbulb",
        "entries": [
            ConfigEntry(key="platform", type="string", label="Platform", required=True),
            ConfigEntry(key="name", type="string", label="Name", required=True),
            ConfigEntry(key="icon", type="icon", label="Icon"),
            ConfigEntry(key="default_transition_length", type="string", label="Default Transition", default_value="1s"),
            ConfigEntry(key="restore_mode", type="select", label="Restore Mode", options=_options([
                "RESTORE_DEFAULT_OFF", "RESTORE_DEFAULT_ON",
                "ALWAYS_OFF", "ALWAYS_ON", "RESTORE_INVERTED_DEFAULT_OFF", "RESTORE_INVERTED_DEFAULT_ON",
                "DISABLED",
            ])),
            ConfigEntry(key="effects", type="label", label="Effects can be configured in YAML."),
        ],
    },
    "button": {
        "title": "Button",
        "description": "Expose a momentary action as a button entity.",
        "docs_url": "https://esphome.io/components/button/index.html",
        "icon": "gesture-tap-button",
        "entries": [
            ConfigEntry(key="platform", type="string", label="Platform", required=True),
            ConfigEntry(key="name", type="string", label="Name", required=True),
            ConfigEntry(key="pin", type="string", label="GPIO Pin"),
            ConfigEntry(key="icon", type="icon", label="Icon"),
        ],
    },
    "fan": {
        "title": "Fan",
        "description": "Control fan speed and direction.",
        "docs_url": "https://esphome.io/components/fan/index.html",
        "icon": "fan",
        "entries": [
            ConfigEntry(key="platform", type="string", label="Platform", required=True),
            ConfigEntry(key="name", type="string", label="Name", required=True),
            ConfigEntry(key="icon", type="icon", label="Icon"),
        ],
    },
    "output": {
        "title": "Output",
        "description": "Configure output pins for PWM, GPIO, etc.",
        "docs_url": "https://esphome.io/components/output/index.html",
        "icon": "export",
        "entries": [
            ConfigEntry(key="platform", type="string", label="Platform", required=True),
            ConfigEntry(key="id", type="string", label="ID", required=True, description="Unique ID for this output."),
            ConfigEntry(key="pin", type="string", label="GPIO Pin"),
        ],
    },
    "text_sensor": {
        "title": "Text Sensor",
        "description": "Expose text-based state values.",
        "docs_url": "https://esphome.io/components/text_sensor/index.html",
        "icon": "form-textbox",
        "entries": [
            ConfigEntry(key="platform", type="string", label="Platform", required=True),
            ConfigEntry(key="name", type="string", label="Name", required=True),
            ConfigEntry(key="icon", type="icon", label="Icon"),
        ],
    },
}

# Automation section entries
AUTOMATION_SECTION_ENTRIES: dict[str, dict[str, Any]] = {
    "script": {
        "title": "Scripts",
        "description": "Define reusable action sequences.",
        "docs_url": "https://esphome.io/components/script.html",
        "icon": "script",
        "entries": [
            ConfigEntry(key="_info", type="alert", label="Scripts are best edited directly in YAML. Use the YAML editor to define script actions."),
        ],
    },
    "globals": {
        "title": "Global Variables",
        "description": "Define global variables that persist across automations.",
        "docs_url": "https://esphome.io/components/globals.html",
        "icon": "variable",
        "entries": [
            ConfigEntry(key="_info", type="alert", label="Global variables are best edited directly in YAML."),
        ],
    },
    "interval": {
        "title": "Interval",
        "description": "Execute actions at regular intervals.",
        "docs_url": "https://esphome.io/components/interval.html",
        "icon": "timer",
        "entries": [
            ConfigEntry(key="interval", type="string", label="Interval", required=True, default_value="60s", description="How often to run the actions."),
            ConfigEntry(key="_info", type="alert", label="Interval actions are best edited directly in YAML."),
        ],
    },
}


# ---------------------------------------------------------------------------
# YAML value extraction
# ---------------------------------------------------------------------------

def _parse_yaml_section_values(yaml_text: str, section_key: str) -> dict[str, Any]:
    """Extract key-value pairs from a top-level YAML section.

    This is a simple text-based parser — it handles flat and one-level nested keys
    (e.g. 'encryption.key' maps to 'encryption:\\n  key: value').
    """
    values: dict[str, Any] = {}
    lines = yaml_text.splitlines()

    # Find the section start
    section_start = -1
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(section_key)}\s*:", line):
            section_start = i
            break

    if section_start == -1:
        return values

    # Determine section end (next top-level key or EOF)
    section_end = len(lines)
    for i in range(section_start + 1, len(lines)):
        stripped = lines[i]
        if stripped and not stripped[0].isspace() and not stripped.startswith("#"):
            section_end = i
            break

    # Parse values within the section
    current_parent = ""
    for i in range(section_start + 1, section_end):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Check indentation level
        indent = len(line) - len(line.lstrip())

        # Handle list items (- platform: gpio)
        list_match = re.match(r"^(\s*)-\s+(\w[\w.]*)\s*:\s*(.+)$", line)
        if list_match:
            key = list_match.group(2)
            val = list_match.group(3).strip()
            values[key] = _parse_value(val)
            current_parent = ""
            continue

        list_item_start = re.match(r"^(\s*)-\s+(\w[\w.]*)\s*:\s*$", line)
        if list_item_start:
            current_parent = ""
            continue

        # Handle nested key-value pairs
        kv_match = re.match(r"^\s+(\w[\w.]*)\s*:\s*(.+)$", line)
        if kv_match:
            key = kv_match.group(1)
            val = kv_match.group(2).strip()
            if current_parent:
                values[f"{current_parent}.{key}"] = _parse_value(val)
            else:
                values[key] = _parse_value(val)
            continue

        # Handle nested parent keys (key with no value, e.g. 'encryption:')
        parent_match = re.match(r"^\s+(\w[\w.]*)\s*:\s*$", line)
        if parent_match:
            current_parent = parent_match.group(1)
            continue

    return values


def _parse_value(raw: str) -> str | int | float | bool:
    """Convert a raw YAML value string to a typed Python value."""
    if raw.lower() in ("true", "yes"):
        return True
    if raw.lower() in ("false", "no"):
        return False
    # Keep secret references and quoted strings as-is
    if raw.startswith("!secret") or raw.startswith('"') or raw.startswith("'"):
        return raw
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def get_section_config(yaml_text: str, section_key: str) -> SectionConfigResponse | None:
    """Build a SectionConfigResponse for a given YAML section key.

    Returns None if the section key is not recognized.
    """
    # Look up in all catalogs
    section_type = "core"
    definition = CORE_SECTION_ENTRIES.get(section_key)
    if definition is None:
        section_type = "component"
        definition = COMPONENT_SECTION_ENTRIES.get(section_key)
    if definition is None:
        section_type = "automation"
        definition = AUTOMATION_SECTION_ENTRIES.get(section_key)
    if definition is None:
        # Unknown section — return a generic entry
        return SectionConfigResponse(
            section_key=section_key,
            section_type="core",
            title=section_key,
            description=f"Configuration for '{section_key}'.",
            docs_url=f"https://esphome.io/components/{section_key}.html",
            icon="cog",
            entries=[
                ConfigEntry(
                    key="_info",
                    type="alert",
                    label=f"No visual editor available for '{section_key}'. Use the YAML editor.",
                ),
            ],
        )

    # Parse current values from YAML
    current_values = _parse_yaml_section_values(yaml_text, section_key)

    # Clone entries and fill in current values
    entries: list[ConfigEntry] = []
    for entry_def in definition["entries"]:
        entry = ConfigEntry(
            key=entry_def.key,
            type=entry_def.type,
            label=entry_def.label,
            default_value=entry_def.default_value,
            required=entry_def.required,
            options=entry_def.options,
            range=entry_def.range,
            description=entry_def.description,
            help_link=entry_def.help_link,
            multi_value=entry_def.multi_value,
            hidden=entry_def.hidden,
            value=current_values.get(entry_def.key, entry_def.value),
        )
        entries.append(entry)

    return SectionConfigResponse(
        section_key=section_key,
        section_type=section_type,
        title=definition["title"],
        description=definition["description"],
        docs_url=definition["docs_url"],
        icon=definition["icon"],
        entries=entries,
    )
