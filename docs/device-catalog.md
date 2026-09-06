# Device catalog from submitted diagnostics

Everything below was read out of the diagnostics downloads (and inline dumps) attached to issues and pull requests between 2026-03-06 and 2026-09-06 — 67 files, 107 SKUs. It records what Govee's APIs actually returned for each model, not what the integration supports; see the README for the support table and `govee-protocol-reference.md` for protocol detail.

How to read an entry:

- **Developer API** — `type` and every capability the `/user/devices` list advertised (`type/instance`), with the parameters that matter (option names→values, ranges, segment sizes).
- **State readback** — what `/device/state` returned per instance in the captures. `""` means Govee returned an empty string on poll (the integration has to keep optimistic state for it).
- **Account (BFF) list** — whether the model appears in the app-account device list, which `deviceSettings` / `lastDeviceData` keys it carried, and the gateway it hangs off.
- **AWS IoT push** — the keys seen in the device's `state` push, when a capture had one.
- **Rejected commands** — control commands Govee answered with an error, verbatim.
- **Seen in** — the issues/PRs the captures came from.

Models the account list knows but the Developer API never returned (no `type`, no capabilities — they are BLE-only or simply not enabled for the public API): H3500, H3510, H5086, H5122, H5126, H5129, H6006, H6046, H605C, H616C, H61B5, H7057, H7075, H7162, H805C.

## H1250

Developer API type `devices.types.light`; seen in #131.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–16 elementRange 0–15; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–16 elementRange 0–15; field rgb range 0–16777215
- `toggle/backgroundLightToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0
- `toggle/mainLightToggle` — options on=1, off=0

State readback: `brightness`=100/80, `colorRgb`=16711680/16768952, `colorTemperatureK`=0/4500, `online`=true, `powerSwitch`=0/1; returns `""` for `backgroundLightToggle`, `diyScene`, `gradientToggle`, `lightScene`, `mainLightToggle`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `result`.

## H1270

Developer API type `devices.types.light`; seen in #131.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot` — options Work=4118470
- `music_setting/musicMode` — field musicMode options Ripple=0, Gridding=1, Flame=2, Sky=3, Color Painting=4, Sprouting=5, Hopping=6, Disassociate=7, Floating Mist=8, Separation=9, Meteor shower=10, Flexing=11; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–12 elementRange 0–11; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–12 elementRange 0–11; field rgb range 0–16777215
- `toggle/backgroundLightToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0
- `toggle/mainLightToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=0, `colorTemperatureK`=6500, `online`=true, `powerSwitch`=1; returns `""` for `backgroundLightToggle`, `diyScene`, `gradientToggle`, `lightScene`, `mainLightToggle`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `result`.

## H1310

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #74, #114, #181.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `mode/fanSpeedMode` — options Speed 1=1, Speed 2=2, Speed 3=3, Speed 4=4, Speed 5=5, Speed 6=6
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–8 elementRange 0–7; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–8 elementRange 0–7; field rgb range 0–16777215
- `toggle/backgroundLightToggle` — options on=1, off=0
- `toggle/fanToggle` — options on=1, off=0
- `toggle/mainLightToggle` — options on=1, off=0
- `toggle/reverseAirflowToggle` — options on=1, off=0

State readback: `brightness`=2/36, `colorRgb`=0/10027263, `colorTemperatureK`=0/4000, `online`=true, `powerSwitch`=0; returns `""` for `backgroundLightToggle`, `diyScene`, `fanSpeedMode`, `fanToggle`, `lightScene`, `mainLightToggle`, `reverseAirflowToggle`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `subDevices`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`, `wifiFuncList`.

## H1370

Developer API type `devices.types.light`; seen in #105, #114.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `mode/fanSpeedMode` — options Speed 1=1, Speed 2=2, Speed 3=3, Speed 4=4, Speed 5=5, Speed 6=6, Speed 7=7, Speed 8=8, Speed 9=9, Speed 10=10, Speed 11=11, Speed 12=12
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–14 elementRange 0–13; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–14 elementRange 0–13; field rgb range 0–16777215
- `toggle/backgroundLightToggle` — options on=1, off=0
- `toggle/fanOscillateToggle` — options on=1, off=0
- `toggle/fanToggle` — options on=1, off=0
- `toggle/mainLightToggle` — options on=1, off=0
- `toggle/reverseAirflowToggle` — options on=1, off=0

State readback: `brightness`=20/29, `colorRgb`=0, `colorTemperatureK`=4220/5025, `online`=true, `powerSwitch`=0/1; returns `""` for `backgroundLightToggle`, `diyScene`, `fanOscillateToggle`, `fanSpeedMode`, `fanToggle`, `lightScene`, `mainLightToggle`, `reverseAirflowToggle`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `subDevices`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`, `wifiFuncList`.

## H14C0

