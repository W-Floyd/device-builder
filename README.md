# ESPHome Device Builder — Backend

> **Status: In Development**
> This project is under active development and aimed to replace the [current ESPHome dashboard](https://github.com/esphome/dashboard).

## What is this?

A new dashboard for [ESPHome](https://github.com/esphome/esphome) that provides a guided interface for composing device configurations. Users can explore devices, add components and boards step-by-step, manage automations, and push firmware updates — all without needing to learn YAML.

This repository contains the **backend API server**. The frontend is a separate project: [esphome/device-builder-dashboard-frontend](https://github.com/esphome/device-builder-dashboard-frontend).

## Development

### Setup

Requires [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/esphome/device-builder-dashboard-backend
cd device-builder-dashboard-backend
script/setup
```

### Running

```bash
source .venv/bin/activate
esphome-device-builder ./configs --log-level debug
```

The server starts on `http://localhost:6052`. Use the VS Code debugger (F5 → "Run Server") for breakpoint debugging.

### CLI Options

```
esphome-device-builder [configuration] [options]

  configuration      Path to ESPHome config directory (default: ./configs)
  --port PORT        HTTP port (default: 6052)
  --host HOST        Bind address (default: 0.0.0.0)
  --username USER    Dashboard username
  --password PASS    Dashboard password
  --ha-addon         Running as Home Assistant add-on
  --log-level LEVEL  Log level: debug, info (default), warning, error
  --log-file PATH    Log to rotating file
```

## Architecture

**WebSocket-first API** on `/ws` — 43 commands across 6 controllers, all through a single multiplexed WebSocket with command/response protocol.

```
DeviceBuilder (singleton)
├── controllers/devices.py       — 14 commands: device CRUD, validation, live logs
├── controllers/firmware.py      — 13 commands: job queue, compile, install, download
├── controllers/boards.py        —  3 commands: 559 boards with pin maps
├── controllers/components.py    —  3 commands: 655 components from ESPHome
├── controllers/automations.py   —  3 commands: context-aware triggers + actions
├── controllers/config.py        —  5 commands: version, preferences, secrets
├── api/ws.py                    — /ws WebSocket dispatch
└── api/legacy.py                — HA backward compat (4 endpoints)
```

### Key concepts

- **A device** = a YAML config file on disk. Has `state` (online/offline/unknown via mDNS + ping), `has_pending_changes` (config changed since compile), and `update_available` (ESPHome version mismatch)
- **Device discovery** = mDNS browser for instant online/offline detection, ping sweep every 60s as fallback
- **Board definitions** = YAML manifests in `definitions/boards/`, synced from PlatformIO. 559 boards across 7 platforms (esp32, esp8266, rp2040, bk72xx, rtl87xx, ln882x, nrf52) with pin maps, hardware specs, images
- **Component catalog** = `definitions/components.json`, synced from ESPHome source + docs. 655 components with config entries
- **Firmware jobs** = persistent queue, one at a time. Compile/install/upload. Survive page refresh and server restart
- **Real-time events** = subscribe once, get instant updates. No polling

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full architecture and [docs/API.md](docs/API.md) for the complete API reference with all 43 commands.

### Scripts

The board catalog is a curated set of ~80 popular boards with rich metadata.
To add a new board, create a subfolder under `esphome_device_builder/definitions/boards/`
with a `manifest.yaml`. See [definitions/README.md](esphome_device_builder/definitions/README.md).

```bash
python script/sync_components.py      # Sync components from ESPHome source
python script/validate_definitions.py # Validate all manifests
```

## Board Definitions

Boards live in `esphome_device_builder/definitions/boards/`. Each board is a subfolder with a `manifest.yaml` and optional images. See [definitions/README.md](esphome_device_builder/definitions/README.md) for the schema and contributor guide.

## Contributing

Contributions welcome — especially board definitions (add a subfolder to `definitions/boards/`).

## License

Apache-2.0 — Maintained by [Open Home Foundation](https://www.openhomefoundation.io/).
