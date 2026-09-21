# CLAUDE.md

Instructions for Claude Code when working with this repository.

## Project Overview

**Govee Integration for Home Assistant** - A HACS custom component that controls Govee lights, LED strips, and smart devices via the Govee Cloud API v2.0.

| Attribute | Value |
|-----------|-------|
| Type | Home Assistant Custom Component |
| Language | Python 3.12+ |
| Integration Type | Hub (cloud service) |
| IoT Class | cloud_push (MQTT + polling) |
| API Version | Govee API v2.0 |

## Quick Commands

```bash
# Run tests (recommended)
tox

# Run pytest directly
pytest

# Single test file
pytest tests/test_config_flow.py

# Single test
pytest tests/test_models.py::TestRGBColor::test_valid_color

# Format code
black .

# Lint
flake8 .

# Type check
mypy custom_components/govee
```

## Directory Structure

```
custom_components/govee/
├── __init__.py          # async_setup (services), async_setup_entry, cleanup, device removal
├── config_flow.py       # Config/options/reauth/reconfigure flows
├── coordinator.py       # DataUpdateCoordinator (REST poll, MQTT, LAN, BLE, BFF); GoveeConfigEntry
├── entity.py            # Base GoveeEntity class (device info, availability, _async_send_command)
├── light.py             # Light platform (main light, nightlight, main panel)
├── select.py            # Scene/DIY/snapshot/HDMI/music/fan-speed/purifier selects
├── switch.py            # Plugs, toggles, music mode, DreamView, probe polling
├── fan.py               # Tower fans and ceiling fans
├── humidifier.py        # Humidifiers and dehumidifiers
├── number.py            # Music sensitivity, heater target, probe limits
├── sensor.py            # Readings and diagnostic sensors
├── binary_sensor.py     # Connectivity, leak, occupancy, water-tank sensors
├── event.py             # Leak sensor button presses
├── button.py            # Refresh scenes, clear water alert
├── services.py          # Service actions (registered from async_setup)
├── repairs.py           # Repairs framework integration
├── diagnostics.py       # Diagnostics for troubleshooting
├── scene_cache.py       # Scene / DIY scene cache with TTL
├── transport_health.py  # Per-device, per-transport health tracker
├── ble_advertisement.py # BLE advertisement correlation and enrolment
├── ble_passthrough.py   # BLE frames tunnelled over AWS IoT
├── const.py             # Constants
├── icons.json           # Entity icons by translation key
├── strings.json         # UI strings (mirrored in translations/en.json)
├── models/              # Domain models (frozen devices/commands, mutable state)
│   ├── device.py        # GoveeDevice, GoveeCapability, leak sensor models
│   ├── state.py         # GoveeDeviceState, RGBColor
│   ├── commands.py      # Command pattern implementations
│   └── transport.py     # TransportHealth
├── platforms/           # Segment light entities (individual and grouped)
└── api/                 # API layer
    ├── client.py        # GoveeApiClient (REST)
    ├── auth.py          # GoveeAuthClient (account login + 2FA, BFF reads)
    ├── mqtt.py          # GoveeAwsIotClient (AWS IoT MQTT)
    ├── openapi_events.py# Official event push channel (API key only)
    ├── lan*.py          # LAN discovery, client, and control mapping
    ├── ble*.py          # Direct BLE transport, packets, crypto
    ├── mqtt_control.py  # Native MQTT command mapping
    ├── probe_thermometer.py # Probe thermometer frames
    └── exceptions.py    # Exception hierarchy
```

## Architecture Patterns

### Layers
- **Models**: Devices, capabilities, colors, and commands are frozen dataclasses; `GoveeDeviceState` is mutable and updated in place by the coordinator. No I/O.
- **API Layer**: HTTP/MQTT/LAN/BLE clients, exception handling
- **Coordinator**: State management, orchestration, transport selection
- **Entities**: Home Assistant platform integration (all subclass `GoveeEntity`, a `CoordinatorEntity`)

