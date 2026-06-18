<!-- no-registry: single-device feature investigation; no multi-sprint quantified scope to carry forward -->
# H7161 Aroma Diffuser Support (issue #99)

**Type:** Feature Investigation · **Date:** 2026-06-08 · **Stack:** HA custom component, Python 3.12, Govee Cloud API v2.0

## Summary

H7161 (aroma diffuser, `devices.types.aroma_diffuser`) is discovered and parsed but produces **zero control entities** — every platform's `async_setup_entry` guard excludes the diffuser device type. Real diagnostics (issue #99, anonymized) confirm the device exposes exactly **3 capabilities** via the Developer API: `on_off/powerSwitch`, `mode/presetScene` (5 named scenes), `event/lackWaterEvent`. **Power → switch and presetScene → select are high-confidence, shippable now** — both reuse existing entity classes and the existing `ModeCommand` (no new command class, no coordinator/client changes). **`lackWaterEvent` is low-confidence and should be deferred**: it is absent from the REST `raw_api_state` and H7161 reports `mqtt=false`, so no transport currently delivers a water-low value. Recommendation: widen the switch guard + add a `presetScene` select; gate the water-low entity behind real push evidence.

## Research Questions

1. **Why no entities today?** Every platform guard keys on device type / capability-instance whitelists that omit `aroma_diffuser`, `presetScene`, and `lackWaterEvent`. `switch.py:102-106` allows appliance power only for `is_heater or is_kettle`; `select.py:53-198` matches scenes/diy/hdmi/music/heater/purifier — not `CAPABILITY_MODE/presetScene`; `event.py:25-43` iterates `coordinator.leak_sensors` only. **Nothing creates an entity for `devices.types.aroma_diffuser`.**
2. **Best HA entity types?** Power → `SwitchEntity`. Scenes → `SelectEntity` (idiomatic for a fixed ENUM of modes; `fan.preset_modes` is for intensity, wrong fit). Water-low → contested (see Dissent); REST/transport reality makes it deferrable.
3. **Is HumidifierEntity appropriate?** No — on semantic-fit grounds, not a hard API requirement. Per developers.home-assistant.io/docs/core/entity/humidifier, `target_humidity` and `current_humidity` are **optional** (both default `None`); only `mode` is required when `SUPPORT_MODES` is set. But `HumidifierEntity` models a humidity-control appliance with a setpoint slider — H7161 has no humidity setpoint and no humidity sensor, so a humidifier entity exposes a non-functional control surface. `SwitchEntity` + `SelectEntity` is the honest fit.
4. **New command class needed?** No. `ModeCommand(mode_instance="presetScene", value=171396)` (commands.py:314-335) already serializes arbitrary `CAPABILITY_MODE` instances to the flat v2.0 payload.
5. **Are all 3 capabilities parsed?** Yes. `GoveeDevice.from_api_response()` (device.py:841-849) stores every capability with no type filter; all 3 are on `device.capabilities`.
6. **Mist intensity / RGB brightness?** Not in the Developer API. App-only features. Out of scope.

## Findings

### F1 — Entity creation is gated, capabilities are not dropped *(codebase-analyst, device.py:841-849)*
Parsing keeps all capabilities. The gap is purely in platform `async_setup_entry` guards:
- `switch.py:102-106` — `device.supports_power and not device.is_group and (device.is_heater or device.is_kettle)`. Aroma diffuser is neither.
- `select.py:53-198` — no branch for `CAPABILITY_MODE/presetScene`. `is_purifier` checks `devices.types.air_purifier` only.
- `event.py:25-43` — scoped to BFF leak sensors (H5058). `lackWaterEvent` wired nowhere; only `waterFullEvent` (binary_sensor.py:69) and `bodyAppearedEvent` exist.

### F2 — powerSwitch reuses GoveeAppliancePowerSwitchEntity unchanged *(codebase-analyst, switch.py:484)*
`GoveeAppliancePowerSwitchEntity` already sends `PowerCommand` and reads `state.power_state`. Only the setup guard needs widening (`or device.is_aroma_diffuser`). `is_light_device` (device.py:753) returns False for the diffuser → no light-entity conflict.

### F3 — presetScene is a direct clone of the purifier/HDMI select *(codebase-analyst, select.py:530/817)*
Options live in `cap.parameters["options"]` (`{"name":"Bach","value":171396}`, …) — same shape as purifier mode (device.py:729-731). New `GoveePresetSceneSelectEntity` builds an option map and sends `ModeCommand(mode_instance="presetScene", value=int)`, mirroring `GoveeHdmiSourceSelectEntity` / `GoveePurifierModeSelectEntity`. State read-back parallels `hdmiSource` (state.py:291-292).

