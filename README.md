# Govee Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/lasswellt/hacs-govee.svg)](https://github.com/lasswellt/hacs-govee/releases)
[![GitHub License](https://img.shields.io/github/license/lasswellt/hacs-govee.svg)](https://github.com/lasswellt/hacs-govee/blob/master/LICENSE)

Control your Govee lights, LED strips, and smart plugs through Home Assistant using the official Govee API v2.0.

**Current Version:** 2025.12.1

## Features

- **Lights & LED Strips** - On/off, brightness, color (RGB), color temperature
- **Scenes** - Select from dynamic scenes and DIY scenes via dropdown
- **Segment Control** - Control individual segments on RGBIC strips
- **Music Mode** - Activate music-reactive lighting modes
- **Smart Plugs** - On/off control for Govee smart outlets
- **Rate Limiting** - Built-in protection against API limits (100/min, 10,000/day)

---

## Table of Contents

- [Installation](#installation)
  - [HACS Installation (Recommended)](#hacs-installation-recommended)
  - [Manual Installation](#manual-installation)
- [Getting Your API Key](#getting-your-api-key)
- [Configuration](#configuration)
  - [Initial Setup](#initial-setup)
  - [Configuration Options](#configuration-options)
- [Supported Devices](#supported-devices)
- [Features & Usage](#features--usage)
  - [Light Control](#light-control)
  - [Scene Selection](#scene-selection)
  - [Segment Control](#segment-control)
  - [Music Mode](#music-mode)
- [Services](#services)
- [Troubleshooting](#troubleshooting)
- [Support](#support)

---

## Installation

### HACS Installation (Recommended)

This is a custom HACS repository. Follow these steps to install:

1. **Install HACS** if you haven't already - [HACS Installation Guide](https://hacs.xyz/docs/setup/download)

2. **Add Custom Repository**
   - Open HACS in your Home Assistant sidebar
   - Click on **Integrations**
   - Click the **three dots menu** (⋮) in the top right corner
   - Select **Custom repositories**
   - In the dialog that appears:
     - **Repository:** `https://github.com/lasswellt/hacs-govee`
     - **Category:** `Integration`
   - Click **Add**

3. **Download the Integration**
   - The Govee integration should now appear in HACS
   - Click on **Govee**
   - Click **Download**
   - Select the latest version and click **Download**

4. **Restart Home Assistant**
   - Go to **Settings** > **System** > **Restart**

5. **Add the Integration**
   - Go to **Settings** > **Devices & Services**
   - Click **+ Add Integration**
   - Search for **"Govee"**
   - Follow the setup wizard

### Manual Installation

1. Download the latest release from [GitHub Releases](https://github.com/lasswellt/hacs-govee/releases)
2. Extract and copy the `custom_components/govee` folder to your Home Assistant `custom_components` directory
3. Restart Home Assistant
4. Add the integration via **Settings** > **Devices & Services**

---

## Getting Your API Key

An API key is required to use this integration. Here's how to get one:

1. **Open the Govee Home App** on your mobile device

2. **Navigate to Settings**
   - Tap the **profile icon** (far right at bottom)
   - Tap the **gear icon** (Settings) in the top right

3. **Request API Key**
   - Tap **About Us**
   - Tap **Apply for API Key**
   - Fill out the form with your information

4. **Check Your Email**
   - Your API key will be sent to the email associated with your Govee account
   - This usually takes a few minutes, but can take up to 24 hours

> **Note:** Keep your API key secure. Do not share it publicly.

---

## Configuration

### Initial Setup

When adding the integration, you'll be prompted for:

| Field | Description |
|-------|-------------|
| **API Key** | Your Govee API key from the app |
| **Poll Interval** | How often to check device state (default: 30 seconds) |

### Configuration Options

After setup, you can configure additional options via **Settings** > **Devices & Services** > **Govee** > **Configure**:

| Option | Description | Default |
|--------|-------------|---------|
| **API Key** | Update your API key (requires restart) | - |
| **Poll Interval** | State polling frequency in seconds (requires restart) | 30 |
| **Use Assumed State** | Shows two buttons (on/off) instead of toggle | True |
| **Offline is Off** | Show offline devices as "off" instead of "unavailable" | False |
| **Disable Attribute Updates** | Advanced: disable specific state updates | Empty |

---

## Supported Devices

This integration supports Govee devices that are compatible with the Govee API v2.0:

| Device Type | Platforms Created |
|-------------|-------------------|
| LED Lights & Strips | Light, Select (scenes) |
| Smart Plugs/Sockets | Switch |
| RGBIC Strips | Light, Select (scenes) + Segment services |

> **Note:** Not all Govee devices support the cloud API. Bluetooth-only devices are not supported.

---

## Features & Usage

### Light Control

Light entities support standard Home Assistant light controls:

- **On/Off** - Turn lights on or off
- **Brightness** - Adjust brightness (0-100%)
- **Color** - Set RGB color
- **Color Temperature** - Set warm/cool white temperature
- **Effects** - Select from available scenes (if supported)

### Scene Selection

For devices with many scenes, a **Select** entity is created:

- **Scene** - Dynamic scenes from Govee cloud
- **DIY Scene** - Your custom DIY scenes (disabled by default)

To enable DIY scenes:
1. Go to **Settings** > **Devices & Services** > **Govee**
2. Click on your device
3. Find the "DIY Scene" entity and enable it

### Segment Control

RGBIC strips support individual segment control via services:

```yaml
# Set segments 0-4 to red
service: govee.set_segment_color
target:
  entity_id: light.govee_led_strip
data:
  segments: [0, 1, 2, 3, 4]
  rgb_color: [255, 0, 0]
```

### Music Mode

Activate music-reactive modes on supported devices:

```yaml
service: govee.set_music_mode
target:
  entity_id: light.govee_led_strip
data:
  mode: "Energic"
  sensitivity: 80
  auto_color: true
```

---

## Services

### govee.set_segment_color

Set color for specific segments of an RGBIC light strip.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `segments` | list | Yes | List of segment indices (0-based) |
| `rgb_color` | list | Yes | RGB color as [R, G, B] (0-255) |

### govee.set_segment_brightness

Set brightness for specific segments.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `segments` | list | Yes | List of segment indices (0-based) |
| `brightness` | int | Yes | Brightness percentage (0-100) |

### govee.set_music_mode

Activate music reactive mode.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `mode` | string | Yes | Music mode name |
| `sensitivity` | int | No | Microphone sensitivity (0-100, default: 50) |
| `auto_color` | bool | No | Enable automatic color changes (default: true) |
| `rgb_color` | list | No | Fixed color when auto_color is false |

### govee.refresh_scenes

Refresh the scene list from Govee cloud (for Select entities).

---

## Troubleshooting

### Common Issues

**"Cannot connect" error during setup**
- Verify your API key is correct
- Check your internet connection
- Ensure your Govee account has API access enabled

**Devices not appearing**
- Only cloud-enabled devices appear in the API
- Bluetooth-only devices are not supported
- Try refreshing the integration

**State not updating**
- Increase the poll interval if you have many devices
- Some devices don't support state queries (assumed state is used)

**Rate limit errors**
- The API has limits: 100 requests/minute, 10,000/day
- Increase your poll interval
- Reduce the number of automations calling Govee services

### Enable Debug Logging

Add to your `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.govee: debug
```

Then restart Home Assistant and check **Settings** > **System** > **Logs**.

### Disable Attribute Updates (Advanced)

If API returns incorrect state for specific attributes, you can disable them:

1. Go to **Govee** integration options
2. In "Disable Attribute Updates", enter: `API:power_state`
3. Format: `SOURCE:attribute` where SOURCE is `API` or `HISTORY`
4. Multiple: `API:power_state;HISTORY:online`

> **Warning:** This is a workaround. Report the underlying issue.

---

## Support

- **Issue Tracker:** [GitHub Issues](https://github.com/lasswellt/hacs-govee/issues)
- **Source Code:** [GitHub Repository](https://github.com/lasswellt/hacs-govee)

### Reporting Bugs

When reporting issues, please include:
1. Home Assistant version
2. Integration version
3. Debug logs (with personal data removed)
4. Device model (SKU)
5. Steps to reproduce

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Acknowledgments

- Original integration by [@LaggAt](https://github.com/LaggAt)
- Govee API v2.0 migration by [@lasswellt](https://github.com/lasswellt)
- All contributors and community members
