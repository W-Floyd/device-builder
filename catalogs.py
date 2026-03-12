"""Static catalog data: board catalog, component catalog, automation catalog, config sections."""

from __future__ import annotations

from .models import (
    AutomationAction,
    AutomationCatalogResponse,
    AutomationTrigger,
    Board,
    BoardCatalogEntry,
    BoardCatalogResponse,
    ComponentCatalogResponse,
    ComponentField,
    ComponentPlatform,
    ComponentType,
    ConfigCatalogResponse,
    ConfigSection,
)

# ---------------------------------------------------------------------------
# Board catalog
# ---------------------------------------------------------------------------

BOARD_CATALOG = BoardCatalogResponse(
    boards=[
        BoardCatalogEntry(
            id="apollo-esp32-c6",
            name="Apollo Automation ESP32-C6 Starter Kit",
            description="Apollo Automation's ESP32-C6 based starter kit with Wi-Fi 6, Zigbee, and Thread/Matter support.",
            platform="esp32",
            board="esp32-c6-devkitc-1",
            tags=["esp32-c6", "starter-kit", "apollo-automation", "wifi", "bluetooth", "zigbee", "thread", "matter"],
            docs_url="https://esphome.io/components/esp32.html",
            image_url=None,
        ),
        BoardCatalogEntry(
            id="apollo-esp32-s3",
            name="Apollo Automation ESP32-S3 Starter Kit",
            description="Apollo Automation's ESP32-S3 based starter kit with USB and Bluetooth LE.",
            platform="esp32",
            board="esp32-s3-devkitc-1",
            tags=["esp32-s3", "starter-kit", "apollo-automation", "wifi", "bluetooth", "usb"],
            docs_url="https://esphome.io/components/esp32.html",
            image_url=None,
        ),
        BoardCatalogEntry(
            id="esp32-devkit-v1",
            name="ESP32 DevKit V1",
            description="Generic ESP32 development board with 30 GPIO pins.",
            platform="esp32",
            board="esp32dev",
            tags=["esp32", "dev-kit", "wifi", "bluetooth"],
            docs_url="https://esphome.io/components/esp32.html",
            image_url=None,
        ),
        BoardCatalogEntry(
            id="esp32-s3-devkitc-1",
            name="ESP32-S3 DevKitC-1",
            description="Espressif's official ESP32-S3 development board.",
            platform="esp32",
            board="esp32-s3-devkitc-1",
            tags=["esp32-s3", "dev-kit", "wifi", "bluetooth", "usb"],
            docs_url="https://esphome.io/components/esp32.html",
            image_url=None,
        ),
        BoardCatalogEntry(
            id="esp32-c3-devkitm-1",
            name="ESP32-C3 DevKitM-1",
            description="Espressif's low-cost RISC-V ESP32-C3 development board.",
            platform="esp32",
            board="esp32-c3-devkitm-1",
            tags=["esp32", "dev-kit", "wifi", "bluetooth", "low-power"],
            docs_url="https://esphome.io/components/esp32.html",
            image_url=None,
        ),
        BoardCatalogEntry(
            id="nodemcuv2",
            name="NodeMCU v2 (ESP8266)",
            description="Popular ESP8266-based development board.",
            platform="esp8266",
            board="nodemcuv2",
            tags=["esp8266", "dev-kit", "wifi"],
            docs_url="https://esphome.io/components/esp8266.html",
            image_url=None,
        ),
        BoardCatalogEntry(
            id="d1_mini",
            name="Wemos D1 Mini (ESP8266)",
            description="Compact ESP8266 board with USB-C.",
            platform="esp8266",
            board="d1_mini",
            tags=["esp8266", "dev-kit", "wifi"],
            docs_url="https://esphome.io/components/esp8266.html",
            image_url=None,
        ),
        BoardCatalogEntry(
            id="rpi-pico",
            name="Raspberry Pi Pico W",
            description="RP2040-based board with Wi-Fi.",
            platform="rp2040",
            board="rpipicow",
            tags=["rp2040", "dev-kit", "wifi"],
            docs_url="https://esphome.io/components/rp2040.html",
            image_url=None,
        ),
    ]
)


