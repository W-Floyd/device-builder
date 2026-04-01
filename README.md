# ESPHome Device Builder — Backend

> **Status: In Development**
> This project is under active development and aimed to replace the [current ESPHome dashboard](https://github.com/esphome/dashboard) soon.

## What is this?

A new dashboard for [ESPHome](https://github.com/esphome/esphome) that goes beyond a YAML editor by providing a guided interface for composing device configurations. Users can explore devices, add components and boards step-by-step, manage automations, and push firmware updates — all without needing to learn YAML.

### Running

```bash
source .venv/bin/activate

# Create a config directory for device YAML files
mkdir -p configs

# Start the API server
esphome-device-builder ./configs --verbose
```

The server starts on `http://localhost:6052`. To develop with the frontend, start the frontend dev server in the [frontend repo](https://github.com/esphome/device-builder-dashboard-frontend) — it proxies API calls to port 6052.

### CLI Options

```
esphome-device-builder [configuration] [options]

positional arguments:
  configuration      Path to ESPHome config directory (default: ./configs)

options:
  --port PORT        HTTP port (default: 6052)
  --host HOST        Bind address (default: 0.0.0.0)
  --username USER    Dashboard username
  --password PASS    Dashboard password
  --ha-addon         Running as Home Assistant add-on
  --verbose, -v      Verbose logging
```

## Development

### Setup

Requires [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/esphome/device-builder-dashboard-backend
cd device-builder-dashboard-backend
script/setup
```

This creates a virtualenv, installs the package in editable mode with all dependencies, and sets up pre-commit hooks.

## Architecture

The dashboard is a **standalone project** that uses ESPHome as a dependency:

- **Operations** (compile, upload, logs) shell out to the `esphome` CLI as subprocesses
- **Data access** (device metadata, board definitions, serial ports) uses ESPHome Python imports
- **ESPHome is an optional dependency** — use `pip install .[esphome]` for standalone installs, or plain `pip install .` when esphome is already present (e.g. inside the ESPHome container)

The [frontend](https://github.com/esphome/device-builder-dashboard-frontend) is built separately and distributed as a Python wheel. The backend try-imports it and serves the static files automatically. During development, the frontend dev server connects directly to the backend API on port 6052.

### Board & Component Definitions

Boards and components live in `esphome_device_builder/definitions/`. Each board or component gets its own subfolder with a `manifest.yaml` and optional assets (e.g. images):

```
definitions/
├── boards/
│   ├── esp32-devkit-v1/
│   │   ├── manifest.yaml
│   │   └── image.png         (optional)
│   └── ...
└── components/
    ├── binary_sensor/
    │   └── manifest.yaml
    └── ...
```

Adding a new board or component = adding a subfolder with a `manifest.yaml`. See any existing manifest for the schema.

## Contributing

Contributions are welcome, especially:

- Board definitions (add a subfolder to `definitions/boards/`)
- Component definitions (add a subfolder to `definitions/components/`)
- Bug reports and feature requests via GitHub Issues

## License

Apache-2.0 — Maintained by [Open Home Foundation](https://www.openhomefoundation.io/).
