# Govee Integration Architecture

How the integration is put together, as of the code in this repository. CLAUDE.md carries the working conventions; TESTING.md the test guide; `docs/govee-protocol-reference.md` the protocol detail this document only names.

---

## Overview

A **hub** integration for Govee's cloud. The Developer API (REST, API key) is the stable path: device list, state polling, and control. An optional account login adds three undocumented paths: AWS IoT MQTT for push state and native commands, the account (BFF) API for devices and readings the Developer API omits, and gateway-relayed BLE frames. Two local transports need no credentials: Govee's LAN API for lights that have it enabled, and direct Bluetooth for an allowlist of models.

**Integration type** `hub` · **IoT class** `cloud_push` · **Config** UI only, config entry schema version 2.

Layers, outermost first:

| Layer | Where | Role |
|---|---|---|
| Entities | `light.py`, `select.py`, `switch.py`, `fan.py`, `humidifier.py`, `water_heater.py`, `number.py`, `sensor.py`, `binary_sensor.py`, `event.py`, `button.py`, `platforms/` | Home Assistant platforms; every entity is a `CoordinatorEntity` (most through `GoveeEntity`) |
| Coordinator | `coordinator.py` | Discovery, polling, every transport, command routing, optimistic state, repairs |
| API clients | `api/` | REST, account login and BFF reads, AWS IoT MQTT, OpenAPI events, LAN, BLE, probe frames |
| Models | `models/` | Devices, capabilities, colours, and commands as frozen dataclasses; device state as a mutable object the coordinator updates in place |

---

## Directory structure

```
custom_components/govee/
├── __init__.py              # async_setup (services), async_setup_entry, unload, migration, cleanup, device removal
├── config_flow.py           # user, account, verification_code, bluetooth, reauth, reconfigure, options
├── coordinator.py           # GoveeCoordinator (DataUpdateCoordinator); GoveeConfigEntry alias
├── entity.py                # GoveeEntity: unique id, device info, availability, _async_send_command
├── light.py                 # Main light, nightlight, main panel
├── select.py                # Scene, DIY, snapshot, HDMI, music, fan-speed, purifier, preset selects
├── switch.py                # Plugs, sockets, outlets, zones, named lights, music, DreamView, auto-stop, probe polling
├── fan.py                   # Tower and purifier fans, ceiling fans
├── humidifier.py            # Humidifiers and dehumidifiers
├── water_heater.py          # H7175 kettle: target, brew modes, power
├── number.py                # Music sensitivity, heater target, probe alarm limits
├── sensor.py                # Readings, thermometers, probes, filter, AQI/CO2, diagnostics
├── binary_sensor.py         # Connectivity, water tank, pump, leak, occupancy, leak/hub online
├── event.py                 # Leak sensor button presses
├── button.py                # Refresh scenes, clear water alert
├── services.py              # govee.refresh_scenes, govee.set_segment_color
├── repairs.py               # Repair issues and their fix flows
├── diagnostics.py           # Config-entry and device diagnostics with redaction
├── scene_cache.py           # Scene and DIY scene cache with TTL
├── transport_health.py      # Per-device, per-transport health tracking
├── ble_advertisement.py     # Bluetooth advertisement correlation and enrolment
├── ble_passthrough.py       # BLE frames tunnelled over AWS IoT
├── kettle/                  # H7175 kettle: frame decoder, coordinator-owned push and poll rules,
│                            #   brew modes, kettle entities, custom-slot labels
├── const.py                 # Constants, SKU lists, option keys and ranges
├── manifest.json            # Metadata, requirements, Bluetooth matchers
├── strings.json             # UI strings (mirrored in translations/en.json; ca and es partial)
├── icons.json               # Entity icons by translation key
├── services.yaml            # Service action fields
├── quality_scale.yaml       # Quality scale self-assessment, one comment per rule
├── py.typed                 # PEP 561 marker
├── models/
│   ├── device.py            # GoveeDevice, GoveeCapability, leak sensor models, synthetic probe thermometers
│   ├── state.py             # GoveeDeviceState (mutable), RGBColor
│   ├── commands.py          # Command objects (Power, Brightness, Color, Scene, Segment, Toggle, ...)
│   └── transport.py         # TransportHealth
├── platforms/
│   ├── segment.py           # One light entity per RGBIC segment
│   └── grouped_segment.py   # One light entity for all segments
└── api/
    ├── client.py            # GoveeApiClient: REST with aiohttp-retry, rate-limit accounting
    ├── auth.py              # GoveeAuthClient: login, 2FA, IoT credentials, BFF reads
    ├── mqtt.py              # GoveeAwsIotClient: AWS IoT MQTT over mutual TLS, ptReal
    ├── mqtt_control.py      # Native MQTT command mapping
    ├── openapi_events.py    # Official event push channel (API key only)
    ├── lan.py, lan_client.py, lan_control.py   # LAN discovery, client, command mapping
    ├── ble.py, ble_packet.py, ble_crypto.py    # Direct BLE transport, frames, encrypted handshake
    ├── probe_thermometer.py # Probe thermometer frame decoding and encoding
    └── exceptions.py        # GoveeApiError hierarchy
```

