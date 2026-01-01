[![hacs][hacsbadge]][hacs]
![Project Maintenance][maintenance-shield]

_Custom integration to control [Govee](https://www.govee.com/) devices via the Govee Cloud API v2.0._

**This component will set up the following platforms.**

Platform | Description
-- | --
`light` | Control lights and LED strips with brightness, color, color temperature, and scenes
`select` | Scene selection (Dynamic, DIY, and Snapshot scenes)
`switch` | Toggle controls for smart plugs and device features
`sensor` | Rate limit monitoring (diagnostic)
`button` | Manual scene refresh and device identification

{% if not installed %}
## Installation

1. In HACS/Integrations, search for 'Govee' and click install.
2. Restart Home Assistant.
3. In the HA UI go to "Settings" → "Devices & Services" → "+ Add Integration" and search for "Govee".

{% endif %}

## Configuration

You'll need an API key from Govee:

1. Open the **Govee Home** app on your phone
2. Go to **Profile** → **Settings** → **About Us** → **Apply for API Key**
3. Check your email for the API key (usually arrives within 5-10 minutes)

Enter your API key during integration setup. You can optionally adjust the poll interval.

## Documentation

* [Full Documentation on GitHub](https://github.com/lasswellt/hacs-govee/blob/master/README.md)
* [Support thread on Home Assistant Community](https://community.home-assistant.io/t/govee-led-strips-integration/228516/1)

***

[hacs-govee]: https://github.com/lasswellt/hacs-govee
[hacs]: https://github.com/hacs/integration
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-lasswellt-blue.svg?style=for-the-badge
