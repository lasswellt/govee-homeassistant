# #236 research: live readings for gateway-attached thermometers (H5151/H5111)

Read-only domain research, 2026-10-25. Issue text, diagnostics and web pages were treated as untrusted data.
Anything I could not confirm against a source is marked UNVERIFIED.

## 1. What the reporter's v2026.10.1 diagnostics show

Files: `scratchpad/diag-1025/236/govee-gefrierfach-diagnose.json` (freezer) and `govee-kuehlschrank-diagnose.json` (fridge).

- Freezer BFF `lastDeviceData` (gatewayInfo.index 2): `{online:false, tem:-2090, hum:6000, lastTime:1791145080000}`.
  The timestamp is 2026-10-04 20:18 UTC (22:18 Berlin), the same value HA shows. HA is rendering the account copy correctly.
- Fridge BFF `lastDeviceData` (index 1): `{online:false}`, with no reading at all.
- The same BFF response has fresh data for the account's WiFi H5103s: `online:true`, `lastTime` 2026-10-25 06:21 UTC,
  about 20 minutes before the download. So the BFF call itself is current. Only the two H5151 sub-devices have stopped
  updating in Govee's cloud: the freezer since 2026-10-04, and the fridge has no cloud reading at all.
- The H5151 itself is **not** in the BFF device list (8 entries: 2x H5111, 2x H619C, 3x H5103, 1x H61D5). It appears only
  inside `deviceSettings.gatewayInfo` (`sku H5151`, fw `1.00.27`, wifi `2.06.23`, plus a topic). We therefore cannot see
  whether the gateway is online in the cloud.
- `gateway_route_count: 2`: both H5111 sub-devices map to the H5151 topic. `uploadRate: 10` is set on both sensors.
- MQTT connected at 06:39:21. 6 inbound messages, the last at 06:42:14, none for either H5111. `recent_multisync: []`.
  The capture window was only about 4 minutes, shorter than the 10-minute uploadRate, so a missing `ee34` frame here
  proves nothing on its own.
- Developer API raw state (fridge): `online:false`, `sensorTemperature: ""`. It is as stale as the BFF copy.
- Side note: `hum:6000` (60.00 %) on a temperature-only sensor looks like a placeholder. A third-party H5111 capture also
  has exactly `"hum":6000` (§3.3). No humidity entity is created, because `supports_humidity_sensor` follows capabilities
  and H5111 only advertises `sensorTemperature`. Only `state.sensor_humidity` holds the 60.0.

**Conclusion from the data alone:** the Developer API, the BFF list and MQTT all show the H5151 stopped relaying these
sensors to Govee's cloud on or before 2026-10-04. If the app shows live values, they come from a path that does not go
through the cloud device list.

## 2. Codebase: what we already read (no new endpoint is needed to explain the symptom)