Developer API type `devices.types.light`; seen in #131.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `toggle/dreamViewToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=0, `colorTemperatureK`=2700, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `dreamViewToggle`, `lightScene`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H3500

Not returned by the Developer API; seen in #114.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H3510

Not returned by the Developer API; seen in #114.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H5053

Developer API type `devices.types.thermometer`; seen in #173.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=true, `sensorHumidity`=53.9, `sensorTemperature`=70.55.

Account (BFF) list: not seen (captures without account login, or not listed).

## H5054

Developer API type `devices.types.sensor`; seen in #62, #145.

Capabilities:

- `event/bodyAppearedEvent`

State readback: `online`=false.

Account (BFF) list: yes — `deviceSettings` carries `battery`, `deviceName`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `gwonline`, `lastTime`, `online`, `read`.

## H5058

Developer API type `devices.types.sensor`; gateway: `H5043`; seen in #134.

Capabilities:

- `event/bodyAppearedEvent`

Account (BFF) list: yes — `deviceSettings` carries `battery`, `deviceName`, `gatewayInfo`, `sno`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `gwonline`, `lastTime`, `online`, `read`.

## H5059

Developer API type `devices.types.sensor`; seen in #87, #101.

Capabilities:

- `event/bodyAppearedEvent`

State readback: `online`=false.

Account (BFF) list: not seen (captures without account login, or not listed).

## H5075

Developer API type `devices.types.thermometer`; seen in #83, #99, #102, #132, #159.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=false/true, `sensorHumidity`=""/41.7/46.6, `sensorTemperature`=""/68.36/71.24.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`.

## H5080

Developer API type `devices.types.socket`; seen in #131, #181.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0

State readback: `online`=true, `powerSwitch`=0/1.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H5083

Developer API type `devices.types.socket`; seen in #62.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0

State readback: `online`=true, `powerSwitch`=0.

Account (BFF) list: not seen (captures without account login, or not listed).

## H5086

Not returned by the Developer API; seen in #114.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H5089

Developer API type `devices.types.socket`; seen in #59, #114.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `mode/nightlightScene` — options Forest=0, Ocean=1, Wetland=2, Leisurely=3, Asleep=4
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100
- `toggle/nightlightToggle` — options on=1, off=0
- `toggle/socketToggle1` — options on=1, off=0
- `toggle/socketToggle2` — options on=1, off=0

State readback: `brightness`=17/18, `colorRgb`=16769647/65280, `nightlightScene`=4, `nightlightToggle`=0/1, `online`=true, `powerSwitch`=1, `socketToggle1`=1, `socketToggle2`=0/1.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `subDevices`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H5103

Developer API type `devices.types.thermometer`; seen in #85.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

Account (BFF) list: not seen (captures without account login, or not listed).

## H5106

Developer API type `devices.types.thermometer`; seen in #114, #150.

Capabilities:

- `property/airQuality`
- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `airQuality`=1, `online`=true, `sensorHumidity`=48/49/49.2, `sensorTemperature`=71.6/71.78/73.04.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H5109

Developer API type `devices.types.thermometer`; gateway: `H5042`; seen in #62, #83, #96, #132, #134.

Capabilities:

- `property/sensorTemperature`

State readback: `online`=true, `sensorTemperature`=100.83/81.68/84.48.

Account (BFF) list: yes — `deviceSettings` carries `battery`, `deviceName`, `fahOpen`, `gatewayInfo`, `sno`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`.

## H5110

Developer API type `devices.types.thermometer`; gateway: `H5044`; seen in #83, #102, #114, #132.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=true, `sensorHumidity`=35.9/48.4/48.8, `sensorTemperature`=68.9/71.24/72.14.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `gatewayInfo`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`.

## H5111

Developer API type `devices.types.thermometer`; gateway: `H5151`; seen in #83, #134, #144.

Capabilities:

- `property/sensorTemperature`

State readback: `online`=false/true, `sensorTemperature`=""/-2.2/-4.54.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `gatewayInfo`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`.

## H5112

Developer API type `devices.types.thermometer`; gateway: `H5044`; seen in #150.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=false; returns `""` for `sensorHumidity`, `sensorTemperature`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `gatewayInfo`, `pactCode`, `pactType`, `sno`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`, `tem2`.

## H5122

Not returned by the Developer API; seen in #131.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `bind`, `logTime`, `logType`, `online`.

## H5126

Not returned by the Developer API; seen in #114, #128.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `bind`, `logTime`, `logType`, `online`.

## H5129

Not returned by the Developer API; seen in #114.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `bind`, `logTime`, `logType`, `online`.

## H5140

Developer API type `devices.types.air_quality_monitor`; seen in #116.

Capabilities:

- `property/carbonDioxideConcentration`
- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `carbonDioxideConcentration`=609, `online`=true, `sensorHumidity`=51.9, `sensorTemperature`=73.94.

Account (BFF) list: not seen (captures without account login, or not listed).

AWS IoT push `state` keys: `onOff`, `result`, `sta`.

