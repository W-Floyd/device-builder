#!/bin/bash
# Bootstrap script for the ESPHome container / HA add-on.
#
# When the beta toggle is enabled (env var or HA add-on option), this script:
#   1. Installs (or upgrades) the new dashboard + frontend from PyPI
#   2. Launches the new dashboard instead of the legacy `esphome dashboard`
#
# --- Integration into ESPHome HA add-on ---
# The add-on's run script checks the toggle and sources this file:
#
#   if bashio::config.true 'new_dashboard_beta'; then
#       /path/to/install_new_dashboard.sh /config "$@"
#       exit $?
#   fi
#   # ... otherwise run legacy: exec esphome dashboard /config
#
# --- Integration into ESPHome Docker ---
# Set the env var USE_NEW_DASHBOARD=1:
#
#   docker run -e USE_NEW_DASHBOARD=1 esphome/esphome
#
# The ESPHome entrypoint checks this var and delegates here.
# ---------------------------------------------------------------------------

set -e

CONFIG_DIR="${1:-/config}"
shift || true

echo "[new-dashboard] Installing ESPHome Device Builder..."

# Install or upgrade the dashboard backend + frontend.
# --pre allows pre-release versions during the beta phase.
pip install --upgrade --pre \
    esphome-device-builder \
    esphome-device-builder-frontend \
    2>&1 | sed 's/^/[new-dashboard] /'

echo "[new-dashboard] Starting new dashboard on port 6052..."
exec esphome-device-builder "$CONFIG_DIR" --host 0.0.0.0 --port 6052 "$@"
