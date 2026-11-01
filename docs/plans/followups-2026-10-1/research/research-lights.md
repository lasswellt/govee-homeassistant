# Research: light follow-ups (#261, #240, #213, #239/#269)

Read-only research, 2026-10-25. Repo at `main` (27f0581, v2026.10.1). Diagnostics under the session
scratchpad `diag-1025/<issue>/`. Device IDs, names and account data are redacted throughout.

## 1. #261 H1A42 (Light Strip 2): `set_segment_color` powers the strip on, no segment changes

**Diagnostics** (`diag-1025/261/config_entry-*.json`, v2026.10.1):
- Caps: `powerSwitch`, `gradientToggle` (on=1/off=0), `brightness` 1-100, `segmentedBrightness` and
  `segmentedColorRgb` both `STRUCT{segment: Array size{1..15} elementRange{0..14}, rgb|brightness}`,
  `colorRgb`, `colorTemperatureK` 1000-10000, `lightScene`/`diyScene`/`snapshot` (options `[]`), `musicMode`.
- Fully standard RGBIC shape: `size.max (15) == elementRange.max+1 (15)`, so no clamp, no
  `SKU_SEGMENT_OVERRIDES` entry, not in `PTREAL_SEGMENT_SKUS`. `segment_count` = 15 is plausible.
- Poll state: `segmentedColorRgb`/`segmentedBrightness`/`gradientToggle` read back `""` (normal for every
  RGBIC SKU in `docs/device-catalog.md`). `powerSwitch`=0 at capture, `colorRgb`=0, `colorTemperatureK`=2272.
- `has_iot_credentials: false`, `mqtt: null`, LAN `device_count: 0`, BLE false: **REST is the only path**.
- `recent_commands` is empty (captured after a restart), so the HTTP 200s in the issue body are not in the file.
- Options: `segment_mode_by_device` = `both` (per-segment + grouped entities).

**Code path** (service -> REST, nothing SKU-specific):
- `services.py:150-194` `async_set_segment_color_handler` validates index `< device.segment_count`, builds
  `SegmentColorCommand(segment_indices=(0,), color=...)`.
- `models/commands.py:240-258` value `{"segment": [0], "rgb": 65280}` - exactly the documented v2 payload.
- `coordinator.py:5283-5287` records the colour; `coordinator.py:5384-5388` applies optimistic state then
  `_dispatch_segment_command` (`coordinator.py:5537-5576`) -> `api/client.py:654-729` POSTs
  `{"capability": {"type": "devices.capabilities.segment_color_setting", "instance": "segmentedColorRgb", ...}}`.
- No power-on is sent by the service; the strip powering on is the firmware reacting to the cloud write.

**Root cause:** not provable from the code. The integration sends the same payload that works on every other
RGBIC strip, Govee answers 200, and the device wakes but ignores the colour. That is the "accepted, does
nothing" pattern seen on H7076 indices 4-14 (#160) and H7026 16-29 (#208): a cloud-to-firmware mapping
problem for this SKU. Two candidates the data can't separate:
1. `gradientToggle` on: the firmware smooths/overrides per-segment colour (the app hides this). The
   integration has **no** `gradientToggle` handling anywhere (`grep -rn gradientToggle custom_components` is
   empty), so the user can't turn it off from HA.
2. The Platform API's `segmentedColorRgb` -> BLE translation is wrong for the H1A42 (new "Light Strip 2"
   generation). The masked ptReal frame `33 05 15 01 RR GG BB 00 00 00 00 00 LL HH` (`api/ble_packet.py:196`)
   is the standard RGBIC segment frame and is the likely working path, but needs account login (AWS IoT).

**Fix design (once confirmed):**
- If (1): expose `gradientToggle` as an optimistic `GoveeNamedLightToggleSwitch`-style switch
  (`switch.py:529+` pattern, translation key `gradient`), and optionally send `gradientToggle=0` before a
  segment write for SKUs in a new `GRADIENT_OFF_FOR_SEGMENTS_SKUS`.