## H5179

Developer API type `devices.types.thermometer`; seen in #72, #141.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=false; returns `""` for `sensorHumidity`, `sensorTemperature`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `pactCode`, `pactType`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`.

## H5220

Developer API type `devices.types.thermometer`; gateway: `H5044`; seen in #114, #128.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=true, `sensorHumidity`=43.9, `sensorTemperature`=76.28.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `gatewayInfo`, `pactCode`, `pactType`, `sno`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H5310

Developer API type `devices.types.thermometer`; gateway: `H5044`; seen in #86, #97, #150, #157.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=true, `sensorTemperature`=88.34.

Account (BFF) list: yes — `deviceSettings` carries `battery`, `deviceName`, `fahOpen`, `gatewayInfo`, `sno`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`.

## H6006

Not returned by the Developer API; seen in #114.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `subDevices`, `supportBleBroadV3`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H6008

Developer API type `devices.types.light`; seen in #131, #150, #158.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

State readback: `brightness`=""/100/20, `colorRgb`=""/0/16764326, `colorTemperatureK`=""/4000/4300, `online`=false/true, `powerSwitch`=""/0/1; returns `""` for `diyScene`, `lightScene`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `subDevices`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H6010

Developer API type `devices.types.light`; seen in #150.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

State readback: `brightness`=100/49/50, `colorRgb`=0, `colorTemperatureK`=3800/4000/4200, `online`=false, `powerSwitch`=1; returns `""` for `diyScene`, `lightScene`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H601A

Developer API type `devices.types.light`; seen in #131.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

State readback: `brightness`=85, `colorRgb`=16712192, `colorTemperatureK`=5485, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `lightScene`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `color`, `colorTemInKelvin`, `result`.

## H601F

Developer API type `devices.types.light`; seen in #159.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–7 elementRange 0–6; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–7 elementRange 0–6; field rgb range 0–16777215

State readback: `brightness`=1/100, `colorRgb`=0/16777215, `colorTemperatureK`=0/2700, `online`=false/true, `powerSwitch`=0/1; returns `""` for `diyScene`, `lightScene`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`.

## H6022

Developer API type `devices.types.light`; seen in #72, #186.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=5, Rhythm=3, Spectrum=6, Rolling=4; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215

State readback: `brightness`=80, `colorRgb`=16777215, `colorTemperatureK`=0, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `lightScene`, `musicMode`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: not seen (captures without account login, or not listed).

Rejected commands:

- `musicMode={"musicMode": 1, "sensitivity": 44, "autoColor": 1} -> HTTP 200 Parameter value out of range`
- `musicMode={"musicMode": 1, "sensitivity": 50, "autoColor": 1} -> HTTP 200 Parameter value out of range`
- `musicMode={"musicMode": 1, "sensitivity": 77, "autoColor": 1} -> HTTP 200 Parameter value out of range`
- `musicMode={"musicMode": 1, "sensitivity": 78, "autoColor": 1} -> HTTP 200 Parameter value out of range`
- `musicMode={"musicMode": 1, "sensitivity": 79, "autoColor": 1} -> HTTP 200 Parameter value out of range`

## H6046

Not returned by the Developer API; seen in #126.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H6054

Developer API type `devices.types.light`; seen in #158.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Vivid=0, Strike=1, Rhythm=2, Vibrate=3, Beat=4, Torch=5, RainbowCircle=6, Shiny=7; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `toggle/dreamViewToggle` — options on=1, off=0

State readback: `brightness`=11/94, `colorRgb`=16776960/65535, `colorTemperatureK`=0, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `dreamViewToggle`, `lightScene`, `musicMode`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `supportBleBroadV3`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `result`.

## H605A

Developer API type `devices.types.light`; seen in #85, #99.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Rhythm=1, Windmill=2, Hooray=3, Sprouting=4; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–24 elementRange 0–23; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–24 elementRange 0–23; field rgb range 0–16777215
- `toggle/backLightToggle` — options on=1, off=0
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0
- `toggle/leftLightToggle` — options on=1, off=0
- `toggle/rightLightToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=16777215, `colorTemperatureK`=0, `online`=false, `powerSwitch`=0; returns `""` for `backLightToggle`, `diyScene`, `dreamViewToggle`, `gradientToggle`, `leftLightToggle`, `lightScene`, `musicMode`, `rightLightToggle`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H605C

Not returned by the Developer API; seen in #126.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H6061

Developer API type `devices.types.light`; seen in #72.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Calm=0, Dynamic=1, Energic=2, Hopping=3, Stacking=4, Rippling=5, Swiping=6; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–21 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–21 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

Account (BFF) list: not seen (captures without account login, or not listed).

## H6072

Developer API type `devices.types.light`; seen in #60.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=0, Dynamic=1, Calm=2, Bounce=3, Hopping=4, Strike=5, Vibrate=6, Skittles=7, Torch=8, CandyCrush=9, Fusion=10, Luminous=11, Separation=12; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–8 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–8 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

Account (BFF) list: not seen (captures without account login, or not listed).

## H6076

Developer API type `devices.types.light`; seen in #60, #104.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2200–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=0, Dynamic=1, Calm=2, Bounce=3, Hopping=4, Strike=5, Vibrate=6, Skittles=7, Torch=8, CandyCrush=9, Fusion=10, Luminous=11, Separation=12; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–7 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–7 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=50, `colorRgb`=0, `colorTemperatureK`=2700, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H6095

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #175.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

State readback: `brightness`=70, `colorRgb`=507576, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `lightScene`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `subDevices`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`.