### Command Pattern
Device control uses immutable command objects (no device ID inside; the coordinator takes it):
```python
await coordinator.async_control_device(device_id, PowerCommand(power_on=True))
await coordinator.async_control_device(device_id, BrightnessCommand(brightness=50))
await coordinator.async_control_device(device_id, ColorCommand(color=RGBColor(255, 0, 0)))
```
`async_control_device` returns `False` when Govee rejects the command. Entities must not swallow that: call `self._async_send_command(command)` (raises a translated `HomeAssistantError`) or `raise self._command_failed()` for other coordinator methods that return a bool. Invalid user input raises `ServiceValidationError` with a key from the `exceptions` block of `strings.json`.

### Coordinator updates
Entities are `CoordinatorEntity` subscribers. Push paths (MQTT, LAN, BLE) call `coordinator.async_set_updated_data` only when a value changed; the BLE advertisement handler uses `async_update_listeners` so it never reschedules the poll.

## Key Components

### GoveeDataUpdateCoordinator
Central hub managing:
- Device discovery and state polling
- MQTT real-time updates
- Scene caching
- Optimistic state updates
- Repairs integration

### GoveeApiClient
REST client with:
- aiohttp-retry for resilience
- Rate limit tracking
- Parallel state fetching
- Command serialization

### GoveeAuthClient
Account login for MQTT credentials:
- 2FA email verification flow (since March 2026)
- Login: `/account/rest/account/v2/login` -> status 454 = 2FA required
- Verification: `/account/rest/account/v1/verification` with `{"type": 8, "email": "..."}`
- Retry login with `"code"` field -> returns token + IoT certs
- App version must be `7.4.10` with matching User-Agent
- IoT credentials are persisted in `entry.data` (config entry schema v2) to survive entry reloads
- Never log the account email, password, token, or certificates

### GoveeAwsIotClient
MQTT client for real-time updates:
- AWS IoT Core connection
- Certificate-based auth (P12/PEM from login)
- State push notifications
- Only started when IoT credentials available (email/password configured + 2FA verified)

## Testing

About 3,250 tests across 92 files (`pytest --co -q | tail -1` for the current count). Most are unit tests on entities and the coordinator built with `MagicMock`; the `tests/test_cov_<module>.py` files close each module's remaining branches. `tests/test_setup_entry*.py`, `tests/test_config_flow_manager*.py`, and `tests/test_repairs.py` drive the real config entry, flow manager, and repair flows with `MockConfigEntry`. Prefer that style for anything that touches registries, setup, or flow steps.

## Code Style

- **Formatting**: Black (line length 119, configured in `pyproject.toml`); CI runs `black --check`, so run `black .` before committing
- **Linting**: Flake8 (configured in setup.cfg)
- **Types**: mypy strict mode; use `GoveeConfigEntry` for the config entry type
- **Docstrings**: Google style
- **Coverage**: 95% floor (tox and .coveragerc); 99.9% measured, every module above 96%; config_flow.py must stay at 100%
- **Logging**: `%s` formatting, no trailing period, no usernames/emails/tokens; info level only for things the user must act on
- **Names and icons**: every entity has `_attr_translation_key`; names live in `strings.json` and icons in `icons.json`, never `_attr_name`/`_attr_icon`

## Common Tasks

### Add a new platform
1. Create `platform.py` with entity class
2. Register in `__init__.py` PLATFORMS list
3. Add to coordinator device processing
4. Add tests

### Add a new command
1. Add command class to `models/commands.py`
2. Implement in `api/client.py`
3. Add coordinator method
4. Add entity method that raises on failure (`_async_send_command`)
5. Add tests

### Add a service action
1. Add the handler to `services.py` and register it in `async_setup_services` (called from `async_setup`, never per entry)
2. Validate input with `ServiceValidationError`; resolve `device_id` through `_get_coordinator_for_device`
3. Add the `services.yaml` fields and the `services` and `exceptions` strings

