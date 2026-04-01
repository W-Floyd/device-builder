# API Reference

Base URL: `http://localhost:6052`

## WebSocket API (`/ws`)

The primary API. A single multiplexed WebSocket handles all commands.

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

### Devices

> Models: [`Device`](../esphome_device_builder/models/devices.py), [`DevicesResponse`](../esphome_device_builder/models/devices.py), [`WizardResponse`](../esphome_device_builder/models/devices.py), [`UpdateDeviceResponse`](../esphome_device_builder/models/devices.py)
>
> Controller: [`DevicesController`](../esphome_device_builder/controllers/devices.py)

| Command | Args | Response | Description |
|---------|------|----------|-------------|
| `devices/list` | — | `DevicesResponse` | List configured + importable devices |
| `devices/get_states` | — | `dict` | Get device online/offline states |
| `devices/create` | `{name, config_type?, platform?, board?, ssid?, psk?, password?, file_content?, board_id?}` | `WizardResponse` | Create new device config |
| `devices/update` | `{name, friendly_name?, comment?, board_id?}` | `UpdateDeviceResponse` | Update device metadata |
| `devices/rename` | `{configuration, new_name}` | — | Rename device via ESPHome CLI |
| `devices/delete` | `{configuration}` | — | Delete device and associated files |
| `devices/get_config` | `{configuration}` | `string` | Read device YAML config |
| `devices/update_config` | `{configuration, content}` | — | Write device YAML config |
| `devices/add_component` | `{configuration, component_id, fields?, sub_entities?}` | `AddComponentResponse` | Add component to device config |
| `devices/import` | `{name, project_name?, package_import_url?, friendly_name?, encryption?}` | `dict` | Import/adopt discovered device |
| `devices/ignore` | `{name, ignore?}` | — | Toggle device visibility in import list |
| `devices/compile` | `{configuration}` | Streaming | Compile device firmware |
| `devices/upload` | `{configuration, port?}` | Streaming | Upload firmware to device |
| `devices/logs` | `{configuration, port?}` | Streaming | Stream device logs |
| `devices/validate` | `{configuration}` | Streaming | Validate YAML config |
| `devices/clean` | `{configuration}` | Streaming | Clean build files |

### Boards

> Models: [`BoardCatalogEntry`](../esphome_device_builder/models/boards.py), [`PagedBoardsResponse`](../esphome_device_builder/models/boards.py)
>
> Controller: [`BoardCatalog`](../esphome_device_builder/controllers/boards.py)
>
> Enums: [`Platform`](../esphome_device_builder/models/boards.py), [`Esp32Variant`](../esphome_device_builder/models/boards.py), [`BoardTag`](../esphome_device_builder/models/boards.py)

| Command | Args | Response | Description |
|---------|------|----------|-------------|
| `boards/get_boards` | `{query?, platform?, variant?, tag?, offset?, limit?}` | `PagedBoardsResponse` | Search/list boards |
| `boards/get_board` | `{board_id}` | `BoardCatalogEntry` | Get single board with pin map |

**`boards/get_boards` args detail:**

| Arg | Type | Description |
|-----|------|-------------|
| `query` | `string` | Free-text search across name, description, manufacturer, id, tags |
| `platform` | [`Platform`](../esphome_device_builder/models/boards.py) | Filter: `esp32`, `esp8266`, `rp2040`, `bk72xx`, `rtl87xx`, `ln882x` |
| `variant` | [`Esp32Variant`](../esphome_device_builder/models/boards.py) | Filter: `esp32`, `esp32s2`, `esp32s3`, `esp32c3`, `esp32c6`, `esp32h2`, ... |
| `tag` | [`BoardTag`](../esphome_device_builder/models/boards.py) | Filter: `compact`, `dev-kit`, `starter-kit`, `display`, `poe`, `usb-c`, ... |
| `offset` | `int` | Pagination offset (default: 0) |
| `limit` | `int` | Page size (default: 50, max: 200) |

### Components