## H6097

Developer API type `devices.types.light`; seen in #85.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Rhythm=1, Spectrum=2, Rolling=3, Separation=4, Hopping=5, PianoKeys=6, Fountain=7, DayAndNight=8, Sprouting=9, Shiny=10, Energic=11; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–14 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–14 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

Account (BFF) list: not seen (captures without account login, or not listed).

## H60A1

Developer API type `devices.types.light`; seen in #85, #104, #114.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2200–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–13 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–13 elementRange 0–12; field rgb range 0–16777215

State readback: `brightness`=100, `colorRgb`=16711680, `colorTemperatureK`=2200/6000, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `lightScene`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H60A6

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #127, #159.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `toggle/backgroundLightToggle` — options on=1, off=0
- `toggle/mainLightToggle` — options on=1, off=0

State readback: `brightness`=100/75, `colorRgb`=0/16756308, `colorTemperatureK`=2700/2708, `online`=true, `powerSwitch`=0; returns `""` for `backgroundLightToggle`, `diyScene`, `lightScene`, `mainLightToggle`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `result`.

## H60B0

Developer API type `devices.types.light`; seen in #83.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options DIY=0, Stippling=1, Hopping=2, Flowing Light=3, Luminous=4, Sprouting=5, Rhythm=6, Shiny=7; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/bottomLightToggle` — options on=1, off=0
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/rippleLightToggle` — options on=1, off=0
- `toggle/sideLightToggle` — options on=1, off=0

State readback: `online`=false; returns `""` for `bottomLightToggle`, `brightness`, `colorRgb`, `colorTemperatureK`, `diyScene`, `dreamViewToggle`, `lightScene`, `musicMode`, `powerSwitch`, `rippleLightToggle`, `segmentedBrightness`, `segmentedColorRgb`, `sideLightToggle`, `snapshot`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H60B2

Developer API type `devices.types.light`; seen in #104.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Stippling=0, Rhythm=1, Hopping=2, Colorful=3, Luminous=4, Rolling=5, Sprouting=6; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–3 elementRange 0–2; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–3 elementRange 0–2; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/light1Toggle` — options on=1, off=0
- `toggle/light2Toggle` — options on=1, off=0
- `toggle/light3Toggle` — options on=1, off=0

State readback: `brightness`=26, `colorRgb`=0, `colorTemperatureK`=3967, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `dreamViewToggle`, `light1Toggle`, `light2Toggle`, `light3Toggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H60B3

Developer API type `devices.types.light`; seen in #126.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options DIY=0, Stippling=1, Hopping=2, Flowing Light=3, Luminous=4, Sprouting=5, Rhythm=6, Shiny=7; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/bottomLightToggle` — options on=1, off=0
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/nebulaLightToggle` — options on=1, off=0
- `toggle/sideLightToggle` — options on=1, off=0

State readback: `bottomLightToggle`=1, `brightness`=100, `colorRgb`=0, `colorTemperatureK`=6500, `nebulaLightToggle`=0, `online`=true, `powerSwitch`=0, `sideLightToggle`=1; returns `""` for `diyScene`, `dreamViewToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `result`.

## H60C1

Developer API type `devices.types.light`; seen in #131.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Stippling=0, Hopping=1, Colorful=2, Luminous=3, Rolling=4, Piano Keys=5; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–3 elementRange 0–2; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–3 elementRange 0–2; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0

State readback: `brightness`=89, `colorRgb`=0, `colorTemperatureK`=5196/5200, `online`=true, `powerSwitch`=0/1; returns `""` for `diyScene`, `dreamViewToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H612D

Developer API type `devices.types.light`; seen in #159.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot` — options Matrix=2460679
- `music_setting/musicMode` — field musicMode options Rhythm=0, Sprouting=1, Shiny=2; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–10 elementRange 0–9; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–10 elementRange 0–9; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=16777215, `colorTemperatureK`=0, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`.

## H612F

Developer API type `devices.types.light`; seen in #159.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Rhythm=0, Sprouting=1, Shiny=2; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–10 elementRange 0–9; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–10 elementRange 0–9; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=16777215, `colorTemperatureK`=0, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`.

## H6144

