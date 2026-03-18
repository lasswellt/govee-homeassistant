"""Constants for Govee integration."""

from typing import Final

DOMAIN: Final = "govee"

# Config entry keys
CONF_API_KEY: Final = "api_key"
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"

# Options keys
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_ENABLE_GROUPS: Final = "enable_groups"
CONF_ENABLE_SCENES: Final = "enable_scenes"
CONF_ENABLE_DIY_SCENES: Final = "enable_diy_scenes"
CONF_ENABLE_SEGMENTS: Final = "enable_segments"
CONF_SEGMENT_MODE: Final = "segment_mode"

# Defaults
DEFAULT_POLL_INTERVAL: Final = 60  # seconds
DEFAULT_ENABLE_GROUPS: Final = False
DEFAULT_ENABLE_SCENES: Final = True
DEFAULT_ENABLE_DIY_SCENES: Final = True
DEFAULT_ENABLE_SEGMENTS: Final = True
DEFAULT_SEGMENT_MODE: Final = "individual"  # "disabled", "grouped", or "individual"

# Segment mode options
SEGMENT_MODE_DISABLED: Final = "disabled"
SEGMENT_MODE_GROUPED: Final = "grouped"
SEGMENT_MODE_INDIVIDUAL: Final = "individual"

# Config entry version (fresh start)
CONFIG_VERSION: Final = 1

# Keys for storing cached data in hass.data[DOMAIN]
KEY_IOT_CREDENTIALS: Final = "iot_credentials"
KEY_IOT_LOGIN_FAILED: Final = "iot_login_failed"

# Entity unique_id suffixes
# Used in entity creation and orphan cleanup to keep patterns consistent
SUFFIX_SEGMENT: Final = "_segment_"
SUFFIX_GROUPED_SEGMENT: Final = "_grouped_segments"
SUFFIX_SCENE_SELECT: Final = "_scene_select"
SUFFIX_DIY_SCENE_SELECT: Final = "_diy_scene_select"
SUFFIX_DIY_STYLE_SELECT: Final = "_diy_style_select"
SUFFIX_HDMI_SOURCE_SELECT: Final = "_hdmi_source_select"
SUFFIX_MUSIC_MODE_SELECT: Final = "_music_mode_select"
SUFFIX_REFRESH_SCENES: Final = "_refresh_scenes"
SUFFIX_NIGHT_LIGHT: Final = "_night_light"
SUFFIX_MUSIC_MODE: Final = "_music_mode"
SUFFIX_MUSIC_SENSITIVITY: Final = "_music_sensitivity"
SUFFIX_DREAMVIEW: Final = "_dreamview"
SUFFIX_HEATER_TEMPERATURE: Final = "_heater_temperature"
SUFFIX_HEATER_FAN_SPEED: Final = "_heater_fan_speed"
SUFFIX_HEATER_AUTO_STOP: Final = "_heater_auto_stop"
SUFFIX_PURIFIER_MODE_SELECT: Final = "_purifier_mode_select"
