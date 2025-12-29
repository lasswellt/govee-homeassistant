# Govee Integration Architecture

This document provides a comprehensive overview of the Govee Home Assistant integration architecture, data flow, and implementation details.

---

## Table of Contents

- [Overview](#overview)
- [Component Architecture](#component-architecture)
- [Data Flow](#data-flow)
- [State Management](#state-management)
- [Rate Limiting](#rate-limiting)
- [Device Capability Detection](#device-capability-detection)
- [Error Handling Strategy](#error-handling-strategy)
- [Group Device Support](#group-device-support)
- [Performance Optimizations](#performance-optimizations)

---

## Overview

The Govee integration is a **hub-type** Home Assistant integration that connects to the Govee cloud API v2.0 to control lights, LED strips, and smart plugs. It follows the modern Home Assistant architecture patterns with:

- **Config Flow**: UI-based configuration with re-authentication support
- **DataUpdateCoordinator**: Centralized state management and polling
- **Platform Entities**: Light, Switch, and Select platforms
- **Type Safety**: 100% type annotations (mypy strict compliant)
- **Async Architecture**: Fully asynchronous using asyncio

**Integration Type**: `hub` (cloud service managing multiple devices)
**IoT Class**: `cloud_polling`
**API Version**: Govee API v2.0

---

## Component Architecture

### Directory Structure

```
custom_components/govee/
├── __init__.py              # Integration entry point
├── config_flow.py           # UI configuration flow
├── coordinator.py           # DataUpdateCoordinator
├── entity.py                # Base entity class
├── light.py                 # Light platform
├── switch.py                # Switch platform
├── select.py                # Select platform (scenes)
├── services.py              # Custom services
├── models.py                # Data models
├── const.py                 # Constants
├── manifest.json            # Integration metadata
├── strings.json             # UI strings (base)
├── translations/            # Localized strings
│   ├── en.json
│   ├── de.json
│   ├── fr.json
│   └── pt-BR.json
└── api/
    ├── client.py            # API client
    ├── const.py             # API constants
    └── exceptions.py        # API exceptions
```

### Component Responsibilities

#### `__init__.py` - Integration Setup
**Purpose**: Entry point for Home Assistant integration loading

**Key Functions**:
- `async_setup_entry()`: Initialize integration on HA startup
- `async_unload_entry()`: Clean up on integration removal
- `async_reload_entry()`: Handle configuration changes

**Responsibilities**:
- Create API client with user's API key
- Initialize DataUpdateCoordinator
- Set up platforms (light, switch, select)
- Store runtime data in `entry.runtime_data`

**Flow**:
```
HA Startup → async_setup_entry() → Create API Client → Create Coordinator
          → coordinator._async_setup() → Platform Setup → Entities Created
```

#### `coordinator.py` - State Management
**Purpose**: Central hub for device state polling and caching

**Class**: `GoveeDataUpdateCoordinator(DataUpdateCoordinator)`

**Key Methods**:
- `_async_setup()`: Discover devices on first load
- `_async_update_data()`: Poll device states (parallel fetching)
- `async_control_device()`: Send control commands with optimistic updates
- `async_get_dynamic_scenes()`: Fetch and cache scenes
- `async_get_diy_scenes()`: Fetch and cache DIY scenes

**Features**:
- Parallel state fetching (10x faster for multiple devices)
- Scene caching (minimize API calls)
- Optimistic state updates
- Group device special handling
- Rate limit tracking

#### `config_flow.py` - Configuration
**Purpose**: UI-based configuration and re-authentication

**Classes**:
- `GoveeFlowHandler`: Initial setup flow
- `GoveeOptionsFlowHandler`: Runtime options configuration

**Flows**:
1. **Initial Setup** (`async_step_user`):
   - Validate API key
   - Set poll interval
   - Create config entry

2. **Options** (`async_step_user`):
   - Update API key
   - Modify poll interval
   - Toggle assumed state
   - Enable/disable group devices
   - Configure attribute update filters

3. **Re-authentication** (`async_step_reauth`):
   - Triggered on 401 errors
   - Validate new API key
   - Update config entry
   - Reload integration

#### `light.py` - Light Platform
**Purpose**: Light entities with full color control

**Class**: `GoveeLightEntity(GoveeEntity, LightEntity, RestoreEntity)`

**Features**:
- Auto-detected color modes (RGB, color temp, brightness, on/off)
- Brightness conversion (HA 0-255 ↔ Device ranges)
- Scene support via effect list
- Segment control (RGBIC strips)
- Music mode activation
- State restoration for group devices

**Color Mode Detection**:
```python
if device.supports_rgb and device.supports_color_temp:
    color_modes = {ColorMode.RGB, ColorMode.COLOR_TEMP}
elif device.supports_rgb:
    color_modes = {ColorMode.RGB}
elif device.supports_brightness:
    color_modes = {ColorMode.BRIGHTNESS}
else:
    color_modes = {ColorMode.ON_OFF}
```

#### `switch.py` - Switch Platform
**Purpose**: Switch entities for plugs and night lights

**Entity Types**:
1. **Smart Plug Switches**: On/off control for outlets
2. **Night Light Switches**: Warm backlight mode toggle

**Class**: `GoveeSwitchEntity(GoveeEntity, SwitchEntity)`

#### `select.py` - Select Platform
**Purpose**: Scene selection dropdowns

**Entity Types**:
1. **Dynamic Scenes**: Cloud-provided scenes
2. **DIY Scenes**: User-created scenes (disabled by default)

**Class**: `GoveeSelectEntity(GoveeEntity, SelectEntity)`

**Features**:
- Lazy loading (scenes fetched on first access)
- Caching (minimize API calls)
- Refresh service support

#### `api/client.py` - API Client
**Purpose**: Govee API v2.0 communication

**Classes**:
1. **`RateLimiter`**: Dual-limit rate limiting (100/min, 10,000/day)
2. **`GoveeApiClient`**: HTTP client for Govee API

**Key Methods**:
- `get_devices()`: Fetch all devices
- `get_device_state()`: Query device state
- `control_device()`: Send control commands
- `get_dynamic_scenes()`: Fetch scenes
- `get_diy_scenes()`: Fetch DIY scenes

**Rate Limiting**:
- Tracks requests in sliding windows
- Automatically waits when limits approached
- Updates from API response headers

---

## Data Flow

### Device Discovery Flow

```
User Configures Integration
        ↓
GoveeFlowHandler.async_step_user()
        ↓
validate_api_key() → Test connection
        ↓
async_setup_entry() → Create coordinator
        ↓
coordinator._async_setup() → Fetch devices
        ↓
Filter group devices (unless enabled)
        ↓
Store in coordinator.devices
        ↓
Platform setup (light, switch, select)
        ↓
Entities created for each device
```

### State Update Flow (Polling)

```
Update Interval Timer
        ↓
coordinator._async_update_data()
        ↓
┌─────────────────────────────────┐
│ Parallel State Fetching:        │
│  - Create tasks for all devices │
│  - asyncio.gather(*tasks)       │
│  - 30s timeout protection       │
└─────────────────────────────────┘
        ↓
Process results:
  - Success → Update state
  - Auth Error → Trigger reauth
  - Rate Limit → Keep previous state
  - Other Error → Log, keep previous
        ↓
coordinator.data = states
        ↓
coordinator.async_set_updated_data()
        ↓
Entities notified of state change
        ↓
UI updated
```

### Control Command Flow

```
User Action (turn on/off, set color, etc.)
        ↓
Entity method (async_turn_on, async_turn_off, etc.)
        ↓
coordinator.async_control_device()
        ↓
RateLimiter.acquire() → Wait if needed
        ↓
client.control_device() → Send to API
        ↓
Apply optimistic state update
        ↓
coordinator.async_set_updated_data()
        ↓
UI updated immediately (don't wait for polling)
        ↓
Next poll cycle confirms actual state
```

---

## State Management

### State Sources

The integration handles multiple state sources:

1. **API State**: Polled from Govee cloud API
   - Authoritative source for regular devices
   - Not available for group devices

2. **Optimistic State**: Assumed based on commands sent
   - Used immediately after control commands
   - Used exclusively for group devices
   - Persists via RestoreEntity

3. **Previous State**: Fallback when API fails
   - Used during rate limit errors
   - Used when device offline

### State Update Strategy

```python
def update_state(device_id, api_response):
    if device_id in coordinator.data:
        # Preserve optimistic state (scenes, etc.)
        state = coordinator.data[device_id]
        state.update_from_api(api_response)
    else:
        # Create new state from API
        state = GoveeDeviceState.from_api(device_id, api_response)

    return state
```

### Optimistic Updates

Optimistic updates provide responsive UI by updating state immediately:

```python
async def async_turn_on(self):
    # Send command
    await coordinator.async_control_device(...)

    # Update state immediately (don't wait for API)
    if coordinator.data and device_id in coordinator.data:
        coordinator.data[device_id].power_state = True
        coordinator.async_set_updated_data(coordinator.data)

    # Next poll cycle will sync actual state
```

---

## Rate Limiting

### Govee API Limits

- **Per-Minute**: 100 requests/minute
- **Per-Day**: 10,000 requests/day

### Implementation

**Algorithm**:
1. Track timestamps of all requests (minute and day windows)
2. Before each request, check if limits would be exceeded
3. If limit reached, sleep until oldest request expires
4. Update limits from API response headers

**Code**:
```python
async def acquire(self):
    async with self._lock:
        # Clean expired timestamps
        now = time.time()
        self._minute_timestamps = [t for t in self._minute_timestamps if now - t < 60]

        # Check minute limit
        if len(self._minute_timestamps) >= self._per_minute:
            wait_time = 60 - (now - self._minute_timestamps[0])
            await asyncio.sleep(wait_time)

        # Record request
        self._minute_timestamps.append(now)
```

### Strategies to Avoid Rate Limits

1. **Increase Poll Interval**: Default 30s, recommend 60s+ for many devices
2. **Scene Caching**: Scenes fetched once and cached
3. **Parallel Fetching**: Faster updates = fewer requests per time window
4. **Optimistic Updates**: Don't query state after every command

---

## Device Capability Detection

Devices report capabilities in their API response. The integration automatically detects:

### Capability Types

```python
CAPABILITY_ON_OFF = "devices.capabilities.on_off"
CAPABILITY_COLOR_SETTING = "devices.capabilities.color_setting"
CAPABILITY_RANGE = "devices.capabilities.range"
CAPABILITY_DYNAMIC_SCENE = "devices.capabilities.dynamic_scene"
CAPABILITY_SEGMENT_COLOR = "devices.capabilities.segment_color_setting"
```

### Color Mode Detection

```python
def _determine_color_modes(self) -> set[ColorMode]:
    modes = set()

    if self._device.supports_rgb:
        modes.add(ColorMode.RGB)

    if self._device.supports_color_temp:
        modes.add(ColorMode.COLOR_TEMP)

    if not modes and self._device.supports_brightness:
        modes.add(ColorMode.BRIGHTNESS)

    if not modes:
        modes.add(ColorMode.ON_OFF)

    return modes
```

### Brightness Range Detection

Different devices use different ranges:
- Some: 0-100
- Others: 0-254

Detection:
```python
brightness_cap = device.get_capability(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS)
if brightness_cap:
    min_val = brightness_cap["range"]["min"]
    max_val = brightness_cap["range"]["max"]
```

Conversion:
```python
def ha_to_device_brightness(ha_brightness: int, device_max: int) -> int:
    # HA uses 0-255, convert to device range
    return int(ha_brightness / 255 * device_max)
```

---

## Error Handling Strategy

### Exception Hierarchy

```
GoveeApiError (base)
├── GoveeAuthError (401)
├── GoveeRateLimitError (429)
└── GoveeConnectionError (network)
```

### Handling by Type

**Auth Errors (401)**:
```python
try:
    await client.get_devices()
except GoveeAuthError as err:
    raise ConfigEntryAuthFailed("Invalid API key") from err
    # Triggers re-authentication flow
```

**Rate Limit Errors (429)**:
```python
except GoveeRateLimitError as err:
    _LOGGER.warning("Rate limit hit, keeping previous state")
    # Keep previous state, retry next poll cycle
```

**Connection Errors**:
```python
except GoveeConnectionError as err:
    _LOGGER.error("Connection failed: %s", err)
    raise UpdateFailed(f"Connection error: {err}") from err
```

**Group Device Errors** (expected):
```python
except GoveeApiError as err:
    if is_group_device:
        _LOGGER.info("State query failed for group [EXPECTED]")
        # Use optimistic state
    else:
        _LOGGER.warning("Unexpected error: %s", err)
```

---

## Group Device Support

### Challenge

Govee Home app groups (SameModeGroup, BaseGroup, DreamViewScenic) have limited API support:
- ✅ Control commands work
- ❌ State queries fail ("devices not exist")

### Solution

**Optimistic State Tracking**:
1. State cannot be queried from API
2. Track state based on commands sent
3. Use `RestoreEntity` to persist across restarts
4. Mark as "available" even with `online=False`

**Implementation**:
```python
# Entity availability
@property
def available(self) -> bool:
    if self._is_group_device:
        return True  # Always available for control

    state = self.device_state
    return state and state.online

# State restoration
async def async_added_to_hass(self):
    await super().async_added_to_hass()

    if self._is_group_device:
        last_state = await self.async_get_last_state()
        if last_state:
            # Restore power state
            if last_state.state == STATE_ON:
                self.coordinator.data[self._device_id].power_state = True
```

---

## Performance Optimizations

### 1. Parallel State Fetching

**Before** (Sequential):
```python
for device_id, device in self.devices.items():
    state = await self.client.get_device_state(device_id, device.sku)
    # Time: O(n) - 10 devices = 10 seconds
```

**After** (Parallel):
```python
tasks = [
    fetch_device_state(device_id, device)
    for device_id, device in self.devices.items()
]
results = await asyncio.gather(*tasks)
# Time: O(1) - 10 devices = 1 second (10x faster!)
```

### 2. Scene Caching

Scenes are fetched once and cached:
```python
async def async_get_dynamic_scenes(self, device_id: str, refresh: bool = False):
    if not refresh and device_id in self._scene_cache:
        return self._scene_cache[device_id]  # Return cached

    # Fetch from API only if not cached
    scenes = await self.client.get_dynamic_scenes(device_id, device.sku)
    self._scene_cache[device_id] = scenes
    return scenes
```

### 3. Optimistic Updates

State updated immediately after commands:
```python
await self.client.control_device(...)
# Don't wait for API confirmation, update now
self.data[device_id].apply_optimistic_update(instance, value)
self.async_set_updated_data(self.data)
```

---

## Best Practices

### For Contributors

1. **Maintain Type Safety**: All code must have type annotations
2. **Use Coordinator**: Never access API client directly from entities
3. **Handle Errors**: Every API call must have error handling
4. **Test Thoroughly**: 95%+ coverage required
5. **Document Complex Logic**: Add comments for non-obvious code

### For Users

1. **Increase Poll Interval**: Use 60s+ for many devices
2. **Use Optimistic Updates**: Don't disable for better responsiveness
3. **Monitor Rate Limits**: Check `rate_limit_remaining` attributes
4. **Control Individual Devices**: Avoid Govee Home app groups when possible

---

## Version History

- **2025.12.8**: Added re-authentication flow, parallel fetching, comprehensive docs
- **2025.12.7**: Added group device support (experimental)
- **2025.12.3**: Fixed scene selection persistence
- Earlier: Initial Govee API v2.0 implementation

---

## References

- [Home Assistant Integration Documentation](https://developers.home-assistant.io/docs/creating_integration_manifest)
- [DataUpdateCoordinator Best Practices](https://developers.home-assistant.io/docs/integration_fetching_data)
- [Govee API v2.0 Documentation](https://developer.govee.com/)
