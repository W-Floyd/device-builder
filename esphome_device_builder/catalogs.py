"""Catalog data: boards, components, automations, and config sections.

Board and component definitions are loaded from YAML files in their respective
directories. Automation and config-section catalogs remain inline as they are
simpler and less likely to receive community contributions.
"""

from __future__ import annotations

from .boards import load_board_catalog
from .components import load_component_catalog
from .models import (
    AutomationAction,
    AutomationCatalogResponse,
    AutomationTrigger,
    Board,
    BoardCatalogResponse,
    ComponentCatalogResponse,
    ComponentField,
    ConfigCatalogResponse,
    ConfigSection,
)

# ---------------------------------------------------------------------------
# Board catalog (loaded from YAML files)
# ---------------------------------------------------------------------------

BOARD_CATALOG: BoardCatalogResponse = load_board_catalog()


def get_boards_for_platform(platform: str) -> list[Board]:
    """Return flat board list for a given platform, using ESPHome's BOARDS dicts."""
    from esphome import const

    try:
        if platform.startswith("esp32"):
            from esphome.components.esp32.boards import BOARDS

            boards = {
                k: v
                for k, v in BOARDS.items()
                if v.get(const.KEY_VARIANT, "").lower() == platform.lower()
                or (platform == "esp32" and v.get(const.KEY_VARIANT) == "ESP32")
            }
        elif platform == const.PLATFORM_ESP8266:
            from esphome.components.esp8266.boards import BOARDS as boards
        elif platform == const.PLATFORM_RP2040:
            from esphome.components.rp2040.boards import BOARDS as boards
        elif platform == const.PLATFORM_BK72XX:
            from esphome.components.bk72xx.boards import BOARDS as boards
        elif platform == const.PLATFORM_RTL87XX:
            from esphome.components.rtl87xx.boards import BOARDS as boards
        else:
            return []
    except (ImportError, AttributeError):
        return []

    return sorted(
        [Board(name=v[const.KEY_NAME], board=k) for k, v in boards.items()],
        key=lambda b: b.name,
    )


# ---------------------------------------------------------------------------
# Component catalog (loaded from YAML files)
# ---------------------------------------------------------------------------

COMPONENT_CATALOG: ComponentCatalogResponse = load_component_catalog()


# ---------------------------------------------------------------------------
# Automation catalog
# ---------------------------------------------------------------------------

AUTOMATION_CATALOG = AutomationCatalogResponse(
    triggers=[
        AutomationTrigger(
            id="on_press",
            name="On Press",
            description="Fires when a binary sensor is pressed (transitions to ON).",
            applicable_to=["binary_sensor", "button"],
            fields=[],
        ),
        AutomationTrigger(
            id="on_release",
            name="On Release",
            description="Fires when a binary sensor is released (transitions to OFF).",
            applicable_to=["binary_sensor"],
            fields=[],
        ),
        AutomationTrigger(
            id="on_state",
            name="On State Change",
            description="Fires whenever the component state changes.",
            applicable_to=["binary_sensor", "switch", "sensor"],
            fields=[],
        ),
        AutomationTrigger(
            id="on_value",
            name="On Value",
            description="Fires when a sensor publishes a new value.",
            applicable_to=["sensor"],
            fields=[],
        ),
        AutomationTrigger(
            id="on_value_range",
            name="On Value Range",
            description="Fires when a sensor value enters or leaves a range.",
            applicable_to=["sensor"],
            fields=[
                ComponentField(
                    key="above", label="Above", type="number", required=False
                ),
                ComponentField(
                    key="below", label="Below", type="number", required=False
                ),
            ],
        ),
        AutomationTrigger(
            id="on_turn_on",
            name="On Turn On",
            description="Fires when a switch or light turns on.",
            applicable_to=["switch", "light", "fan"],
            fields=[],
        ),
        AutomationTrigger(
            id="on_turn_off",
            name="On Turn Off",
            description="Fires when a switch or light turns off.",
            applicable_to=["switch", "light", "fan"],
            fields=[],
        ),
    ],
    actions=[
        AutomationAction(
            id="switch.toggle",
            name="Toggle Switch",
            description="Toggle a switch between on and off.",
            fields=[
                ComponentField(
                    key="id", label="Switch ID", type="string", required=True
                ),
            ],
        ),
        AutomationAction(
            id="switch.turn_on",
            name="Turn Switch On",
            description="Turn a switch on.",
            fields=[
                ComponentField(
                    key="id", label="Switch ID", type="string", required=True
                ),
            ],
        ),
        AutomationAction(
            id="switch.turn_off",
            name="Turn Switch Off",
            description="Turn a switch off.",
            fields=[
                ComponentField(
                    key="id", label="Switch ID", type="string", required=True
                ),
            ],
        ),
        AutomationAction(
            id="light.turn_on",
            name="Turn Light On",
            description="Turn a light on, optionally with brightness/colour.",
            fields=[
                ComponentField(
                    key="id", label="Light ID", type="string", required=True
                ),
                ComponentField(
                    key="brightness",
                    label="Brightness (0-1)",
                    type="number",
                    required=False,
                ),
            ],
        ),
        AutomationAction(
            id="light.turn_off",
            name="Turn Light Off",
            description="Turn a light off.",
            fields=[
                ComponentField(
                    key="id", label="Light ID", type="string", required=True
                ),
            ],
        ),
        AutomationAction(
            id="delay",
            name="Delay",
            description="Wait for a specified duration.",
            fields=[
                ComponentField(
                    key="duration",
                    label="Duration (e.g. 1s, 500ms)",
                    type="string",
                    required=True,
                ),
            ],
        ),
        AutomationAction(
            id="logger.log",
            name="Log Message",
            description="Print a message to the ESPHome log.",
            fields=[
                ComponentField(
                    key="message", label="Message", type="string", required=True
                ),
                ComponentField(
                    key="level",
                    label="Log Level",
                    type="select",
                    required=False,
                    default="INFO",
                    options=["DEBUG", "INFO", "WARN", "ERROR"],
                ),
            ],
        ),
    ],
)


