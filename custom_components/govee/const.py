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
CONF_EXPOSE_TRANSPORT_ENTITIES: Final = "expose_transport_entities"
CONF_ENABLE_MQTT_CONTROL: Final = "enable_mqtt_control"

# Some Govee thermometer/hygrometer SKUs report temperatures in Fahrenheit via
# the Cloud API without unit metadata, while the native sensor unit is tagged
# Celsius — so a 101°F reading surfaces as 213.5°F in HA (issues #72, #78, #96).
# This option controls normalization:
#   "auto"       -> convert only for SKUs in FAHRENHEIT_REPORTING_SKUS (default)
#   "fahrenheit" -> always convert (treat API value as °F)
#   "celsius"    -> never convert (trust API value as °C)
CONF_API_TEMPERATURE_UNIT: Final = "api_temperature_unit"

# SKUs observed reporting sensorTemperature in Fahrenheit regardless of account
# locale. Used by the "auto" mode to convert out-of-the-box. Compared
# case-insensitively against GoveeDevice.sku.
FAHRENHEIT_REPORTING_SKUS: Final = frozenset(
    {"H5179", "H5109", "H5110", "HS5108", "HS5106"}
)


def resolve_fahrenheit_conversion(sku: str, api_unit: str) -> bool:
    """Whether a Developer-API ``sensor_temperature`` should be treated as °F.

    Shared by the sensor entity (which converts °F→°C for display) and the
    coordinator's BFF reading path (which must store the value in the SAME
    unit the entity expects, so a true-°C BFF reading round-trips correctly
    instead of being double-converted) — issues #96, #83.
    """
    if api_unit == "auto":
        return sku.upper() in FAHRENHEIT_REPORTING_SKUS
    return api_unit == "fahrenheit"


# Defaults
DEFAULT_POLL_INTERVAL: Final = 60  # seconds
DEFAULT_ENABLE_GROUPS: Final = False
DEFAULT_ENABLE_SCENES: Final = True
DEFAULT_ENABLE_DIY_SCENES: Final = True
DEFAULT_ENABLE_SEGMENTS: Final = True
DEFAULT_SEGMENT_MODE: Final = "individual"  # "disabled", "grouped", or "individual"
DEFAULT_EXPOSE_TRANSPORT_ENTITIES: Final = False
DEFAULT_ENABLE_MQTT_CONTROL: Final = False
DEFAULT_API_TEMPERATURE_UNIT: Final = "auto"

# Optimistic state handling
# Grace window (seconds) during which API polls do NOT overwrite optimistic
# power/brightness. Masks out-of-range BLE devices and slow cloud responses
# without producing the "UI flipflop" that longer windows would. MQTT push
# confirmations clear the window early.
OPTIMISTIC_GRACE_CAP_SECONDS: Final = 15

# BLE constants
# Govee AWS/BLE advert manufacturer ID. Verified against
# Bluetooth-Devices/govee-ble (used by H5127 and related). Additional IDs
# remain unverified and are omitted until observed in the wild.
GOVEE_BLE_MANUFACTURER_IDS: Final = (0x8803,)  # 34819

# Segment mode options
SEGMENT_MODE_DISABLED: Final = "disabled"
SEGMENT_MODE_GROUPED: Final = "grouped"
SEGMENT_MODE_INDIVIDUAL: Final = "individual"

# Config entry schema version. Bumped to 2 in sprint-4 when IoT credentials
# moved from hass.data[DOMAIN] to entry.data (see async_migrate_entry).
CONFIG_VERSION: Final = 2

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
SUFFIX_PRESET_SCENE_SELECT: Final = "_preset_scene_select"