### Handle a new error type
1. Add exception to `api/exceptions.py`
2. Handle in coordinator
3. Consider repairs integration
4. Add tests

## Important Notes

- All I/O must be async
- Use `asyncio.gather()` for parallel operations
- Entities inherit from `GoveeEntity` base class
- Coordinator manages all state - entities are `CoordinatorEntity` subscribers
- MQTT is optional - polling is the fallback; a total cloud outage raises `UpdateFailed` so entities go unavailable
- Rate limits: 100/min, 10,000/day
- Orphan cleanup (`__init__.py`) only removes entities of devices missing from a complete discovery; leak sensors, hubs, and the `hub` diagnostics device are protected, and `async_remove_config_entry_device` covers manual deletion

## 2FA Authentication Flow

Govee requires email verification (2FA) for account login since March 2026.

### Flow
1. `login(email, password)` -> JSON `{"status": 454}` = 2FA required
2. `request_verification_code(email)` -> Govee sends 4-digit code to email
3. `login(email, password, code="1234")` -> success, returns token + IoT certs

### Config Flow Integration
- `async_step_account()` catches `Govee2FARequiredError` -> triggers code send -> `async_step_verification_code()`
- Same flow in `async_step_reconfigure()`
- `client_id` (UUID hex) must be generated BEFORE the first login and reused across all steps
- IoT credentials obtained in the config flow are written to `entry.data[KEY_IOT_CREDENTIALS]` (schema v2) so the entry reload finds them (avoids re-login hitting 2FA again); nothing is kept in `hass.data` at runtime

### Startup Behavior
- `Govee2FARequiredError` at startup -> log warning, record failure, create repairs issue, continue polling-only
- Cannot prompt for code at startup — only config/reconfigure flows are interactive

### Key Constants
- `GOVEE_APP_VERSION = "7.4.10"`
- `GOVEE_VERIFICATION_URL = "https://app2.govee.com/account/rest/account/v1/verification"`
- Verification payload: `{"type": 8, "email": "..."}`
- Code expires in ~15 minutes

### Exception Hierarchy for Auth
- `Govee2FARequiredError` — status 454, no code provided
- `Govee2FACodeInvalidError` — status 454, code provided but wrong/expired
- `GoveeAuthError` — status 401, bad credentials
- `GoveeLoginRejectedError` — other non-200 status codes

## Govee API v2.0 Patterns

### Control Command Payload
Commands use a flat structure (NOT nested):
```json
{
  "requestId": "uuid",
  "payload": {
    "sku": "H601F",
    "device": "03:9C:DC:06:75:4B:10:7C",
    "capability": {
      "type": "devices.capabilities.on_off",
      "instance": "powerSwitch",
      "value": 1
    }
  }
}
```

Reference: `docs/govee-protocol-reference.md`

### Device ID Detection
- **Regular devices**: MAC address format `03:9C:DC:06:75:4B:10:7C`
- **Group devices**: Numeric-only IDs like `11825917`
- Detection: `device_id.isdigit()` returns True for groups

### Segment Capability Parsing
RGBIC segment count is in `fields[].elementRange.max + 1`:
```python
# API returns elementRange with 0-based max index
# e.g., {"min": 0, "max": 6} = 7 segments (0-6)
segment_count = element_range["max"] + 1
```

