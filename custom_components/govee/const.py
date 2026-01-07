from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "govee"

CONF_ENABLE_GROUP_DEVICES = "enable_group_devices"
CONF_OFFLINE_IS_OFF = "offline_is_off"
CONF_POLL_INTERVAL = "delay"
CONF_USE_ASSUMED_STATE = "use_assumed_state"
CONF_INTER_COMMAND_DELAY = "inter_command_delay"

HA_BRIGHTNESS_MAX = 255
API_BRIGHTNESS_MAX = 100

COLOR_TEMP_KELVIN_MIN = 2000
COLOR_TEMP_KELVIN_MAX = 9000

DEFAULT_POLL_INTERVAL = 60  # Increased from 30 to reduce API usage
DEFAULT_INTER_COMMAND_DELAY = 500  # milliseconds between sequential commands

# Adaptive polling thresholds based on daily quota (10,000/day)
# When remaining quota falls below these, polling interval is increased
QUOTA_CRITICAL = 100     # Emergency: 10 min polling
QUOTA_DANGER = 500       # Danger: 5 min polling
QUOTA_WARNING = 2000     # Warning: 2 min polling
QUOTA_OK = 5000          # OK: User's configured interval

# Polling interval limits for adaptive polling (in seconds)
POLL_INTERVAL_MIN = 30
POLL_INTERVAL_CRITICAL = 600   # 10 minutes
POLL_INTERVAL_DANGER = 300     # 5 minutes
POLL_INTERVAL_WARNING = 120    # 2 minutes

# MQTT Configuration for real-time device events
MQTT_BROKER = "mqtt.openapi.govee.com"
MQTT_PORT = 8883
MQTT_KEEPALIVE = 120  # seconds
MQTT_RECONNECT_BASE = 5  # Base reconnect interval in seconds
MQTT_RECONNECT_MAX = 300  # Max reconnect interval (5 minutes)

DEVICE_TYPE_LIGHT = "devices.types.light"
DEVICE_TYPE_SOCKET = "devices.types.socket"
DEVICE_TYPE_AIR_PURIFIER = "devices.types.air_purifier"
DEVICE_TYPE_HUMIDIFIER = "devices.types.humidifier"
DEVICE_TYPE_DEHUMIDIFIER = "devices.types.dehumidifier"
DEVICE_TYPE_HEATER = "devices.types.heater"
DEVICE_TYPE_THERMOMETER = "devices.types.thermometer"
DEVICE_TYPE_SENSOR = "devices.types.sensor"
DEVICE_TYPE_AROMA_DIFFUSER = "devices.types.aroma_diffuser"

UNSUPPORTED_DEVICE_SKUS = {"SameModeGroup", "BaseGroup", "DreamViewScenic"}

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

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_ENTRY_VERSION = 2
