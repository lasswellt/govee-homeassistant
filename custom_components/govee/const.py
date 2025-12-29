"""Constants for the Govee integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "govee"

# Config entry options
CONF_DISABLE_ATTRIBUTE_UPDATES = "disable_attribute_updates"
CONF_ENABLE_GROUP_DEVICES = "enable_group_devices"
CONF_OFFLINE_IS_OFF = "offline_is_off"
CONF_USE_ASSUMED_STATE = "use_assumed_state"

# Color temperature range (Kelvin)
COLOR_TEMP_KELVIN_MIN = 2000
COLOR_TEMP_KELVIN_MAX = 9000

# Default poll interval (seconds)
DEFAULT_POLL_INTERVAL = 30

# Device types from API
DEVICE_TYPE_LIGHT = "devices.types.light"
DEVICE_TYPE_SOCKET = "devices.types.socket"
DEVICE_TYPE_AIR_PURIFIER = "devices.types.air_purifier"
DEVICE_TYPE_HUMIDIFIER = "devices.types.humidifier"
DEVICE_TYPE_DEHUMIDIFIER = "devices.types.dehumidifier"
DEVICE_TYPE_HEATER = "devices.types.heater"
DEVICE_TYPE_THERMOMETER = "devices.types.thermometer"
DEVICE_TYPE_SENSOR = "devices.types.sensor"
DEVICE_TYPE_AROMA_DIFFUSER = "devices.types.aroma_diffuser"

# Group device SKUs (Govee Home app groups/shortcuts)
# API Behavior (tested 2025-12-29):
#   ✅ Discovery: Groups appear in device list
#   ✅ Control: Commands succeed (on/off works)
#   ❌ State: Queries return "devices not exist" error
# Result: Groups can be controlled but will show as "unavailable" in UI
UNSUPPORTED_DEVICE_SKUS = {
    "SameModeGroup",  # Same Model device group
    "BaseGroup",  # Base device group
    "DreamViewScenic",  # DreamView scene shortcut
}

# Platform mapping by device type
DEVICE_TYPE_PLATFORMS: dict[str, list[Platform]] = {
    DEVICE_TYPE_LIGHT: [Platform.LIGHT, Platform.SELECT],
    DEVICE_TYPE_SOCKET: [Platform.SWITCH],
    DEVICE_TYPE_AIR_PURIFIER: [Platform.FAN, Platform.SENSOR],
    DEVICE_TYPE_HUMIDIFIER: [Platform.HUMIDIFIER, Platform.SENSOR],
    DEVICE_TYPE_DEHUMIDIFIER: [Platform.HUMIDIFIER, Platform.SENSOR],
    DEVICE_TYPE_HEATER: [Platform.CLIMATE],
    DEVICE_TYPE_THERMOMETER: [Platform.SENSOR],
    DEVICE_TYPE_SENSOR: [Platform.SENSOR, Platform.BINARY_SENSOR],
    DEVICE_TYPE_AROMA_DIFFUSER: [Platform.FAN, Platform.SELECT],
}

# All supported platforms
PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.SELECT,
    Platform.SWITCH,
]

# Config entry version for migrations
CONFIG_ENTRY_VERSION = 2
