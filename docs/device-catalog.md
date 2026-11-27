# Device catalog from submitted diagnostics

Everything below was read out of the diagnostics downloads (and inline dumps) attached to issues and pull requests in two passes: 2026-03-06 → 2026-09-06 (67 files, 107 SKUs) and 2026-05-24 → 2026-11-24 (109 attachments: 86 readable diagnostics, a LAN-discovery excerpt, 17 logs and exports, and 5 files their uploaders had deleted). 148 SKUs in all. It records what Govee's APIs actually returned for each model, not what the integration supports; see the README for the support table and `govee-protocol-reference.md` for protocol detail. Device names, ids, MAC and IP addresses, Wi-Fi names, BLE names and user-named snapshots/DIY scenes from the captures are left out on purpose.

How to read an entry:

- **Developer API** — `type` and every capability the `/user/devices` list advertised (`type/instance`), with the parameters that matter (option names→values, ranges, segment sizes).
- **State readback** — what `/device/state` returned per instance in the captures. `""` means Govee returned an empty string on poll (the integration has to keep optimistic state for it).
- **Account (BFF) list** — whether the model appears in the app-account device list, which `deviceSettings` / `lastDeviceData` keys it carried, and the gateway it hangs off.
- **AWS IoT push** — the keys seen in the device's `state` push, when a capture had one.
- **Rejected commands** — control commands Govee answered with an error, verbatim.
- **Rejected or unconfirmed commands** — from the `recent_commands` log newer diagnostics carry: the transport the integration used (`cloud`, `lan`, `mqtt`, `ble`) and either Govee's error or the integration's own confirmation verdict ("device reported a different value than was sent", "no readback within the confirm window"). A LAN mismatch is a read-back disagreement, not a refusal by Govee.
- **Segments** — the integration's segment resolution (`api` = `elementRange.max + 1`, `size.max`, the `SKU_SEGMENT_OVERRIDES` entry, and the count it used).
- **Event push** — instances and state names seen on the official OpenAPI events channel.
- **Seen in** — the issues/PRs the captures came from.

