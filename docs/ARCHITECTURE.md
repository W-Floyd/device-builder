# Architecture

## Principles

1. **ESPHome is a CLI tool.** Operations (compile, upload, logs) shell out to `python -m esphome <command>`. Data access (device metadata, board definitions, serial ports) uses ESPHome Python imports.

2. **ESPHome is an optional dependency.** `pip install .[esphome]` pulls it in for standalone use. Plain `pip install .` works when esphome is already present (e.g. inside the ESPHome container).

3. **Frontend and backend are separate repos.** The frontend is published as a pip package (`esphome-device-builder-frontend`). The backend try-imports it and serves the static files. During development, the frontend dev server proxies API calls to the backend.

4. **Backward compatible.** The `esphome/dashboard-api` client (used by Home Assistant) must keep working. All endpoints it calls are preserved.

## ESPHome Interaction

| What | How | Why |
|------|-----|-----|
| Compile, upload, logs, validate, clean | Subprocess (`python -m esphome`) | Streaming output over WebSocket |
| Device metadata (StorageJSON) | Python import | Direct file access, no CLI equivalent |
| Board definitions per platform | Python import | ESPHome's built-in BOARDS dicts |
| Serial port listing | Python import | `esphome.util.get_serial_ports` |
| Device import/adoption | Python import | `esphome.config_helpers.import_config` |

## Frontend Distribution

- The frontend repo builds to a Python package containing static files and a `where()` function
- Can be built via GitHub Actions and distributed as a wheel artifact — no PyPI needed during development
- The backend detects it at startup; if absent, runs in API-only mode (for development)

## Deployment Strategy

### Beta Phase

The existing ESPHome HA add-on and Docker image get an opt-in toggle to try the new dashboard:

**HA add-on** — a config toggle `new_dashboard_beta: true`:
```
# add-on run script checks the toggle:
if bashio::config.true 'new_dashboard_beta'; then
    pip install --pre esphome-device-builder esphome-device-builder-frontend
    exec esphome-device-builder /config --host 0.0.0.0 --port 6052
fi
# otherwise: exec esphome dashboard /config
```

**Docker** — an env var `USE_NEW_DASHBOARD=1`:
```bash
docker run -e USE_NEW_DASHBOARD=1 esphome/esphome
```

Both use `docker/install_new_dashboard.sh` which pip-installs the latest pre-release and starts the new dashboard. Users flip the toggle back to return to the legacy dashboard instantly.

### Production

Remove the toggle. The ESPHome container ships with the new dashboard baked in. The entrypoint runs `esphome-device-builder` instead of `esphome dashboard`. The old `esphome/dashboard` module is deprecated.

## Board & Component Definitions

Both are individual YAML files in their respective package directories. A loader reads all `*.yaml` files at startup. Adding a board or component = adding a YAML file. These directories can later be extracted into separate community-contributed repositories.