- If (2): add `"H1A42"` to `PTREAL_SEGMENT_SKUS` (`const.py:210`). The existing tier at
  `coordinator.py:5356-5382` already folds indices into one mask and falls back to REST when the passthrough
  is down; no override needed (API count 15 is within the mask). `PTREAL_MAIN_PANEL_BIT` (`const.py:216`) is a per-SKU
  dict without H1A42, so no panel bit leaks in.

**Verdict: needs reporter data.** Ask: (a) in the Govee app, switch the strip's *Gradient* off, set a solid
colour, then rerun `set_segment_color` segments [0] green; does segment 1 turn green? (b) If not: enable the
account login (email/password) in the integration, then run `govee.send_raw_ptreal` with frame
`33 05 15 01 00 FF 00 00 00 00 00 00 01 00 00` (segment 1 green; 3-byte LE mask, bit i = index i) and
`33 05 15 01 00 FF 00 00 00 00 00 00 FF 7F 00` (all 15); report what lights.
(c) a diagnostics download right after (a), so `recent_commands` holds the segment write and Govee's answer.

## 2. #240 H7073 ("Projektor"): no colour control, only power/brightness/scenes

**Diagnostics** (`diag-1025/240/`, v2026.10.0, account login on, AWS IoT connected, 9 inbound messages):
- **The Platform API advertises no colour at all**: caps are `powerSwitch`, `brightness` 1-100,
  `lightScene`/`diyScene`/`snapshot` only. No `colorRgb`, no `colorTemperatureK`, no segments. `raw_api_state`
  matches (no colour fields).
- The device itself reports colour over AWS IoT: `last_mqtt_message` = `{"onOff":1,"brightness":100,
  "color":{"r":128,"g":176,"b":255},"colorTemInKelvin":0,"mode":3,...}` - the same schema `colorwc` writes.
  `_op_frames` carry `aa12 ... 00 ff ae 54` (the classic `33 05 02 RR GG BB 00 FF AE 54` white-reference
  tail), so the firmware speaks the standard BLE colour frames too.
- `recent_commands`: three `powerSwitch` writes, all HTTP 200. Options: `enable_mqtt_control` absent (default
  `False`, `const.py:270`). LAN none. BFF `wifiSoftVersion 1.06.24`, `pactType 1`.

**Root cause (provable):** `light.py:225-241` `_determine_color_modes` derives modes only from
`device.supports_rgb` / `supports_color_temp` (`models/device.py:427-435`), which look for `colorRgb` /
`colorTemperatureK` capabilities. Govee omits them for the H7073, so HA gets `{BRIGHTNESS}` and never shows a
colour picker. Nothing the integration sends is wrong; it has nothing to send.

**Fix design:**
- `const.py`: `MQTT_COLOR_SKUS: Final = frozenset({"H7073"})` (comment: #240, Platform API omits `colorRgb`,
  AWS IoT status reports `color`).
- `models/device.py` `from_api_response` (`:1403`): for those SKUs, when no `colorRgb` cap exists, append a
  synthesized `GoveeCapability(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB, {"dataType":"INTEGER",
  "range":{"min":0,"max":16777215}})`, mirroring `synthetic_thermometer` (`:1443`). No colour temp (no
  range advertised, `colorTemInKelvin` is 0).
- `coordinator.py` `_async_control_device` (`:5329-5341`): a `ColorCommand` for an `MQTT_COLOR_SKUS` device
  goes to `_try_mqtt_command` when `mqtt_connected and _mqtt_heard_from(device_id)` **regardless of
  `_enable_mqtt_control`** (it already sends `colorwc` plus the legacy `color` follow-up, `:5841-5848`). If
  AWS IoT is down, fall through to REST (Govee may still accept `colorRgb`; a rejection already raises the
  translated `command_failed`).
- Only synthesize when the entry has IoT credentials? Entities are built once at setup, so gate the synthesis
  in the coordinator's device processing on `has_iot_credentials` to avoid a colour picker that can only fail
  for API-key-only users.