---

## Component responsibilities

### Entry point (`__init__.py`)

- `async_setup` registers the two service actions once for the domain; each call resolves a loaded entry or raises `ServiceValidationError`.
- `async_setup_entry` builds the REST client and the coordinator, stores the coordinator in `entry.runtime_data` (typed as `GoveeConfigEntry`), runs the first refresh (`ConfigEntryAuthFailed` for a bad key, `ConfigEntryNotReady` for a cloud outage), removes entities and devices the account no longer reports, registers the Bluetooth unsubscribe callbacks and the options listener with `entry.async_on_unload`, and forwards the ten platforms.
- `async_unload_entry` unloads the platforms and shuts the coordinator down, which stops MQTT, the OpenAPI listener, LAN, BLE, and every timer.
- `async_migrate_entry` moves v1 entries to schema v2 (IoT credentials live in `entry.data`).
- `_async_cleanup_orphaned_entities` removes entities of devices missing from a complete discovery and honours the feature toggles; leak sensors, hubs, and the diagnostics device are protected, and a failed or empty discovery skips removal. `async_remove_config_entry_device` lets the user delete a device the account no longer reports.

### Coordinator (`coordinator.py`)

`GoveeCoordinator` is a `DataUpdateCoordinator` and the only owner of device state.

- **Discovery.** Developer API device list, then, with account login, the account list for leak sensors and their hubs, gateway-bridged thermometers, and probe thermometers the Developer API does not return. A rediscovery pass every 5 minutes schedules a reload when a new device appears.
- **Polling.** One request per device, in parallel, each with its own deadline. Devices whose entities are all disabled are skipped. A total cloud outage raises `UpdateFailed` after LAN reads and transport health refresh, so cloud-dependent entities go unavailable and the coordinator logs once; a rate-limit answer backs the interval off and raises the `rate_limited` repair.
- **Push.** MQTT (`_on_mqtt_state_update`), OpenAPI events, LAN reads, and BLE advertisements update the state object in place and call `async_set_updated_data` only when a value changed; the advertisement handler uses `async_update_listeners` so it never reschedules the poll.
- **Control.** `async_control_device(device_id, command)` routes each command to the fastest transport that can carry and confirm it: BLE, then LAN (verified by reading the device back), then MQTT (opt-in, acknowledged at QoS 1), then REST. It applies the optimistic update, paces segment writes, and returns `False` when Govee rejects the command.
- **Supporting state.** Scene cache with TTL, per-transport health, the segment colour overlay replayed after whole-device writes, MQTT topics per device, credential refresh persisted to `entry.data`, and the repair issues.

Consecutive failed cloud polls still notify listeners of local state and health changes without marking the coordinator successful. The first failure uses Home Assistant's normal failure notification.

### Config flow (`config_flow.py`)

