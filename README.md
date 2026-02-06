<p align="center">
  <img src="https://brands.home-assistant.io/_/govee/logo.png" alt="Govee Logo" width="150"/>
</p>

<h1 align="center">Govee Integration for Home Assistant</h1>

<p align="center">
  <em>Your Govee lights + Home Assistant = RGB bliss with real-time control</em>
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge" alt="HACS Custom"></a>
  <a href="https://github.com/lasswellt/govee-homeassistant/releases"><img src="https://img.shields.io/github/v/release/lasswellt/govee-homeassistant?style=for-the-badge" alt="GitHub Release"></a>
  <a href="https://github.com/lasswellt/govee-homeassistant/blob/master/LICENSE.txt"><img src="https://img.shields.io/github/license/lasswellt/govee-homeassistant?style=for-the-badge" alt="License"></a>
</p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=lasswellt&repository=govee-homeassistant&category=integration">
    <img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open in HACS">
  </a>
</p>

---

## What's This?

Ever wanted your Govee lights to actually *talk* to Home Assistant? This integration gives you:

- **Full light control** — brightness, RGB colors, color temp, the works
- **Scene magic** — your Govee scenes become HA scenes
- **RGBIC segment control** — paint each segment a different color
- **Real-time sync** — optional MQTT for instant updates (bye-bye polling lag)

---

## Get Started

### 1. Grab Your API Key

In the **Govee Home** app: **Profile** → **Settings** → **Apply for API Key**

Check your email in ~5 minutes.

### 2. Install via HACS

Click the button above, search "Govee", hit **Download**, restart Home Assistant.

### 3. Add the Integration

**Settings** → **Devices & Services** → **Add Integration** → **Govee**

Enter your API key. Want instant updates? Add your Govee email/password for MQTT.

---

## What Works

| Device | Features |
|--------|----------|
| **LED Lights & Strips** | On/off, brightness, RGB, color temp |
| **RGBIC Strips** | All the above + per-segment colors |
| **Fans** | On/off, speed (Low/Medium/High), oscillation, preset modes |
| **HDMI Sync Boxes** | On/off, HDMI input selection (1-4) |

> **Note:** Cloud-enabled devices only. Bluetooth-only devices need a different integration.

---

## Real-time Updates

Polling is *so* 2020. Add your Govee account credentials during setup for instant state sync via AWS IoT MQTT.

No credentials? Polling works fine (every 60 seconds by default).

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Devices not showing | Make sure they're WiFi devices, not Bluetooth-only |
| Slow updates | Enable MQTT or reduce poll interval in options |
| Rate limit errors | Increase poll interval (Govee allows 100 req/min) |

---

## Debug Logging

When troubleshooting issues, enable debug logging to capture detailed information about what the integration is doing.

### Enable Debug Logging

**Option 1: From the Integration (Recommended)**
1. Go to **Settings** → **Devices & Services**
2. Find the **Govee** integration card
3. Click the **three dots menu** (⋮) on the integration card
4. Select **Enable debug logging**
5. Reproduce the issue (turn on a light, trigger the error, etc.)
6. Return to the integration card, click the three dots menu again
7. Select **Disable debug logging**
8. Your browser will download a log file automatically

**Option 2: Via configuration.yaml**

For issues during startup or if you need persistent debug logging, add to your `configuration.yaml` and restart:

```yaml
logger:
  default: info
  logs:
    custom_components.govee: debug
```

### Viewing Logs

- Go to **Settings** → **System** → **Logs**
- Click **Load Full Logs** to see everything
- Use the search box to filter for "govee"

See [Home Assistant Logger docs](https://www.home-assistant.io/integrations/logger/) for more details.

### Gathering Logs for Issues

When opening an issue, include relevant log entries. Here's what to capture:

1. **Enable debug logging** (see above)
2. **Reproduce the issue** (turn on/off a device, change a scene, etc.)
3. **Copy the relevant log entries**

**What to include:**
- Logs from when Home Assistant starts (shows device discovery)
- Logs from when the issue occurs
- Any error messages or tracebacks

**Example log snippet to include:**
```
2024-01-15 10:30:45 DEBUG (MainThread) [custom_components.govee.coordinator] Device: Living Room Light (AA:BB:CC:DD:EE:FF:00:11) type=devices.types.light
2024-01-15 10:30:45 DEBUG (MainThread) [custom_components.govee.coordinator]   Capability: type=devices.capabilities.on_off instance=powerSwitch
```

**Before posting**, redact sensitive information:
- Replace device IDs with `XX:XX:XX:XX:XX:XX:XX:XX`
- Remove any email addresses or account IDs

---

## Reporting Issues

When reporting issues with unsupported devices or unexpected behavior, please include your device's API response. This helps us understand your device's capabilities and fix the problem.

### Getting Your Device Data

You'll need your **Govee API key** (the same one you used to set up this integration).

---

#### macOS / Linux

**Step 1: Open Terminal**
- **macOS:** Press `Cmd + Space`, type "Terminal", press Enter
- **Linux:** Press `Ctrl + Alt + T` or search for "Terminal" in your applications

**Step 2: Get your device list**

Copy this command, replace `YOUR_API_KEY` with your actual API key, then paste into Terminal and press Enter:

```bash
curl -s -H "Govee-API-Key: YOUR_API_KEY" "https://openapi.api.govee.com/router/api/v1/user/devices"
```

**Step 3: Get device state** (optional, for more details)

From the output above, find your device's `sku` (e.g., "H7101") and `device` ID (e.g., "AA:BB:CC:DD:EE:FF:00:11"), then run:

```bash
curl -s -X POST -H "Govee-API-Key: YOUR_API_KEY" -H "Content-Type: application/json" -d '{"requestId":"test","payload":{"sku":"YOUR_SKU","device":"YOUR_DEVICE_ID"}}' "https://openapi.api.govee.com/router/api/v1/device/state"
```

---

#### Windows

**Step 1: Open PowerShell**
- Press `Win + X`, then click "Windows PowerShell" or "Terminal"
- Or press `Win + R`, type `powershell`, press Enter

**Step 2: Get your device list**

Copy this command, replace `YOUR_API_KEY` with your actual API key, then paste into PowerShell and press Enter:

```powershell
Invoke-RestMethod -Uri "https://openapi.api.govee.com/router/api/v1/user/devices" -Headers @{"Govee-API-Key"="YOUR_API_KEY"} | ConvertTo-Json -Depth 10
```

**Step 3: Get device state** (optional, for more details)

From the output above, find your device's `sku` and `device` ID, then run (replace the values):

```powershell
Invoke-RestMethod -Uri "https://openapi.api.govee.com/router/api/v1/device/state" -Method POST -Headers @{"Govee-API-Key"="YOUR_API_KEY"; "Content-Type"="application/json"} -Body '{"requestId":"test","payload":{"sku":"YOUR_SKU","device":"YOUR_DEVICE_ID"}}' | ConvertTo-Json -Depth 10
```

---

### Posting Your Results

**Before posting**, redact sensitive information:
- Replace your API key with `REDACTED`
- Replace your email/account ID if visible

Then paste the output in your [GitHub issue](https://github.com/lasswellt/govee-homeassistant/issues).

---

## Contributing

PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT — see [LICENSE.txt](LICENSE.txt)