Developer API type `devices.types.light`; seen in #99.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot` — options 1=162729
- `music_setting/musicMode` — field musicMode options Energic=0, Spectrum=1, Rolling=2, Rhythm=3, Separation=4, Hopping=5, PianoKeys=6, Fountain=7, DayAndNight=8, Sprouting=9, Shiny=10; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=70, `colorRgb`=16777215, `colorTemperatureK`=4333, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H6159

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #149.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `music_setting/musicMode` — field musicMode options Rhythm=0, Sprouting=1, Shiny=2; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

State readback: `brightness`=100, `colorRgb`=16777215/255, `colorTemperatureK`=0, `online`=true, `powerSwitch`=0/1; returns `""` for `diyScene`, `lightScene`, `musicMode`.

Account (BFF) list: not seen (captures without account login, or not listed).

Rejected commands:

- `brightness=47 -> HTTP None device reported a different value than was sent`
- `brightness=9 -> HTTP None device reported a different value than was sent`
- `powerSwitch=0 -> HTTP None device reported a different value than was sent`
- `powerSwitch=1 -> HTTP None device reported a different value than was sent`

## H615A

Developer API type `devices.types.light`; seen in #158.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `music_setting/musicMode` — field musicMode options Rhythm=0, Sprouting=1, Shiny=2; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

State readback: `brightness`=40, `colorRgb`=16711680, `colorTemperatureK`=0, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `lightScene`, `musicMode`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H615B

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #149.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `music_setting/musicMode` — field musicMode options Rhythm=0, Sprouting=1, Shiny=2; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

State readback: `brightness`=100, `colorRgb`=16777215, `colorTemperatureK`=0, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `lightScene`, `musicMode`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H6163

Developer API type `devices.types.light`; seen in #60.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=0, Spectrum=1, Rolling=2, Rhythm=3; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

Account (BFF) list: not seen (captures without account login, or not listed).

## H616C

Not returned by the Developer API; seen in #122.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H6182

Developer API type `devices.types.light`; seen in #104.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `music_setting/musicMode` — field musicMode options Dynamic=1, Calm=2; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=54014, `colorTemperatureK`=0, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H618E

Developer API type `devices.types.light`; seen in #134.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=1, Rhythm=2, Spectrum=3, Rolling=4, Separation=5, Hopping=6, PianoKeys=7, Fountain=8, DayAndNight=9, Sprouting=10, Shiny=11; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=0/16744192/255, `colorTemperatureK`=0/2100, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H618F

Developer API type `devices.types.light`; seen in #131.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=1, Rhythm=2, Spectrum=3, Rolling=4, Separation=5, Hopping=6, PianoKeys=7, Fountain=8, DayandNight=9, Sprouting=10, Shiny=11; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=16711680, `colorTemperatureK`=0, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H6199

Developer API type `devices.types.light`; seen in #60.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=1, Spectrum=2, Rolling=3, Rhythm=4; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

Account (BFF) list: not seen (captures without account login, or not listed).

## H619A

Developer API type `devices.types.light`; seen in #104.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=1, Rhythm=2, Spectrum=3, Rolling=4, Separation=5, Hopping=6, PianoKeys=7, Fountain=8, DayAndNight=9, Sprouting=10, Shiny=11; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=22, `colorRgb`=16713057, `colorTemperatureK`=0, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H61A0

Developer API type `devices.types.light`; seen in #104.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=1, Rhythm=2, Spectrum=3, Rolling=4, Separation=5, Hopping=6, PianoKeys=7, Fountain=8, DayAndNight=9, Sprouting=10, Shiny=11; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–18 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–18 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=0, `colorTemperatureK`=2700, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H61A2

Developer API type `devices.types.light`; seen in #104.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=1, Rhythm=2, Spectrum=3, Rolling=4, Separation=5, Hopping=6, PianoKeys=7, Fountain=8, DayandNight=9, Sprouting=10, Shiny=11; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=4186320, `colorTemperatureK`=0, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H61B5

Not returned by the Developer API; seen in #128.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H61BE

Developer API type `devices.types.light`; seen in #83.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=1, Rhythm=2, Spectrum=3, Rolling=4, Separation=5, Hopping=6, PianoKeys=7, Fountain=8, DayandNight=9, Sprouting=10, Shiny=11; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–20 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–20 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=0, `colorTemperatureK`=3300, `online`=false, `powerSwitch`=1; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H61E1

Developer API type `devices.types.light`; seen in #83.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot` — options Cabs - Christmas=3184326, Cabs - Valentines=3475534, Cabs - StPatricks=3658872, Cabs - Ordanary Time=3810570
- `music_setting/musicMode` — field musicMode options Energic=1, Rhythm=2, Spectrum=3, Rolling=4, Separation=5, Hopping=6, PianoKeys=7, Fountain=8, DayAndNight=9, Sprouting=10, Shiny=11; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=0, `colorTemperatureK`=3700, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H61F2