def get_boards_for_platform(platform: str) -> list[Board]:
    """Return flat board list for a given platform, using ESPHome's BOARDS dicts."""
    from esphome import const

    try:
        if platform.startswith("esp32"):
            from esphome.components.esp32.boards import BOARDS
            from esphome.components.esp32 import const as esp32_const
            boards = {
                k: v for k, v in BOARDS.items()
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
# Component catalog
# ---------------------------------------------------------------------------

_PIN_FIELD = ComponentField(key="pin", label="GPIO Pin", type="pin", required=True)
_NAME_FIELD = ComponentField(key="name", label="Name", type="string", required=True)


COMPONENT_CATALOG = ComponentCatalogResponse(
    components=[
        ComponentType(
            id="binary_sensor",
            name="Binary Sensor",
            description="Detects on/off states such as buttons, door contacts, and PIR sensors.",
            docs_url="https://esphome.io/components/binary_sensor/index.html",
            icon="electric-switch",
            platforms=[
                ComponentPlatform(
                    id="gpio",
                    name="GPIO",
                    description="Read a binary state from a GPIO pin.",
                    yaml_template=(
                        "binary_sensor:\n"
                        "  - platform: gpio\n"
                        "    pin: {pin}\n"
                        "    name: {name}\n"
                    ),
                    fields=[_PIN_FIELD, _NAME_FIELD],
                ),
            ],
        ),
        ComponentType(
            id="switch",
            name="Switch",
            description="Control an output device such as a relay or LED.",
            docs_url="https://esphome.io/components/switch/index.html",
            icon="toggle-switch",
            platforms=[
                ComponentPlatform(
                    id="gpio",
                    name="GPIO",
                    description="Control a GPIO pin as a switch.",
                    yaml_template=(
                        "switch:\n"
                        "  - platform: gpio\n"
                        "    pin: {pin}\n"
                        "    name: {name}\n"
                    ),
                    fields=[_PIN_FIELD, _NAME_FIELD],
                ),
            ],
        ),
        ComponentType(
            id="sensor",
            name="Sensor",
            description="Measure numeric values like temperature, humidity, voltage.",
            docs_url="https://esphome.io/components/sensor/index.html",
            icon="thermometer",
            platforms=[
                ComponentPlatform(
                    id="dht",
                    name="DHT (Temperature & Humidity)",
                    description="Read temperature and humidity from a DHT11/DHT22 sensor.",
                    yaml_template=(
                        "sensor:\n"
                        "  - platform: dht\n"
                        "    pin: {pin}\n"
                        "    model: {model}\n"
                        "    temperature:\n"
                        "      name: {name} Temperature\n"
                        "    humidity:\n"
                        "      name: {name} Humidity\n"
                        "    update_interval: {update_interval}\n"
                    ),
                    fields=[
                        _PIN_FIELD,
                        _NAME_FIELD,
                        ComponentField(
                            key="model",
                            label="Sensor Model",
                            type="select",
                            required=True,
                            default="DHT22",
                            options=["DHT11", "DHT22", "AM2302"],
                        ),
                        ComponentField(
                            key="update_interval",
                            label="Update Interval",
                            type="string",
                            required=False,
                            default="60s",
                        ),
                    ],
                ),
                ComponentPlatform(
                    id="adc",
                    name="ADC (Analog)",
                    description="Read an analog voltage from a GPIO pin.",
                    yaml_template=(
                        "sensor:\n"
                        "  - platform: adc\n"
                        "    pin: {pin}\n"
                        "    name: {name}\n"
                        "    update_interval: {update_interval}\n"
                    ),
                    fields=[
                        _PIN_FIELD,
                        _NAME_FIELD,
                        ComponentField(
                            key="update_interval",
                            label="Update Interval",
                            type="string",
                            required=False,
                            default="60s",
                        ),
                    ],
                ),
            ],
        ),
        ComponentType(
            id="light",
            name="Light",
            description="Control lights including simple on/off, dimmable, and RGB.",
            docs_url="https://esphome.io/components/light/index.html",
            icon="lightbulb",
            platforms=[
                ComponentPlatform(
                    id="binary",
                    name="Binary (On/Off)",
                    description="Simple on/off light controlled by a GPIO pin.",
                    yaml_template=(
                        "light:\n"
                        "  - platform: binary\n"
                        "    name: {name}\n"
                        "    output: {output_id}\n"
                    ),
                    fields=[
                        _NAME_FIELD,
                        ComponentField(key="output_id", label="Output ID", type="string", required=True),
                    ],
                ),
                ComponentPlatform(
                    id="rgb",
                    name="RGB",
                    description="RGB light with red, green, and blue channels.",
                    yaml_template=(
                        "light:\n"
                        "  - platform: rgb\n"
                        "    name: {name}\n"
                        "    red: {red_id}\n"
                        "    green: {green_id}\n"
                        "    blue: {blue_id}\n"
                    ),
                    fields=[
                        _NAME_FIELD,
                        ComponentField(key="red_id", label="Red Output ID", type="string", required=True),
                        ComponentField(key="green_id", label="Green Output ID", type="string", required=True),
                        ComponentField(key="blue_id", label="Blue Output ID", type="string", required=True),
                    ],
                ),
            ],
        ),
        ComponentType(
            id="button",
            name="Button",
            description="Expose a momentary action as a button entity.",
            docs_url="https://esphome.io/components/button/index.html",
            icon="gesture-tap-button",
            platforms=[
                ComponentPlatform(
                    id="gpio",
                    name="GPIO",
                    description="Trigger a momentary GPIO pulse.",
                    yaml_template=(
                        "button:\n"
                        "  - platform: gpio\n"
                        "    pin: {pin}\n"
                        "    name: {name}\n"
                    ),
                    fields=[_PIN_FIELD, _NAME_FIELD],
                ),
            ],
        ),
        ComponentType(
            id="fan",
            name="Fan",
            description="Control fan speed and direction.",
            docs_url="https://esphome.io/components/fan/index.html",
            icon="fan",
            platforms=[
                ComponentPlatform(
                    id="binary",
                    name="Binary (On/Off)",
                    description="Simple on/off fan.",
                    yaml_template=(
                        "fan:\n"
                        "  - platform: binary\n"
                        "    name: {name}\n"
                        "    output: {output_id}\n"
                    ),
                    fields=[
                        _NAME_FIELD,
                        ComponentField(key="output_id", label="Output ID", type="string", required=True),
                    ],
                ),
            ],
        ),
    ]
)


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
                ComponentField(key="above", label="Above", type="number", required=False),
                ComponentField(key="below", label="Below", type="number", required=False),
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
                ComponentField(key="id", label="Switch ID", type="string", required=True),
            ],
        ),
        AutomationAction(
            id="switch.turn_on",
            name="Turn Switch On",
            description="Turn a switch on.",
            fields=[
                ComponentField(key="id", label="Switch ID", type="string", required=True),
            ],
        ),
        AutomationAction(
            id="switch.turn_off",
            name="Turn Switch Off",
            description="Turn a switch off.",
            fields=[
                ComponentField(key="id", label="Switch ID", type="string", required=True),
            ],
        ),
        AutomationAction(
            id="light.turn_on",
            name="Turn Light On",
            description="Turn a light on, optionally with brightness/colour.",
            fields=[
                ComponentField(key="id", label="Light ID", type="string", required=True),
                ComponentField(key="brightness", label="Brightness (0-1)", type="number", required=False),
            ],
        ),
        AutomationAction(
            id="light.turn_off",
            name="Turn Light Off",
            description="Turn a light off.",
            fields=[
                ComponentField(key="id", label="Light ID", type="string", required=True),
            ],
        ),
        AutomationAction(
            id="delay",
            name="Delay",
            description="Wait for a specified duration.",
            fields=[
                ComponentField(key="duration", label="Duration (e.g. 1s, 500ms)", type="string", required=True),
            ],
        ),
        AutomationAction(
            id="logger.log",
            name="Log Message",
            description="Print a message to the ESPHome log.",
            fields=[
                ComponentField(key="message", label="Message", type="string", required=True),
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
                "wifi:\n"
                "  ssid: {ssid}\n"
                "  password: {password}\n"
            ),
            fields=[
                ComponentField(key="ssid", label="SSID", type="string", required=True, default="!secret wifi_ssid"),
                ComponentField(key="password", label="Password", type="string", required=True, default="!secret wifi_password"),
            ],
        ),
        ConfigSection(
            id="api",
            name="Home Assistant API",
            description="Enable the native ESPHome API for Home Assistant integration.",
            docs_url="https://esphome.io/components/api.html",
            icon="home-assistant",
            yaml_template=(
                "api:\n"
                "  encryption:\n"
                "    key: {encryption_key}\n"
            ),
            fields=[
                ComponentField(key="encryption_key", label="Encryption Key", type="string", required=False, default=""),
            ],
        ),
        ConfigSection(
            id="ota",
            name="OTA Updates",
            description="Allow over-the-air firmware updates.",
            docs_url="https://esphome.io/components/ota/esphome.html",
            icon="update",
            yaml_template=(
                "ota:\n"
                "  - platform: esphome\n"
                "    password: {password}\n"
            ),
            fields=[
                ComponentField(key="password", label="OTA Password", type="string", required=False, default=""),
            ],
        ),
        ConfigSection(
            id="logger",
            name="Logger",
            description="Configure serial logging level and output.",
            docs_url="https://esphome.io/components/logger.html",
            icon="text-box",
            yaml_template=(
                "logger:\n"
                "  level: {level}\n"
            ),
            fields=[
                ComponentField(
                    key="level",
                    label="Log Level",
                    type="select",
                    required=False,
                    default="DEBUG",
                    options=["NONE", "ERROR", "WARN", "INFO", "DEBUG", "VERBOSE", "VERY_VERBOSE"],
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
                ComponentField(key="broker", label="Broker Address", type="string", required=True),
                ComponentField(key="username", label="Username", type="string", required=False, default=""),
                ComponentField(key="password", label="Password", type="string", required=False, default=""),
            ],
        ),
        ConfigSection(
            id="web_server",
            name="Web Server",
            description="Enable the built-in HTTP web server on the device.",
            docs_url="https://esphome.io/components/web_server.html",
            icon="web",
            yaml_template=(
                "web_server:\n"
                "  port: {port}\n"
            ),
            fields=[
                ComponentField(key="port", label="Port", type="number", required=False, default=80),
            ],
        ),
    ]
)
