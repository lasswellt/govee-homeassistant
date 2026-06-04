<!-- no-registry: single-bug root-cause research; no quantified multi-item scope -->
# Research: H5109 Thermometer Temperature Stale Until Reload (issue #93)

## Summary

H5109 Pool Thermometer `sensor_temperature` updates on integration load, then never refreshes until reload. Root cause is **client-side**, high confidence, verified in repo: `GoveeDataUpdateCoordinator` is constructed with `always_update=False` (`coordinator.py:147`) and `_async_update_data` returns the **same `self._states` dict instance** every poll (`coordinator.py:951`). HA's coordinator refresh gate (`update_coordinator.py:473-478`) fires `async_update_listeners()` only when `always_update`, `last_update_success` flipped, or `previous_data != self.data` — all three are `False` after poll 1 (same object identity), so entities are never told to re-render. Symptom is thermometer-specific because MQTT-capable devices reach `async_set_updated_data` (line 865), which fires listeners unconditionally; H5109 is BLE→gateway→cloud with no MQTT delivery (`transport_health.mqtt.last_success = null`), so polling is its only update path — exactly the path the bug disables. Recommended fix: `always_update=True` (one line).

## Research Questions

1. **Temp value flow API→coordinator→entity?** Poll → `_fetch_device_state` → `state.update_from_api` parses `devices.capabilities.property`/`sensorTemperature` into `state.sensor_temperature` (`state.py:287-298`) → stored in `self._states[device_id]` → `_async_update_data` returns `self._states` (`coordinator.py:951`) → HA gate decides whether to notify listeners. **Parsing is correct**; break is at listener dispatch.
2. **Why no entity update despite fresh raw value?** HA gate `previous_data != self.data` compares the coordinator's own `self.data` against itself; both reference the same mutated dict → equal → no notify. `always_update=False` removes the override. (`coordinator.py:147`, `update_coordinator.py:473-478`)
3. **Does sensor subscribe / write state?** `GoveeTemperatureSensor` (`sensor.py:227-253`) is a `CoordinatorEntity`; `_handle_coordinator_update` → `async_write_ha_state` only runs when listeners fire. They never fire post-poll-1, so state machine is frozen.
4. **Are thermometers polled each cycle?** Yes — API parse path is exercised and `raw_api_state` carries fresh `80.63`. Polling works; notification does not.
5. **Stale-overwrite / optimistic preservation?** Secondary, **not causal**. `coordinator.py:1077-1086` only preserves `sensor_temperature` when the **new** value is `None`; a real API value is written correctly into the (identity-stable) dict.
6. **MQTT available-but-silent skips API?** No. MQTT path (`coordinator.py:865`, `async_set_updated_data`) would rescue via unconditional notify, but `mqtt.last_success = null` → never executes for H5109.

## Findings

### F1 — Root cause: coordinator never notifies listeners after first poll (client-side)
`always_update=False` (`coordinator.py:147`) + `return self._states` (`coordinator.py:951`, same dict object every call). HA gate (`update_coordinator.py:473-478`):
```python
if (
    self.always_update                              # False
    or self.last_update_success != previous_update_success   # False (stays healthy)
    or previous_data != self.data                   # False — same object identity
):
    self.async_update_listeners()                   # never reached after poll 1
```
**Why initial load works:** `self.data` initialises to `None` (`update_coordinator.py:104`); first poll `None != self._states` → `True` → listeners fire once. **Why reload "fixes" it:** reload reconstructs the coordinator, resetting `data` to `None`, re-arming the one-shot first fire. Source: codebase-analyst trace, verified against repo + venv HA source.

### F2 — Thermometer-specific exposure
Lights/plugs receive MQTT pushes → `async_set_updated_data(self._states)` (`coordinator.py:865`) → `update_coordinator.py:514` notifies **unconditionally**, masking F1. H5109 is BLE-only with no MQTT message (`transport_health.mqtt.last_success = null`), so its sole update path is the broken poll-notify path. This is why the bug surfaces on the thermometer and not on lights.

### F3 — Upstream cadence is a separate, real concern (not the issue-#93 root cause)
web-researcher: H5109 architecture is BLE → WiFi gateway → cloud; Govee `/device/state` returns whatever the gateway last batched, refreshed on the gateway's undocumented schedule (~30-60s+). govee2mqtt shows similar staleness (govee2mqtt#228, #392). **This caps freshness but does NOT explain "stale until reload"** — if the API value never changed, reload wouldn't help. The reload-fixes-it symptom is dispositive evidence the root cause is F1 (client notify), not API caching. Cadence remains relevant for choosing a sane poll interval.