Developer API type `devices.types.light`; seen in #159.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=1, Rhythm=2, Spectrum=3, Rolling=4, Separation=5, Hopping=6, PianoKeys=7, Fountain=8, DayAndNight=9, Sprouting=10, Shiny=11, Splash=12, Orbit=13, UFO=14, Spring=15, Luminous=16; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–4 elementRange 0–3; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–4 elementRange 0–3; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=16777215, `colorTemperatureK`=0, `online`=true, `powerSwitch`=0/1; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`.

## H6604

Developer API type `devices.types.light`; seen in #131.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `mode/hdmiSource` — options HDMI 1=1, HDMI 2=2, HDMI 3=3, HDMI 4=4
- `music_setting/musicMode` — field musicMode options Energic=1, Rhythm=2, Spectrum=3, Rolling=4; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=16711680, `colorTemperatureK`=0, `hdmiSource`=1, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H66A1

Developer API type `devices.types.light`; seen in #104.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2200–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `movie_setting/movieMode` — field moveMode options {'de': 'Spiel', 'ja': 'ゲーム', 'en': 'Game', 'it': 'Gioco', 'fr': 'Jeu', 'key': 'Game', 'es': 'Juego'}=0
- `music_setting/musicMode` — field musicMode options Energic=0, Rhythm=1, Spectrum=2, Rolling=3, Separation=4, Hopping=5, Piano Keys=6, Fountain=7, Day and Night=8, Sprouting=9, Splash=10, Spring=11, Color Painting=12, Beat=13, Windmill=14, Flowing Light=15; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–14 elementRange 0–13; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–14 elementRange 0–13; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=0, `colorTemperatureK`=2700, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `movieMode`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H6811

Developer API type `devices.types.light`; seen in #85.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options MeteorShower=1, Crossing=2, DreamColor=3, FloatingMist=4, Spectrum=5, FallingSand=6, ColorFlip=7, ChristmasNight=8; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

Account (BFF) list: not seen (captures without account login, or not listed).

## H6840

Developer API type `devices.types.light`; seen in #60.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Meteor Shower=1, Crossing=2, Dream Color=3, Floating Mist=4, Spectrum=5, Separation=6, Cadence=7, Dancing Lines=8; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `toggle/dreamViewToggle` — options on=1, off=0

Account (BFF) list: not seen (captures without account login, or not listed).

## H7020

Developer API type `devices.types.light`; seen in #131.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `music_setting/musicMode` — field musicMode options Energic=1, Spectrum=2, Rolling=3, Rhythm=4; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–30 elementRange 0–29; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–30 elementRange 0–29; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=16712960, `colorTemperatureK`=0, `online`=false/true, `powerSwitch`=0/1; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H7037

Developer API type `devices.types.light`; seen in #85.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Hopping=1, BouncingBall=2, Rhythm=3, Rolling=4, Loop=5, Separation=6, PianoKeys=7, Alternate=8; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0

Account (BFF) list: not seen (captures without account login, or not listed).

## H7039

Developer API type `devices.types.light`; seen in #150.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Hopping=1, BouncingBall=2, Rhythm=3, Rolling=4, Loop=5, Separation=6, PianoKeys=7, Alternate=8; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–45 elementRange 0–44; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–45 elementRange 0–44; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=16711680, `colorTemperatureK`=0, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `dreamViewToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H7057

Not returned by the Developer API; seen in #114.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H705A

Developer API type `devices.types.light`; seen in #85.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=1, Shiny=2, HeartBeating=3, Hopping=4, Luminous=5, Rolling=6; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

Account (BFF) list: not seen (captures without account login, or not listed).

## H705E

Developer API type `devices.types.light`; seen in #85.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=1, Shiny=2, HeartBeating=3, Hopping=4, Luminous=5, Rolling=6; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–27 elementRange 0–26; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–27 elementRange 0–26; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

Account (BFF) list: not seen (captures without account login, or not listed).

## H7060

Developer API type `devices.types.light`; seen in #85.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–4 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–4 elementRange 0–14; field rgb range 0–16777215

Account (BFF) list: not seen (captures without account login, or not listed).

## H7068

Developer API type `devices.types.light`; seen in #85, #114.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `music_setting/musicMode` — field musicMode options Rhythm=1, Shiny=2, Luminous=3, Hopping=4, Sprouting=5; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H7070

Developer API type `devices.types.light`; seen in #150.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

State readback: `brightness`=100, `colorRgb`=0, `colorTemperatureK`=2000, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `lightScene`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H7075

Not returned by the Developer API; seen in #114.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H7076

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #160.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot` — options standard lighting=3998000
- `music_setting/musicMode` — field musicMode options Stippling=0, Rhythm=1, Hopping=2, Luminous=3, Beat=4, Heart Beat=5, Starlight=6, Separation=7; field sensitivity range 0–100
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=50, `colorRgb`=16746766, `colorTemperatureK`=0, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `result`.

## H707C

