# Govee Integration for Home Assistant

[![HACS][hacs-badge]][hacs-url]
[![GitHub Release][release-badge]][release-url]
[![GitHub License][license-badge]][license-url]
[![Tests][tests-badge]][tests-url]
[![codecov][codecov-badge]][codecov-url]

*Control your Govee lights, LED strips, and smart devices through Home Assistant using the official Govee Cloud API v2.0.*

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.][hacs-install-badge]][hacs-install-url]

---

## What is this?

This custom integration brings your Govee devices into Home Assistant, giving you local dashboard control, automation capabilities, and voice assistant integration for your Govee ecosystem.

**Highlights:**

- Full control of lights, LED strips, and smart plugs
- Scene and DIY scene selection via dropdown entities
- Individual segment control for RGBIC strips
- Rate limit protection with proactive warnings
- Built-in diagnostics for troubleshooting
- 99.78% test coverage with strict type safety

---

## Table of Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Getting Your API Key](#getting-your-api-key)
- [Configuration](#configuration)
- [Supported Devices](#supported-devices)
- [Entities & Platforms](#entities--platforms)
- [Services](#services)
- [Automation Examples](#automation-examples)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

---

## Requirements

| Requirement | Version |
|-------------|---------|
| Home Assistant | 2024.1.0+ |
| HACS | Any recent version |
| Govee API Key | [Request from Govee App](#getting-your-api-key) |

---

## Installation

### HACS (Recommended)

1. Click the button above, or manually add this repository to HACS:
   - **HACS** > **Integrations** > **⋮** > **Custom repositories**
   - Repository: `https://github.com/lasswellt/hacs-govee`
   - Category: `Integration`

2. Search for **Govee** in HACS and click **Download**

3. Restart Home Assistant

4. Add the integration:
   - **Settings** > **Devices & Services** > **+ Add Integration** > **Govee**

### Manual Installation

1. Download the [latest release][release-url]
2. Extract `custom_components/govee` to your `config/custom_components/` directory
3. Restart Home Assistant
4. Add via **Settings** > **Devices & Services**

---

## Getting Your API Key

1. Open the **Govee Home** app on your mobile device
2. Go to **Profile** (bottom right) > **Settings** (gear icon)
3. Tap **About Us** > **Apply for API Key**
4. Fill out the form and check your email (usually arrives within minutes)

> **Note:** Keep your API key secure. Never share it publicly.

---

## Configuration

### Initial Setup

| Field | Description | Default |
|-------|-------------|---------|
| API Key | Your Govee API key | Required |
| Poll Interval | State refresh frequency (seconds) | 30 |

### Options (Configure after setup)

| Option | Description | Default |
|--------|-------------|---------|
| Use Assumed State | Show On/Off buttons instead of toggle | Yes |
| Offline is Off | Show offline devices as "off" | No |
| Enable Group Devices | Allow Govee app groups (experimental) | No |

Access options via: **Settings** > **Devices & Services** > **Govee** > **Configure**

---

## Supported Devices

| Device Type | Platforms | Features |
|-------------|-----------|----------|
| LED Lights & Strips | Light, Select | On/off, brightness, RGB, color temp, scenes |
| RGBIC Strips | Light, Select | Above + segment control |
| Smart Plugs | Switch | On/off control |

> **Note:** Only cloud-enabled devices are supported. Bluetooth-only devices cannot be controlled via the API.

---

## Entities & Platforms

### Light Entities

Control brightness, color, and color temperature. Effects dropdown shows available scenes.

```yaml
service: light.turn_on
target:
  entity_id: light.bedroom_led_strip
data:
  brightness_pct: 80
  rgb_color: [255, 147, 41]
```

### Select Entities

Dedicated dropdowns for scene and DIY scene selection:

- `select.<device>_scene` - Dynamic scenes from Govee cloud
- `select.<device>_diy_scene` - Your custom DIY scenes (disabled by default)

### Switch Entities

- Smart plug on/off control
- Night light mode toggle (supported devices)

### Sensor Entities (Diagnostic)

Rate limit monitoring (disabled by default):

- `sensor.govee_rate_limit_remaining_minute`
- `sensor.govee_rate_limit_remaining_day`

---

## Services

### `govee.set_segment_color`

Set color for specific segments on RGBIC strips.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| segments | list | Yes | Segment indices (0-based) |
| rgb_color | list | Yes | RGB values [R, G, B] |

```yaml
service: govee.set_segment_color
target:
  entity_id: light.rgbic_strip
data:
  segments: [0, 1, 2, 3, 4]
  rgb_color: [255, 0, 0]
```

### `govee.set_segment_brightness`

Set brightness for specific segments.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| segments | list | Yes | Segment indices (0-based) |
| brightness | int | Yes | Brightness 0-100 |

### `govee.set_music_mode`

Activate music-reactive mode.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| mode | string | Yes | Music mode name |
| sensitivity | int | No | Mic sensitivity 0-100 |
| auto_color | bool | No | Auto color changes |

### `govee.refresh_scenes`

Manually refresh scene list from Govee cloud.

---

## Automation Examples

<details>
<summary><b>Sunset Ambient Lighting</b></summary>

```yaml
automation:
  - alias: "Govee Sunset Ambiance"
    trigger:
      - platform: sun
        event: sunset
        offset: "-00:30:00"
    action:
      - service: light.turn_on
        target:
          entity_id: light.living_room_strip
        data:
          brightness_pct: 70
          rgb_color: [255, 147, 41]
```

</details>

<details>
<summary><b>Movie Mode with Media Player</b></summary>

```yaml
automation:
  - alias: "Movie Mode Lighting"
    trigger:
      - platform: state
        entity_id: media_player.tv
        to: "playing"
    action:
      - service: light.turn_on
        target:
          entity_id: light.tv_backlight
        data:
          brightness_pct: 30
          effect: "Movie"
```

</details>

<details>
<summary><b>Rainbow Segments</b></summary>

```yaml
automation:
  - alias: "RGBIC Rainbow"
    trigger:
      - platform: event
        event_type: activate_rainbow
    action:
      - service: govee.set_segment_color
        target:
          entity_id: light.rgbic_strip
        data:
          segments: [0, 1, 2]
          rgb_color: [255, 0, 0]
      - service: govee.set_segment_color
        target:
          entity_id: light.rgbic_strip
        data:
          segments: [3, 4, 5]
          rgb_color: [0, 255, 0]
      - service: govee.set_segment_color
        target:
          entity_id: light.rgbic_strip
        data:
          segments: [6, 7, 8]
          rgb_color: [0, 0, 255]
```

</details>

---

## Troubleshooting

### Common Issues

| Problem | Solution |
|---------|----------|
| Devices not appearing | Only cloud-enabled devices are supported. Check Govee app settings. |
| State not updating | Increase poll interval. Some devices don't support state queries. |
| Rate limit errors | Reduce poll interval or automation frequency. Limits: 100/min, 10,000/day. |
| "Cannot connect" | Verify API key is correct. Check internet connection. |

### Enable Debug Logging

Add to `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.govee: debug
```

### Diagnostics

Download diagnostics for bug reports:

**Settings** > **Devices & Services** > **Govee** > **⋮** > **Download diagnostics**

### Repair Issues

The integration creates repair notifications for:

- Rate limit warnings (minute and daily)
- Group device limitations

View in: **Settings** > **System** > **Repairs**

---

## Group Devices (Experimental)

Govee app groups have limited API support:

| Feature | Status |
|---------|--------|
| Discovery | Works |
| Control (on/off) | Works |
| State queries | Not supported by API |

Groups use optimistic state tracking. Enable via integration options.

---

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Run tests: `pytest`
4. Submit a pull request

### Development

```bash
# Install dependencies
pip install -r requirements_test.txt

# Run tests
pytest

# Type checking
mypy --strict custom_components/govee

# Linting
flake8 custom_components/govee
```

---

## Support

- **Issues:** [GitHub Issues][issues-url]
- **Discussions:** [GitHub Discussions][discussions-url]

When reporting issues, include:
- Home Assistant version
- Integration version
- Debug logs (with personal data removed)
- Device SKU

---

## Credits

- Original integration by [@LaggAt](https://github.com/LaggAt)
- API v2.0 migration by [@lasswellt](https://github.com/lasswellt)

---

## License

MIT License - see [LICENSE](LICENSE) for details.

<!-- Badge References -->
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/lasswellt/hacs-govee?style=flat-square
[release-url]: https://github.com/lasswellt/hacs-govee/releases
[license-badge]: https://img.shields.io/github/license/lasswellt/hacs-govee?style=flat-square
[license-url]: https://github.com/lasswellt/hacs-govee/blob/master/LICENSE
[tests-badge]: https://img.shields.io/github/actions/workflow/status/lasswellt/hacs-govee/tox.yaml?style=flat-square&label=tests
[tests-url]: https://github.com/lasswellt/hacs-govee/actions
[codecov-badge]: https://img.shields.io/codecov/c/github/lasswellt/hacs-govee?style=flat-square
[codecov-url]: https://codecov.io/gh/lasswellt/hacs-govee
[hacs-install-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[hacs-install-url]: https://my.home-assistant.io/redirect/hacs_repository/?owner=lasswellt&repository=hacs-govee&category=integration
[issues-url]: https://github.com/lasswellt/hacs-govee/issues
[discussions-url]: https://github.com/lasswellt/hacs-govee/discussions