## Compatibility Analysis

- Fix touches only coordinator construction; no schema/model/entity API change. Python 3.12+, HA DataUpdateCoordinator contract unchanged.
- `always_update=True` is the documented HA default for coordinators whose data object is mutated in place rather than replaced — matches this coordinator's `self._states` pattern.
- No dependency changes. Existing tests in `test_coordinator.py` (32) should be extended, not rewritten.

## Recommendation

**Apply Option A: set `always_update=True` at `coordinator.py:147`.** One line, guarantees every successful poll notifies listeners, mirrors HA convention for in-place-mutated coordinator data. Resolves the thermometer staleness and any other poll-only device silently affected.

| Option | Change | Pros | Cons |
|---|---|---|---|
| **A (recommended)** | `coordinator.py:147` `always_update=True` | 1 line; explicit; HA-idiomatic for mutated data | Listeners fire even when nothing changed (cheap; HA dedupes on entity state) |
| B | `coordinator.py:951` `return dict(self._states)` | Distinct object each poll satisfies `previous_data != self.data` | Shallow copy per poll; relies on dict-value comparison which can still miss in-place mutation of nested `GoveeDeviceState`; subtler |

Option A is preferred: B's value-comparison path can mis-fire if a nested mutable state object is reused, whereas A is unconditional and unambiguous.

## Implementation Sketch

1. `custom_components/govee/coordinator.py:147` — `always_update=False` → `always_update=True`.
2. Pair with a sane minimum poll interval given F3 gateway cadence; confirm options `CONF_POLL_INTERVAL` floor (`min=30` per config_flow) is acceptable — document that thermometer freshness is bounded by Govee gateway sync (~30-60s+), not the integration.
3. Regression test in `tests/test_coordinator.py`: assert `async_update_listeners` (or an observer's update callback) fires on the **second** consecutive successful poll, not only the first. Use a thermometer-shaped `GoveeDeviceState` whose `sensor_temperature` changes across polls.
4. Optional: changelog / README known-limitation note on gateway-bounded cadence (F3).

## Risks

- **Increased listener churn:** `always_update=True` fires entity updates every poll even when unchanged. Impact is low — HA's state machine deduplicates identical states, and poll cadence is ≥30s. Mitigation: none required; if churn ever matters, switch to Option B.
- **Masked second bug:** F3 means even after the fix, displayed temperature can lag real water temperature by the gateway's batch interval. This is upstream and not fixable client-side; document it so it isn't mistaken for a regression.
- **Other poll-only devices:** any non-MQTT device (BLE-only sensors, some plugs) was equally affected and silently stale; the fix corrects all of them, which may change long-standing observed behavior — call out in release notes.

## Open Questions

- Exact Govee gateway → cloud sync interval for H5109 (undocumented; community estimates 30-60s+). Affects only the recommended minimum poll interval, not the fix.
- Whether any existing test asserts the buggy single-fire behavior and would need updating alongside the fix.

## References

- `custom_components/govee/coordinator.py:147` — `always_update=False` (root cause)
- `custom_components/govee/coordinator.py:951` — `return self._states` (same dict identity)
- `custom_components/govee/coordinator.py:865` — MQTT `async_set_updated_data` (unconditional notify, masks bug on lights)
- `custom_components/govee/coordinator.py:1077-1086` — sensor preservation (secondary, non-causal)
- `custom_components/govee/models/state.py:287-298` — `update_from_api` sensorTemperature parse (correct)
- `custom_components/govee/sensor.py:227-253` — `GoveeTemperatureSensor`
- `homeassistant/helpers/update_coordinator.py:473-478` — refresh gate; `:514` unconditional notify; `:104` `data=None` init
- [Govee Get Device State](https://developer.govee.com/reference/get-devices-status)
- [govee2mqtt #228 — H5109 support](https://github.com/wez/govee2mqtt/issues/228)
- [govee2mqtt #392 — H5179 polling](https://github.com/wez/govee2mqtt/issues/392)
- [HA core #88775 — H5071 stale data](https://github.com/home-assistant/core/issues/88775)