**Verdict: fix now (root cause provable from data); delivery path unverified.** Ship SKU-scoped per the
"HACS reporters can't test branches" rule and ask in the release reply whether colour now changes. Fallback ask
if it doesn't: `govee.send_raw_ptreal` with `33 05 02 FF 00 00 00 FF AE 54` (red) and
`33 05 15 01 FF 00 00 00 00 00 00 00 FF FF 00` (masked red) and say which one turns it red.

## 3. #213 H66A0 TV Backlight 3 Pro: DreamView switch doesn't start screen sync

**Diagnostics** (`diag-1025/213/`, v2026.10.0, account login on, AWS IoT connected, 12k inbound), taken while
DreamView ran **from the app** (Video mode, Movie):
- Caps include `toggle/dreamViewToggle` (on/off), `movie_setting/movieMode` `STRUCT{movieMode: ENUM[Game=0]}`,
  `gradientToggle`, segments, `musicMode`. REST state: `dreamViewToggle` `""`, `movieMode` `""` (no readback).
- `state.dreamview_enabled: true` is **not device truth**: REST gives `""` (`bool("")` is False,
  `models/state.py:456`), so the True is the HA optimistic value from an earlier `switch.turn_on`
  (`coordinator.py:6153`) kept across polls by `_preserve_optimistic_field(... "dreamview_enabled")`
  (`coordinator.py:4902`). That is why Audiosenpai sees the switch ON while sync isn't running.
- Device truth is in `last_mqtt_message`: `"mode": 0` and op frame
  **`aa 05 00 00 08 64 00 02 00 ... c1`** - the mode report for video mode as the *app* set it.
- `recent_commands` holds only power/colour/brightness REST writes; no DreamView attempt in this capture.

**What the integration sends for H66A0** (`PTREAL_DREAMVIEW_SKUS`, `const.py:529`):
- `coordinator.py:6078-6157` `async_send_dreamview`: `skip_rest` True for H66A0 (`:6096`), so ON goes to
  `ble_passthrough.py:130` -> `build_dreamview_packet()` (`api/ble_packet.py:311-339`) =
  **`33 05 00 01 00 64`** + zero pad + XOR (all segments, style movie, saturation 100). OFF restores colour.
- Byte-for-byte vs the app's state: `[3]` 01 vs **00**, `[4]` 00 vs **08**, `[5]` 64 = 64, `[7]` 00 vs **02**.
  The H66A0 uses a different video-mode payload layout from the H6199-era frame the builder encodes. `aa`
  status replies mirror the `33` write payload on Govee lights (e.g. `aa 05 04` scene, `aa 11`, `aa 12`), so
  `33 05 00 00 08 64 00 02` is the strongest candidate for the app's ON frame.
- R2A80 data point (Rusich90): REST `dreamViewToggle` works there; response carries `'dreamType': 'movie'`.
  Keep handling keyed on SKU (no product-name logic). R2A80 needs nothing.

**movieMode:** only option is `Game=0`, while the app runs Movie. On the H2A41 `movieMode` is only used for
*detection* (`models/device.py:882-894`); the command path still sends `dreamViewToggle`. Nothing in the
data shows `movieMode` starts sync on the H66A0, and an enum with a single "Game" value isn't worth an
entity. Don't expose it; keep it as candidate 3 only if both frames fail.

**Fix design:**
1. `api/ble_packet.py`: `build_dreamview_packet(sku: str = "")` takes a per-SKU payload table
   `DREAMVIEW_VIDEO_PAYLOAD_BY_SKU = {"H66A0": (0x00, 0x08, 0x64, 0x00, 0x02)}` (bytes after `33 05 00`);
   default stays `01 00 64`. `ble_passthrough.py:130` passes `sku` through.
