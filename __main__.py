"""Entry point: python -m device_builder_backend [options]"""

from __future__ import annotations

import argparse

from .server import run
from .settings import DashboardSettings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Device Builder Dashboard Backend",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "configuration",
        nargs="?",
        default="./configs",
        help="Path to the ESPHome configuration directory (default: ./configs)",
    )
    parser.add_argument("--port", type=int, default=6052, help="HTTP port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Host/IP to bind to")
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