# ---------------------------------------------------------------------------
# Config section catalog
# ---------------------------------------------------------------------------

CONFIG_CATALOG = ConfigCatalogResponse(
    sections=[
        ConfigSection(
            id="wifi",
            name="Wi-Fi",
            description="Connect the device to a Wi-Fi network.",
            docs_url="https://esphome.io/components/wifi.html",
            icon="wifi",
            yaml_template=(
                "wifi:\n" "  ssid: {ssid}\n" "  password: {password}\n"
            ),
            fields=[
                ComponentField(
                    key="ssid",
                    label="SSID",
                    type="string",
                    required=True,
                    default="!secret wifi_ssid",
                ),
                ComponentField(
                    key="password",
                    label="Password",
                    type="string",
                    required=True,
                    default="!secret wifi_password",
                ),
            ],
        ),
        ConfigSection(
            id="api",
            name="Home Assistant API",
            description="Enable the native ESPHome API for Home Assistant integration.",
            docs_url="https://esphome.io/components/api.html",
            icon="home-assistant",
            yaml_template=(
                "api:\n" "  encryption:\n" "    key: {encryption_key}\n"
            ),
            fields=[
                ComponentField(
                    key="encryption_key",
                    label="Encryption Key",
                    type="string",
                    required=False,
                    default="",
                ),
            ],
        ),
        ConfigSection(
            id="ota",
            name="OTA Updates",
            description="Allow over-the-air firmware updates.",
            docs_url="https://esphome.io/components/ota/esphome.html",
            icon="update",
            yaml_template=(
                "ota:\n" "  - platform: esphome\n" "    password: {password}\n"
            ),
            fields=[
                ComponentField(
                    key="password",
                    label="OTA Password",
                    type="string",
                    required=False,
                    default="",
                ),
            ],
        ),
        ConfigSection(
            id="logger",
            name="Logger",
            description="Configure serial logging level and output.",
            docs_url="https://esphome.io/components/logger.html",
            icon="text-box",
            yaml_template=("logger:\n" "  level: {level}\n"),
            fields=[
                ComponentField(
                    key="level",
                    label="Log Level",
                    type="select",
                    required=False,
                    default="DEBUG",
                    options=[
                        "NONE",
                        "ERROR",
                        "WARN",
                        "INFO",
                        "DEBUG",
                        "VERBOSE",
                        "VERY_VERBOSE",
                    ],
                ),
            ],
        ),
        ConfigSection(
            id="mqtt",
            name="MQTT",
            description="Connect to an MQTT broker for device communication.",
            docs_url="https://esphome.io/components/mqtt.html",
            icon="mqtt",
            yaml_template=(
                "mqtt:\n"
                "  broker: {broker}\n"
                "  username: {username}\n"
                "  password: {password}\n"
            ),
            fields=[
                ComponentField(
                    key="broker",
                    label="Broker Address",
                    type="string",
                    required=True,
                ),
                ComponentField(
                    key="username",
                    label="Username",
                    type="string",
                    required=False,
                    default="",
                ),
                ComponentField(
                    key="password",
                    label="Password",
                    type="string",
                    required=False,
                    default="",
                ),
            ],
        ),
        ConfigSection(
            id="web_server",
            name="Web Server",
            description="Enable the built-in HTTP web server on the device.",
            docs_url="https://esphome.io/components/web_server.html",
            icon="web",
            yaml_template=("web_server:\n" "  port: {port}\n"),
            fields=[
                ComponentField(
                    key="port",
                    label="Port",
                    type="number",
                    required=False,
                    default=80,
                ),
            ],
        ),
    ]
)
