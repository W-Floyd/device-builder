"""Entry point: python -m esphome_device_builder [options]."""

from __future__ import annotations

import argparse

from .const import DEFAULT_HOST, DEFAULT_PORT
from .server import run
from .settings import DashboardSettings


def main() -> None:
    """Run the ESPHome Device Builder."""
    parser = argparse.ArgumentParser(
        description="ESPHome Device Builder",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "configuration",
        nargs="?",
        default="./configs",
        help="Path to the ESPHome configuration directory",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP port to listen on")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host/IP to bind to")
    parser.add_argument("--username", default="", help="Dashboard username")
    parser.add_argument("--password", default="", help="Dashboard password")
    parser.add_argument("--ha-addon", action="store_true", help="Running as HA add-on")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    settings = DashboardSettings()
    settings.parse_args(args)

    run(settings)


if __name__ == "__main__":
    main()
