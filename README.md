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

This creates a virtualenv, installs the package in editable mode with all dependencies, and sets up pre-commit hooks.

### Running

```bash
source .venv/bin/activate
mkdir -p configs
esphome-device-builder ./configs --verbose
```

The server starts on `http://localhost:6052`. To develop with the frontend, start the frontend dev server in the [frontend repo](https://github.com/esphome/device-builder-dashboard-frontend) — it proxies API calls to port 6052. Or use the VS Code debugger (F5 → "Run Server").

### CLI Options

```
esphome-device-builder [configuration] [options]

  configuration      Path to ESPHome config directory (default: ./configs)
  --port PORT        HTTP port (default: 6052)
  --host HOST        Bind address (default: 0.0.0.0)
  --username USER    Dashboard username
  --password PASS    Dashboard password
  --ha-addon         Running as Home Assistant add-on
  --verbose, -v      Verbose logging
  --log-file PATH    Log to rotating file
```

## Architecture

The backend is a **standalone project** using ESPHome as a dependency. It provides a **WebSocket-first API** on `/ws` — all frontend communication goes through a single multiplexed WebSocket with command/response protocol.

```
DeviceBuilder (singleton)
├── controllers/boards.py        — 501 board definitions with pin maps
├── controllers/components.py    — 655 components from definitions
├── controllers/devices.py       — device CRUD, file scanning, compile/upload/logs
├── controllers/automations.py   — context-aware triggers and actions
├── controllers/config.py        — settings, preferences, secrets, version
├── api/ws.py                    — /ws WebSocket dispatch (31 commands)
└── api/legacy.py                — HA backward compat (4 endpoints)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full architecture and [docs/API.md](docs/API.md) for the complete API reference.

### Key concepts

- **A device** = a YAML config file on disk in the config folder
- **Board definitions** = YAML manifests in `definitions/boards/`, synced from PlatformIO repos
- **Component catalog** = `definitions/components.json`, synced from ESPHome source + docs via script
- **Real-time events** = clients subscribe once via WebSocket, get instant updates on device changes

### Sync scripts

```bash
# Sync board definitions from PlatformIO repos (501 boards)
python script/sync_boards.py

# Sync component definitions from ESPHome source (655 components)
python script/sync_components.py

# Prefill pin data from generic boards + ESPHome named pins
python script/prefill_pins.py
```

## Board Definitions

Boards live in `esphome_device_builder/definitions/boards/`. Each board is a subfolder with a `manifest.yaml` and optional images. See [definitions/README.md](esphome_device_builder/definitions/README.md) for the schema and contributor guide.

## Contributing

Contributions are welcome, especially:

- Board definitions (add a subfolder to `definitions/boards/`)
- Bug reports and feature requests via GitHub Issues

## License

Apache-2.0 — Maintained by [Open Home Foundation](https://www.openhomefoundation.io/).
