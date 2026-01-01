from __future__ import annotations

BASE_URL = "https://openapi.api.govee.com/router/api/v1"

ENDPOINT_DEVICES = "user/devices"
ENDPOINT_DEVICE_STATE = "device/state"
ENDPOINT_DEVICE_CONTROL = "device/control"
ENDPOINT_DYNAMIC_SCENES = "device/scenes"
ENDPOINT_DIY_SCENES = "device/diy-scenes"
ENDPOINT_SNAPSHOTS = "device/snapshots"

RATE_LIMIT_PER_MINUTE = 100
RATE_LIMIT_PER_DAY = 10000

SECONDS_PER_MINUTE = 60
SECONDS_PER_DAY = 86400
MAX_RATE_LIMIT_WAIT = 3600

HEADER_RATE_LIMIT_REMAINING = "X-RateLimit-Remaining"
HEADER_RATE_LIMIT_RESET = "X-RateLimit-Reset"
HEADER_API_RATE_LIMIT_REMAINING = "API-RateLimit-Remaining"
HEADER_API_RATE_LIMIT_RESET = "API-RateLimit-Reset"

CAPABILITY_ON_OFF = "devices.capabilities.on_off"
CAPABILITY_RANGE = "devices.capabilities.range"
CAPABILITY_COLOR_SETTING = "devices.capabilities.color_setting"
CAPABILITY_SEGMENT_COLOR = "devices.capabilities.segment_color_setting"
CAPABILITY_DYNAMIC_SCENE = "devices.capabilities.dynamic_scene"
CAPABILITY_DIY_COLOR = "devices.capabilities.diy_color_setting"
CAPABILITY_MUSIC_SETTING = "devices.capabilities.music_setting"
CAPABILITY_TOGGLE = "devices.capabilities.toggle"
CAPABILITY_MODE = "devices.capabilities.mode"
CAPABILITY_WORK_MODE = "devices.capabilities.work_mode"

INSTANCE_POWER_SWITCH = "powerSwitch"
INSTANCE_BRIGHTNESS = "brightness"
INSTANCE_COLOR_RGB = "colorRgb"
INSTANCE_COLOR_TEMP = "colorTemperatureK"
INSTANCE_SEGMENTED_BRIGHTNESS = "segmentedBrightness"
INSTANCE_SEGMENTED_COLOR = "segmentedColorRgb"
INSTANCE_LIGHT_SCENE = "lightScene"
INSTANCE_DIY_SCENE = "diyScene"
INSTANCE_SNAPSHOT = "snapshot"
INSTANCE_MUSIC_MODE = "musicMode"
INSTANCE_GRADIENT_TOGGLE = "gradientToggle"
INSTANCE_NIGHTLIGHT_TOGGLE = "nightlightToggle"
INSTANCE_OSCILLATION_TOGGLE = "oscillationToggle"
INSTANCE_THERMOSTAT_TOGGLE = "thermostatToggle"
INSTANCE_WARM_MIST_TOGGLE = "warmMistToggle"
INSTANCE_AIR_DEFLECTOR_TOGGLE = "airDeflectorToggle"

DEVICE_TYPE_LIGHT = "devices.types.light"
DEVICE_TYPE_SOCKET = "devices.types.socket"
DEVICE_TYPE_AIR_PURIFIER = "devices.types.air_purifier"
DEVICE_TYPE_HUMIDIFIER = "devices.types.humidifier"
DEVICE_TYPE_DEHUMIDIFIER = "devices.types.dehumidifier"
DEVICE_TYPE_HEATER = "devices.types.heater"
DEVICE_TYPE_THERMOMETER = "devices.types.thermometer"
DEVICE_TYPE_SENSOR = "devices.types.sensor"
DEVICE_TYPE_AROMA_DIFFUSER = "devices.types.aroma_diffuser"

COLOR_TEMP_MIN = 2000
COLOR_TEMP_MAX = 9000

BRIGHTNESS_MIN = 0
BRIGHTNESS_MAX = 100

RGB_MAX = 16777215

REQUEST_TIMEOUT = 10
