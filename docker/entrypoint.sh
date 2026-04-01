#!/bin/bash
# Entrypoint for the ESPHome Device Builder container.
#
# This script is used both standalone and as the entrypoint snippet
# injected into the ESPHome container/HA add-on when the beta toggle
# is enabled.

set -e

# ---------------------------------------------------------------------------
# PlatformIO / build cache paths (mirrors ESPHome's own entrypoint)
# ---------------------------------------------------------------------------
if [ -d /cache ]; then
    export PLATFORMIO_GLOBALLIB_DIR=/cache/platformio/lib
    export PLATFORMIO_PLATFORMS_DIR=/cache/platformio/platforms
    export PLATFORMIO_PACKAGES_DIR=/cache/platformio/packages
    export PLATFORMIO_CACHE_DIR=/cache/platformio/cache
fi

if [ -d /build ]; then
    export ESPHOME_BUILD_PATH=/build
fi

# ---------------------------------------------------------------------------
# Launch the dashboard
# ---------------------------------------------------------------------------
exec esphome-device-builder "$@"