| Path | Where | Status for #236 |
|---|---|---|
| Developer API state poll | `api/client.py` | `online:false`, `""` reading |
| Legacy account list `POST /device/rest/devices/v1/list` | `api/auth.py:214` `GOVEE_DEVICE_LIST_URL` | Used for topics |
| BFF list `GET /bff-app/v1/device/list` | `api/auth.py:215`, `lastDeviceData` read at `api/auth.py:906` | Stale (Oct 4) / empty |
| Gateway `ee34` multiSync thermo frames over AWS IoT | `api/mqtt.py:212` `_decode_thermo_frame`, `coordinator.py:3587` `_handle_thermo_frame` | Proven on H5044 only (#151/#157); none seen from this H5151 |
| Gateway routes (gateway topic per sub-device) | `api/auth.py:657` `_extract_gateway_routes`, `api/mqtt.py:1310` `async_publish_gateway_ptreal` | Known (2 routes); only used for ptReal writes |
| Status query to the device topic | `const.py:441` `MQTT_STATUS_QUERY_EXCLUDED_SKUS` contains H5111 | Disabled: querying BLE-class topics drops the AWS IoT session (#195, #237) |

Prior research reached the same wall. `docs/_research/2026-05-29_thermometer-freshness.md:15` says: "AWS IoT carries **no**
sensor telemetry; govee2mqtt hits the identical wall. The only real-time path is **local BLE advertisement decode**".
`docs/govee-protocol-reference.md:4708` lists H5111 behind H5151 in #83, #134 and #144, where the BFF path did work.

## 3. Candidate sources, one by one

### 3.1 Account device list `lastDeviceData` (BFF `/bff-app/v1/device/list` and legacy `/device/rest/devices/v1/list`)
- Shape: `deviceExt.lastDeviceData` is a JSON string `{online, tem, hum, lastTime, avgDayTem, avgDayHum}`. Auth: account
  Bearer token plus `appVersion`/`clientId`/`clientType`/`iotVersion` headers. Cost: one call covers every device; no
  Developer-API quota.
- Every open-source client reads gateway thermometers from this field and nothing else:
  - homebridge-govee `lib/connection/http.js` (HEAD c7b3037, 2026-10-18): `url: 'https://app2.govee.com/bff-app/v1/device/list',`;
    `lib/platform.js`: `parsedData = JSON.parse(device1.deviceExt.lastDeviceData)` then `toReturn.temperature = parsedData.tem`.
    https://github.com/homebridge-plugins/homebridge-govee/blob/latest/lib/platform.js
  - govee2mqtt `src/undoc_api.rs` (HEAD 6f5a844): `"https://app2.govee.com/device/rest/devices/v1/list",` and
    `pub struct LastDeviceData { ... pub tem: Option<i64>, pub hum: Option<i64>, /// timestamp in milliseconds pub last_time ...`
    https://github.com/wez/govee2mqtt/blob/main/src/undoc_api.rs
  - clong/govee_h5042_sensor README (pool thermometer behind an H5042 gateway): "it was trivial to reply the network request
    that the mobile Govee app makes", and the request is `https://app2.govee.com/device/rest/devices/v1/list`.
    https://github.com/clong/govee_h5042_sensor
  - ioBroker.govee-app (TA2k) `main.js`: `url: "https://app2.govee.com/device/rest/devices/v1/list"`, then
    `device.deviceExt.lastDeviceData = JSON.parse(...)`. https://github.com/TA2k/ioBroker.govee-app/blob/master/main.js
- Evidence the field is live for H5111-on-H5151 **when the gateway relays**: homebridge-govee #1117 (H5111 "Thermometer
  R1 Lite", gateway `"sku":"H5151"`) posted `"lastDeviceData":"{\"online\":false,\"tem\":2180,\"hum\":6000,\"lastTime\":1750976816000,...}"`.
  That lastTime is 2025-06-26 22:26 UTC; the issue was opened 22:47 UTC, 21 minutes later.
  https://github.com/homebridge-plugins/homebridge-govee/issues/1117
  So the field does refresh, and `online:false` also shows up on a working unit. The `online` flag is not a reliable health signal for H5111.
- Verdict: this is what we already read. It is stale here because the cloud has nothing newer, not because we chose the wrong endpoint.

### 3.2 `POST https://app2.govee.com/th/rest/devices/v1/data/load` (thermometer history)
- Source: landenlabs/all-Sensor `DeviceGoveeHelper.java` (file last committed 2025-02-04):
  `postTo("https://app2.govee.com/th/rest/devices/v1/data/load", devDataHeaders, devDataPostJsonStr)`. The body is
  `{"device": ..., "sku": ..., "index": 0, "limit": 20}`, and the headers are `clientId`, `appVersion`,
  `Authorization: Bearer <token>`. The response is parsed as `{status, message, index, datas:[{tem, hum, time}]}`.
  https://github.com/landenlabs/all-Sensor/blob/master/sensor/src/main/java/com/landenlabs/all_sensor/sensor/DeviceGoveeHelper.java
- Auth: account token. Cost: one request per device per page, paged by `index`, up to `limit` samples. No published rate limit (UNVERIFIED).
- Evidence of fresher-than-`lastDeviceData` sub-device data: **none found**. It is history the gateway or phone uploaded.
  If the H5151 is not uploading, it can only be newer if the phone app uploads the BLE history it reads (UNVERIFIED).
  It has not been tested against an H5151 sub-device, and nobody has confirmed it still works with app 7.4.x.

### 3.3 `GET https://app2.govee.com/app/v1/gateways` (gateway list)
- The only mention is a forum comment, homebridge-govee #225 (2022): "Looks like there is a new API at
  https://app2.govee.com/app/v1/gateways to get the gateway H5151".
  https://github.com/homebridge-plugins/homebridge-govee/issues/225#issuecomment-1150082518
- No client code uses it. I found no request or response shape, auth or cost documentation. **UNVERIFIED.** At best it
  might expose the gateway's own online state, which our BFF list lacks; nothing shows it carries sub-device readings.

### 3.4 AWS IoT: unsolicited gateway `multiSync` (`ee34`) frames
- Proven for H5044 thermometers in this repo (`docs/govee-protocol-reference.md` §H5310: "An H5044 bridging an H5310 pushes
  an `ee34` frame every ~10 minutes"). homebridge-govee decodes the same frames for leak sensors: `// 0xEE34 is a gateway
  subdevice record` and `only accept the real-time status type (0x02) with a current timestamp` (`lib/platform.js`
  `receiveUpdateAWSGateway`).
- **H5151: not observed anywhere**, in this repo's captures or in public sources. The #236 window is too short to rule it
  out. Even if the H5151 pushes frames, a gateway that has not uploaded since Oct 4 is unlikely to be pushing them (UNVERIFIED).
- Auth: the existing IoT certs. Cost: free (push).

### 3.5 AWS IoT: `status` request published to the gateway topic
- Message shape used by other clients on a device topic (TA2k `main.js`):
  `{"msg":{"accountTopic":"<acct>","cmd":"status","cmdVersion":0,"transaction":"x_<ms>","type":0}}`.
- No source shows an H5151 (or an H5044) answering `status` with sub-device readings. **UNVERIFIED.**
- Risk: querying silent BLE-class topics already got H5110/H5111/H5075/H5074/H5108/H5220 excluded, because AWS IoT drops
  the session within about 5 s (`const.py:419-441`, #195, #237). How the H5151 topic behaves is unknown. A probe could
  cost every MQTT-only device on the account its push channel.

### 3.6 Local BLE (what the phone app most likely does)
- H5111 is a BLE sensor (#1117 `"bleName":"GV5111..."`) and can pair with a phone without a gateway. homebridge-govee
  lists it in its BLE thermo set: `'H5111', // same broadcast format as the H5101 family` (`lib/utils/constants.js`).
- When the app's device page opens near the sensor, it reads over Bluetooth. homebridge-govee's maintainer asked the H5151
  user exactly this: "Do the values update over time if you don't connect your phone to the sensors within the govee app?"
  https://github.com/homebridge-plugins/homebridge-govee/issues/225#issuecomment-1172913867
- ioBroker.govee-smart describes the failure mode: "a flat battery or a device out of radio range stays silent while the
  gateway keeps reporting cheerfully". https://github.com/krobipd/ioBroker.govee-smart/wiki/Sensors-and-Appliances
- Govee support to a govee2mqtt user: "Sorry to tell you that the H5151 devices do not support API functionalities."
  https://github.com/wez/govee2mqtt/issues/238
- In HA: govee-ble (`Bluetooth-Devices/govee-ble` `parser.py`) has no explicit `H5111` branch. Its 6/8-byte H51xx branch
  matches on local name, plus a `mgr_id == 0x0001 and msg_length == 8` fallback. Whether an H5111 advert decodes there is UNVERIFIED.

## 4. Assessment

1. Every cloud copy agrees the H5151 stopped relaying these two sensors on or before 2026-10-04. The fridge sensor may
   never have been relayed, since its entry has no reading at all.
2. The most likely source of the app's "live" value is the phone's own BLE connection to each H5111, not a cloud
   endpoint we are missing. This rests on circumstantial evidence (§3.6), and the reporter can confirm or rule it out in a minute.
3. No public source documents a cloud endpoint or IoT message that returns live H5151 sub-device readings. `data/load`
   (§3.2) is the only concrete candidate. It is history, and its freshness for a non-relaying gateway is UNVERIFIED.
4. An IoT `status` probe of the gateway (§3.5) is speculative and risks the session drops seen in #195/#237. Don't ship it blind.

## Recommendation

**Ask the reporter for one thing first.** Turn the phone's Bluetooth off, or check from away from home on mobile data,
then open each H5111 in Govee Home. Does it still show the current value, with a recent "updated" time? Also ask whether
the H5151's own page shows it online and lists both sensors as sub-devices.
- If the values disappear or go stale, the app is reading over BLE and the H5151 is not relaying. No integration change
  can fix that. Reply with gateway steps (remove and re-add the sub-devices on the H5151, check range, battery and
  firmware) and point them at HA Bluetooth (`govee_ble`, or an ESPHome Bluetooth proxy near the fridge). Separately, make
  the staleness honest: do not present a 3-week-old BFF reading as current (task 1).
- If the values stay live remotely, there is a cloud path we don't read. The cheapest next step is a one-off,
  diagnostics-only `data/load` read (task 2), to see whether its newest `time` beats `lastDeviceData.lastTime`. Only then
  consider wiring it into the poll.

Do not add a gateway `status` query on this evidence.

## Proposed tasks

1. **Mark BFF thermometer readings stale by `lastTime`.** When `lastDeviceData.lastTime` is older than N x `uploadRate`
   (for example, more than 6 h), stop serving the value as live (unavailable, or keep it with a `last_reading_at` attribute
   the user can see). Files: `custom_components/govee/api/auth.py`, `custom_components/govee/coordinator.py`,
   `tests/test_coordinator_bff*.py`. Grep symbol: `_BFF_NO_VALUE_SENTINELS` (staleness gate next to it), or `lastTime` in `api/auth.py`.
2. **Diagnostics-only probe of `/th/rest/devices/v1/data/load`** for BFF-read thermometers whose `lastTime` is stale.
   Record only the newest sample's `time` and the sample count, with no values and no identifiers. Gate it behind the
   diagnostics download so normal polling sends no extra requests. Files: `custom_components/govee/api/auth.py`,
   `custom_components/govee/diagnostics.py`, `tests/test_diagnostics.py`. Grep symbol: `GOVEE_TH_DATA_LOAD_URL` (new constant).
3. **Treat `hum == 6000` on temperature-only H5111 as no value**, so `state.sensor_humidity` doesn't carry a fake 60 %.
   Files: `custom_components/govee/api/auth.py`, `tests/test_api_auth.py`. Grep symbol: `_BFF_NO_VALUE_SENTINELS`.
4. **Queue the #236 reply** asking the BLE-off and gateway-page questions above. Files: `docs/release-replies.md`.
   Grep symbol: `## #236`.

## Sources
- https://github.com/lasswellt/govee-homeassistant/issues/236
- https://github.com/homebridge-plugins/homebridge-govee/blob/latest/lib/platform.js
- https://github.com/homebridge-plugins/homebridge-govee/blob/latest/lib/connection/http.js
- https://github.com/homebridge-plugins/homebridge-govee/issues/1117
- https://github.com/homebridge-plugins/homebridge-govee/issues/225#issuecomment-1150082518
- https://github.com/homebridge-plugins/homebridge-govee/issues/225#issuecomment-1172913867
- https://github.com/wez/govee2mqtt/blob/main/src/undoc_api.rs
- https://github.com/wez/govee2mqtt/issues/238
- https://github.com/wez/govee2mqtt/issues/308
- https://github.com/clong/govee_h5042_sensor
- https://github.com/TA2k/ioBroker.govee-app/blob/master/main.js
- https://github.com/landenlabs/all-Sensor/blob/master/sensor/src/main/java/com/landenlabs/all_sensor/sensor/DeviceGoveeHelper.java
- https://github.com/krobipd/ioBroker.govee-smart/wiki/Sensors-and-Appliances
- https://github.com/Bluetooth-Devices/govee-ble/blob/main/src/govee_ble/parser.py