| Step | Purpose |
|---|---|
| `user` | API key; validated against the device list; one entry per key |
| `account` | Optional email and password; obtains IoT credentials |
| `verification_code` | Email code when Govee requires 2FA |
| `bluetooth` / `bluetooth_confirm` | Discovery prompt from the manifest's Bluetooth matchers; one prompt, then the user step |
| `reauth` / `reauth_confirm` | New API key through `async_update_reload_and_abort` |
| `reconfigure` | Replace key or account; clears stored IoT material when the account changes |
| Options `init` | Intervals, unit handling, feature toggles, transport options, LAN targets |
| Options `select_segment_devices`, `configure_device_mode` | Per-device segment mode: disabled, grouped, individual, both |

### Entities (`entity.py` and the platforms)

`GoveeEntity` sets the unique id from the device id plus a suffix, builds `device_info` (with `via_device` for hub-attached devices), and reports availability as coordinator health combined with the device's online flag (groups follow coordinator health only). Actions call `_async_send_command`, which raises a translated `HomeAssistantError` when the coordinator returns `False`; invalid input raises `ServiceValidationError`. Entities that keep optimistic state (segments, several switches and numbers) use `RestoreEntity`. Leak-sensor entities subscribe to a dispatcher signal instead of the coordinator so unrelated entities do not churn.

The whole-device `GoveeLightEntity` also accepts healthy LAN transport plus existing device state as sufficient availability for its power, brightness, RGB and colour-temperature controls. Groups and auxiliary entities retain their existing availability. In particular, `GoveeMainLightEntity` opts out because its panel actions reassert ring segments over a non-LAN transport. Scenes and effects do not gain LAN support.

### Models (`models/`)

`GoveeDevice` and `GoveeCapability` are frozen; `GoveeDevice` derives its platform support from capabilities, clamps over-reported segment counts, and can be synthesised for probe thermometers. `GoveeDeviceState` is mutable and updated in place. Commands are frozen objects without a device id; the coordinator supplies it.

### API layer (`api/`)