Developer API type `devices.types.light`; seen in #60.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Rhythm=0, Hopping=1, Luminous=2, Beat=3, Touching=4, Fusion=5, Dance Stage=6, Overlap=7; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–24 elementRange 0–23; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–24 elementRange 0–23; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

Account (BFF) list: not seen (captures without account login, or not listed).

## H70B6

Developer API type `devices.types.light`; seen in #85.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Floating Mist=0, Spectrum=1, Separation=2, Meteor shower=3, Hopping=4, Shrink=5, Sound Wave=6, Falling Sand=7, Color Flip=8, Christmas Night=9; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `toggle/dreamViewToggle` — options on=1, off=0

Account (BFF) list: not seen (captures without account login, or not listed).

## H70C2

Developer API type `devices.types.light`; seen in #150.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=0, Rhythm=1, Spectrum=2, Rolling=3, Separation=4, Hopping=5, PianoKeys=6, Fountain=7, DayAndNight=8, Sprouting=9, Shiny=10; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–10 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–10 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=0, `colorTemperatureK`=2700, `online`=false, `powerSwitch`=0; returns `""` for `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H70C4

Developer API type `devices.types.light`; seen in #150.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=0, Rhythm=1, Hopping=2, Piano Keys=3, Fountain=4, Day and Night=5, Flow=6, Spin=7, Spring=8, Ripple=9; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–10 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–10 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=16711680/16744192, `colorTemperatureK`=0, `online`=false/true, `powerSwitch`=1; returns `""` for `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H70C5

Developer API type `devices.types.light`; seen in #83, #114, #150.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=0, Rhythm=1, Hopping=2, Piano Keys=3, Fountain=4, Day and Night=5, Flow=6, Spin=7, Spring=8, Ripple=9; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–10 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–10 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100/50, `colorRgb`=0/16711680/255, `colorTemperatureK`=0/2000, `online`=false/true, `powerSwitch`=0/1; returns `""` for `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H70C9

Developer API type `devices.types.light`; seen in #150.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=0, Rhythm=1, Hopping=2, Piano Keys=3, Fountain=4, Day and Night=5, Flow=6, Spin=7, Spring=8, Ripple=9; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–10 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–10 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=255, `colorTemperatureK`=0, `online`=false, `powerSwitch`=1; returns `""` for `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H7101

Developer API type `devices.types.fan`; seen in #72.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0
- `toggle/oscillationToggle` — options on=1, off=0
- `work_mode/workMode` — field workMode options FanSpeed=1, Custom=2, Auto=3, Sleep=5, Nature=6; field modeValue options FanSpeed=None, Custom=None, Auto=None, Sleep=None, Nature=None

Account (BFF) list: not seen (captures without account login, or not listed).

## H7107

Developer API type `devices.types.fan`; seen in #121.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0
- `toggle/oscillationToggle` — options on=1, off=0
- `work_mode/workMode` — field workMode options FanSpeed=1, Auto=2, Sleep=3, Nature=4, Custom=5; field modeValue options FanSpeed=None, Auto=None, Sleep=None, Nature=None, Custom=None

State readback: `online`=true, `oscillationToggle`=1, `powerSwitch`=1, `workMode`={"workMode": 1, "modeValue": 8}.

Account (BFF) list: not seen (captures without account login, or not listed).

## H7124

Developer API type `devices.types.air_purifier`; seen in #114, #150.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `mode/nightlightScene` — options Forest=1, Ocean=2, Wetland=3, Leisurely=4, Asleep=5
- `on_off/powerSwitch` — options on=1, off=0
- `property/airQuality`
- `property/filterLifeTime`
- `range/brightness` — range 1–100
- `toggle/nightlightToggle` — options on=1, off=0
- `work_mode/workMode` — field workMode options gearMode=1, Sleep=5, Auto=3, Turbo=7; field modeValue options gearMode=None, Sleep=None, Auto=None, Turbo=None

State readback: `airQuality`=1, `brightness`=11/50, `colorRgb`=""/16777215, `filterLifeTime`=100/89/92, `nightlightScene`=""/5, `nightlightToggle`=0/1, `online`=true, `powerSwitch`=1, `workMode`={"workMode": 3, "modeValue": 0}/{"workMode": 5, "modeValue": 0}.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H7126

Developer API type `devices.types.air_purifier`; seen in #114, #150.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0
- `property/airQuality`
- `property/filterLifeTime`
- `work_mode/workMode` — field workMode options gearMode=1, Custom=2, Auto=3; field modeValue options gearMode=None, Custom=None, Auto=None

State readback: `airQuality`=1, `filterLifeTime`=22/86, `online`=true, `powerSwitch`=0/1; returns `""` for `workMode`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `result`.

## H7129

