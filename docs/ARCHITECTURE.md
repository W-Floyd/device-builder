# Architecture

## Principles

1. **ESPHome is a CLI tool.** Firmware operations shell out to `esphome` via subprocess. Device metadata and serial ports use ESPHome Python imports. Board and component definitions come from our own `definitions/` directory.

2. **ESPHome is an optional dependency.** `pip install .[esphome]` pulls it in for standalone use. Plain `pip install .` works inside the ESPHome container.

3. **Frontend and backend are separate repos.** The frontend is a separate pip package. The backend try-imports it and serves the static files.

4. **WS-first API.** Everything goes through a single `/ws` WebSocket with command/response protocol (43 commands). REST endpoints only for HA backward compat.

5. **Real-time events.** Clients subscribe once via `subscribe_events`, get instant push notifications. No polling.

6. **Persistent firmware jobs.** Compile/upload jobs are queued, run one at a time, survive page refreshes and server restarts.

## Project Structure

```
esphome_device_builder/
├── device_builder.py          # Core singleton — owns controllers, event bus, web app
├── __main__.py                # CLI entry point
├── constants.py               # Version + defaults
│
├── models/                    # Data shapes only — no logic
│   ├── common.py              # EventType, ConfigEntry, PagedResponse
│   ├── devices.py             # Device, AdoptableDevice, DevicesResponse
│   ├── boards.py              # Board enums + models
│   ├── components.py          # Component enums + models
│   ├── firmware.py            # FirmwareJob, JobStatus, JobType
│   ├── preferences.py         # UserPreferences, Theme, DashboardView
│   └── api.py                 # WebSocket protocol models
│
├── controllers/               # Business logic — all state lives here
│   ├── boards.py              # BoardCatalog: 505 boards with pin maps
│   ├── components.py          # ComponentCatalog: 655 components
│   ├── devices.py             # DevicesController: CRUD, file scanning, logs
│   ├── firmware.py            # FirmwareController: job queue, compile, install
│   ├── automations.py         # AutomationsController: triggers + actions
│   └── config.py              # ConfigController + DashboardSettings + metadata
│
├── helpers/                   # Pure utilities
│   ├── api.py                 # @api_command decorator
│   ├── event_bus.py           # EventBus
│   ├── json.py                # JSON response, CORS
│   └── yaml.py                # YAML generation
│
├── api/                       # Transport layer
│   ├── ws.py                  # /ws WebSocket dispatch
│   └── legacy.py              # HA compat endpoints
│
└── definitions/               # Data files
    ├── boards/                # 505 board YAML manifests
    ├── components.json        # 655 components
    └── schemas/               # JSON schemas
```

## Controllers

| Controller | Commands | Responsibility |
|-----------|----------|---------------|
| Devices | 14 | Device CRUD, file scanning, YAML validation, live logs |
| Firmware | 13 | Job queue, compile, install, upload, download binaries |
| Boards | 3 | Board catalog with search, filtering, pin maps |
| Components | 3 | Component catalog with search, config entries |
| Automations | 3 | Context-aware triggers + actions |
| Config | 5 | Version, serial ports, preferences, secrets |
| Built-in | 2 | ping, subscribe_events |

## Firmware Job Queue

Jobs are persistent, event-driven, and decoupled from WebSocket connections:

```
firmware/install {configuration} → QUEUED → RUNNING → output... → COMPLETED/FAILED
                                     │                                    │
                                     └──── persisted to disk ─────────────┘
```

- One job runs at a time, others wait in queue
- Output buffered in `FirmwareJob.output` — survives disconnect
- `firmware/follow_job` sends history then streams live
- Error detection scans output for failure patterns (not just exit code)
- Jobs persist across server restarts

## Deployment

### Beta (HA add-on)

Toggle `new_dashboard_beta` in the ESPHome add-on. Pip-installs the device builder and runs it.

### Production

Baked into the ESPHome container. Legacy dashboard deprecated.

## Legacy HA Compatibility

`api/legacy.py` serves: `GET /devices`, `GET /json-config`, `/compile`, `/upload` (spawn protocol).