- `GoveeApiClient`: REST with `aiohttp-retry`, a 30 s timeout, in-body error mapping, and local request accounting (Govee reports the per-minute allowance but not the daily one).
- `GoveeAuthClient`: login, 2FA code request and retry, IoT credential extraction (PEM or PKCS#12), and the account (BFF) reads for topics, leak sensors, thermometers, and the device census.
- `GoveeAwsIotClient`: mutual-TLS MQTT with reconnect backoff, a once-per-outage log, status re-queries, and `ptReal` frames; blocking TLS and temp-file work runs in the executor.
- `GoveeOpenApiEventClient`: the official push channel for events such as water-tank-full, API key only.
- LAN, BLE, and probe modules: discovery and verified writes over UDP, direct BLE with the encrypted handshake newer firmware needs, and the probe thermometer register map.

Both HTTP clients take the Home Assistant `aiohttp` session and never create their own.

---

## Data flow

### Poll

```
update_interval → _async_update_data
  → rediscovery (every 5 min) → reload if a new device appeared
  → gather(_fetch_device_state per pollable device)
  → GoveeAuthError → ConfigEntryAuthFailed (reauth)
  → partial failures keep the previous state per device
  → LAN overlay, transport health (also during cloud outages)
  → every cloud read failed to reach Govee → UpdateFailed (cloud-dependent entities unavailable)
  → consecutive cloud failures still publish local state and health
  → entities re-render through CoordinatorEntity
```

### Push

```
MQTT / OpenAPI event / LAN read / BLE advertisement
  → decode → update GoveeDeviceState in place
  → changed? → async_set_updated_data (BLE advertisements: async_update_listeners)
```

### Control

```
entity action → _async_send_command(command)
  → coordinator.async_control_device(device_id, command)
     → BLE (allowlisted models) → LAN (verified by read-back) → MQTT (opt-in) → REST
     → optimistic state update
  → False → HomeAssistantError("command_failed")
```

---

## Platforms

| Platform | Entities |
|---|---|
| `light` | Main light, nightlight, main panel; per-segment and grouped-segment lights (`platforms/`) |
| `select` | Scene, DIY scene, snapshot, HDMI source, music mode, fan speed, purifier mode, preset scene, nightlight scene, kettle brew mode |
| `switch` | Plugs, sockets, MQTT outlets, night light, light zones, named lights, music mode, DreamView, heater auto-stop, appliance power, probe live polling |
| `fan` | Tower and purifier fans, ceiling fans |
| `humidifier` | Humidifiers and dehumidifiers |
| `water_heater` | H7175 kettle: target temperature, brew modes (stable keys, translated), power |
| `number` | Music sensitivity, heater target temperature, probe alarm limits |
| `sensor` | Temperature, humidity, probe temperatures, battery, filter life, AQI, CO2, dehumidifier mode, kettle temperature, kettle heating status, keep-warm duration and DIY slot, connection mode, and diagnostic timestamps; hub-level rate limit and MQTT status |
| `binary_sensor` | Device connectivity, per-transport connectivity (opt-in), water tank full, pump state, kettle keep warm, water leak, occupancy, leak sensor and hub online |
| `event` | Leak sensor button press |
| `button` | Refresh scenes, clear water alert |

Every platform declares `PARALLEL_UPDATES = 0`; the coordinator paces writes. Noisy diagnostics (rate limit, last update, last command, MQTT received, leak addresses) are disabled by default.

---

## Services

| Action | Behaviour |
|---|---|
| `govee.refresh_scenes` | Re-fetches the scene catalogue for one device or all; `device_id` accepts a Home Assistant device or a Govee id |
| `govee.set_segment_color` | Sets the RGB colour of listed segments; indices past the device's segment count raise `ServiceValidationError` |

Both are registered in `async_setup` and raise `HomeAssistantError` when Govee rejects the command.

---

## Error handling

```
GoveeApiError
├── GoveeAuthError (401)             setup and polling → ConfigEntryAuthFailed → reauth flow
├── GoveeRateLimitError (429)        interval backs off; rate_limited repair (fixable)
├── GoveeConnectionError             setup → ConfigEntryNotReady; polling → per-device isolation, UpdateFailed on total outage
├── GoveeDeviceNotFoundError (400)   expected for groups and probe thermometers; optimistic state
├── GoveeLoginRejectedError          account login rejected; mqtt_disconnected repair (fixable)
├── Govee2FARequiredError (454)      config flow asks for the code; at startup → mqtt_2fa_required repair
└── Govee2FACodeInvalidError (454)   config flow error
```

User-facing failures carry translation keys from the `exceptions` block of `strings.json`.

### Repairs (`repairs.py`)

| Issue | Kind | Fix |
|---|---|---|
| `rate_limited` | Fixable | The flow doubles the polling interval (up to 300 s) |
| `mqtt_disconnected` | Fixable | The flow clears the stored login-failure marker and reloads the entry |
| `mqtt_2fa_required` | Informational | Reconfigure and enter the email code |
| `mqtt_token_expired` | Informational | Reconfigure with the current password |

An invalid API key does not raise a repair; Home Assistant's reauth flow handles it.

---

## Configuration options

| Option | Default | Description |
|--------|---------|-------------|
| `poll_interval` | 60 s | State refresh frequency (30 to 300) |
| `water_detector_poll_interval` | 120 s | Leak poll for standalone RF detectors (60 to 3600) |
| `probe_poll_interval` | 30 s | Read rate for armed probe thermometers (10 to 600) |
| `mqtt_status_interval` | 300 s | MQTT status re-query interval (60 to 3600, 0 = off) |
| `api_temperature_unit` | auto | Fahrenheit handling for thermometer readings |
| `enable_groups` | false | Include Govee app groups |
| `enable_scenes` | true | Scene selects and light effects |
| `enable_diy_scenes` | true | DIY scene selects |
| `expose_transport_entities` | false | Per-transport connectivity sensors |
| `enable_mqtt_control` | false | Route power, brightness, and colour over MQTT |
| `lan_targets` | empty | Extra LAN scan targets, `device_id=ip[!]` overrides, or `off` |
| `segment_mode_by_device` | individual | Per-device segment entity mode |

---

## Quality scale

`manifest.json` declares **platinum**. `quality_scale.yaml` records every rule with a comment saying how it is met or why it is exempt, and `docs/code-review-2026-09-13.md` holds the rule-by-rule validation against the code.
