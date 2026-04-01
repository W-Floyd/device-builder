"""Dataclass models matching the TypeScript API interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mashumaro.mixins.orjson import DataClassORJSONMixin

# ---------------------------------------------------------------------------
# Device models
# ---------------------------------------------------------------------------


@dataclass
class ConfiguredDevice(DataClassORJSONMixin):
    name: str
    friendly_name: str
    configuration: str
    path: str
    comment: str | None
    address: str
    web_port: int | None
    target_platform: str
    current_version: str
    deployed_version: str
    loaded_integrations: list[str]
    board_id: str = ""


@dataclass
class AdoptableDevice(DataClassORJSONMixin):
    name: str
    friendly_name: str
    package_import_url: str
    project_name: str
    project_version: str
    network: str
    ignored: bool


@dataclass
class DevicesResponse(DataClassORJSONMixin):
    configured: list[ConfiguredDevice]
    importable: list[AdoptableDevice]


@dataclass
class WizardRequest(DataClassORJSONMixin):
    name: str
    ssid: str
    psk: str
    type: str  # "basic" | "upload" | "empty"
    platform: str | None = None
    board: str | None = None
    password: str | None = None
    file_content: str | None = None
    board_id: str | None = None


@dataclass
class WizardResponse(DataClassORJSONMixin):
    configuration: str


@dataclass
class UpdateDeviceRequest(DataClassORJSONMixin):
    friendly_name: str | None = None
    comment: str | None = None
    board_id: str | None = None


@dataclass
class UpdateDeviceResponse(DataClassORJSONMixin):
    name: str
    friendly_name: str
    comment: str | None
    board_id: str | None


@dataclass
class ImportRequest(DataClassORJSONMixin):
    name: str
    project_name: str
    package_import_url: str
    friendly_name: str | None = None
    encryption: str | None = None


@dataclass
class IgnoreDeviceRequest(DataClassORJSONMixin):
    name: str
    ignore: bool


# ---------------------------------------------------------------------------
# Board enums
# ---------------------------------------------------------------------------


class PinFeature(StrEnum):
    """Known GPIO pin features/capabilities."""

    ADC = "adc"
    DAC = "dac"
    TOUCH = "touch"
    PWM = "pwm"
    I2C_SDA = "i2c_sda"
    I2C_SCL = "i2c_scl"
    SPI_MOSI = "spi_mosi"
    SPI_MISO = "spi_miso"
    SPI_CLK = "spi_clk"
    SPI_CS = "spi_cs"
    UART_TX = "uart_tx"
    UART_RX = "uart_rx"
    USB_DP = "usb_dp"
    USB_DM = "usb_dm"
    RGB_LED = "rgb_led"
    JTAG = "jtag"
    STRAPPING = "strapping"
    INPUT_ONLY = "input_only"
    BOOT_BUTTON = "boot_button"


class Connectivity(StrEnum):
    """Known connectivity types."""

    WIFI = "wifi"
    BLUETOOTH = "bluetooth"
    ETHERNET = "ethernet"
    ZIGBEE = "zigbee"
    THREAD = "thread"
    CAN = "can"
    MATTER = "matter"


class Platform(StrEnum):
    """ESPHome target platforms."""

    ESP32 = "esp32"
    ESP8266 = "esp8266"
    RP2040 = "rp2040"
    BK72XX = "bk72xx"
    RTL87XX = "rtl87xx"
    LN882X = "ln882x"


class Esp32Variant(StrEnum):
    """ESP32 chip variants."""

    ESP32 = "esp32"
    ESP32S2 = "esp32s2"
    ESP32S3 = "esp32s3"
    ESP32C2 = "esp32c2"
    ESP32C3 = "esp32c3"
    ESP32C5 = "esp32c5"
    ESP32C6 = "esp32c6"
    ESP32C61 = "esp32c61"
    ESP32H2 = "esp32h2"
    ESP32P4 = "esp32p4"


class BoardTag(StrEnum):
    """Board tags for unique features not captured by other fields."""

    # Form factor
    COMPACT = "compact"
    DEV_KIT = "dev-kit"
    STARTER_KIT = "starter-kit"
    MODULE = "module"
    BREAKOUT = "breakout"

    # Onboard peripherals
    DISPLAY = "display"
    CAMERA = "camera"
    RGB_LED = "rgb-led"
    RELAY = "relay"
    MOTOR_DRIVER = "motor-driver"
    SD_CARD = "sd-card"
    MICROPHONE = "microphone"
    SPEAKER = "speaker"
    IMU = "imu"

    # Power / connectivity
    LIPO = "lipo"
    POE = "poe"
    USB_C = "usb-c"
    EXTERNAL_ANTENNA = "external-antenna"
    SOLAR = "solar"
    BATTERY = "battery"

    # Ecosystem / OEM
    SONOFF = "sonoff"
    TUYA = "tuya"
    SHELLY = "shelly"


# ---------------------------------------------------------------------------
# Board models
# ---------------------------------------------------------------------------


@dataclass
class BoardPin(DataClassORJSONMixin):
    """A single GPIO pin on a board."""

    gpio: int
    label: str = ""
    features: list[PinFeature] = field(default_factory=list)
    available: bool | None = None  # True=exposed, False=internal, None=unknown
    occupied_by: str | None = None  # e.g. "Built-in LED", "SPI Flash"
    notes: str | None = None


@dataclass
class BoardEsphomeConfig(DataClassORJSONMixin):
    """Maps this board to an ESPHome YAML platform configuration."""

    platform: Platform
    board: str  # PlatformIO board ID
    variant: Esp32Variant | None = None
    framework: str | None = None  # "arduino" or "esp-idf"


@dataclass
class BoardHardware(DataClassORJSONMixin):
    """Hardware specifications of a board."""

    flash_size: str | None = None
    ram_size: int | None = None
    cpu_frequency: str | None = None
    connectivity: list[Connectivity] = field(default_factory=list)


@dataclass
class BoardCatalogEntry(DataClassORJSONMixin):
    """A board definition in the catalog."""

    id: str
    name: str
    description: str
    manufacturer: str
    esphome: BoardEsphomeConfig
    hardware: BoardHardware = field(default_factory=BoardHardware)
    images: list[str] = field(default_factory=list)
    tags: list[BoardTag] = field(default_factory=list)
    pins: list[BoardPin] = field(default_factory=list)
    docs_url: str = ""
    product_url: str = ""
    featured: bool = False
    is_generic: bool = False


@dataclass
class BoardCatalogResponse(DataClassORJSONMixin):
    boards: list[BoardCatalogEntry]


# ---------------------------------------------------------------------------
# Component models
# ---------------------------------------------------------------------------


@dataclass
class ComponentField(DataClassORJSONMixin):
    key: str
    label: str
    type: str  # "string" | "number" | "boolean" | "select" | "pin"
    required: bool
    default: str | int | bool | None = None
    options: list[str] | None = None


@dataclass
class ConfigValueOption(DataClassORJSONMixin):
    label: str
    value: str


@dataclass
class ConfigEntry(DataClassORJSONMixin):
    """A rich configuration entry for visual editing of YAML sections."""

    key: str
    # boolean, string, secure_string, integer, float, label, divider, select, icon, alert
    type: str
    label: str
    default_value: str | int | float | bool | None = None
    required: bool = False
    options: list[ConfigValueOption] | None = None
    range: list[int | float] | None = None
    description: str | None = None
    help_link: str | None = None
    multi_value: bool = False
    hidden: bool = False
    value: str | int | float | bool | list[str] | None = None


@dataclass
class SectionConfigResponse(DataClassORJSONMixin):
    """Response from GET /devices/{config}/section-config."""

    section_key: str
    section_type: str  # "core" | "component" | "automation"
    title: str
    description: str
    docs_url: str
    icon: str
    entries: list[ConfigEntry]


@dataclass
class ComponentPlatform(DataClassORJSONMixin):
    id: str
    name: str
    description: str
    yaml_template: str
    fields: list[ComponentField]


@dataclass
class ComponentType(DataClassORJSONMixin):
    id: str
    name: str
    description: str
    docs_url: str
    icon: str
    platforms: list[ComponentPlatform]


@dataclass
class ComponentCatalogResponse(DataClassORJSONMixin):
    components: list[ComponentType]


@dataclass
class AddComponentRequest(DataClassORJSONMixin):
    component: str
    platform: str
    fields: dict[str, Any]


@dataclass
class AddComponentResponse(DataClassORJSONMixin):
    yaml: str


# ---------------------------------------------------------------------------
# Automation models
# ---------------------------------------------------------------------------


@dataclass
class AutomationTrigger(DataClassORJSONMixin):
    id: str
    name: str
    description: str
    applicable_to: list[str]
    fields: list[ComponentField]


@dataclass
class AutomationAction(DataClassORJSONMixin):
    id: str
    name: str
    description: str
    fields: list[ComponentField]


@dataclass
class AutomationCatalogResponse(DataClassORJSONMixin):
    triggers: list[AutomationTrigger]
    actions: list[AutomationAction]


@dataclass
class AutomationActionCall(DataClassORJSONMixin):
    action: str
    fields: dict[str, Any]


@dataclass
class AddAutomationRequest(DataClassORJSONMixin):
    target_component_name: str
    trigger: str
    actions: list[AutomationActionCall]


@dataclass
class AddAutomationResponse(DataClassORJSONMixin):
    yaml: str


# ---------------------------------------------------------------------------
# Config section models
# ---------------------------------------------------------------------------


@dataclass
class ConfigSection(DataClassORJSONMixin):
    id: str
    name: str
    description: str
    docs_url: str
    icon: str
    yaml_template: str
    fields: list[ComponentField]


@dataclass
class ConfigCatalogResponse(DataClassORJSONMixin):
    sections: list[ConfigSection]


@dataclass
class AddConfigSectionRequest(DataClassORJSONMixin):
    section: str
    fields: dict[str, Any]


@dataclass
class AddConfigSectionResponse(DataClassORJSONMixin):
    yaml: str


# ---------------------------------------------------------------------------
# Utility models
# ---------------------------------------------------------------------------


@dataclass
class VersionResponse(DataClassORJSONMixin):
    version: str


@dataclass
class SerialPort(DataClassORJSONMixin):
    port: str
    desc: str


@dataclass
class DownloadItem(DataClassORJSONMixin):
    title: str
    file: str


@dataclass
class UserPreferences(DataClassORJSONMixin):
    editor_layout: str = "both"  # "both" | "left" | "right"