The 5 options from real diagnostics (German locale — **names are localized; command must use the integer id**):
| Name (de) | value |
|---|---|
| Bach | 171396 |
| Wärme am Kamin | 171397 |
| Morgen | 171398 |
| Gutenachtkuss | 171399 |
| Nachtlicht | 171400 |

### F4 — Mode command payload confirmed, no new command class *(codebase-analyst commands.py:314; web-researcher §5)*
```json
{"requestId":"uuid","payload":{"sku":"H7161","device":"...",
 "capability":{"type":"devices.capabilities.mode","instance":"presetScene","value":171396}}}
```
`client.control_device()` (client.py:355-397) already wraps `ModeCommand` this way. Coordinator path identical to HDMI/purifier selects.

### F5 — lackWaterEvent has NO transport on this device *(diagnostics-verified)*
Decisive, verified against the issue-#99 diagnostics:
- `raw_api_state.capabilities` instances = `['online','powerSwitch','presetScene']` — **`lackWaterEvent` is not in the REST poll response.**
- H7161 `transport = {cloud_api: true, mqtt: false, ble: false}`.
- Govee `devices.capabilities.event` instances deliver via MQTT push (same class as leak/`bodyAppeared`). With `mqtt=false` and no REST field, **no path currently carries a water-low value for H7161.**

A binary_sensor parsed from REST state (the `waterFullEvent` pattern at state.py:312-319) would therefore render perpetually `None`/`off` and never trigger. This is why the water-low entity is deferred, not which entity type to pick.

### F6 — API gaps: mist intensity, RGB nightlight, timer *(web-researcher §1; confirmed by diagnostics)*
The app exposes a mist-level slider and RGB nightlight, but the Developer API does **not** surface them — `presetScene` bundles light+mist into discrete named scenes. No `range`/brightness capability in the device profile. Out of scope; document as a known limitation.

## Dissent / Contradictory Evidence

**How to surface `lackWaterEvent` — three positions:**

| Source | Recommendation | Rationale |
|---|---|---|
| codebase-analyst | `BinarySensorEntity` w/ `device_class=PROBLEM` | Matches existing `waterFullEvent` pattern (binary_sensor.py:119); treats low-water as sustained state |
| library-docs | `EventEntity` (`EventDeviceClass.PROBLEM`) | Govee `event` capability is momentary; matches transient push semantics |
| web-researcher | Diagnostic sensor (or event entity); **reject binary_sensor PROBLEM** | No recovery signal, ambiguous `PROBLEM` class, one-shot push |

**Resolution:** the dispute is moot for H7161 *right now* — F5 shows no transport delivers the value, so any of the three renders empty. The transport fact (not the entity-type debate) governs. **Defer** until a real push is observed; when implementing, `EventEntity` is the better semantic match for a momentary `devices.capabilities.event` (library-docs + web-researcher both lean away from a REST-parsed binary_sensor), but it only ever fires if MQTT becomes active for diffusers.

## Compatibility Analysis

- **Version:** no new deps. All touch points are existing modules (`models/`, `switch.py`, `select.py`, `models/commands.py`).
- **Command/coordinator:** zero changes — `ModeCommand` + `PowerCommand` + `async_control_device` already cover the payloads.
- **Optimistic state:** `presetScene` read-back works from REST (`presetScene` IS in `raw_api_state`, value `""` when unset) — but like other Govee modes the active value may be unreliable; reuse the existing optimistic-state pattern (mirror HDMI/purifier select, which already do this).
- **Risk surface:** small. Switch guard widen is 1 line; select entity is a ~35-line clone of a tested class.

## Recommendation

Ship in two tiers.

**Tier 1 — now (high confidence, REST-backed):**
1. `switch.py:104` — widen guard: `... and (device.is_heater or device.is_kettle or device.is_aroma_diffuser)`.
2. New `GoveePresetSceneSelectEntity` in `select.py` (clone `GoveePurifierModeSelectEntity`), gated by `device.is_aroma_diffuser`; options from `get_preset_scene_options()`; sends `ModeCommand("presetScene", value)`.
3. `models/device.py` — `DEVICE_TYPE_AROMA_DIFFUSER`, `INSTANCE_PRESET_SCENE`, `is_aroma_diffuser`, `get_preset_scene_options()`.
4. `models/state.py` — `preset_scene: int | None`; parse `presetScene` in `update_from_api()` (parallel to `hdmiSource`, state.py:291-292).
5. `strings.json` + `translations/en.json` — keys for the new select.

