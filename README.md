<div align="center">

# Govee Cloud Integration for Home Assistant

**Control Govee lights, plugs, fans, humidifiers, heaters, thermometers, air‑quality & CO₂ monitors, presence & leak sensors — with optional real‑time push over Govee's AWS IoT MQTT and automatic local LAN control for devices that support it.**

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square)](https://github.com/hacs/integration)
[![Release](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/release.json)](https://github.com/lasswellt/govee-homeassistant/releases)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.11+-41BDF5?style=flat-square&logo=home-assistant&logoColor=white)
![Quality scale](https://img.shields.io/badge/quality%20scale-silver-silver?style=flat-square)
[![License](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/license.json)](LICENSE.txt)

[![Active installs](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/installs.json)](https://analytics.home-assistant.io/)
[![Govee API status](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/api-status.json)](#-live-status)
[![Stars](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/stars.json)](https://github.com/lasswellt/govee-homeassistant/stargazers)

</div>

> **Hub (cloud)** · IoT class `cloud_push` (MQTT + polling) · UI‑only config, no YAML

---

## 📊 Live status

<div align="center">

<img alt="Active installs trend" src="https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/installs-trend.svg?v=3" width="49%" />
&nbsp;
<img alt="Govee API uptime" src="https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/api-uptime.svg?v=3" width="49%" />

<img alt="Installs by version" src="https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/versions.svg?v=3" width="99%" />

<img alt="GitHub star growth" src="https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/stars-trend.svg?v=1" width="99%" />

</div>

<sub>**Active installs** counts only versions **released by this repository** — other `govee` forks and legacy installs sharing the same domain are excluded — and reflects Home Assistant instances opted into Usage‑level analytics, so true usage is higher. **Govee API status** pings `openapi.api.govee.com` and `app2.govee.com` hourly: round of red bars on the right = an outage today, not a problem with your setup. Both graphs update automatically via GitHub Actions ([uptime](.github/workflows/uptime.yml) · [install‑stats](.github/workflows/install-stats.yml)).</sub>

---

## What this is

A custom component that talks to Govee's cloud. Add your Govee API key and your devices show up in Home Assistant. Add your Govee account email/password as well and you also get **real‑time updates** (push) instead of polling alone, plus support for **hub‑based leak sensors**. Devices with Govee's **LAN API** enabled are additionally controlled **locally, automatically** — with the cloud as fallback.

It is **capability‑based**: entities are created from the capabilities Govee reports for each device, not a hard‑coded SKU list — so new models in a known device class generally work without an update. A handful of things Govee's API can't express are keyed to specific models (leak sensors and their hubs, presence sensors, thermometers absent from the developer API, and models that report Fahrenheit without saying so); everything else is derived from what the device advertises.

> **Cloud / WiFi devices only.** Bluetooth‑only devices (e.g. a standalone H5075 thermometer with no gateway) don't appear in Govee's cloud API. For those, use Home Assistant's first‑party [**Govee Bluetooth (`govee_ble`)**](https://www.home-assistant.io/integrations/govee_ble/) integration. The two can run side by side.

---

## How this compares

Govee in Home Assistant has several integrations, and it's easy to pick one that can't control your devices. Quick orientation:

| Integration | How it talks to Govee | Scenes / RGBIC segments | Non‑light devices | Notes |
|---|---|---|---|---|
| **This integration** | Cloud API v2 **+ AWS IoT MQTT push + local LAN (auto)** | ✅ Yes | Plugs, fans, humidifiers, heaters, sensors, leak hubs | Full feature set; push updates; LAN‑enabled lights controlled locally with cloud fallback; handles Govee's 2026 email‑2FA login |
| [`govee_light_local`](https://www.home-assistant.io/integrations/govee_light_local/) (HA built‑in) | LAN UDP | ❌ No | Lights only | Fast & local, but on/off + brightness + color only, and only models with LAN control enabled |
| [`govee_ble`](https://www.home-assistant.io/integrations/govee_ble/) (HA built‑in) | Bluetooth | ❌ No | Sensors only | Read‑only sensors — **no light control** |
| [govee2mqtt](https://github.com/wez/govee2mqtt) | LAN + cloud + MQTT | ✅ Yes | Wide | Most capable, but requires a separate MQTT broker/add‑on to run |
| [goveelife](https://github.com/disforw/goveelife) | Cloud OpenAPI v2 | ✅ Yes | Best for appliances | Polling‑only; strong on heaters/fans/humidifiers |

**Why pick this one:**

- **Full control of cloud‑only WiFi devices.** Many bulbs/strips (e.g. H6099) have **no LAN API** and **no light control over BLE** — the cloud path is the only way to get scenes, RGBIC segments, music mode and DreamView. The HA built‑in LAN/BLE integrations can't do this; people often conclude "Govee + HA is broken" when really they're using the wrong integration for the device.
- **MQTT push, not just polling.** Real‑time state arrives over AWS IoT, which also eases the Govee cloud rate limits (100 req/min, 10,000/day) that poll‑only integrations can hit on larger setups.
- **Local LAN control, zero setup.** Devices with Govee's LAN API enabled are discovered automatically and get local reads plus verified local writes (power, brightness, color, color temperature) — no toggle to flip, no broker to run. Every LAN write is confirmed by reading the device back; if it doesn't confirm, the command falls through to MQTT/REST so a device is never stranded. This also rescues devices whose color changes Govee's cloud accepts and then never delivers.
- **Resilient account login.** Govee added mandatory email **2FA** in 2026, which silently broke older account‑login integrations at startup. This one handles 2FA in an interactive setup/reconfigure flow and caches IoT credentials across reloads.
- **No extra infrastructure.** Full features without standing up a separate MQTT broker the way a bridge‑style setup (govee2mqtt) requires.

---

## Supported Govee devices

| Category | Examples | Entities you get |
|---|---|---|
| **Lights** (strips, bulbs, bars, TV backlights, sync boxes) | H619x, H61xx, H6058, H6099, H66A0, H6604 | Light (on/off, brightness, RGB, color temp), scene & DIY selectors, music‑mode switch, DreamView switch; sync boxes return to their HDMI/Video source when you clear the scene |
| **RGBIC lights** | H619C, H6198, H60A6 | Everything above **plus** per‑segment color control (see [Segments](#rgbic-segment-control)); Ceiling Light Pro (H60A6) adds an ambient/backlight‑ring switch |
| **Multi‑zone lamps** | H60B2, H60B3 | Per‑zone on/off switches (Light Zone 1/2/3); the H60B3 uplighter adds Nebula/Side/Bottom light switches |
| **Smart plugs / sockets** | H5080, H5083, H5089, H5160, H5161 | Switch; outlet extenders (H5089) expose each outlet separately **plus** an RGB Night Light; three‑outlet strips (H5160/H5161) get per‑outlet switches with account login (optimistic until their readback is decoded) |
| **Ceiling fan + light combos** | H1310, H1370 | Separate Main Light & Background Light **and** a Fan entity (on/off, speed, reverse, oscillation) |
| **Tower / pedestal fans** | H7101, H7102, H7105, H7106, H7107 | Fan (speed, oscillation, preset modes); on the Tower Fan 2 (H7105/H7107) oscillation needs account login — see below |
| **Air purifiers** | H7120–H7127 | Fan / work modes, filter‑life sensor, air‑quality (AQI) sensor, optional nightlight |
| **Humidifiers & dehumidifiers** | H7140, H7141, H7150, H7151, H7152 | Modes + target‑humidity setpoint; dehumidifiers add a **Water Tank Full** sensor (real‑time event push, API key only) with a paired **Clear Water Alert** button |
| **Aroma diffusers** | H7161 | Power switch + light/mist scene selector |
| **Space heaters** | H7130, H7131, H713B, H721C | Power switch, target‑temperature number, auto‑stop switch; temperature unit follows what the device itself reports |
| **Thermometers / hygrometers** | H5103, H5107, H5109, H5111, H5112, H5179, H5301, H5310 | Temperature & humidity sensors, **Battery** (account login) + a "Last Changed" timestamp; gateway‑bridged models (H5301/H5310 via an H5044) nest under the hub |
| **Probe (cooking) thermometers** | H5192 | Core and ambient temperature per probe, plus the four alarm limits as editable numbers. These are **pull** devices — they answer a read and otherwise stay silent — so a **Live polling** switch (off by default, to spare the battery) controls whether readings update |
| **Air‑quality & CO₂ monitors** | H5106, H5140 | CO₂ (ppm), air‑quality (AQI), temperature & humidity sensors |
| **Presence sensors** | H5127 | Occupancy binary sensor, updated in real time over MQTT |
| **Leak sensors** | H5054, H5055, H5058, H5059 (via an H5040/H5043/H5044 hub) | Moisture binary sensor, battery, sensor/gateway connectivity, last‑wet timestamp, button‑press event |

Don't see your device, or a capability is missing? [Open an issue](https://github.com/lasswellt/govee-homeassistant/issues) with a diagnostics download (see [Diagnostics](#diagnostics--debug-logging)).

---

## How to install Govee in Home Assistant

### HACS (recommended)

1. HACS → **⋮** → **Custom repositories**
2. Repository: `https://github.com/lasswellt/govee-homeassistant`, Category: **Integration**
3. Install **Govee Cloud Integration**, then **restart Home Assistant**

### Manual

Copy `custom_components/govee/` into your Home Assistant `config/custom_components/` directory and restart.

---

## Set up

### 1. Get a Govee API key

In the **Govee Home** app: **Profile → Settings (gear) → Apply for API Key**. You'll receive it by email, usually within minutes.

### 2. Add the integration

**Settings → Devices & Services → Add Integration → Govee Cloud Integration**, then paste your API key.

The API key alone gives you device control and **polling** for state.

### 3. (Optional but recommended) Add account login for real‑time updates

In the same setup flow you can enter your **Govee account email and password**. This enables:

- **Real‑time push updates** over AWS IoT MQTT (no waiting for the next poll)
- **Leak‑sensor support** (H5054 / H5055 / H5058 / H5059 via an H5040/H5043/H5044 hub)
- **Battery levels** on battery‑powered sensors — the developer API doesn't expose them at all
- **Thermometers the developer API doesn't return** (e.g. H5301, H5310), and readings for those it returns empty (e.g. H5179, H5112)
- **Oscillation on the Tower Fan 2 (H7105/H7107)** — the developer API's oscillation toggle is accepted but does nothing on these fans; with account login the integration sends the raw frame the fan actually obeys
- **MQTT‑based control**, if you turn it on in options

#### Two‑factor (email code)

Since 2026 Govee requires email verification for account login. If your account has it on, the flow will pause, Govee emails you a **code**, and you enter it to finish. The code expires in ~15 minutes. Credentials are stored encrypted in your config entry.

> Account login is optional. Without it, the integration runs in polling‑only mode and everything except the features listed above still works. You can add or remove it later via **⋮ → Reconfigure** without losing your devices.

---

## Configuration options

After setup, open **Settings → Devices & Services → Govee Cloud Integration → ⚙️ Configure**:

| Option | Default | What it does |
|---|---|---|
| **Polling interval (seconds)** | `60` | How often to poll the cloud for state (30–300). MQTT and LAN updates arrive between polls. |
| **Leak sensor polling interval (seconds)** | `120` | How often standalone RF water detectors (e.g. H5054) are checked for a leak (60–3600). These have no push channel, so a leak surfaces with up to this much delay — lower reacts faster but makes more account API calls. Needs account login; ignored if you have no such detectors. |
| **Temperature unit from Govee API (thermometers)** | `Auto` | Govee returns thermometer values in the device's app unit with **no** unit metadata. **Auto** (default) reads your account's own °C/°F preference where Govee exposes it, falls back to converting the models known to report Fahrenheit, and trusts the rest; pick **Fahrenheit** if a reading still looks ~1.8× too high (e.g. 74 instead of 23), or **Celsius** to never convert. |
| **Enable group devices** | `off` | Surface the device groups you created in the Govee app as single light entities (power/brightness/color; state is best‑effort). |
| **Enable scene selector** | `on` | Create a per‑device dropdown to activate Govee scenes. |
| **Enable DIY scene selector** | `on` | Create a per‑device dropdown for your DIY scenes. |
| **Expose per‑device transport connectivity sensors** | `off` | Add diagnostic binary sensors showing each device's MQTT/BLE/LAN reachability. |
| **Send power/brightness/color over MQTT (experimental)** | `off` | Routes those commands through Govee's MQTT channel instead of the REST API — lower latency, bypasses REST rate limits. Requires account login; falls back to REST automatically. Uses an undocumented channel, so leave off if commands misbehave. |
| **LAN device addresses / subnets (advanced)** | *(blank)* | Only needed when LAN‑enabled devices sit on a different subnet/VLAN than Home Assistant. Comma‑separated IPs, broadcast addresses, and/or CIDR subnets (/24 or smaller). Leave blank when everything shares HA's network — discovery is automatic. Enter `off` to disable LAN discovery and local control entirely. |

RGBIC devices get a second step after submitting, where you choose a **segment mode** for each device individually — see [Segments](#rgbic-segment-control).

---

## Real‑time updates & local LAN control

With account login configured, the integration maintains an AWS IoT MQTT connection and applies state changes the moment they happen. Without it, state comes from polling on your configured interval. A **"Govee Integration"** device exposes diagnostics for this: API rate‑limit remaining, MQTT status, and a **"Last MQTT Received"** timestamp.

Every device also gets two diagnostic timestamps — **Last Updated** (when data last arrived) and **Last Command Sent** — plus a **Connectivity** sensor. Turning on **Expose per‑device transport connectivity sensors** adds one reachability sensor per transport (Cloud API, MQTT, Bluetooth, LAN) for pinpointing which path a device is actually using.

**Local LAN control is automatic.** If a device has Govee's LAN API turned on (Govee Home app → device settings → LAN Control), the integration finds it via a periodic local discovery scan and starts using the LAN for state reads and for **power, brightness, color and color temperature** commands — no option to enable. Every LAN write is **verified by reading the device back**; an unconfirmed write falls through to MQTT/REST instantly, and a device that stops answering is demoted back to cloud transports until it reappears. Devices on another subnet/VLAN can be reached via the **LAN device addresses** option (see above).

This matters beyond speed: Govee's cloud sometimes answers a color command with `success` and never delivers it to the device (the light doesn't change, and nothing reports an error). Sending color locally sidesteps the cloud entirely — see [Colors don't apply](#troubleshooting).

**Command routing.** Each command takes the fastest transport that can carry it *and confirm it*, falling back automatically: **BLE → LAN → MQTT → cloud REST**. LAN carries power, brightness, color and color temperature — exactly the four values a device reports back, which is what makes verify‑by‑read possible. MQTT (opt‑in) carries power, brightness and color. Direct BLE control is deliberately limited to one model confirmed to honour it (H6199); other models advertise Bluetooth but silently drop writes. Everything else — scenes, segments, music mode, work modes, toggles — always goes over the cloud API, with one exception: Tower Fan 2 (H7105/H7107) oscillation goes over the AWS IoT session when account login is configured, because the cloud toggle is a no-op on those fans.

Commands always use optimistic updates, so the UI reflects your action immediately and reconciles with the next confirmed state.

---

## RGBIC segment control

For RGBIC strips/bars you can control individual lighting segments. After saving the options you're asked which RGBIC devices to configure, then a mode for **each one separately** — so a 14‑segment strip can be Individual while a bar you only ever set as a whole is Grouped:

- **Individual** (default) — one light entity per segment, for maximum control.
- **Grouped** — a single "Segments" entity that sets all segments together.
- **Disabled** — no segment entities.

Segment colors aren't reliably returned by the API, so segment entities keep optimistic state and restore it across restarts.

There's also a service for automations:

```yaml
service: govee.set_segment_color
data:
  device_id: "AA:BB:CC:DD:EE:FF:00:11"
  segments: [0, 1, 2]
  rgb_color: [255, 0, 0]
```

---

## Scenes, DIY, music & DreamView

- **Scenes / DIY scenes** — activated through per‑device select dropdowns (toggle in options). The API doesn't reliably report the active scene, so the selection is preserved optimistically and cleared when you switch to another mode (color, color temp, music, etc.).
- **Music mode** — exposed as a switch on capable lights.
- **DreamView / video sync** — exposed as a switch on capable backlights.
- Use the **`govee.refresh_scenes`** service to re‑pull the scene catalog (optionally for one `device_id`).

---

## Device groups

Enable **group devices** in options to surface Govee‑app groups as single light entities. A command to a group is sent once and fanned out to all members by Govee's cloud, which syncs better than grouping the same lights with Home Assistant helpers (those fire separate commands that arrive at slightly different times). Group state is best‑effort (groups can't be polled), and group lights support power/brightness/color only.

---

## Thermometers & sensors

Thermometer/hygrometer readings (H5103, H5107, H5109, H5179, …) come from Govee's **cloud**, which only refreshes them on its own schedule:

- **WiFi‑native sensors** (e.g. H5179): on the order of ~10 minutes.
- **Bluetooth sensors behind a gateway** (e.g. H5075/H5110 through an **H5151** WiFi gateway): the gateway batch‑uploads infrequently — often many minutes (observed ~15–60 min; the exact interval is Govee's, not guaranteed).

So a reading can look "frozen" while polling is perfectly healthy — it's the latest value Govee has. This is a Govee cloud limitation, not an integration bug (govee2mqtt and homebridge‑govee hit the same wall, and AWS IoT MQTT carries no thermometer data at all). Each thermometer exposes a **"Last Changed"** diagnostic timestamp so you can see how old the value is.

**Battery & gateway‑bridged sensors.** Battery level for battery‑powered sensors (thermometers, leak detectors) comes from your Govee **account** data, so it needs account login (email/password) — an API key alone can't see it. It's refreshed every 5 minutes, so give it a few minutes after a restart before assuming it's missing. Sensors that reach the cloud through a hub are handled two different ways, depending on how Govee exposes them. Models the developer API doesn't return at all (H5301, H5310) are discovered from the **account device list** and nested under their hub. Models it does return but with empty readings (H5179, H5112) are discovered normally and only their *values* are read from the account data.

Some gateway‑bridged sensors are listed by Govee with no reading attached. When that happens the integration keeps polling the regular cloud API for them rather than assuming the account data will fill in, and switches over automatically if it ever does — so a sensor isn't left permanently blank because of which source Govee happened to populate.

**Temperature unit.** Govee reports thermometer values with no unit field, so the integration defaults to an **Auto** mode. Auto first looks for your account's own °C/°F display preference, which Govee exposes per device and which the cloud API mirrors when it returns the reading; where that isn't available it falls back to converting the models known to report Fahrenheit, and trusts everything else. If a reading is still ~1.8× off, set the unit explicitly in ⚙️ Configure — see [Configuration options](#configuration-options).

**Other sensors.** Air‑quality/CO₂ monitors (H5106, H5140) expose CO₂ (ppm), AQI, temperature and humidity from the cloud poll (not MQTT). The H5127 presence sensor reports **occupancy** in real time over MQTT. Dehumidifiers surface a **Water Tank Full** sensor driven by Govee's official event push (API key only — no account login needed); it fires when the tank is full **or** the bucket is pulled out. Govee never sends a "cleared" event, so the alert latches — surviving HA restarts — until you press the paired **Clear Water Alert** button after emptying/re‑inserting the tank; the sensor's `changed_at` attribute carries the last event/clear time for custom automations. None of these expose a live PM2.5 or room temp/humidity beyond what's listed — those are Bluetooth‑only in the Govee app.

**Want real‑time (~2 s) readings?** Govee thermometers broadcast over Bluetooth:

1. Enable Home Assistant's first‑party [**Govee Bluetooth (`govee_ble`)**](https://www.home-assistant.io/integrations/govee_ble/) for any sensor within Bluetooth range of your HA host.
2. For distant sensors, add an [**ESPHome Bluetooth proxy**](https://esphome.io/components/bluetooth_proxy.html) nearby.

---

## Services

| Service | Purpose |
|---|---|
| `govee.refresh_scenes` | Re‑fetch the scene catalog from Govee (optional `device_id`). |
| `govee.set_segment_color` | Set RGB color on specific segments of an RGBIC device. |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Devices not showing up | They must be WiFi/cloud devices. Bluetooth‑only devices need [`govee_ble`](https://www.home-assistant.io/integrations/govee_ble/). |
| **Color doesn't apply** — on/off and scenes work, color changes nothing | Govee's cloud sometimes accepts a color command and never delivers it. Turn on **LAN Control** for the device in the Govee Home app so color is sent locally; if the device has no LAN API, enable **Send power/brightness/color over MQTT** in ⚙️ Configure (needs account login). If neither works, the device firmware is ignoring the command — attach diagnostics to an issue. |
| Thermometer reads ~1.8× too high (e.g. 74 vs 23) | Set **Temperature unit from Govee API → Fahrenheit** in ⚙️ Configure. |
| Thermometer value looks "frozen" | Expected — Govee's cloud refreshes on its own cadence. See [Thermometers & sensors](#thermometers--sensors). |
| Sensor shows **Unknown** and never updates | Gateway‑bridged sensors depend on data Govee may not be publishing for your account. Grab a diagnostics download and open an issue — the `bff_device_values` section shows whether the reading exists at all. |
| Leak alert arrives late | Standalone RF detectors (H5054) have no push channel and are polled; lower the **Leak sensor polling interval**. Hub‑attached sensors (H5058/H5059) push in real time and aren't affected. |
| Battery missing on a sensor | Battery comes from your Govee **account** data, so account login is required — an API key alone can't see it. It's fetched every 5 minutes, so allow a few minutes after a restart. |
| A **"Govee MQTT disconnected"** repair appears | Real‑time push is down; polling keeps everything working. The integration retries forever with backoff and clears the repair itself when the connection returns (this can take a few minutes after an outage). If it stays for longer than that, reload the integration; if it comes straight back, use **⋮ → Reconfigure** to refresh the account sign‑in. Two Home Assistant installs on one Govee account will kick each other off AWS IoT in turn — use separate accounts. |
| No real‑time updates / no leak sensors | Add your Govee account email/password (enables MQTT). API key alone is polling‑only. |
| LAN sensor shows Disconnected / device not found locally | Enable **LAN Control** for the device in the Govee Home app. Across subnets/VLANs, add the device's IP or subnet under **LAN device addresses** in ⚙️ Configure. |
| Re‑prompted for a 2FA code / login fails | Reconfigure the integration and complete the email‑code step; codes expire in ~15 minutes. |
| Rate‑limit warnings | The Govee API allows 100 requests/min and 10,000/day. Increase the polling interval if you have many devices. |

If something's still wrong, grab a diagnostics download (below) and [open an issue](https://github.com/lasswellt/govee-homeassistant/issues).

---

## Diagnostics & debug logging

> Steps below are for **Home Assistant 2026.x**. Diagnostics auto‑redact your API key, account credentials, tokens, and device MAC addresses, so they're safe to attach to a GitHub issue.

### Download diagnostics (best for most reports)

**Whole integration:**

1. **Settings → Devices & Services**
2. Click **Govee Cloud Integration**
3. On the integration's entry, open the **⋮** (three‑dot) menu → **Download diagnostics**
4. Attach the downloaded JSON to your issue

**A single device** (when only one device misbehaves):

1. **Settings → Devices & Services → Govee Cloud Integration → _N_ devices**
2. Open the device
3. **⋮** (top‑right) → **Download diagnostics**

The download includes each device's parsed state, the verbatim cloud response, the last MQTT push, per‑transport health (including LAN discovery results), a ring buffer of recent OpenAPI event pushes (e.g. water‑tank‑full), and — for leak‑sensor and gateway‑sensor troubleshooting — recent hub packets and a privacy‑safe summary of what the account API returns for each device.

The most useful section for "I pressed the button and nothing happened" reports is **`recent_commands`**: every recent control command with the exact payload sent, which transport carried it (cloud, LAN, MQTT or BLE), and how the device or cloud answered — including *why* a local write wasn't confirmed. If a command shows `success` there and the device still didn't react, that's strong evidence the problem is on Govee's side rather than in Home Assistant.

### Capture a debug log (no YAML needed)

Home Assistant can record a scoped debug log with one click:

1. **Settings → Devices & Services → Govee Cloud Integration**
2. On the entry's **⋮** menu → **Enable debug logging**
3. **Reproduce the problem** (toggle the device, wait for an update, etc.)
4. Return to the **⋮** menu → **Disable debug logging** — Home Assistant **automatically downloads** the log file
5. Attach it to your issue

<details>
<summary>YAML alternative (advanced)</summary>

Add to `configuration.yaml`, restart, reproduce, then collect from **Settings → System → Logs → Download full log**:

```yaml
logger:
  default: warning
  logs:
    custom_components.govee: debug
    custom_components.govee.api.auth: debug   # add for login / leak‑sensor issues
    aiomqtt: debug                            # add for real‑time / MQTT issues
```
</details>

### What to include in an issue

- The device **SKU / model** (e.g. `H6199`) and what's wrong
- A **diagnostics download** (and a **debug log** if it's a control/connectivity problem)
- Your Home Assistant and integration versions

---

## Contributing

Issues and PRs welcome. Development quick start:

```bash
# Tests, type-check, lint, format
pytest          # or: tox
mypy custom_components/govee
flake8 .
black .
```

Some conventions worth knowing before you open a PR:

- **Capability-based, not SKU-based.** Entities come from the capabilities Govee reports. Add a SKU allowlist entry only when the API genuinely can't express the difference — and put the evidence in a comment, the way `FAHRENHEIT_REPORTING_SKUS` and `SKU_SEGMENT_OVERRIDES` do.
- **Explain the *why* in comments.** Most of this codebase works around undocumented Govee behaviour. A comment saying what the code does is redundant; one saying which capture or issue proved it is not.
- **Tests carry the evidence.** Where a fix comes from a real capture, the test uses the real bytes. `tests/test_mqtt_multisync.py` and `tests/test_dual_probe.py` are the pattern.
- **CI runs on pull requests, including from forks.** A first-time contributor's first run needs a one-click approval from a maintainer; after that they run automatically. `test (3.12)`, `test (3.13)`, `mypy`, `HACS Action` and `Home Assistant Validation` must pass before a PR can merge — running `pytest` and `flake8` locally first still saves a round trip. Note `mypy` only runs on 3.12: it fails on 3.13 against Home Assistant core's PEP 696 type-parameter defaults, so a local 3.13 run will not catch type errors.

---

## Credits

### Origin

This integration began as **[LaggAt/hacs-govee](https://github.com/LaggAt/hacs-govee)** by **[@LaggAt](https://github.com/LaggAt)** (Florian Lagg), first published in August 2020. That work is the foundation everything here is built on — the original API client, the config flow, and the HACS packaging — and it remains the copyright holder in [LICENSE](LICENSE.txt). The current codebase has been substantially rewritten (Govee API v2, MQTT push, LAN control, the non-light platforms), but it exists because that came first. The two dozen people who contributed to `hacs-govee` between 2020 and 2022 are credited in that repository's own history rather than here, since their pull requests live there — the work is still in this lineage.

### Contributors

People who have landed code here, alphabetically:

[@anant-j](https://github.com/anant-j) ·
[@andreuSignes](https://github.com/andreuSignes) ·
[@brian6932](https://github.com/brian6932) ·
[@chrisns](https://github.com/chrisns) ·
[@Danimal4326](https://github.com/Danimal4326) ·
[@DUC750](https://github.com/DUC750) ·
[@maxi07](https://github.com/maxi07) ·
[@momousta](https://github.com/momousta) ·
[@phylix](https://github.com/phylix) ·
[@thephw](https://github.com/thephw) ·
[@TomK](https://github.com/TomK) ·
[@tonyrsutton](https://github.com/tonyrsutton) ·
[@yyolk](https://github.com/yyolk)

### Investigation and hardware testing

Govee's cloud API is undocumented in the places that matter most, and several fixes here exist only because someone with the hardware did the work to prove what was actually happening. This is not a thank-you list — each of these produced a specific result:

- **[@Araknus13](https://github.com/Araknus13)** — decoded the gateway thermometer frame format by pairing 30 on-the-hour captures against their own logged history, establishing `T[°C] = (byte13 + 112) / 10` to within 0.1 K and identifying bytes 9–12 as a big-endian timestamp. Two earlier candidate formulas were both wrong; their data is what separated them. ([#151](https://github.com/lasswellt/govee-homeassistant/issues/151))
- **[@chrisns](https://github.com/chrisns)** — separated two LAN failures that looked identical, with an A/B test across subnets proving the same command works from the device's own VLAN and is silently ignored from another. That distinction is the whole design of the `device_id=ip[!]` override. ([#164](https://github.com/lasswellt/govee-homeassistant/issues/164), [#131](https://github.com/lasswellt/govee-homeassistant/issues/131))
- **[@Danimal4326](https://github.com/Danimal4326)** — traced silent leak-detection failures to Govee writing alert strings with non-breaking spaces, which the matcher stripped only as ASCII. Found it down to the byte. ([#145](https://github.com/lasswellt/govee-homeassistant/issues/145))
- **[@andreuSignes](https://github.com/andreuSignes)** — identified the `elementRange` / `size.max` split behind phantom RGBIC segment entities, and verified the fix end-to-end on four live H7075 units. ([#161](https://github.com/lasswellt/govee-homeassistant/pull/161))
- **[@thephw](https://github.com/thephw)** — captured and decoded the gateway BLE relay path for the H5901 water timer, including establishing that the account certificate is policy-denied from the gateway command topic. ([#135](https://github.com/lasswellt/govee-homeassistant/issues/135))
- **[@Eschwinm](https://github.com/Eschwinm)** — tested all 15 advertised H7076 segments individually to establish which four indices actually move the light. ([#160](https://github.com/lasswellt/govee-homeassistant/issues/160))
- **[@ThomasADavis](https://github.com/ThomasADavis)** — reproduced a reported brightness fault in the Govee app itself, which is what identified it as firmware rather than an integration bug. ([#159](https://github.com/lasswellt/govee-homeassistant/issues/159))
- **[@matteotomasoni](https://github.com/matteotomasoni)**, **[@thrstnbecker](https://github.com/thrstnbecker)**, **[@mikejhendricks](https://github.com/mikejhendricks)**, **[@ftremblay91](https://github.com/ftremblay91)**, **[@CrazyLukas98](https://github.com/CrazyLukas98)**, **[@PaulTubeTV](https://github.com/PaulTubeTV)**, **[@kayandwill0306-cyber](https://github.com/kayandwill0306-cyber)** — diagnostics downloads and patient re-testing across multiple releases. Several root causes in this integration were found in their attachments, not in the code.

If you filed an issue with a diagnostics download attached, you probably made something here work. That is genuinely the most useful thing a user can do.

### Protocol research

Govee publishes no specification for the account API, the AWS IoT topics, the BLE packet formats or the LAN protocol. Everything the integration does on those paths was reverse-engineered, much of it first by other projects. Where this codebase relies on their findings, the source is cited in the comment next to the code:

- **[homebridge-govee](https://github.com/homebridge-plugins/homebridge-govee)** — account login flow, AWS IoT credential exchange, and the leak-sensor warning-message path
- **[wez/govee2mqtt](https://github.com/wez/govee2mqtt)** — device-settings schemas, LAN protocol details, and independent confirmation of several API quirks
- **[Beshelmek/govee_ble_lights](https://github.com/Beshelmek/govee_ble_lights)** — BLE command frame formats used for the passthrough (`ptReal`) path
- **[disforw/goveelife](https://github.com/disforw/goveelife)** — real-device capability fixtures used to cross-validate the parser
- **[constructorfleet/homebridge-ultimate-govee](https://github.com/constructorfleet/homebridge-ultimate-govee)** — mmWave presence-report decoding
- **[Bluetooth-Devices/govee-ble](https://github.com/Bluetooth-Devices/govee-ble)** — BLE advertisement formats
- **[Galorhallen/govee-local-api](https://github.com/Galorhallen/govee-local-api)** — LAN UDP discovery and control, also used by Home Assistant's built-in `govee_light_local`
- **[TheOneOgre/govee-cloud](https://github.com/TheOneOgre/govee-cloud)** — client-id derivation and account-API behaviour
- **[egold555](https://github.com/egold555/Govee-Reverse-Engineering)** and **[BeauJBurroughs](https://github.com/BeauJBurroughs/Govee-H6127-Reverse-Engineering)** — early BLE protocol reverse-engineering that most later work builds on

Detailed findings, byte maps and API shapes are documented in [`docs/govee-protocol-reference.md`](docs/govee-protocol-reference.md).

---

## Disclaimer & license

This is an unofficial integration and is not affiliated with or endorsed by Govee. "Govee" is a trademark of its respective owner. Use at your own risk.

The undocumented account API, MQTT and LAN paths are reverse-engineered and can stop working whenever Govee changes them. The documented Developer API path is the stable one; everything built on top of it is best-effort by nature.

Licensed under the terms in [LICENSE](LICENSE.txt) — originally © 2021 Florian Lagg (@LaggAt), and maintained since under the same terms.