Developer API type `devices.types.air_purifier`; seen in #150.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `mode/nightlightScene` — options Forest=1, Ocean=2, Wetland=3, Relax=4, Asleep=5
- `on_off/powerSwitch` — options on=1, off=0
- `property/airQuality`
- `property/filterLifeTime`
- `range/brightness` — range 1–100
- `toggle/nightlightToggle` — options on=1, off=0
- `work_mode/workMode` — field workMode options gearMode=1, Sleep=5, Auto=3, Turbo=7; field modeValue options gearMode=None, Auto=None, Sleep=None, Turbo=None

State readback: `airQuality`=1, `brightness`=50, `filterLifeTime`=40/43/89, `nightlightToggle`=0, `online`=true, `powerSwitch`=1, `workMode`={"workMode": 1, "modeValue": 1}/{"workMode": 5, "modeValue": 0}; returns `""` for `colorRgb`, `nightlightScene`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `onOff`, `result`, `sta`.

## H7135

Developer API type `devices.types.heater`; seen in #131.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0
- `property/sensorTemperature`
- `temperature_setting/targetTemperature` — field autoStop options Auto Stop=1, Maintain=0; field temperature range 5–30; field unit options Celsius=Celsius, Fahrenheit=Fahrenheit
- `work_mode/workMode` — field workMode options gearMode=1, Fan=9, Auto=3; field modeValue options gearMode=None, Fan=None, Auto=None

State readback: `online`=true, `powerSwitch`=0, `sensorTemperature`=73.0, `targetTemperature`={"unit": "Fahrenheit", "targetTemperature": 79}, `workMode`={"workMode": 1, "modeValue": 3}.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `fahOpen`, `ic`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H7141

Developer API type `devices.types.humidifier`; seen in #150.

Capabilities:

- `event/lackWaterEvent`
- `on_off/powerSwitch` — options on=1, off=0
- `range/humidity` — range 40–70; unit.percent
- `work_mode/workMode` — field workMode options Manual=1, Custom=2, Auto=3; field modeValue options Manual=None, Custom=None, Auto=None

State readback: `online`=false, `powerSwitch`=0, `workMode`={"workMode": 4, "modeValue": 3}; returns `""` for `humidity`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H7142

Developer API type `devices.types.humidifier`; seen in #150.

Capabilities:

- `event/lackWaterEvent`
- `on_off/powerSwitch` — options on=1, off=0
- `range/humidity` — range 40–70; unit.percent
- `work_mode/workMode` — field workMode options Manual=1, Custom=2, Auto=3; field modeValue options Manual=None, Custom=None, Auto=None

State readback: `online`=false, `powerSwitch`=0/1, `workMode`={"workMode": 3, "modeValue": 1}/{"workMode": 3, "modeValue": 5}/{"workMode": 3, "modeValue": 9}; returns `""` for `humidity`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H714E

Developer API type `devices.types.humidifier`; seen in #85.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `event/lackWaterEvent`
- `mode/nightlightScene` — options Forest=1, Ocean=2, Wetland=3, Leisurely=4, Sleep=5
- `on_off/powerSwitch` — options on=1, off=0
- `property/sensorHumidity`
- `range/brightness` — range 1–100
- `range/humidity` — range 40–80; unit.percent
- `toggle/nightlightToggle` — options on=1, off=0
- `work_mode/workMode` — field workMode options Manual=1, Custom=2, Auto=3; field modeValue options Manual=None, Custom=None, Auto=None

Account (BFF) list: not seen (captures without account login, or not listed).

## H7152

Developer API type `devices.types.dehumidifier`; seen in #114.

Capabilities:

- `event/waterFullEvent`
- `on_off/powerSwitch` — options on=1, off=0
- `range/humidity` — range 30–80; unit.percent
- `work_mode/workMode` — field workMode options gearMode=1, Auto=3, Dryer=8; field modeValue options gearMode=None, Auto=None, Dryer=None

State readback: `humidity`=50/60, `online`=true, `powerSwitch`=1, `workMode`={"workMode": 3, "modeValue": 0}.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `subDevices`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `onOff`, `result`, `sta`.

## H7161

Developer API type `devices.types.aroma_diffuser`; seen in #99.

Capabilities:

- `event/lackWaterEvent`
- `mode/presetScene` — options Bach=171396, Wärme am Kamin=171397, Morgen=171398, Gutenachtkuss=171399, Nachtlicht=171400
- `on_off/powerSwitch` — options on=1, off=0

State readback: `online`=true, `powerSwitch`=0; returns `""` for `presetScene`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H7162

Not returned by the Developer API; seen in #181.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H717A

Developer API type `devices.types.kettle`; seen in #63.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0
- `property/sensorTemperature`
- `temperature_setting/sliderTemperature` — field temperature range 40–100; field unit options Celsius=Celsius, Fahrenheit=Fahrenheit
- `work_mode/workMode` — field workMode options M1=2, M2=3, M3=4, M4=5; field modeValue options M1=None, M2=None, M3=None, M4=None

Account (BFF) list: not seen (captures without account login, or not listed).

## H805C

Not returned by the Developer API; seen in #114.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

