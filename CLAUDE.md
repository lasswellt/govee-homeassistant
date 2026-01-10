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
├── __init__.py          # Entry point, async_setup_entry
├── config_flow.py       # Config/options/reauth/reconfigure flows
├── coordinator.py       # DataUpdateCoordinator with MQTT
├── entity.py            # Base GoveeEntity class
├── light.py             # Light platform
├── scene.py             # Scene platform
├── switch.py            # Switch platform (plugs, night light)
├── sensor.py            # Diagnostic sensors
├── button.py            # Refresh scenes button
├── services.py          # Custom services
├── repairs.py           # Repairs framework integration
├── diagnostics.py       # Diagnostics for troubleshooting
├── const.py             # Constants
├── models/              # Domain models (frozen dataclasses)
│   ├── device.py        # GoveeDevice, GoveeCapability
│   ├── state.py         # GoveeDeviceState, RGBColor
│   └── commands.py      # Command pattern implementations
├── protocols/           # Protocol interfaces (Clean Architecture)
│   ├── api.py           # IApiClient, IAuthProvider
│   └── state.py         # IStateProvider, IStateObserver
└── api/                 # API layer
    ├── client.py        # GoveeApiClient (REST)
    ├── auth.py          # GoveeAuthClient (account login)
    ├── mqtt.py          # GoveeAwsIotClient (AWS IoT MQTT)
    └── exceptions.py    # Exception hierarchy
```

## Architecture Patterns

### Clean Architecture
- **Models**: Immutable frozen dataclasses, no I/O
- **Protocols**: Abstract interfaces (Python Protocols)
- **API Layer**: HTTP/MQTT clients, exception handling
- **Coordinator**: State management, orchestration
- **Entities**: Home Assistant platform integration

### Command Pattern
Device control uses immutable command objects:
```python
PowerCommand(device_id="xxx", value=True)
BrightnessCommand(device_id="xxx", value=128)
ColorCommand(device_id="xxx", value=RGBColor(255, 0, 0))
```

### Observer Pattern
Entities register as observers for state changes:
```python
coordinator.register_observer(device_id, entity)
```

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

### GoveeAwsIotClient
MQTT client for real-time updates:
- AWS IoT Core connection
- Certificate-based auth
- State push notifications

## Testing

| File | Tests | Focus |
|------|-------|-------|
| test_models.py | 50 | RGBColor, Device, State, Commands |
| test_config_flow.py | 41 | Config, options, reauth, reconfigure |
| test_coordinator.py | 32 | Observer pattern, commands, state |
| test_api_client.py | 28 | Exceptions, client, rate limits |
| **Total** | **151** | |

## Code Style

- **Formatting**: Black (line length 119)
- **Linting**: Flake8
- **Types**: mypy strict mode
- **Docstrings**: Google style
- **Coverage**: 95% minimum

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
4. Add entity method
5. Add tests

### Handle a new error type
1. Add exception to `api/exceptions.py`
2. Handle in coordinator
3. Consider repairs integration
4. Add tests

## Important Notes

- All I/O must be async
- Use `asyncio.gather()` for parallel operations
- Entities inherit from `GoveeEntity` base class
- Coordinator manages all state - entities are observers
- MQTT is optional - polling is the fallback
- Rate limits: 100/min, 10,000/day

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

## API Limitations & State Handling

### Scene State
- **Limitation**: API doesn't reliably return active scene
- **Solution**: Preserve scene via optimistic state
- **Clear when**: Device is turned off (scene is no longer active)
- **Implementation**: `coordinator.py` preserves `active_scene` on API poll if device is ON

### Segment Colors
- **Limitation**: API returns empty strings for segment colors
- **Solution**: Segment entities use local optimistic state + `RestoreEntity`
- **Clear when**: Never (persists across restarts via HA state machine)
- **Implementation**: `platforms/segment.py` does NOT subscribe to coordinator updates

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
Options are defined in `GoveeOptionsFlowHandler.async_step_init()`:
```python
vol.Optional(CONF_POLL_INTERVAL, default=...): vol.All(vol.Coerce(int), vol.Range(min=30, max=600)),
vol.Optional(CONF_ENABLE_GROUPS, default=...): bool,
vol.Optional(CONF_ENABLE_SCENES, default=...): bool,
vol.Optional(CONF_ENABLE_SEGMENTS, default=...): bool,
```

### Translations
Update both files when changing option labels:
- `strings.json` - Primary source
- `translations/en.json` - English translation

## Release Process

1. **Bump version** in `manifest.json` (CalVer: `YYYY.MM.patch`)
2. **Commit**: `git add -A && git commit -m "message"`
3. **Push**: `git push origin master`
4. **Wait for CI**: Check with `gh run list --limit 5`
5. **Create release**: `gh release create vYYYY.MM.patch --title "vYYYY.MM.patch" --notes "..."`

## Directory Updates

The project structure has evolved:
```
custom_components/govee/
├── select.py            # Scene selector dropdowns (replaced scene.py)
├── platforms/
│   └── segment.py       # RGBIC segment light entities
```

- **select.py**: One dropdown per device for scene selection
- **segment.py**: Individual light entities for each RGBIC segment