**Tier 2 — defer (blocked on transport evidence):**
6. `lackWaterEvent` water-low entity — **do not build from REST state.** Add only after a real MQTT push for an aroma diffuser is captured (debug log / diagnostics `last_mqtt_message`). When added, prefer `EventEntity`. Track as a follow-up; mention the API/transport limitation in the issue reply.

**Out of scope (API gaps):** mist intensity, RGB nightlight brightness, timer — not in the Developer API.

## Implementation Sketch

```python
# models/device.py
DEVICE_TYPE_AROMA_DIFFUSER = "devices.types.aroma_diffuser"
INSTANCE_PRESET_SCENE = "presetScene"

@property
def is_aroma_diffuser(self) -> bool:
    return self.device_type == DEVICE_TYPE_AROMA_DIFFUSER

def get_preset_scene_options(self) -> list[dict]:
    cap = self.get_capability(CAPABILITY_MODE, INSTANCE_PRESET_SCENE)
    return cap.parameters.get("options", []) if cap else []
```
```python
# switch.py:102-106 — guard widen
if (device.supports_power and not device.is_group
        and (device.is_heater or device.is_kettle or device.is_aroma_diffuser)):
    entities.append(GoveeAppliancePowerSwitchEntity(coordinator, device))
```
```python
# select.py — new entity (clone of GoveePurifierModeSelectEntity)
class GoveePresetSceneSelectEntity(GoveeEntity, SelectEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "govee_preset_scene"
    # options = [o["name"] for o in device.get_preset_scene_options()]
    # async_select_option -> ModeCommand(mode_instance="presetScene", value=<id for name>)
    # current_option <- state.preset_scene -> name
```
Wire the select guard in `select.py async_setup_entry`: `if device.is_aroma_diffuser: entities.append(GoveePresetSceneSelectEntity(...))`.

Tests: add an `aroma_diffuser` device factory to `tests/`; assert switch + select created, `ModeCommand` payload value is the integer id, and a non-diffuser device gets neither.

## Risks

- **Active scene unreliable from cloud (medium).** Govee often returns `""`/stale mode values. The select may not reflect changes made in the Govee app. Mitigation: reuse the existing optimistic-state approach already applied to scene/HDMI/purifier selects — set the option locally on command, tolerate empty API read-back. This is a known, accepted limitation in this integration, not a new defect.
- **Localized scene names (low).** Option `name` values are locale-dependent (diagnostics show German). The command must key on the integer `value`, and the select must map display name → id from `cap.parameters["options"]` per device, never hardcode the five names.
- **Water-low entity would be dead on arrival (high if built now).** Because no transport carries `lackWaterEvent` for a cloud-only diffuser (F5), shipping a REST-parsed binary_sensor would create an entity that never updates — a worse user experience than omitting it. Deferring is the correct call until a real push is observed.
- **Single-device evidence (medium).** All findings rest on one user's H7161 diagnostics. Other firmware/regions may expose more options or additional capabilities. Mitigation: drive entity creation off the live `cap.parameters["options"]` (already the plan), so extra scenes appear automatically; ask the reporter to confirm control works after release.

## Open Questions

- Does any aroma-diffuser firmware ever push `lackWaterEvent` over the account MQTT topic? Needs a captured `last_mqtt_message`. Until then Tier 2 stays deferred.
- Does `presetScene` reliably read back the active scene after an app-side change, or is it always `""` on poll? Determines whether the select needs full optimistic state or can trust the API.

## References

1. Issue #99 diagnostics (anonymized) — `bff`/`raw_api_state` capability shape, transport flags. github.com/lasswellt/govee-homeassistant/issues/99
2. HA SwitchEntity — https://developers.home-assistant.io/docs/core/entity/switch
3. HA SelectEntity — https://developers.home-assistant.io/docs/core/entity/select
4. HA EventEntity — https://developers.home-assistant.io/docs/core/entity/event
5. HA HumidifierEntity (target/current humidity optional, default None; rejected on semantic fit) — https://developers.home-assistant.io/docs/core/entity/humidifier
6. Govee protocol reference (flat `devices.capabilities.mode` payload) — `docs/govee-protocol-reference.md`
7. govee2mqtt — https://github.com/wez/govee2mqtt (no H7161-specific config; generic on_off+mode handling)
8. Code: `switch.py:102-106,484`, `select.py:53-198,530,817`, `event.py:25-43`, `binary_sensor.py:69,119`, `models/commands.py:314-335`, `models/device.py:841-849`, `models/state.py:291-319`, `api/client.py:355-397`