> Models: [`ComponentCatalogEntry`](../esphome_device_builder/models/components.py), [`PagedComponentsResponse`](../esphome_device_builder/models/components.py), [`ConfigEntry`](../esphome_device_builder/models/common.py)
>
> Controller: [`ComponentCatalog`](../esphome_device_builder/controllers/components.py)
>
> Enums: [`ComponentCategory`](../esphome_device_builder/models/components.py), [`ConfigEntryType`](../esphome_device_builder/models/common.py)

| Command | Args | Response | Description |
|---------|------|----------|-------------|
| `components/get_categories` | — | `[{id, name, count}]` | List component categories with counts |
| `components/get_components` | `{query?, category?, offset?, limit?}` | `PagedComponentsResponse` | Search/list components |
| `components/get_component` | `{component_id}` | `ComponentCatalogEntry` | Get component with config entries |

**`components/get_components` args detail:**

| Arg | Type | Description |
|-----|------|-------------|
| `query` | `string` | Free-text search across name, description, id |
| `category` | [`ComponentCategory`](../esphome_device_builder/models/components.py) | Filter: `sensor`, `binary_sensor`, `switch`, `light`, `climate`, `core`, `bus`, ... |
| `offset` | `int` | Pagination offset (default: 0) |
| `limit` | `int` | Page size (default: 50, max: 200) |

### Automations

> Models: [`AutomationTrigger`](../esphome_device_builder/controllers/automations.py), [`AutomationAction`](../esphome_device_builder/controllers/automations.py)
>
> Controller: [`AutomationsController`](../esphome_device_builder/controllers/automations.py)

| Command | Args | Response | Description |
|---------|------|----------|-------------|
| `automations/get_triggers` | `{platform_type?}` | `[AutomationTrigger]` | List triggers, optionally filtered by platform type |
| `automations/get_actions` | — | `[AutomationAction]` | List all available actions |
| `automations/get_available` | `{configuration}` | `{triggers, actions, present_platform_types}` | Context-aware: returns triggers + actions for a specific device based on its config |

**`automations/get_available` flow:**
1. Reads the device YAML config
2. Detects which platform types are present (binary_sensor, sensor, switch, etc.)
3. Returns device-level triggers (on_boot, on_shutdown) + component-level triggers matching present types + all actions

### Config

> Controller: [`ConfigController`](../esphome_device_builder/controllers/config.py)

| Command | Args | Response | Description |
|---------|------|----------|-------------|
| `config/version` | — | `{server_version, esphome_version}` | Get versions |
| `config/serial_ports` | — | `[{port, desc}]` | List serial ports |
| `config/get_preferences` | — | `dict` | Get user preferences |
| `config/set_preferences` | `{...prefs}` | `dict` | Update user preferences |
| `config/get_secrets` | — | `[string]` | List secret key names |
| `config/get_info` | `{configuration}` | `dict` | Get compiled device metadata |

### Utility

| Command | Args | Response | Description |
|---------|------|----------|-------------|
| `ping` | — | `{pong: true}` | Health check |
| `subscribe_events` | — | Streaming [`EventType`](../esphome_device_builder/models/common.py) | Subscribe to real-time device events |

**`subscribe_events` flow:**
1. Immediately sends `initial_state` event with current device list
2. Confirms with `{subscribed: true}` result
3. Pushes events as they happen — no polling needed:
   - `device_added` — new config file detected
   - `device_removed` — config file deleted
   - `device_updated` — config file modified
   - `device_state_changed` — device online/offline state changed
   - `importable_device_added` / `importable_device_removed` — discovered devices

---

## Models

All models are dataclasses with mashumaro `DataClassORJSONMixin` for serialization.

