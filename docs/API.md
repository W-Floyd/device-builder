# API Reference

Base URL: `http://localhost:6052`

## WebSocket API (`/ws`)

The primary API. A single multiplexed WebSocket handles all 43 commands.

### Protocol

**Connect:** `ws://localhost:6052/ws`

On connect, the server sends a [`ServerInfoMessage`](../esphome_device_builder/models/api.py):
```json
{"server_version": "0.0.0", "esphome_version": "2026.3.1"}
```

**Send a [`CommandMessage`](../esphome_device_builder/models/api.py):**
```json
{"command": "devices/list", "message_id": "1", "args": {}}
```

**Receive a [`ResultMessage`](../esphome_device_builder/models/api.py):**
```json
{"message_id": "1", "result": { ... }}
```

**Streaming output ([`EventMessage`](../esphome_device_builder/models/api.py)):**
```json
{"message_id": "1", "event": "output", "data": "Compiling...\n"}
{"message_id": "1", "event": "result", "data": {"success": true, "code": 0}}
```

**Error ([`ErrorMessage`](../esphome_device_builder/models/api.py)):**
```json
{"message_id": "1", "error_code": "unknown_command", "details": "..."}
```

### Error Codes ([`ErrorCode`](../esphome_device_builder/models/api.py))

| Code | Description |
|------|-------------|
| `invalid_message` | Malformed JSON or missing fields |
| `unknown_command` | Command not found |
| `invalid_args` | Missing or invalid arguments |
| `not_found` | Resource not found |
| `internal_error` | Server error |

---

## Commands

### Devices (14 commands)

> Models: [`Device`](../esphome_device_builder/models/devices.py), [`DevicesResponse`](../esphome_device_builder/models/devices.py)
>
> Controller: [`DevicesController`](../esphome_device_builder/controllers/devices.py)

| Command | Args | Response | Description |
|---------|------|----------|-------------|
| `devices/list` | — | `DevicesResponse` | List configured + importable devices |
| `devices/get_states` | — | `dict` | Get device online/offline states |
| `devices/create` | `{name, board_id, config_type?, ssid?, psk?, file_content?}` | `WizardResponse` | Create device from board definition |
| `devices/update` | `{name, friendly_name?, comment?, board_id?}` | `UpdateDeviceResponse` | Update device metadata |
| `devices/rename` | `{configuration, new_name}` | — | Rename device via ESPHome CLI |
| `devices/delete` | `{configuration}` | — | Delete device and associated files |
| `devices/delete_bulk` | `{configurations: string[]}` | `[{configuration, success, error?}]` | Delete multiple devices |
| `devices/get_config` | `{configuration}` | `string` | Read device YAML config |
| `devices/update_config` | `{configuration, content}` | — | Write device YAML config |
| `devices/add_component` | `{configuration, component_id, fields?, sub_entities?}` | `AddComponentResponse` | Add component to device config |
| `devices/import` | `{name, project_name?, package_import_url?, ...}` | `dict` | Import/adopt discovered device |
| `devices/ignore` | `{name, ignore?}` | — | Toggle device visibility |
| `devices/validate` | `{configuration}` | Streaming | Validate YAML config |
| `devices/logs` | `{configuration, port?}` | Streaming | Stream live device logs |

`Device.has_pending_changes`: `true` = YAML newer than compiled binary, `false` = up to date, `null` = never compiled.

### Firmware (13 commands)

> Models: [`FirmwareJob`](../esphome_device_builder/models/firmware.py), [`JobStatus`](../esphome_device_builder/models/firmware.py), [`JobType`](../esphome_device_builder/models/firmware.py)
>
> Controller: [`FirmwareController`](../esphome_device_builder/controllers/firmware.py)

