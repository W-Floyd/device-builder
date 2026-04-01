# API Reference

Base URL: `http://localhost:6052`

All responses are JSON. WebSocket endpoints are noted.

## Boards

### `GET /boards`

List boards with search, filtering, and pagination.

**Query parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | — | Free-text search (name, description, manufacturer, tags) |
| `platform` | string | — | Filter by platform (`esp32`, `esp8266`, `rp2040`, …) |
| `variant` | string | — | Filter by ESP32 variant (`esp32`, `esp32s3`, `esp32c3`, …) |
| `tag` | string | — | Filter by tag |
| `offset` | int | 0 | Pagination offset |
| `limit` | int | 50 | Page size (max 200) |

**Response:**

```json
{
  "boards": [ { "id": "...", "name": "...", ... } ],
  "total": 123,
  "offset": 0,
  "limit": 50
}
```

### `GET /boards/{board_id}`

Get a single board definition by ID, including pin map.

## Devices

### `GET /devices`

List all configured and importable (adoptable) devices.

### `GET /ping`

Get online/offline state for all devices.

### `POST /wizard`

Create a new device configuration.

**Body:** `{ "name", "ssid", "psk", "type": "basic"|"upload"|"empty", "platform", "board", "board_id" }`

### `PUT /devices/{name}`

Update device metadata (friendly_name, comment, board_id).

### `GET /edit?configuration=NAME.yaml`

Read a device config file.

### `POST /edit?configuration=NAME.yaml`

Write a device config file. **Body:** raw YAML text.

### `POST /delete?configuration=NAME.yaml`

Delete a device and its associated files.

### `POST /import`

Import/adopt a discovered device.

**Body:** `{ "name", "project_name", "package_import_url", "friendly_name", "encryption" }`

### `POST /ignore-device`

Toggle device visibility in the import list.

**Body:** `{ "name", "ignore": true|false }`

## Configuration Editing

### `GET /devices/{config}/section-config?key=SECTION`

Get editable config entries for a YAML section (e.g. `wifi`, `logger`, `api`).

### `POST /devices/{config}/section-config`

Update values in a YAML section.

### `POST /devices/{config}/components`

Add a component to a device config.

**Body:** `{ "component", "platform", "fields": { ... } }`

### `POST /devices/{config}/automations`

Add an automation to a device config.

**Body:** `{ "target_component_name", "trigger", "actions": [ ... ] }`

### `POST /devices/{config}/config-sections`

Add a config section (wifi, api, ota, etc.) to a device config.

**Body:** `{ "section", "fields": { ... } }`

## Catalogs

### `GET /components/catalog`

List all available component types with their platforms and fields.

### `GET /automations/catalog`

List all automation triggers and actions.

### `GET /config/catalog`

List all config section templates (wifi, api, ota, logger, …).

## Operations (WebSocket)

All operation endpoints use WebSocket. The client sends a spawn message, then receives streaming output.

**Protocol:**

```
→  { "type": "spawn", "configuration": "device.yaml", "port": "/dev/ttyUSB0" }
←  { "event": "line", "data": "Compiling...\n" }
←  { "event": "line", "data": "Done.\n" }
←  { "event": "exit", "code": 0 }
```

### `GET /compile` (WebSocket)

Compile device firmware.

### `GET /upload` (WebSocket)

Upload firmware to device. Supports `port` in spawn message.

### `GET /logs` (WebSocket)

Stream device logs. Supports `port` in spawn message.

### `GET /validate` (WebSocket)

Validate a device YAML config.

### `GET /clean` (WebSocket)

Clean build artifacts.

### `GET /rename` (WebSocket)

Rename a device. Spawn message includes `newName`.

### `POST /update-all`

Trigger OTA update for all online devices (fire-and-forget).

## Real-time Events (WebSocket)

### `GET /events` (WebSocket)

Subscribe to real-time dashboard state changes.

**Events sent by server:**

| Event | Description |
|-------|-------------|
| `INITIAL_STATE` | Full device list + ping status on connect |
| `ENTRY_ADDED` | New config file detected |
| `ENTRY_REMOVED` | Config file deleted |
| `ENTRY_UPDATED` | Config file modified |
| `ENTRY_STATE_CHANGED` | Device online/offline state changed |
| `IMPORTABLE_DEVICE_ADDED` | New adoptable device discovered |
| `IMPORTABLE_DEVICE_REMOVED` | Adoptable device disappeared |

## Utilities

### `GET /version`

ESPHome version. Response: `{ "version": "2024.x.x" }`

### `GET /serial-ports`

List available serial ports.

### `GET /secret_keys`

List keys from `secrets.yaml`.

### `GET /preferences`

Get user preferences.

### `PUT /preferences`

Save user preferences. **Body:** `{ "editor_layout": "both"|"left"|"right" }`

### `GET /info?configuration=NAME.yaml`

Get compiled device metadata (address, versions, integrations).

### `GET /json-config?configuration=NAME.yaml`

Get parsed YAML config as JSON.

### `GET /downloads?configuration=NAME.yaml`

List available firmware binaries for download.

### `GET /download.bin?configuration=NAME.yaml&file=TYPE`

Download a compiled firmware binary.