2. New `GoveeDeviceState.update_dreamview_from_frames` in `models/state.py`, wired next to the other op-frame
   decoders at `coordinator.py:3764-3794`: treat `aa 05 00` as `dreamview_enabled = True` and any other
   `aa 05 <mode>` as False, for devices that `supports_dreamview` - so the switch follows the device and the
   stale optimistic ON (`coordinator.py:4902`) is overwritten by a real report. This also helps H605B (#220,
   same `aa 05 00` signature). Applies to every DreamView device that reports op frames, so test widely.

**Verdict: needs reporter data (one quick test), then fix.** Ask Audiosenpai: with the backlight on and NOT
in video mode, run `govee.send_raw_ptreal` with frame `33 05 00 00 08 64 00 02`; does screen sync start (in
Movie)? If not, try `33 60 01 01 01` (the H2A41 frame). Item 2 (state from `aa 05 00`) is provable from this
capture and can ship independently.

## 4. #239 H7045 stuck "White light" scene, and #269 scene select should show None while off

**Data:** #239 has no diagnostics (the data request is unanswered; reporter replied in text only). #269 is a
feature request, no data needed. #18 (closed) is the opposite request that shipped in v2026.3.1: keep the scene
shown while off "so it reflects the scene that will resume".

**Current behaviour, traced:**
- `select.py:315-328` `GoveeSceneSelectEntity.current_option` returns the option for `state.active_scene`,
  else `"None"`; **no power check**. Same in `GoveeDIYSceneSelectEntity.current_option` (`select.py:411-424`)
  and `GoveeSnapshotSelectEntity` (`select.py:615-623`, returns `None` = unknown).
- `coordinator.py:4781-4788`: every poll copies `active_scene`/`active_scene_name`/`active_diy_scene` from the
  previous state ("Scenes persist on device across power cycles - always preserve"). Power off
  (`models/state.py:894-897` `apply_optimistic_power`) does not touch them. Only colour/CT/music/DreamView/
  DIY/scene writes clear them (`models/state.py:904-947`).
- `select.py:330-349` `async_select_option` always sends `SceneCommand` (`models/commands.py:176-194`) via
  `_async_send_command`; the coordinator has no same-scene dedupe.
- HA core: `SelectEntity.async_handle_select_option` only validates the option, then awaits
  `async_select_option` (checked in the HA 2026.9 source: `components/select/__init__.py:188-191`). So the
  **`select.select_option` service with the already-selected scene does resend it**; automations work today.
- HA frontend: the select row / more-info dropdown only fires on a *change*, so picking the option already
  shown does nothing (frontend behaviour, from the HA frontend select row; not verifiable in this repo). This
  is the whole of #269's step 3 in the UI.
- Existing workaround for both UI and automations: `light.turn_on` with `effect: <scene>` always sends the
  scene (`light.py:343-359`), and HA already hides `effect` while the light is off.

**#269 design options** (every option changes behaviour for every light with scenes, so it ships alone on its
own release day per the broad-behaviour-change rule, after a hands-on try):
- **A. Presentation only (recommended).** `current_option` returns `SCENE_NONE` when
  `state.power_state is False`; `active_scene` stays in state untouched. Turning the light on by any path
  (HA, app, MQTT/LAN push, which updates `power_state`) shows the scene again, which is what the device does
  (it resumes the scene). Re-picking the scene while off now differs from the shown "None", so the frontend
  fires and the scene command powers the light on. Small diff (2-3 properties + tests), no coordinator change.
  Cost: reverses #18's "show what will resume"; the select's history/logbook gets a scene->None->scene pair
  per off/on, and automations triggered on the select's state change fire on power changes. `power_state None`
  (unknown) keeps showing the scene.
- **B. Clear on off, restore on on.** Clear `active_scene` in `apply_optimistic_power(False)` and the poll
  merge, keep `last_scene_id`, re-set `active_scene` when power returns. More state paths (poll merge at
  `coordinator.py:4781`, MQTT/LAN power pushes, music-mode restore which already reads `last_scene_id` at
  `coordinator.py:6048`), easy to get wrong, and an off->on via the app that doesn't resume the scene would
  still be shown as the scene. No benefit over A.