Models the account list knows but the Developer API never returned in a capture that lists every API device (no `type`, no capabilities — they are BLE-only or simply not enabled for the public API): H3351, H3500, H3510, H5086, H5104, H5122, H5126, H5129, H6006, H605C, H616C, H7057, H7075, H7094, H7105, H7162, H8057, H805C. H6046 and H61B5 were on this list after the first pass; the newer captures (#241 for the H6046) show the Developer API returning both as full RGBIC lights. Entries marked "presence unknown" come from captures that hold only the devices the integration created, so they can't tell an unreturned model from an unsupported one.

Since 2026-06 the account list's `deviceSettings` for Wi-Fi devices also carries an app-wide key set that says nothing about the model: `address`, `appVersion`, `autoShutDownOnOff`, `bleName`, `boilWaterCompletedNotiOnOff`, `completionNotiOnOff`, `device`, `deviceName`, `ic`, `language`, `matterId`, `pactCode`, `pactType`, `playState`, `secretCode`, `sku`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiHardVersion`, `wifiMac`, `wifiName`, `wifiSoftVersion` (the kettle flags appear on lights too). Entries call it "the common Wi-Fi key set" and list only the keys beyond it.

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

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `powerOffMemory`.

AWS IoT push `state` keys: `result`.

## H1270

Developer API type `devices.types.light`; seen in #131, #246.

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

AWS IoT push `state` keys: `result`; `op.command` frame headers: `3305`.

Segments (integration's resolution): api 12, size.max 12, no override → 12.

Rejected or unconfirmed commands (2026-05 → 2026-11, `recent_commands`):

- ble brightness=8 → BLE: no Bluetooth adapter/proxy slot could reach the device (connect failed)
- ble colorRgb=13089535 → BLE: no Bluetooth adapter/proxy slot could reach the device (connect failed)
- lan colorRgb=13089535 → device reported a different value than was sent
- lan colorTemperatureK=3494 → device reported a different value than was sent

## H1310

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #74, #102, #114, #181, #214.

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

Developer API type `devices.types.light`; seen in #102, #105, #114, #214.

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

## H1401

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #105.

Account (BFF) list: yes (census row only; the capture has no key dump for it).

## H14C0

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #57, #131.

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

## H1741

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #105.

Account (BFF) list: yes (census row only; the capture has no key dump for it).

## H1A42

Developer API type `devices.types.light`; seen in #261.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 1000–10000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=0, Rhythm=1, Spectrum=2, Rolling=3, Separation=4, Hopping=5, Piano Keys=6, Fountain=7, Day and Night=8, Sprouting=9, Shiny=10, Splash=11, Orbit=12, UFO=13, Spring=14, Luminous=15; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100/57, `colorRgb`=0/15873316, `colorTemperatureK`=0/2272, `online`=true, `powerSwitch`=0/1; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `segmentCount`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`, `wifiFuncList`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa40`, `aa41`, `aaa5`, `aaf1`.

Segments (integration's resolution): api 15, size.max 15, no override → 15.

## H3200

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #214.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `spec`; `lastDeviceData` keys: `online`.

## H3351

Not returned by the Developer API; seen in #306.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

## H3500

Not returned by the Developer API; seen in #102, #114, #214.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `installType`, `spec`.

## H3510

Not returned by the Developer API; seen in #102, #114, #214.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `spec`.

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

Event push (OpenAPI events channel): `bodyAppearedEvent [LEAKED]`, `bodyAppearedEvent [UN_LEAKED]`.

## H5058

Developer API type `devices.types.sensor`; gateway: `H5043`; seen in #134.

Capabilities:

- `event/bodyAppearedEvent`

Account (BFF) list: yes — `deviceSettings` carries `battery`, `deviceName`, `gatewayInfo`, `sno`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `gwonline`, `lastTime`, `online`, `read`. Newer captures add `batteryWarning`, `criticalOnOff`, `deviceType`, `emailWarningOnOff`, `gatewayId`, `gatewayVersionHard`, `gatewayVersionSoft`, `gwSubType`, `muteLevel`, `openPush`, `pushState`, `smsWarningOnOff`.

## H5059

Developer API type `devices.types.sensor`; gateway: `H5044`; seen in #87, #101.

Capabilities:

- `event/bodyAppearedEvent`

State readback: `online`=false.

Account (BFF) list: yes (census row only; the capture has no key dump for it).

## H5075

Developer API type `devices.types.thermometer`; gateway: `H5151`; seen in #83, #99, #102, #105, #132, #159.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=false/true, `sensorHumidity`=""/41.7/46.6, `sensorTemperature`=""/68.36/71.24; returns `""` for `sensorHumidity`, `sensorTemperature`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`. Newer captures add `bdType`, `criticalOnOff`, `emailWarningOnOff`, `gwSubType`, `humCali`, `humMax`, `humMin`, `humWarning`, `netWaring`, `normalPushOnOff`, `temCali`, `temMax`, `temMin`, `temWarning`, `uploadRate`, `wifiLevel`.

## H5080

Developer API type `devices.types.socket`; seen in #131, #181.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0

State readback: `online`=true, `powerSwitch`=0/1.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `spec`.

## H5083

Developer API type `devices.types.socket`; seen in #62.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0

State readback: `online`=true, `powerSwitch`=0.

Account (BFF) list: not seen (captures without account login, or not listed).

## H5086

Not returned by the Developer API; seen in #102, #114, #214.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H5089

Developer API type `devices.types.socket`; seen in #59, #102, #114, #214.

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

## H5100

Developer API type `devices.types.thermometer`; gateway: `H5151`; seen in #209, #246.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=true, `sensorHumidity`=46.6/49.7/50.2/59.9, `sensorTemperature`=69.44/70.53/71.42/72.86.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `criticalOnOff`, `delayPushTime`, `device`, `deviceName`, `emailWarningOnOff`, `fahOpen`, `gatewayId`, `gatewayInfo`, `gatewayVersionHard`, `gatewayVersionSoft`, `gwSubType`, `humCali`, `humMax`, `humMin`, `humWarning`, `netWaring`, `normalPushOnOff`, `pactCode`, `pactType`, `sku`, `temCali`, `temMax`, `temMin`, `temWarning`, `uploadRate`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiHardVersion`, `wifiLevel`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`.

## H5102

Developer API type `devices.types.thermometer`; gateway: `H5151`; seen in #209.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=true, `sensorHumidity`=49.1/49.2/51.3/52.3, `sensorTemperature`=69.08/70.52/71.78/71.97.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bdType`, `bleName`, `criticalOnOff`, `delayPushTime`, `device`, `deviceName`, `emailWarningOnOff`, `fahOpen`, `gatewayId`, `gatewayInfo`, `gatewayVersionHard`, `gatewayVersionSoft`, `gwSubType`, `humCali`, `humMax`, `humMin`, `humWarning`, `netWaring`, `normalPushOnOff`, `pactCode`, `pactType`, `sku`, `temCali`, `temMax`, `temMin`, `temWarning`, `uploadRate`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiHardVersion`, `wifiLevel`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`.

## H5103

Developer API type `devices.types.thermometer`; seen in #85, #209, #236.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=true, `sensorHumidity`=54.1/54.5/54.6/54.7, `sensorTemperature`=70.53/70.7.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `batteryWarning`, `bdType`, `bleName`, `criticalOnOff`, `delayPushTime`, `device`, `deviceName`, `emailWarningOnOff`, `fahOpen`, `goodsType`, `groupOrder`, `gwSubType`, `humCali`, `humMax`, `humMin`, `humWarning`, `netWaring`, `normalPushOnOff`, `pactCode`, `pactType`, `secretCode`, `sku`, `temCali`, `temMax`, `temMin`, `temWarning`, `uploadRate`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiHardVersion`, `wifiLevel`, `wifiMac`, `wifiName`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`.

## H5104

Not returned by the Developer API; seen in #306.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `batteryWarning`, `bdType`, `bleName`, `criticalOnOff`, `delayPushTime`, `device`, `deviceName`, `emailWarningOnOff`, `fahOpen`, `gwSubType`, `humCali`, `humMax`, `humMin`, `humWarning`, `netWaring`, `normalPushOnOff`, `pactCode`, `pactType`, `secretCode`, `sku`, `temCali`, `temMax`, `temMin`, `temWarning`, `topic`, `uploadRate`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiHardVersion`, `wifiLevel`, `wifiMac`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`.

## H5106

Developer API type `devices.types.thermometer`; seen in #102, #105, #114, #150, #209, #214.

Capabilities:

- `property/airQuality`
- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `airQuality`=1, `online`=true, `sensorHumidity`=48/49/49.2, `sensorTemperature`=71.6/71.78/73.04.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `airQualityOnOff`, `batteryWarning`, `criticalOnOff`, `delayPushTime`, `emailWarningOnOff`, `goodsType`, `groupOrder`, `gwSubType`, `humCali`, `humMax`, `humMin`, `humWarning`, `netWaring`, `normalPushOnOff`, `pm25Max`, `pm25Min`, `pm25Warning`, `standard`, `temCali`, `temMax`, `temMin`, `temWarning`, `uploadRate`, `wifiLevel`.

AWS IoT push `state` keys: `result`; `op.command` frame headers: `0100`, `091a`.

## H5109

Developer API type `devices.types.thermometer`; gateway: `H5042`; seen in #62, #83, #96, #132, #134.

Capabilities:

- `property/sensorTemperature`

State readback: `online`=true, `sensorTemperature`=100.83/81.68/84.48.

Account (BFF) list: yes — `deviceSettings` carries `battery`, `deviceName`, `fahOpen`, `gatewayInfo`, `sno`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`. Newer captures add `criticalOnOff`, `deviceType`, `emailWarningOnOff`, `gatewayId`, `gatewayVersionHard`, `gatewayVersionSoft`, `gwSubType`, `humCali`, `humMax`, `humMin`, `humWarning`, `netWaring`, `normalPushOnOff`, `powerSaveModeState`, `pushState`, `temCali`, `temMax`, `temMin`, `temWarning`, `uploadRate`, `wifiLevel`.

## H5110

Developer API type `devices.types.thermometer`; gateway: `H5044`, `H5151`; seen in #83, #102, #114, #132, #214.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=true, `sensorHumidity`=35.9/48.4/48.8, `sensorTemperature`=68.9/71.24/72.14.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `gatewayInfo`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`. Newer captures add `batteryWarning`, `bdType`, `criticalOnOff`, `delayPushTime`, `emailWarningOnOff`, `gatewayId`, `gatewayVersionHard`, `gatewayVersionSoft`, `gwSubType`, `humCali`, `humMax`, `humMin`, `humWarning`, `netWaring`, `normalPushOnOff`, `temCali`, `temMax`, `temMin`, `temWarning`, `uploadRate`, `wifiLevel`.

## H5111

Developer API type `devices.types.thermometer`; gateway: `H5151`, `H5042`; seen in #83, #134, #144, #236.

Capabilities:

- `property/sensorTemperature`

State readback: `online`=false/true, `sensorTemperature`=""/-2.2/-4.54; returns `""` for `sensorTemperature`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `gatewayInfo`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`. Newer captures add `batteryWarning`, `bdType`, `criticalOnOff`, `delayPushTime`, `emailWarningOnOff`, `gatewayId`, `gatewayVersionHard`, `gatewayVersionSoft`, `gwSubType`, `humCali`, `humMax`, `humMin`, `humWarning`, `muteLevel`, `netWaring`, `normalPushOnOff`, `temCali`, `temMax`, `temMin`, `temWarning`, `uploadRate`, `wifiLevel`.

## H5112

Developer API type `devices.types.thermometer`; gateway: `H5044`; seen in #150.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=false; returns `""` for `sensorHumidity`, `sensorTemperature`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `gatewayInfo`, `pactCode`, `pactType`, `sno`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`, `tem2`. Newer captures add `batteryWarning`, `criticalOnOff`, `delayPushTime`, `delayPushTime2`, `delayPushTimeHum`, `emailWarningOnOff`, `gatewayId`, `gatewayVersionHard`, `gatewayVersionSoft`, `gatewayWarning`, `gwSubType`, `humCali`, `humMax`, `humMin`, `humWarning`, `muteLevel`, `netWaring`, `normalPushOnOff`, `pb1IconIndex`, `pb2IconIndex`, `probeName1`, `probeName2`, `temCali`, `temCali2`, `temMax`, `temMax2`, `temMin`, `temMin2`, `temWarning`, `temWarning2`, `uploadRate`, `wifiLevel`.

## H5122

Not returned by the Developer API; seen in #131, #214.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `bind`, `logTime`, `logType`, `online`. Newer captures add `bdType`, `time`.

## H5126

Not returned by the Developer API; seen in #102, #114, #128, #214.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `bind`, `logTime`, `logType`, `online`. Newer captures add `bdType`, `time`.

## H5129

Not returned by the Developer API; seen in #102, #114, #214, #306.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `bind`, `logTime`, `logType`, `online`. Newer captures add `bdType`, `time`.

## H5140

Developer API type `devices.types.air_quality_monitor`; seen in #116.

Capabilities:

- `property/carbonDioxideConcentration`
- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `carbonDioxideConcentration`=609, `online`=true, `sensorHumidity`=51.9, `sensorTemperature`=73.94.

Account (BFF) list: not seen (captures without account login, or not listed).

AWS IoT push `state` keys: `onOff`, `result`, `sta`.

## H5171

Developer API type `devices.types.thermometer`; seen in #173 (a `raw_api_state` paste, so only the state shape is known).

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=true, `sensorHumidity`=58.8, `sensorTemperature`=71.06 — the temperature is the °F reading with no unit field, so the SKU is on `FAHRENHEIT_REPORTING_SKUS`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H5179

Developer API type `devices.types.thermometer`; seen in #72, #141, #209.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=false; returns `""` for `sensorHumidity`, `sensorTemperature`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `pactCode`, `pactType`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`. Newer captures add `bdType`, `criticalOnOff`, `delayPushTime`, `emailWarningOnOff`, `gwSubType`, `humCali`, `humMax`, `humMin`, `humWarning`, `netWaring`, `normalPushOnOff`, `temCali`, `temMax`, `temMin`, `temWarning`, `uploadRate`, `wifiLevel`.

## H5192

Not in the Developer API: `/device/state` answers 404 for the SKU, so the Developer poll skips it. Seen in #174 and #185, from `ptReal` captures and the fixtures in `tests/test_probe_thermometer.py` rather than a diagnostics download.

Account (BFF) list: yes — listed in the thermometer view with `temperature`, `humidity`, and `battery` all absent, so the device is synthesised as a probe thermometer from its SKU (`PROBE_THERMOMETER_BFF_SKUS`).

AWS IoT: a pull device. Readings and alarm limits travel as 20-byte `ptReal` packets with an XOR checksum over bytes 0-18: register `0x08` current core and ambient values, `0x12` the four limits of a probe (one read returns all four, one write sets all four, `0xFFFF` marks an unset limit), `0x24` the probe data with its history buffer, `0x26` the battery voltage. The device only answers reads; the integration polls it on the probe interval while its Live polling switch is on. Register map and evidence: `custom_components/govee/api/probe_thermometer.py`.

## H5220

Developer API type `devices.types.thermometer`; gateway: `H5044`; seen in #102, #114, #128, #214.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=true, `sensorHumidity`=43.9, `sensorTemperature`=76.28.

Account (BFF) list: yes — `deviceSettings` carries `address`, `battery`, `bleName`, `deviceName`, `fahOpen`, `gatewayInfo`, `pactCode`, `pactType`, `sno`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `batteryWarning`, `bdType`, `comfortHumMax`, `comfortHumMin`, `comfortTemMax`, `comfortTemMin`, `criticalOnOff`, `delayPushTime`, `emailWarningOnOff`, `gatewayId`, `gatewayVersionHard`, `gatewayVersionSoft`, `gatewayWarning`, `gwSubType`, `humCali`, `humMax`, `humMin`, `humWarning`, `netWaring`, `normalPushOnOff`, `openHourClock`, `standard`, `temCali`, `temMax`, `temMin`, `temWarning`, `uploadRate`, `weatherDisplay`, `weatherForecastOnOff`, `wifiLevel`.

## H5310

Developer API type `devices.types.thermometer`; gateway: `H5044`; seen in #86, #97, #150, #157.

Capabilities:

- `property/sensorHumidity`
- `property/sensorTemperature`

State readback: `online`=true, `sensorTemperature`=88.34.

Account (BFF) list: yes — `deviceSettings` carries `battery`, `deviceName`, `fahOpen`, `gatewayInfo`, `sno`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `avgDayHum`, `avgDayTem`, `hum`, `lastTime`, `online`, `tem`. Newer captures add `batteryWarning`, `criticalOnOff`, `deviceType`, `emailWarningOnOff`, `gatewayId`, `gatewayVersionHard`, `gatewayVersionSoft`, `gatewayWarning`, `gwSubType`, `humCali`, `humMax`, `humMin`, `humWarning`, `muteLevel`, `netWaring`, `normalPushOnOff`, `powerSaveModeState`, `pushState`, `temCali`, `temMax`, `temMin`, `temWarning`, `uploadRate`, `wifiLevel`.

## H6006

Not returned by the Developer API; seen in #102, #114, #203, #214.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `subDevices`, `supportBleBroadV3`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H6008

Developer API type `devices.types.light`; seen in #131, #150, #158, #203, #281.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

State readback: `brightness`=""/100/20, `colorRgb`=""/0/16764326, `colorTemperatureK`=""/4000/4300, `online`=false/true, `powerSwitch`=""/0/1; returns `""` for `brightness`, `colorRgb`, `colorTemperatureK`, `diyScene`, `lightScene`, `powerSwitch`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `subDevices`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`, `wifiFuncList`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `ab00`.

## H6010

Developer API type `devices.types.light`; seen in #150, #213.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

State readback: `brightness`=100/49/50, `colorRgb`=0, `colorTemperatureK`=3800/4000/4200, `online`=false, `powerSwitch`=1; returns `""` for `diyScene`, `lightScene`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`, `wifiFuncList`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `ab00`.

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

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #57, #72, #186, #306.

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

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `discriminator`, `manualCode`; `lastDeviceData` keys: `online`.

Rejected commands:

- `musicMode={"musicMode": 1, "sensitivity": 44, "autoColor": 1} -> HTTP 200 Parameter value out of range`
- `musicMode={"musicMode": 1, "sensitivity": 50, "autoColor": 1} -> HTTP 200 Parameter value out of range`
- `musicMode={"musicMode": 1, "sensitivity": 77, "autoColor": 1} -> HTTP 200 Parameter value out of range`
- `musicMode={"musicMode": 1, "sensitivity": 78, "autoColor": 1} -> HTTP 200 Parameter value out of range`
- `musicMode={"musicMode": 1, "sensitivity": 79, "autoColor": 1} -> HTTP 200 Parameter value out of range`

## H6046

Developer API type `devices.types.light` in the 2026-05 → 2026-11 captures (the earlier capture in #126 did not return it); seen in #126, #241.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Vivid=1, Rhythm=2, Strike=3, Vibrate=4, Beat=5, Torch=6, RainbowCircle=7, Shiny=8; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100/47, `colorRgb`=16777215/8454911, `colorTemperatureK`=0/2000, `online`=true, `powerSwitch`=0/1; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa33`, `aaa5`.

Segments (integration's resolution): api 15, size.max 15, no override → 15.

## H6052

Developer API type `devices.types.light`; seen in #241.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Spectrum=1, Vibrate=2, Energic=3, Rhythm=4; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100/81/90, `colorRgb`=16777215, `colorTemperatureK`=2000/2700, `online`=true, `powerSwitch`=0/1; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`.

Segments (integration's resolution): api 15, size.max 15, no override → 15.

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

Developer API type `devices.types.light`; seen in #85, #99, #246.

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

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

## H605B

Developer API type `devices.types.light`; seen in #220.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Rhythm=1, Windmill=2, Hooray=3, Sprouting=4; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–24 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–24 elementRange 0–14; field rgb range 0–16777215
- `toggle/backLightToggle` — options on=1, off=0
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0
- `toggle/leftLightToggle` — options on=1, off=0
- `toggle/rightLightToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=16711680, `colorTemperatureK`=0, `online`=true, `powerSwitch`=0; returns `""` for `backLightToggle`, `diyScene`, `dreamViewToggle`, `gradientToggle`, `leftLightToggle`, `lightScene`, `musicMode`, `rightLightToggle`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `bleHardVersion`, `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `wifiHardVersion`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa36`, `aa55`, `aaa9`, `aaae`.

## H605C

Not returned by the Developer API; seen in #126.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H6061

Developer API type `devices.types.light`; seen in #72, #246.

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

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `shapes`; `lastDeviceData` keys: `online`.

## H6062

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #246.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `segmentCount`; `lastDeviceData` keys: `online`.

## H6072

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #60, #198, #241.

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

State readback: `brightness`=100/40, `colorRgb`=16777215, `colorTemperatureK`=2000/2304, `online`=true, `powerSwitch`=0/1; returns `""` for `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aaa5`, `aaba`.

Segments (integration's resolution): api 15, size.max 8, no override → 8.

LAN status keys: `devStatus:brightness`, `devStatus:color`, `devStatus:colorTemInKelvin`, `devStatus:onOff`.

## H6076

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #57, #60, #104, #198, #265, #306.

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

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aaa5`.

## H607C

Developer API type `devices.types.light`; seen in #241, #306.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2200–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Touching=0, Rhythm=1, Hopping=2, Blend=3, Luminous=4, Fantasy=5, Stippling=6, Splash=7, Spring=8; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–16 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–16 elementRange 0–14; field rgb range 0–16777215
- `toggle/baseLightToggle` — options on=1, off=0
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0
- `toggle/pillarLightToggle` — options on=1, off=0

State readback: `brightness`=65, `colorRgb`=0, `colorTemperatureK`=2200, `online`=true, `powerSwitch`=1; returns `""` for `baseLightToggle`, `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `musicMode`, `pillarLightToggle`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `dspVersionSoft`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`; `op.command` frame headers: `aa05`, `aa07`, `aa11`, `aa12`, `aa23`, `aa41`, `aaa4`, `aaa5`.

Segments (integration's resolution): api 15, size.max 16, no override → 15.

## H6088

Developer API type `devices.types.light`; seen in #203.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `music_setting/musicMode` — field musicMode options Shiny=1, Luminous=2, Rolling=3, HeartBeating=4, Hopping=5, PianoKeys=6, Waves=7, Colorful=8; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–6 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–6 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=30, `colorRgb`=16777215, `colorTemperatureK`=0, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `spec`; `lastDeviceData` keys: `online`.

## H6095

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #175, #241.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

State readback: `brightness`=70, `colorRgb`=507576, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `lightScene`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `subDevices`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa34`, `aaa5`.

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

## H6099

Developer API type `devices.types.light`; seen in #241.

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
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=65280, `colorTemperatureK`=0, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `segmentCount`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `bleHardVersion`, `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`, `wifiHardVersion`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa41`, `aaa9`, `aaae`.

Segments (integration's resolution): api 15, size.max 14, no override → 14.

## H60A1

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #57, #85, #102, #104, #105, #114, #214.

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

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #84, #127, #159, #308.

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

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `powerOffMemory`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `stc`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa30`, `aa41`, `aaa5`, `aaf1`. `aa30 <main> <ring>` reports the two zones (`01 01` both lit, `00 01` main off, `01 00` ring off, `00 00` powered off; FW 1.00.49) and drives the main/background light switches (`LIGHT_ZONE_FRAME_SKUS`).

Rejected or unconfirmed commands (2026-05 → 2026-11, `recent_commands`):

- lan powerSwitch=0 → no readback within the confirm window

## H60B0

Developer API type `devices.types.light`; seen in #83, #211, #246.

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

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa41`, `aa42`, `aaa5`.

Rejected or unconfirmed commands (2026-05 → 2026-11, `recent_commands`):

- lan brightness=1 → device reported a different value than was sent
- lan colorRgb=0 → device reported a different value than was sent
- lan powerSwitch=0 → device reported a different value than was sent

## H60B1

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #214.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

## H60B2

Developer API type `devices.types.light`; seen in #104, #306.

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

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `subDevices`; `lastDeviceData` keys: `online`.

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

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `dspVersionSoft`.

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

## H610A

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #105.

Account (BFF) list: yes (census row only; the capture has no key dump for it).

## H6125

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #220.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `device`, `deviceName`, `ic`, `pactCode`, `pactType`, `sku`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiHardVersion`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

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

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `ic_sub_1`, `ic_sub_2`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`.

## H612F

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #159, #215.

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

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `discriminator`, `ic_sub_1`, `ic_sub_2`, `manualCode`, `subDevices`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa40`, `aa41`.

LAN status keys: `devStatus:brightness`, `devStatus:color`, `devStatus:colorTemInKelvin`, `devStatus:onOff`.

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

LAN status keys: `devStatus:brightness`, `devStatus:color`, `devStatus:colorTemInKelvin`, `devStatus:onOff`.

## H6163

Developer API type `devices.types.light`; seen in #60, #198.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=0, Spectrum=1, Rolling=2, Rhythm=3; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

State readback: `brightness`=254, `colorRgb`=4980991, `colorTemperatureK`=0, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `lightScene`, `musicMode`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

## H616C

Not returned by the Developer API; seen in #122.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H6175

Seen only in a LAN discovery excerpt (no Developer API or account data in that capture); LAN API reachable in at least one capture; seen in #57.

## H617C

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #246.

Account (BFF) list: yes — `deviceSettings` carries `address`, `appVersion`, `bleName`, `device`, `deviceName`, `language`, `pactCode`, `pactType`, `secretCode`, `sku`, `supportBleBroadV3`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiHardVersion`, `wifiSoftVersion`; `lastDeviceData` keys: `online`.

## H6182

Developer API type `devices.types.light`; seen in #86, #104.

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

Account (BFF) list: yes (census row only; the capture has no key dump for it).

## H618A

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #57, #281.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot` — 1 user option(s)
- `music_setting/musicMode` — field musicMode options Energic=1, Rhythm=2, Spectrum=3, Rolling=4, Separation=5, Hopping=6, PianoKeys=7, Fountain=8, DayAndNight=9, Sprouting=10, Shiny=11; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=16711680/2895103/9109759, `colorTemperatureK`=0, `online`=true, `powerSwitch`=0/1; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa41`, `aaa5`.

Segments (integration's resolution): api 15, size.max 15, no override → 15.

## H618E

Developer API type `devices.types.light`; seen in #134, #281.

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

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa41`, `aaa5`.

Segments (integration's resolution): api 15, size.max 15, no override → 15.

## H618F

Developer API type `devices.types.light`; seen in #131, #281.

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

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`.

Segments (integration's resolution): api 15, size.max 15, no override → 15.

## H6199

Developer API type `devices.types.light`; seen in #60, #198.

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

State readback: `brightness`=100, `colorRgb`=16777215, `colorTemperatureK`=6667, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `bleHardVersion`, `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `wifiHardVersion`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa55`, `aaa5`, `aaa9`, `aaae`.

## H619A

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #57, #104, #306.

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

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa41`, `aaa5`.

Segments (integration's resolution): api 15, size.max 15, no override → 15.

## H619C

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #203, #236.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

## H61A0

Developer API type `devices.types.light`; seen in #104, #246.

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

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

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

Developer API type `devices.types.light` in the 2026-05 → 2026-11 captures (the earlier capture in #128 did not return it); seen in #128, #241.

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

State readback: `brightness`=100, `colorRgb`=0, `colorTemperatureK`=2304, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `ic_sub_1`, `ic_sub_2`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`, `wifiFuncList`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa40`, `aa41`, `aaa5`.

Segments (integration's resolution): api 15, size.max 15, no override → 15.

## H61BA

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #203.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

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

Account (BFF) list: yes (census row only; the capture has no key dump for it).

## H61D5

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #236.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

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

Account (BFF) list: yes (census row only; the capture has no key dump for it).

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

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `dspVersionSoft`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`.

## H6604

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #57, #131.

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

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `dspVersionSoft`.

## H66A0

Developer API type `devices.types.light`; seen in #213.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2200–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `movie_setting/movieMode` — field movieMode options Game=0
- `music_setting/musicMode` — field musicMode options Energic=0, Rhythm=1, Spectrum=2, Rolling=3, Separation=4, Hopping=5, Piano Keys=6, Fountain=7, Day and Night=8, Sprouting=9, Splash=10, Spring=11, Color Painting=12, Beat=13, Windmill=14, Flowing Light=15; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–14 elementRange 0–13; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–14 elementRange 0–13; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=98, `colorRgb`=16777215, `colorTemperatureK`=0, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `movieMode`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `subDevices`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `bleHardVersion`, `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `wifiHardVersion`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa41`, `aaa9`, `aaae`, `ee30`.

Segments (integration's resolution): api 14, size.max 14, no override → 14.

## H66A1

Developer API type `devices.types.light`; seen in #104, #203.

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

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `subDevices`; `lastDeviceData` keys: `online`.

## H6810

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #86.

Account (BFF) list: yes (census row only; the capture has no key dump for it).

## H6811

Developer API type `devices.types.light`; seen in #85, #86.

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

Account (BFF) list: yes (census row only; the capture has no key dump for it).

## H6840

Developer API type `devices.types.light`; seen in #60, #86, #198.

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

State readback: `brightness`=100, `colorRgb`=0, `colorTemperatureK`=2000, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `dreamViewToggle`, `lightScene`, `musicMode`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

## H6871

Developer API type `devices.types.light`; seen in #306.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2000–9000
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `music_setting/musicMode` — field musicMode options Energic=0, Rhythm=1, Separation=2, Hopping=3, Piano Keys=4, Fountain=5, Day and Night=6, Flow=7, Spin=8, Blend=9, Spring=10, Ripple=11; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=0, `colorTemperatureK`=2000, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

Segments (integration's resolution): api 15, size.max 15, no override → 15.

## H7020

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #131.

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

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `segmentCount`.

LAN status keys: `devStatus:brightness`, `devStatus:color`, `devStatus:colorTemInKelvin`, `devStatus:onOff`, `status:brightness`, `status:onOff`, `status:pt`.

## H702B

Developer API type `devices.types.light`; seen in #281.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot` — 3 user option(s)
- `music_setting/musicMode` — field musicMode options Rhythm=0, Sway=1, Flow=2, Star=3, Twinkle=4, Hopping=5, Rolling=6, Day and Night=7, Piano Keys=8, Separation=9; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–30 elementRange 0–29; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–30 elementRange 0–29; field rgb range 0–16777215
- `toggle/gradientToggle` — options on=1, off=0

State readback: `brightness`=100, `colorRgb`=255, `colorTemperatureK`=0, `online`=true, `powerSwitch`=1; returns `""` for `diyScene`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `segmentCount`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa40`, `aa41`.

Segments (integration's resolution): api 30, size.max 30, no override → 30.

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

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `segmentCount`.

## H7045

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #239.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot` — 4 user option(s)
- `music_setting/musicMode` — field musicMode options Cover=0, Flowing Light=1, Color Painting=2, Fire=3, Light Waves=4, Smudge=5, Rhythm=6, Sway=7, Hopping=8, Flow=9, Separation=10, Luminous=11; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–15 elementRange 0–14; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–15 elementRange 0–14; field rgb range 0–16777215
- `toggle/dreamViewToggle` — options on=1, off=0

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `segmentCount`, `spec`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `result`; `op.command` frame headers: `3305`, `a358`.

Segments (integration's resolution): api 15, size.max 15, no override → 15.

## H7057

Not returned by the Developer API; seen in #102, #114.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `spec`.

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

Developer API type `devices.types.light`; seen in #85, #102, #114, #214.

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

## H7073

Developer API type `devices.types.light`; seen in #240.

Capabilities:

- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot`
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent

State readback: `brightness`=100, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `lightScene`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`, `wifiFuncList`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa41`, `aa42`, `ac00`, `acff`.

## H7075

Not returned by the Developer API; seen in #102, #114, #214, #306.

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

Developer API type `devices.types.light`; seen in #60, #198.

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

State readback: `brightness`=50, `colorRgb`=0, `colorTemperatureK`=3700, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `dreamViewToggle`, `gradientToggle`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `dspVersionSoft`, `sceneSeg`, `segmentCount`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`, `wifiFuncList`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa16`, `aa23`, `aa41`, `aa48`, `aaa5`, `ac00`, `acff`.

## H7086

Developer API type `devices.types.light`; LAN API reachable in at least one capture; seen in #239.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `color_setting/colorTemperatureK` — range 2700–6500
- `dynamic_scene/diyScene`
- `dynamic_scene/lightScene`
- `dynamic_scene/snapshot` — 2 user option(s)
- `music_setting/musicMode` — field musicMode options Rhythm=0, Cover=1, Meteor shower=2, Party=3, Smudge=4, Rolling=5, Flowing Light=6, Torch=7, Energic=8; field sensitivity range 0–100; field autoColor options on=1, off=0; field rgb range 0–16777215
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100; unit.percent
- `segment_color_setting/segmentedBrightness` — field segment size 1–4 elementRange 0–3; field brightness range 0–100
- `segment_color_setting/segmentedColorRgb` — field segment size 1–4 elementRange 0–3; field rgb range 0–16777215

State readback: `brightness`=100, `colorRgb`=0, `colorTemperatureK`=2700, `online`=true, `powerSwitch`=0; returns `""` for `diyScene`, `lightScene`, `musicMode`, `segmentedBrightness`, `segmentedColorRgb`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `discriminator`, `manualCode`; `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`, `wifiFuncList`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa41`, `aaa5`.

Segments (integration's resolution): api 4, size.max 4, no override → 4.

LAN status keys: `devStatus:brightness`, `devStatus:color`, `devStatus:colorTemInKelvin`, `devStatus:onOff`, `status:brightness`, `status:onOff`, `status:pt`.

## H7093

Seen only in a LAN discovery excerpt (no Developer API or account data in that capture); LAN API reachable in at least one capture; seen in #57.

## H7094

Not returned by the Developer API; seen in #306.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `spec`; `lastDeviceData` keys: `online`.

## H70A1

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #203.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

## H70B1

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #86.

Account (BFF) list: yes (census row only; the capture has no key dump for it).

## H70B6

Developer API type `devices.types.light`; seen in #85, #281.

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

State readback: `brightness`=100, `colorRgb`=0, `colorTemperatureK`=2000, `online`=false/true, `powerSwitch`=1; returns `""` for `diyScene`, `dreamViewToggle`, `lightScene`, `musicMode`, `snapshot`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top); `lastDeviceData` keys: `online`.

AWS IoT push `state` keys: `bleHardVersion`, `brightness`, `color`, `colorTemInKelvin`, `mode`, `onOff`, `result`, `sta`, `wifiFuncList`, `wifiHardVersion`; `op.command` frame headers: `aa05`, `aa11`, `aa12`, `aa23`, `aa25`, `aa7d`, `aa7e`, `ab00`.

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

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `dspVersionSoft`.

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

Developer API type `devices.types.light`; seen in #83, #102, #114, #150, #203, #214.

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

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `discriminator`, `manualCode`.

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

## H7105

Not returned by the Developer API; seen in #209.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `discriminator`, `manualCode`, `mcuHardVersion`, `mcuSoftVersion`, `subDevices`, `temCali`; `lastDeviceData` keys: `online`.

## H7107

Developer API type `devices.types.fan`; seen in #121.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0
- `toggle/oscillationToggle` — options on=1, off=0
- `work_mode/workMode` — field workMode options FanSpeed=1, Auto=2, Sleep=3, Nature=4, Custom=5; field modeValue options FanSpeed=None, Auto=None, Sleep=None, Nature=None, Custom=None

State readback: `online`=true, `oscillationToggle`=1, `powerSwitch`=1, `workMode`={"workMode": 1, "modeValue": 8}.

Account (BFF) list: not seen (captures without account login, or not listed).

## H7124

Developer API type `devices.types.air_purifier`; seen in #102, #114, #150, #209, #214.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `mode/nightlightScene` — options Forest=1, Ocean=2, Wetland=3, Leisurely=4, Asleep=5
- `on_off/powerSwitch` — options on=1, off=0
- `property/airQuality`
- `property/filterLifeTime`
- `range/brightness` — range 1–100
- `toggle/nightlightToggle` — options on=1, off=0
- `work_mode/workMode` — field workMode options gearMode=1, Sleep=5, Auto=3, Turbo=7; field modeValue options gearMode=None, Sleep=None, Auto=None, Turbo=None

State readback: `airQuality`=1, `brightness`=11/50, `colorRgb`=""/16777215, `filterLifeTime`=100/89/92, `nightlightScene`=""/5, `nightlightToggle`=0/1, `online`=true, `powerSwitch`=1, `workMode`={"workMode": 3, "modeValue": 0}/{"workMode": 5, "modeValue": 0}; returns `""` for `colorRgb`, `nightlightScene`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `discriminator`, `filterExpireOnOff`, `manualCode`, `mcuHardVersion`, `mcuSoftVersion`.

AWS IoT push `state` keys: `onOff`, `result`, `sta`; `op.command` frame headers: `aa05`, `aa08`, `aa16`, `aa17`, `aa19`, `aa1b`, `aa26`, `ac00`.

## H7126

Developer API type `devices.types.air_purifier`; seen in #102, #114, #150, #214.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0
- `property/airQuality`
- `property/filterLifeTime`
- `work_mode/workMode` — field workMode options gearMode=1, Custom=2, Auto=3; field modeValue options gearMode=None, Custom=None, Auto=None

State readback: `airQuality`=1, `filterLifeTime`=22/86, `online`=true, `powerSwitch`=0/1; returns `""` for `workMode`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `discriminator`, `filterExpireOnOff`, `manualCode`, `mcuHardVersion`, `mcuSoftVersion`.

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

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `discriminator`, `filterExpireOnOff`, `manualCode`, `mcuHardVersion`, `mcuSoftVersion`.

AWS IoT push `state` keys: `onOff`, `result`, `sta`.

## H7130

Developer API type `devices.types.heater`; seen in #265.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0
- `property/sensorTemperature`
- `temperature_setting/targetTemperature` — field autoStop options Auto Stop=1, Maintain=0; field temperature range 5–30; field unit options Celsius=Celsius, Fahrenheit=Fahrenheit
- `toggle/oscillationToggle` — options on=1, off=0
- `toggle/thermostatToggle` — options on=1, off=0
- `work_mode/workMode` — field workMode options Low=1, Medium=2, High=3; field modeValue options Low=None, Medium=None, High=None

State readback: `online`=true, `oscillationToggle`=1, `powerSwitch`=1, `sensorTemperature`=68.0, `targetTemperature`={targetTemperature,unit}, `workMode`={modeValue,workMode}; returns `""` for `thermostatToggle`.

Account (BFF) list: not seen (captures without account login, or not listed).

## H7131

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #265.

## H7135

Developer API type `devices.types.heater`; seen in #131.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0
- `property/sensorTemperature`
- `temperature_setting/targetTemperature` — field autoStop options Auto Stop=1, Maintain=0; field temperature range 5–30; field unit options Celsius=Celsius, Fahrenheit=Fahrenheit
- `work_mode/workMode` — field workMode options gearMode=1, Fan=9, Auto=3; field modeValue options gearMode=None, Fan=None, Auto=None

State readback: `online`=true, `powerSwitch`=0, `sensorTemperature`=73.0, `targetTemperature`={"unit": "Fahrenheit", "targetTemperature": 79}, `workMode`={"workMode": 1, "modeValue": 3}.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `fahOpen`, `ic`, `pactCode`, `pactType`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `dumpOnOff`, `mcuHardVersion`, `mcuSoftVersion`.

## H713B

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #246.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `dumpOnOff`, `mcuHardVersion`, `mcuSoftVersion`, `subDevices`, `temCali`; `lastDeviceData` keys: `online`.

## H7140

Developer API type `devices.types.humidifier`; seen in #209.

Capabilities:

- `color_setting/colorRgb` — range 0–16777215
- `event/lackWaterEvent`
- `mode/nightlightScene` — options Forest=1, Ocean=2, Wetland=3, Leisurely=4, Sleep=5
- `on_off/powerSwitch` — options on=1, off=0
- `range/brightness` — range 1–100
- `range/humidity` — range 40–80; unit.percent
- `toggle/nightlightToggle` — options on=1, off=0
- `work_mode/workMode` — field workMode options Manual=1, Custom=2, Auto=3; field modeValue options Manual=None, Custom=None, Auto=None

State readback: `brightness`=10, `colorRgb`=9109759, `nightlightScene`=5, `nightlightToggle`=0, `online`=false, `powerSwitch`=0, `workMode`={modeValue,workMode}; returns `""` for `humidity`.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `mcuHardVersion`, `mcuSoftVersion`, `waterShortageOnOff`; `lastDeviceData` keys: `online`.

## H7141

Developer API type `devices.types.humidifier`; seen in #150.

Capabilities:

- `event/lackWaterEvent`
- `on_off/powerSwitch` — options on=1, off=0
- `range/humidity` — range 40–70; unit.percent
- `work_mode/workMode` — field workMode options Manual=1, Custom=2, Auto=3; field modeValue options Manual=None, Custom=None, Auto=None

State readback: `online`=false, `powerSwitch`=0, `workMode`={"workMode": 4, "modeValue": 3}; returns `""` for `humidity`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `mcuHardVersion`, `mcuSoftVersion`, `waterShortageOnOff`.

## H7142

Developer API type `devices.types.humidifier`; seen in #150.

Capabilities:

- `event/lackWaterEvent`
- `on_off/powerSwitch` — options on=1, off=0
- `range/humidity` — range 40–70; unit.percent
- `work_mode/workMode` — field workMode options Manual=1, Custom=2, Auto=3; field modeValue options Manual=None, Custom=None, Auto=None

State readback: `online`=false, `powerSwitch`=0/1, `workMode`={"workMode": 3, "modeValue": 1}/{"workMode": 3, "modeValue": 5}/{"workMode": 3, "modeValue": 9}; returns `""` for `humidity`.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `mcuHardVersion`, `mcuSoftVersion`, `waterShortageOnOff`.

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

Developer API type `devices.types.dehumidifier`; seen in #102, #114, #214.

Capabilities:

- `event/waterFullEvent`
- `on_off/powerSwitch` — options on=1, off=0
- `range/humidity` — range 30–80; unit.percent
- `work_mode/workMode` — field workMode options gearMode=1, Auto=3, Dryer=8; field modeValue options gearMode=None, Auto=None, Dryer=None

State readback: `humidity`=50/60, `online`=true, `powerSwitch`=1, `workMode`={"workMode": 3, "modeValue": 0}.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `subDevices`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `humCali`, `mcuHardVersion`, `mcuSoftVersion`, `temCali`, `thermometerUnit`, `waterFull`.

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

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `pactCode`, `pactType`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `mcuHardVersion`, `mcuSoftVersion`, `waterShortageOnOff`.

## H7170

Developer API presence unknown (seen only in the account list; the capture holds only the devices the integration created); seen in #105.

Account (BFF) list: yes (census row only; the capture has no key dump for it).

## H7175

Developer API type `devices.types.kettle` (gooseneck kettle); see docs/_research/2026-09-30_h7175-kettle.md.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0
- `temperature_setting/sliderTemperature` — field temperature range 40–100; field unit options Celsius=Celsius, Fahrenheit=Fahrenheit
- `property/sensorTemperature`
- `work_mode/workMode` — field workMode options Custom=1, Green Tea=2, Oolong Tea=3, Coffee=4, Black Tea/Boil=5; field modeValue: Custom has nested values 1–4, the others defaultValue 0

AWS IoT push: `sta` (`setTem`, `curTem`, ×100 in the display unit) plus BLE-format status frames in `op.command` (decoded by `kettle/frames.py`).

## H717A

Developer API type `devices.types.kettle`; seen in #63.

Capabilities:

- `on_off/powerSwitch` — options on=1, off=0
- `property/sensorTemperature`
- `temperature_setting/sliderTemperature` — field temperature range 40–100; field unit options Celsius=Celsius, Fahrenheit=Fahrenheit
- `work_mode/workMode` — field workMode options M1=2, M2=3, M3=4, M4=5; field modeValue options M1=None, M2=None, M3=None, M4=None

Account (BFF) list: not seen (captures without account login, or not listed).

## H8057

Not returned by the Developer API; seen in #306.

Account (BFF) list: yes — `deviceSettings` carries the common Wi-Fi key set (see the note at the top) plus `spec`; `lastDeviceData` keys: `online`.

## H805C

Not returned by the Developer API; seen in #102, #114, #214.

Account (BFF) list: yes — `deviceSettings` carries `address`, `bleName`, `deviceName`, `ic`, `matterId`, `pactCode`, `pactType`, `supportBleBroadV3`, `supportEnc`, `topic`, `versionHard`, `versionSoft`, `wifiFuncList`, `wifiSoftVersion`; `lastDeviceData` keys: `online`. Newer captures add `sceneSeg`, `segmentCount`.