| Command | Args | Response | Description |
|---------|------|----------|-------------|
| `firmware/compile` | `{configuration}` | `FirmwareJob` | Queue compile job |
| `firmware/upload` | `{configuration, port?}` | `FirmwareJob` | Queue upload of existing binary |
| `firmware/install` | `{configuration, port?: "OTA"}` | `FirmwareJob` | Queue compile + upload |
| `firmware/clean` | `{configuration}` | `FirmwareJob` | Queue build clean |
| `firmware/compile_bulk` | `{configurations: string[]}` | `[FirmwareJob]` | Queue multiple compiles |
| `firmware/install_bulk` | `{configurations: string[], port?: "OTA"}` | `[FirmwareJob]` | Queue multiple installs |
| `firmware/get_jobs` | `{status?, configuration?}` | `[FirmwareJob]` | List jobs with filters |
| `firmware/get_job` | `{job_id}` | `FirmwareJob` | Get job with full output |
| `firmware/follow_job` | `{job_id}` | Streaming | Historical output + live stream |
| `firmware/get_binaries` | `{configuration}` | `[{title, file}]` | List compiled firmware files |
| `firmware/download` | `{configuration, file, compressed?}` | `{filename, data, size}` | Download binary (base64) |
| `firmware/cancel` | `{job_id}` | — | Cancel queued job |
| `firmware/clear` | `{status?}` | — | Remove finished jobs |

**Job queue**: one job runs at a time, others wait. Jobs persist across server restarts. Output buffered in `FirmwareJob.output` — clients can reconnect via `firmware/follow_job`.

**Job events** (broadcast to all subscribed clients):
- `job_queued`, `job_started`, `job_output`, `job_completed`, `job_failed`

### Boards (3 commands)

> Controller: [`BoardCatalog`](../esphome_device_builder/controllers/boards.py)
>
> Enums: [`Platform`](../esphome_device_builder/models/boards.py), [`Esp32Variant`](../esphome_device_builder/models/boards.py), [`BoardTag`](../esphome_device_builder/models/boards.py)

| Command | Args | Response | Description |
|---------|------|----------|-------------|
| `boards/get_boards` | `{query?, platform?, variant?, tag?, offset?, limit?}` | `PagedBoardsResponse` | Search/list boards |
| `boards/get_board` | `{board_id}` | `BoardCatalogEntry` | Get board with pin map |

### Components (3 commands)

> Controller: [`ComponentCatalog`](../esphome_device_builder/controllers/components.py)
>
> Enums: [`ComponentCategory`](../esphome_device_builder/models/components.py), [`ConfigEntryType`](../esphome_device_builder/models/common.py)

| Command | Args | Response | Description |
|---------|------|----------|-------------|
| `components/get_categories` | — | `[{id, name, count}]` | List categories with counts |
| `components/get_components` | `{query?, category?, offset?, limit?}` | `PagedComponentsResponse` | Search/list components |
| `components/get_component` | `{component_id}` | `ComponentCatalogEntry` | Get component with config entries |

### Automations (3 commands)

> Controller: [`AutomationsController`](../esphome_device_builder/controllers/automations.py)

| Command | Args | Response | Description |
|---------|------|----------|-------------|
| `automations/get_triggers` | `{platform_type?}` | `[AutomationTrigger]` | List triggers by platform type |
| `automations/get_actions` | — | `[AutomationAction]` | List all actions |
| `automations/get_available` | `{configuration}` | `{triggers, actions, present_platform_types}` | Context-aware for a device |

### Config (5 commands)

> Controller: [`ConfigController`](../esphome_device_builder/controllers/config.py)
>
> Models: [`UserPreferences`](../esphome_device_builder/models/preferences.py)

| Command | Args | Response | Description |
|---------|------|----------|-------------|
| `config/version` | — | `{server_version, esphome_version}` | Get versions |
| `config/serial_ports` | — | `[{port, desc}]` | List serial ports |
| `config/get_preferences` | — | `UserPreferences` | Get user preferences |
| `config/set_preferences` | `{theme?, dashboard_view?, ...}` | `UserPreferences` | Update preferences (partial) |
| `config/get_secrets` | — | `[string]` | List secret key names |

### Utility (2 commands)

| Command | Args | Response | Description |
|---------|------|----------|-------------|
| `ping` | — | `{pong: true}` | Health check |
| `subscribe_events` | — | Streaming | Subscribe to real-time events |

**`subscribe_events` events:**
- `device_added`, `device_removed`, `device_updated`, `device_state_changed`
- `importable_device_added`, `importable_device_removed`
- `job_queued`, `job_started`, `job_output`, `job_completed`, `job_failed`

---

## Legacy REST Endpoints (Deprecated)

For Home Assistant ESPHome integration backward compat only.

| Endpoint | Description |
|----------|-------------|
| `GET /devices` | List devices |
| `GET /json-config?configuration=...` | Get parsed YAML as JSON |
| `GET /compile` (WebSocket) | Compile via spawn protocol |
| `GET /upload` (WebSocket) | Upload via spawn protocol |