| Model | File | Description |
|-------|------|-------------|
| `Device` | [`models/devices.py`](../esphome_device_builder/models/devices.py) | A configured ESPHome device |
| `AdoptableDevice` | [`models/devices.py`](../esphome_device_builder/models/devices.py) | A discoverable device for import |
| `DevicesResponse` | [`models/devices.py`](../esphome_device_builder/models/devices.py) | List of configured + importable devices |
| `BoardCatalogEntry` | [`models/boards.py`](../esphome_device_builder/models/boards.py) | Board with hardware specs, pins, images |
| `BoardPin` | [`models/boards.py`](../esphome_device_builder/models/boards.py) | GPIO pin with features and availability |
| `PagedBoardsResponse` | [`models/boards.py`](../esphome_device_builder/models/boards.py) | Paginated board list |
| `ComponentCatalogEntry` | [`models/components.py`](../esphome_device_builder/models/components.py) | Component with config entries |
| `ComponentSubEntity` | [`models/components.py`](../esphome_device_builder/models/components.py) | Sub-entity (e.g. DHT temperature) |
| `PagedComponentsResponse` | [`models/components.py`](../esphome_device_builder/models/components.py) | Paginated component list |
| `AutomationTrigger` | [`controllers/automations.py`](../esphome_device_builder/controllers/automations.py) | Trigger that starts an automation |
| `AutomationAction` | [`controllers/automations.py`](../esphome_device_builder/controllers/automations.py) | Action performed in an automation |
| `ConfigEntry` | [`models/common.py`](../esphome_device_builder/models/common.py) | Config field definition |
| `PagedResponse` | [`models/common.py`](../esphome_device_builder/models/common.py) | Base for paginated responses |
| `CommandMessage` | [`models/api.py`](../esphome_device_builder/models/api.py) | Client → Server command |
| `ResultMessage` | [`models/api.py`](../esphome_device_builder/models/api.py) | Server → Client result |
| `ErrorMessage` | [`models/api.py`](../esphome_device_builder/models/api.py) | Server → Client error |
| `EventMessage` | [`models/api.py`](../esphome_device_builder/models/api.py) | Server → Client streaming event |

## Enums

| Enum | File | Values |
|------|------|--------|
| `Platform` | [`models/boards.py`](../esphome_device_builder/models/boards.py) | `esp32`, `esp8266`, `rp2040`, `bk72xx`, `rtl87xx`, `ln882x` |
| `Esp32Variant` | [`models/boards.py`](../esphome_device_builder/models/boards.py) | `esp32`, `esp32s2`, `esp32s3`, `esp32c2`, `esp32c3`, `esp32c5`, `esp32c6`, `esp32c61`, `esp32h2`, `esp32p4` |
| `Connectivity` | [`models/boards.py`](../esphome_device_builder/models/boards.py) | `wifi`, `bluetooth`, `ethernet`, `zigbee`, `thread`, `openthread`, `can`, `matter`, `lora` |
| `BoardTag` | [`models/boards.py`](../esphome_device_builder/models/boards.py) | `compact`, `dev-kit`, `starter-kit`, `display`, `camera`, `rgb-led`, `relay`, `lipo`, `poe`, `usb-c`, `sonoff`, `tuya`, `shelly`, ... |
| `PinFeature` | [`models/boards.py`](../esphome_device_builder/models/boards.py) | `adc`, `dac`, `touch`, `pwm`, `i2c_sda`, `i2c_scl`, `spi_mosi`, `uart_tx`, `strapping`, `input_only`, `boot_button`, ... |
| `ComponentCategory` | [`models/components.py`](../esphome_device_builder/models/components.py) | `sensor`, `binary_sensor`, `switch`, `light`, `climate`, `core`, `bus`, `misc`, ... |
| `ConfigEntryType` | [`models/common.py`](../esphome_device_builder/models/common.py) | `string`, `integer`, `float`, `boolean`, `select`, `pin`, `time_period`, `icon`, `id`, `unknown`, ... |
| `EventType` | [`models/common.py`](../esphome_device_builder/models/common.py) | `device_added`, `device_removed`, `device_updated`, `device_state_changed`, `importable_device_added`, `importable_device_removed` |
| `ErrorCode` | [`models/api.py`](../esphome_device_builder/models/api.py) | `invalid_message`, `unknown_command`, `invalid_args`, `not_found`, `internal_error` |

---

## Legacy REST Endpoints (Deprecated)

For backward compatibility with the Home Assistant ESPHome integration only.
New clients must use the `/ws` WebSocket API.

| Endpoint | Description |
|----------|-------------|
| `GET /devices` | List devices (HA dashboard-api) |
| `GET /json-config?configuration=...` | Get parsed YAML as JSON |
| `GET /compile` (WebSocket) | Compile via spawn protocol |
| `GET /upload` (WebSocket) | Upload via spawn protocol |