For SKUs the API over-reports (H7075: `elementRange.max=14`, device has 3 sections), `GoveeDevice.segment_count` clamps the parser-derived count against `fields[].size.max` first (auto safety net for unknown SKUs) and then applies `SKU_SEGMENT_OVERRIDES` in `const.py` as the authoritative override for known SKUs — both live in `custom_components/govee/models/device.py:1132+`. To add a new SKU: issue + one-line entry + test case, mirroring `FAHRENHEIT_REPORTING_SKUS` (issues #115 / #128 / #129).

## API Limitations & State Handling

### Scene State
- **Limitation**: API doesn't reliably return active scene
- **Solution**: Preserve scene via optimistic state
- **Clear when**: A different mode is activated (color, color_temp, music, DreamView, DIY scene)
- **Implementation**: `coordinator.py` always preserves `active_scene` on API poll regardless of power state

### Segment Colors
- **Limitation**: API returns empty strings for segment colors
- **Solution**: Segment entities use local optimistic state + `RestoreEntity`
- **Clear when**: Never (persists across restarts via HA state machine)
- **Implementation**: `platforms/segment.py` keeps the colours in the entity; coordinator updates only re-render availability, and the grouped entity broadcasts its writes over a per-device dispatcher signal

### Pattern
For API values that aren't reliably returned:
1. Use optimistic state from commands
2. Use `RestoreEntity` to persist across HA restarts
3. Don't overwrite with API responses
4. Clear on appropriate events (e.g., power off for scenes)

## Debug Logging Patterns

Add debug logging when:
1. Processing capabilities during device discovery
2. Creating entities to show which ones are being set up
3. Control commands fail to show payload details
4. State updates from MQTT

Example pattern:
```python
_LOGGER.debug(
    "Device: %s (%s) type=%s is_group=%s",
    device.name, device.device_id, device.device_type, device.is_group,
)
for cap in device.capabilities:
    _LOGGER.debug("  Capability: type=%s instance=%s params=%s",
        cap.type, cap.instance, cap.parameters)
```

## Options/Config Patterns

### Options schema (config_flow.py)
Options are defined in `GoveeOptionsFlow.async_step_init()`:
```python
vol.Optional(CONF_POLL_INTERVAL, default=...): vol.All(vol.Coerce(int), vol.Range(min=30, max=300)),
vol.Optional(CONF_ENABLE_GROUPS, default=...): bool,
vol.Optional(CONF_ENABLE_SCENES, default=...): bool,
vol.Optional(CONF_ENABLE_DIY_SCENES, default=...): bool,
vol.Optional(CONF_API_TEMPERATURE_UNIT, default=...): SelectSelector(...),  # options translated via the selector block
```
Per-device segment modes are stored under `CONF_SEGMENT_MODE_BY_DEVICE`. Enumerated options use `SelectSelector` with a `translation_key` so their labels come from the `selector` block of `strings.json`.

### Translations
Update both files when changing option labels:
- `strings.json` - Primary source
- `translations/en.json` - English translation

## Release Process

**One release per day, cut at the end of the day.** Fixes and merged PRs land on `main` throughout the day (CI must be green), but the version is bumped and the release created once, at the end of the user's local calendar day, covering everything that landed. Never cut a second release the same day, and don't bump `manifest.json` before release time. Users get an update notification per release, and several a week was reported as too many (#202). Issue and PR replies that cite a version go out after that day's release, per the reply rule below.

**Exception: a broken release.** If a release that has already gone out breaks users (the integration fails to load or set up, or a regression stops previously working devices from working), cut a hotfix release immediately, even if one was already cut that day. Keep the hotfix to the regression alone, and say in its release notes which release it corrects. An ordinary bug, a wrong value or a missing feature is not a broken release and waits for the end-of-day release.

1. **Bump version** in `manifest.json` (CalVer: `YYYY.MM.patch`)
2. **Commit**: stage explicit paths (`git add custom_components tests ...`), never a bare `git add -A` (sandbox placeholder dotfiles sit in the repo root)
3. **Push**: `git push origin main`
4. **Wait for CI**: `gh run list --commit "$(git rev-parse HEAD)"` (full SHA; all five workflows must pass)
5. **Create release**: `gh release create vYYYY.MM.patch --title "vYYYY.MM.patch" --notes "..."`
6. **Then reply** on the issues and PRs it fixed, citing the shipped version; leave issues open until the reporter validates
