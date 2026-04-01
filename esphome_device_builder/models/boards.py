"""Board catalog data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from mashumaro.mixins.orjson import DataClassORJSONMixin

from .common import PagedResponse


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
    OPENTHREAD = "openthread"
    CAN = "can"
    MATTER = "matter"
    LORA = "lora"


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
    """Internal: raw board list from definitions loader."""

    boards: list[BoardCatalogEntry]


@dataclass
class PagedBoardsResponse(PagedResponse):
    """Paginated board catalog API response."""

    boards: list[BoardCatalogEntry] = field(default_factory=list)
