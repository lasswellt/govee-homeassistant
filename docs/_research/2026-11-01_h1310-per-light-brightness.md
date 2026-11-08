# H1310 ceiling fan: separate main / background light control

- **Date**: 2026-11-01
- **Type**: Device Investigation
- **Question**: Can the H1310's main downlight and background (ring) light be dimmed and coloured independently from Home Assistant?
- **Status**: Partly answered. The Govee app shows that the device supports it. The Developer API path (`segmentedBrightness` / `segmentedColorRgb`) still needs the step A-D test results below.

## Summary

The H1310 shows Govee's cloud API 9 segments. In the Govee app's segment screen, **segment 0 is the main downlight (the centre disc)** and **segments 1-8 are the background ring**. Each segment has its own colour and a "relative brightness" that is multiplied by the global `brightness`. So the hardware supports separate per-light brightness and colour, layered under one master dimmer. Homebridge and press coverage say "one brightness, one colour"; that describes the global channel only.

The integration does not use this today. A segment-0 colour write from HA locks out `mainLightToggle` until the unit is power-cycled, and a first `segmentedBrightness` test showed no change, probably because global brightness was at 2% at the time.

## Device facts

| Item | Value | Source |
|---|---|---|
| Model | Govee 52" Ceiling Fan with Lights, H1310 (R1310 is a variant) | Govee US product page |
| Main light | RGBWW, 2,800 lm | Govee US product page |
| Background light | RGBICWW ring; no lumen figure published | Govee US product page |
| CCT | 1,000-10,000 K marketed; Developer API accepts 2,700-6,500 K | Product page, diagnostics |
| Fan | 6 speeds, reversible DC motor, 5,000 CFM, 72 W | Product page |
| Matter | 1.3, certified 2026-01-05 (CSA26001MAT49409-24, firmware 1.0) | Matter Alpha |
| Reporter's unit | wifi 1.07.05 / 1.01.05, hw 3.08.01, sw 1.00.31, `ic` 9, no `matterId` in BFF | Diagnostics 2026-11-01 |
| Segments | API `elementRange` 0-8 (9). An earlier capture in `docs/device-catalog.md` had 0-7 (8) | Diagnostics vs catalog |

## Findings

### 1. Segment map (Govee app, reporter-verified)

The app's "segment control" draws a centre disc plus an outer ring of 8. Each one has its own selection checkbox, colour and "Relative brightness" percentage. In the screenshot the centre was at 1% and every ring segment at 100%. Using the HA "Segment 1" entity (index 0) changed the **main light**, which confirms index 0 is the downlight.

### 2. Segment-0 colour write breaks `mainLightToggle`

After a `segmentedColorRgb` write to segment 0 from HA, later `mainLightToggle` commands returned HTTP 200 `success` but the device ignored them. Cycling the whole-unit power (the app's power button, i.e. `powerSwitch`) restored them. This matches the H1270 coupling documented under #131 (`GoveeMainLightEntity` in `light.py`). The cloud reports success, so HA cannot detect the failure.

### 3. Global brightness is a master multiplier

The app labels segment brightness "relative". The first `segmentedBrightness` test (ring at 10%) was run with global `brightness` = 2, so the visible change would have been about 2% → 0.2%. The retest (below) sets global to about 100% first.

### 4. Other integrations

| Project | What it does with the H1310 |
|---|---|
| homebridge-govee v11.37.0 | Two tiles: Main Light (shared brightness + colour) and Background Light (on/off only). States "the fan has only one brightness and one colour between both lights". Also switches the light off with its own toggle "instead of dimming both its lights to nothing", and scales brightness and fan speed (6 steps) |
| HA Matter integration | Works once "Enable test-net DCL usage" is on. Fan entity is good. Whether there are two independent light endpoints is not documented |
| govee2mqtt | Reported to work with more granular control; state does not update unless set from HA; scenes do not work |

Nobody has documented per-segment control for this model yet.

### 5. Transport observations from the same diagnostics

- LAN brightness and CCT writes returned "device reported a different value than was sent", so the integration fell back to the cloud and set `write_suppressed`. The writes still landed, just more slowly.
- The Developer API poll lagged the device: `brightness: 2` and `backgroundLightToggle: 0` while MQTT/LAN reported 98 and on. The coordinator kept the newer pushed values.
- The poll now returns real values for `mainLightToggle`, `backgroundLightToggle`, `fanToggle`, `fanSpeedMode` and `reverseAirflowToggle`. The earlier catalog entry recorded `""` for all of them.
- The segment entities store brightness but never send it (`platforms/segment.py`, `async_turn_on` only sends `SegmentColorCommand`). This affects every RGBIC device.

### 6. Unrelated outage seen during triage

The first diagnostics showed all five account devices `online: false` with zero MQTT inbound messages (`mqtt_silent` issue since 2026-10-25). It was a Wi-Fi-side problem the reporter fixed. The integration's availability logic (`device_reachable`) behaved correctly.

## Pending verification (Developer API, global brightness ~100%)

```bash
sb '[0]' 100                    # A: main back to full
sb '[1,2,3,4,5,6,7,8]' 15       # B: only ring dims?
sb '[0]' 20                     # C: only main dims?
sc '[1,2,3,4,5,6,7,8]' 255      # D: ring blue, main unchanged?
```

`sb` sends `segment_color_setting/segmentedBrightness {segment, brightness}`; `sc` sends `segmentedColorRgb {segment, rgb}`. After each step, record whether `mainLightToggle` still works. Calls made straight to the API do not show up in HA diagnostics, so results must be reported by hand.

## Proposed design (if A-D pass)

| Entity | On/off | Brightness | Colour |
|---|---|---|---|
| Whole fixture (existing) | `powerSwitch` | global `brightness` (master) | whole-device CCT / RGB |
| Main light (new) | `mainLightToggle` | `segmentedBrightness [0]` | CCT via `colorTemperatureK`; RGB via segment 0 only if proven not to lock the toggle |
| Background light (new) | `backgroundLightToggle` | `segmentedBrightness [1-8]` | `segmentedColorRgb [1-8]` |
| Ring segments (optional) | n/a | per-segment `segmentedBrightness` | per-segment colour |

Also:
- Never create a segment entity for index 0 on the H1310/R1310.
- Make segment entities send brightness (all RGBIC devices).
- Keep the per-light state optimistic + `RestoreEntity`, since the poll returns `""` for segment state.
- Add a `SKU_SEGMENT_OVERRIDES`-style constant for the H1310 zone map; do not derive it from the API count (8 vs 9 differs between firmwares).

**Fallback if B/C show no effect:** keep the Homebridge model (shared brightness on the main entity, background on/off only) and drop H1310 segment entities.

## Sources

- https://us.govee.com/products/govee-52-inch-ceiling-fan-with-lights
- https://github.com/homebridge-plugins/homebridge-govee/releases/tag/v11.37.0
- https://community.home-assistant.io/t/govee-h1310-working-both-light-and-fan-in-matter/1020886
- https://www.matteralpha.com/govee/govee-ceiling-fan-with-lights-p4105
- https://www.notebookcheck.net/New-Govee-Ceiling-Fan-with-Lights-on-the-way.1279446.0.html
- Reporter diagnostics and Govee app screenshots, 2026-11-01 (not committed; they contain account data)