- **C. Opt-in option** ("Show no scene while the light is off"). Avoids breaking #18-style users but adds an
  options-flow field, strings, and keeps two behaviours tested forever (config_flow.py must stay at 100%).
- **D. No code change.** Reply with the `light.turn_on effect:` workaround and the fact that the select
  service already resends. Cheapest; doesn't fix the dashboard dropdown.
Recommendation: A for scene + DIY selects (snapshot select is the same shape but returns unknown, decide
separately), with D's workaround in the reply now. Keep the light entity's `effect` as is (HA hides it when off).

**#239 is a different problem.** Picking a *different* scene is a change, so the frontend fires and the
integration sends `lightScene` every time; nothing in HA can "lock" the select on White light. If the light
stays white, Govee accepted (or rejected, which would raise `command_failed` in the UI) a scene the firmware
ignored. Likely an H7045 quirk: "White light" drives the dedicated white LEDs (the #239 feature ask), and the
firmware or cloud doesn't leave that mode for a `lightScene` until a power cycle. One integration-side
suspect worth checking with data: `SceneCommand.get_value` sends `{"id", "name"}` and drops the `paramId`
that the scene list provides and the documented payload uses (`docs/govee-protocol-reference.md:209-218`).
Changing that is a payload change for every scene on every light (own release day), so it needs evidence first.
**Verdict #239: needs reporter data.** Ask for a diagnostics download (account login on) taken right after:
select White light, wait 10 s, select another scene (e.g. Sunset) from HA. Need `recent_commands` (both
`lightScene` writes and Govee's answers) and `last_mqtt_message._op_frames` (does the mode frame change?).
Also: does the Govee app switch straight from White light to another scene without a power cycle?
**Verdict #269: design decision, no data needed;** implement A on its own day if the maintainer accepts
reversing #18.

## Proposed tasks

| # | Title | Files | Grep-able symbol | Ships |
|---|-------|-------|------------------|-------|
| T1 | H7073: synthesize `colorRgb`, send colour over AWS IoT regardless of the MQTT-control option | `const.py`, `models/device.py`, `coordinator.py` | `MQTT_COLOR_SKUS` | normal day; reply asks for confirmation |
| T2 | DreamView state from the device's `aa 05` mode frame | `models/state.py`, `coordinator.py` | `update_dreamview_from_frames` | normal day (all DreamView devices: check H605B/H6199 tests) |
| T3 | H66A0 video-mode payload from the app's frame | `api/ble_packet.py`, `ble_passthrough.py` | `DREAMVIEW_VIDEO_PAYLOAD_BY_SKU` | after the reporter's `send_raw_ptreal` test confirms the frame |
| T4 | Scene/DIY selects show None while the light is off | `select.py`, `tests/test_cov_select.py` | `_scene_hidden_while_off` | own release day (all lights; reverses #18) |
| T5 | (conditional) H1A42 segments via masked ptReal | `const.py` | `"H1A42"` in `PTREAL_SEGMENT_SKUS` | only if the reporter's ptReal test works |
| T6 | (conditional) `gradientToggle` switch | `switch.py`, `strings.json`, `icons.json` | `GoveeGradientToggleSwitch` | only if turning gradient off fixes #261 |

**Reporter replies instead of code (queue in `docs/release-replies.md`):**
- #261 (cbaum0407): gradient-off retest, then account login + the two `send_raw_ptreal` frames, plus a
  diagnostics download right after (section 1).
- #213 (Audiosenpai): `send_raw_ptreal` `33 05 00 00 08 64 00 02`, then `33 60 01 01 01`; say which starts
  sync. Thank Rusich90; R2A80 needs nothing. T2 can be cited once it ships.
- #239 (Peyto79): diagnostics after White light -> other scene from HA, and whether the app switches directly.
- #269: explain `select.select_option` already resends and `light.turn_on effect:` works today; say the
  dropdown change (T4) is planned for its own release.
- #240 (sertanakar-tech): after T1 ships, ask whether colour changes; fallback frames in section 2.
