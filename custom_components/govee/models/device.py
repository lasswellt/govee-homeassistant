"""Device model representing a Govee device and its capabilities.

Frozen dataclass for immutability - device properties don't change at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

_LOGGER = logging.getLogger(__name__)

# Leak sensor SKUs
LEAK_SENSOR_SKUS = frozenset({"H5058", "H5054", "H5055"})
LEAK_HUB_SKUS = frozenset({"H5043", "H5044"})

# Capability type constants (from Govee API v2.0)
CAPABILITY_ON_OFF = "devices.capabilities.on_off"
CAPABILITY_RANGE = "devices.capabilities.range"
CAPABILITY_COLOR_SETTING = "devices.capabilities.color_setting"
CAPABILITY_SEGMENT_COLOR = "devices.capabilities.segment_color_setting"
CAPABILITY_DYNAMIC_SCENE = "devices.capabilities.dynamic_scene"
CAPABILITY_MUSIC_MODE = "devices.capabilities.music_setting"
CAPABILITY_TOGGLE = "devices.capabilities.toggle"
CAPABILITY_WORK_MODE = "devices.capabilities.work_mode"
CAPABILITY_PROPERTY = "devices.capabilities.property"
CAPABILITY_MODE = "devices.capabilities.mode"
CAPABILITY_TEMPERATURE_SETTING = "devices.capabilities.temperature_setting"
CAPABILITY_EVENT = "devices.capabilities.event"

# Device type constants
DEVICE_TYPE_LIGHT = "devices.types.light"
DEVICE_TYPE_PLUG = "devices.types.socket"
DEVICE_TYPE_HEATER = "devices.types.heater"
DEVICE_TYPE_HUMIDIFIER = "devices.types.humidifier"
DEVICE_TYPE_DEHUMIDIFIER = "devices.types.dehumidifier"
DEVICE_TYPE_FAN = "devices.types.fan"
DEVICE_TYPE_PURIFIER = "devices.types.air_purifier"
DEVICE_TYPE_KETTLE = "devices.types.kettle"

# Instance constants
INSTANCE_POWER = "powerSwitch"
INSTANCE_BRIGHTNESS = "brightness"
INSTANCE_COLOR_RGB = "colorRgb"
INSTANCE_COLOR_TEMP = "colorTemperatureK"
INSTANCE_SEGMENT_COLOR = "segmentedColorRgb"
INSTANCE_SCENE = "lightScene"
INSTANCE_DIY = "diyScene"
INSTANCE_NIGHT_LIGHT = "nightlightToggle"
INSTANCE_GRADUAL_ON = "gradientToggle"
INSTANCE_TIMER = "timer"
INSTANCE_OSCILLATION = "oscillationToggle"
INSTANCE_WORK_MODE = "workMode"
INSTANCE_HDMI_SOURCE = "hdmiSource"
INSTANCE_MUSIC_MODE = "musicMode"
INSTANCE_DREAMVIEW = "dreamViewToggle"
INSTANCE_TEMPERATURE = "temperature"
INSTANCE_TARGET_TEMPERATURE = "targetTemperature"
INSTANCE_FAN_SPEED = "fanSpeed"
# Ceiling-fan-with-light combo instances (e.g. H1310, reported as
# devices.types.light with an integrated fan). Distinct from the standalone
# fan shape (workMode / fanSpeed / oscillationToggle) — issue #74.
INSTANCE_FAN_TOGGLE = "fanToggle"
INSTANCE_FAN_SPEED_MODE = "fanSpeedMode"
INSTANCE_REVERSE_AIRFLOW = "reverseAirflowToggle"
INSTANCE_PURIFIER_MODE = "purifierMode"
INSTANCE_THERMOSTAT_TOGGLE = "thermostatToggle"
INSTANCE_HUMIDITY = "humidity"
INSTANCE_WATER_FULL_EVENT = "waterFullEvent"

# Read-only sensor property instances (devices.capabilities.property).
# These appear on stand-alone sensors like H5179 (WiFi Thermometer) and
# H5109 (Smart Temperature Sensor) — issue #62.
INSTANCE_SENSOR_TEMPERATURE = "sensorTemperature"
INSTANCE_SENSOR_HUMIDITY = "sensorHumidity"

# Device type for stand-alone temperature/humidity sensors.
DEVICE_TYPE_THERMOMETER = "devices.types.thermometer"


@dataclass(frozen=True)
class ColorTempRange:
    """Color temperature range in Kelvin."""

    min_kelvin: int
    max_kelvin: int

    @classmethod
    def from_capability(cls, capability: dict[str, Any]) -> ColorTempRange | None:
        """Parse from capability parameters."""
        params = capability.get("parameters", {})
        range_data = params.get("range", {})
        min_k = range_data.get("min")
        max_k = range_data.get("max")
        if min_k is not None and max_k is not None:
            return cls(min_kelvin=int(min_k), max_kelvin=int(max_k))
        return None


@dataclass(frozen=True)
class SegmentCapability:
    """Segment control capability for RGBIC devices."""

    segment_count: int

    @classmethod
    def from_capability(cls, capability: dict[str, Any]) -> SegmentCapability | None:
        """Parse from capability parameters.

        The segment count can be found in different places:
        1. Direct 'segmentCount' parameter
        2. In fields[].elementRange.max + 1 (0-based index)
        3. In fields[].size.max (max array size)
        """
        params = capability.get("parameters", {})

        # Try direct segmentCount parameter
        count = params.get("segmentCount", 0)

        if not count:
            # Try to get from fields array structure
            fields = params.get("fields", [])
            for f in fields:
                if f.get("fieldName") == "segment":
                    # Check elementRange (0-based max index)
                    element_range = f.get("elementRange", {})
                    if "max" in element_range:
                        count = element_range["max"] + 1  # Convert to count
                        break
                    # Fallback to size.max
                    size = f.get("size", {})
                    if "max" in size:
                        count = size["max"]
                        break

        return cls(segment_count=count) if count else None


@dataclass(frozen=True)
class GoveeCapability:
    """Represents a device capability from Govee API."""

    type: str
    instance: str
    parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def is_power(self) -> bool:
        """Check if this is a power on/off capability."""
        return self.type == CAPABILITY_ON_OFF and self.instance == INSTANCE_POWER

    @property
    def is_brightness(self) -> bool:
        """Check if this is a brightness capability."""
        return self.type == CAPABILITY_RANGE and self.instance == INSTANCE_BRIGHTNESS

    @property
    def is_color_rgb(self) -> bool:
        """Check if this is an RGB color capability."""
        return (
            self.type == CAPABILITY_COLOR_SETTING
            and self.instance == INSTANCE_COLOR_RGB
        )

    @property
    def is_color_temp(self) -> bool:
        """Check if this is a color temperature capability."""
        return (
            self.type == CAPABILITY_COLOR_SETTING
            and self.instance == INSTANCE_COLOR_TEMP
        )

    @property
    def is_segment_color(self) -> bool:
        """Check if this is a segment color capability."""
        return self.type == CAPABILITY_SEGMENT_COLOR

    @property
    def is_scene(self) -> bool:
        """Check if this is a lightScene capability.

        Uses case-insensitive matching for robustness.
        """
        return (
            self.type == CAPABILITY_DYNAMIC_SCENE
            and self.instance.lower() == INSTANCE_SCENE.lower()
        )

    @property
    def is_diy_scene(self) -> bool:
        """Check if this is a DIY scene capability.

        Uses case-insensitive matching for robustness.
        """
        return (
            self.type == CAPABILITY_DYNAMIC_SCENE
            and self.instance.lower() == INSTANCE_DIY.lower()
        )

    @property
    def is_toggle(self) -> bool:
        """Check if this is a toggle capability."""
        return self.type == CAPABILITY_TOGGLE

    @property
    def is_night_light(self) -> bool:
        """Check if this is a night light toggle."""
        return self.type == CAPABILITY_TOGGLE and self.instance == INSTANCE_NIGHT_LIGHT

    @property
    def is_oscillation(self) -> bool:
        """Check if this is an oscillation toggle (for fans)."""
        return self.type == CAPABILITY_TOGGLE and self.instance == INSTANCE_OSCILLATION

    @property
    def is_dreamview(self) -> bool:
        """Check if this is a DreamView toggle (Movie Mode)."""
        return self.type == CAPABILITY_TOGGLE and self.instance == INSTANCE_DREAMVIEW

    @property
    def is_work_mode(self) -> bool:
        """Check if this is a work mode capability (for fans)."""
        return self.type == CAPABILITY_WORK_MODE and self.instance == INSTANCE_WORK_MODE

    @property
    def is_hdmi_source(self) -> bool:
        """Check if this is an HDMI source mode capability."""
        return self.type == CAPABILITY_MODE and self.instance == INSTANCE_HDMI_SOURCE

    @property
    def brightness_range(self) -> tuple[int, int]:
        """Get brightness min/max range. Default (0, 100)."""
        if not self.is_brightness:
            return (0, 100)
        range_data = self.parameters.get("range", {})
        return (
            int(range_data.get("min", 0)),
            int(range_data.get("max", 100)),
        )


@dataclass(frozen=True)
class GoveeDevice:
    """Represents a Govee device with its static properties.

    Frozen for immutability - device capabilities don't change at runtime.
    """

    device_id: str
    sku: str
    name: str
    device_type: str
    capabilities: tuple[GoveeCapability, ...] = field(default_factory=tuple)
    is_group: bool = False

    @property
    def supports_power(self) -> bool:
        """Check if device supports on/off control."""
        return any(cap.is_power for cap in self.capabilities)

    @property
    def supports_brightness(self) -> bool:
        """Check if device supports brightness control."""
        return any(cap.is_brightness for cap in self.capabilities)

    @property
    def supports_rgb(self) -> bool:
        """Check if device supports RGB color."""
        return any(cap.is_color_rgb for cap in self.capabilities)

    @property
    def supports_color_temp(self) -> bool:
        """Check if device supports color temperature."""
        return any(cap.is_color_temp for cap in self.capabilities)

    @property
    def supports_segments(self) -> bool:
        """Check if device supports segment control (RGBIC)."""
        return any(cap.is_segment_color for cap in self.capabilities)

    @property
    def supports_scenes(self) -> bool:
        """Check if device supports dynamic scenes."""
        return any(cap.is_scene for cap in self.capabilities)

    @property
    def supports_diy_scenes(self) -> bool:
        """Check if device supports DIY scenes."""
        return any(cap.is_diy_scene for cap in self.capabilities)

    @property
    def supports_night_light(self) -> bool:
        """Check if device supports night light toggle."""
        return any(cap.is_night_light for cap in self.capabilities)

    @property
    def supports_music_mode(self) -> bool:
        """Check if device supports music mode.

        Music mode is available on devices with either:
        - Music setting capability (devices.capabilities.music_setting)
        - DIY scene support (which includes music reactive options)
        """
        return (
            any(cap.type == CAPABILITY_MUSIC_MODE for cap in self.capabilities)
            or self.supports_diy_scenes
        )

    @property
    def is_plug(self) -> bool:
        """Check if device is a smart plug."""
        return self.device_type == DEVICE_TYPE_PLUG

    @property
    def is_fan(self) -> bool:
        """Check if device is a fan or air purifier.

        Air purifiers (devices.types.air_purifier, e.g. H7126) expose the same
        workMode capability shape as fans — gearMode speeds (Sleep/Low/High)
        plus an Auto preset — so they map onto the Home Assistant fan entity.
        """
        return self.device_type in (DEVICE_TYPE_FAN, DEVICE_TYPE_PURIFIER)

    @property
    def is_heater(self) -> bool:
        """Check if device is a heater."""
        return self.device_type == DEVICE_TYPE_HEATER

    @property
    def is_purifier(self) -> bool:
        """Check if device is an air purifier."""
        return self.device_type == DEVICE_TYPE_PURIFIER

    @property
    def is_humidifier(self) -> bool:
        """Check if device is a humidifier or dehumidifier.

        Covers both ``devices.types.humidifier`` and
        ``devices.types.dehumidifier`` so the humidifier platform handles
        both shapes — the entity picks the right HA device class at
        creation time (issue #54).
        """
        return self.device_type in (
            DEVICE_TYPE_HUMIDIFIER,
            DEVICE_TYPE_DEHUMIDIFIER,
        )

    @property
    def is_kettle(self) -> bool:
        """Check if device is a smart kettle (e.g. H717A Smart Kettle Pro)."""
        return self.device_type == DEVICE_TYPE_KETTLE

    @property
    def is_dehumidifier(self) -> bool:
        """Check if device is specifically a dehumidifier."""
        return self.device_type == DEVICE_TYPE_DEHUMIDIFIER

    @property
    def supports_water_full_event(self) -> bool:
        """Check if device exposes a water-tank-full event capability."""
        return any(
            cap.type == CAPABILITY_EVENT and cap.instance == INSTANCE_WATER_FULL_EVENT
            for cap in self.capabilities
        )

    @property
    def supports_temperature_sensor(self) -> bool:
        """Check if device exposes a sensorTemperature property (e.g. H5109,
        H5179). The capability is read-only — surfaced as an HA sensor."""
        return any(
            cap.type == CAPABILITY_PROPERTY
            and cap.instance == INSTANCE_SENSOR_TEMPERATURE
            for cap in self.capabilities
        )

    @property
    def supports_humidity_sensor(self) -> bool:
        """Check if device exposes a sensorHumidity property."""
        return any(
            cap.type == CAPABILITY_PROPERTY and cap.instance == INSTANCE_SENSOR_HUMIDITY
            for cap in self.capabilities
        )

    @property
    def is_thermometer(self) -> bool:
        """Check if device is a stand-alone thermometer/hygrometer."""
        return self.device_type == DEVICE_TYPE_THERMOMETER

    def get_humidity_range(self) -> tuple[int, int]:
        """Extract target humidity range from the range.humidity capability.

        Returns (min, max) tuple, defaulting to (30, 80) — the H7150 range.
        """
        for cap in self.capabilities:
            if cap.type == CAPABILITY_RANGE and cap.instance == INSTANCE_HUMIDITY:
                range_data = cap.parameters.get("range", {})
                return (
                    int(range_data.get("min", 30)),
                    int(range_data.get("max", 80)),
                )
        return (30, 80)

    def get_humidifier_work_mode_options(self) -> list[dict[str, Any]]:
        """Extract top-level work mode options for humidifier/dehumidifier.

        Unlike fans, humidifiers expose work modes at the workMode field
        level (gearMode, Auto, Dryer), not as flattened speed options.
        Returns list of {"name": str, "value": int} dicts.
        """
        for cap in self.capabilities:
            if cap.type == CAPABILITY_WORK_MODE and cap.instance == INSTANCE_WORK_MODE:
                for f in cap.parameters.get("fields", []):
                    if f.get("fieldName") == "workMode":
                        options: list[dict[str, Any]] = f.get("options", [])
                        return [
                            {"name": o.get("name", ""), "value": o.get("value")}
                            for o in options
                            if o.get("value") is not None
                        ]
        return []

    def get_humidifier_gear_options(self) -> list[dict[str, Any]]:
        """Extract gearMode sub-options (e.g. Low/High) for humidifiers.

        Returns list of {"name": "Low", "value": 1} dicts from the
        modeValue.gearMode sub-options.
        """
        for cap in self.capabilities:
            if cap.type == CAPABILITY_WORK_MODE and cap.instance == INSTANCE_WORK_MODE:
                for f in cap.parameters.get("fields", []):
                    if f.get("fieldName") == "modeValue":
                        for opt in f.get("options", []):
                            if opt.get("name") == "gearMode":
                                gears: list[dict[str, Any]] = opt.get("options", [])
                                return [
                                    {
                                        "name": g.get("name", ""),
                                        "value": g.get("value"),
                                    }
                                    for g in gears
                                    if g.get("value") is not None
                                ]
        return []

    @property
    def supports_oscillation(self) -> bool:
        """Check if device supports oscillation (fans)."""
        return any(cap.is_oscillation for cap in self.capabilities)

    @property
    def supports_dreamview(self) -> bool:
        """Check if device supports DreamView (Movie Mode) toggle."""
        return any(cap.is_dreamview for cap in self.capabilities)

    @property
    def supports_thermostat_toggle(self) -> bool:
        """Check if device supports thermostat (auto-stop) toggle."""
        return any(
            cap.type == CAPABILITY_TOGGLE and cap.instance == INSTANCE_THERMOSTAT_TOGGLE
            for cap in self.capabilities
        )

    @property
    def supports_temperature_setting_auto_stop(self) -> bool:
        """Check if device exposes autoStop inside a temperature_setting STRUCT.

        Some heaters (e.g. H713C) carry the auto-stop flag as a field of the
        ``targetTemperature`` STRUCT rather than as a separate
        ``thermostatToggle`` capability. The switch and number entities need
        to know which shape a given device uses (issue #29).
        """
        for cap in self.capabilities:
            if (
                cap.type != CAPABILITY_TEMPERATURE_SETTING
                or cap.instance != INSTANCE_TARGET_TEMPERATURE
            ):
                continue
            fields = cap.parameters.get("fields") if cap.parameters else None
            if not fields:
                continue
            if any(field.get("fieldName") == "autoStop" for field in fields):
                return True
        return False

    @property
    def supports_work_mode(self) -> bool:
        """Check if device supports work mode (fans)."""
        return any(cap.is_work_mode for cap in self.capabilities)

    @property
    def supports_ceiling_fan(self) -> bool:
        """Check if device has an integrated ceiling fan (e.g. H1310).

        These report as devices.types.light but carry a ``fanToggle`` toggle
        plus a ``fanSpeedMode`` mode capability. Distinct from standalone fans
        (workMode / fanSpeed / oscillation) — issue #74.
        """
        has_toggle = any(
            cap.type == CAPABILITY_TOGGLE and cap.instance == INSTANCE_FAN_TOGGLE
            for cap in self.capabilities
        )
        has_speed = any(
            cap.type == CAPABILITY_MODE and cap.instance == INSTANCE_FAN_SPEED_MODE
            for cap in self.capabilities
        )
        return has_toggle and has_speed

    @property
    def supports_reverse_airflow(self) -> bool:
        """Check if the integrated ceiling fan supports reverse airflow."""
        return any(
            cap.type == CAPABILITY_TOGGLE and cap.instance == INSTANCE_REVERSE_AIRFLOW
            for cap in self.capabilities
        )

    def get_ceiling_fan_speed_options(self) -> list[dict[str, Any]]:
        """Get ``fanSpeedMode`` speed options as {"name", "value"} dicts."""
        for cap in self.capabilities:
            if cap.type == CAPABILITY_MODE and cap.instance == INSTANCE_FAN_SPEED_MODE:
                options: list[dict[str, Any]] = cap.parameters.get("options", [])
                return options
        return []

    @property
    def supports_hdmi_source(self) -> bool:
        """Check if device supports HDMI source selection."""
        return any(cap.is_hdmi_source for cap in self.capabilities)

    def get_hdmi_source_options(self) -> list[dict[str, Any]]:
        """Get available HDMI source options from capability parameters."""
        for cap in self.capabilities:
            if cap.is_hdmi_source:
                options: list[dict[str, Any]] = cap.parameters.get("options", [])
                return options
        return []

    @property
    def has_struct_music_mode(self) -> bool:
        """Check if device has STRUCT-based music mode (vs legacy BLE).

        STRUCT-based music mode uses the REST API with a structured payload
        containing musicMode, sensitivity, and optionally autoColor/rgb fields.
        Legacy devices use BLE passthrough via MQTT.
        """
        for cap in self.capabilities:
            if (
                cap.type == CAPABILITY_MUSIC_MODE
                and cap.instance == INSTANCE_MUSIC_MODE
            ):
                # STRUCT capabilities have 'fields' array in parameters
                return "fields" in cap.parameters
        return False

    def get_music_mode_options(self) -> list[dict[str, Any]]:
        """Extract music mode options from capability fields.

        Returns list of {"name": "Rhythm", "value": 1} dicts.
        Pattern validated in external repositories.
        """
        for cap in self.capabilities:
            if (
                cap.type == CAPABILITY_MUSIC_MODE
                and cap.instance == INSTANCE_MUSIC_MODE
            ):
                for f in cap.parameters.get("fields", []):
                    if f.get("fieldName") == "musicMode":
                        options: list[dict[str, Any]] = f.get("options", [])
                        return options
        return []

    def get_music_sensitivity_range(self) -> tuple[int, int]:
        """Extract sensitivity range from capability fields.

        Returns (min, max) tuple, defaulting to (0, 100).
        """
        for cap in self.capabilities:
            if (
                cap.type == CAPABILITY_MUSIC_MODE
                and cap.instance == INSTANCE_MUSIC_MODE
            ):
                for f in cap.parameters.get("fields", []):
                    if f.get("fieldName") == "sensitivity":
                        range_info = f.get("range", {})
                        return (range_info.get("min", 0), range_info.get("max", 100))
        return (0, 100)

    def get_temperature_range(self) -> tuple[int, int]:
        """Extract temperature range from capability.

        Parses STRUCT-based temperature_setting capability where the range
        is nested inside the fields array under the 'temperature' field.

        Returns (min, max) tuple, defaulting to (16, 35) Celsius.
        """
        for cap in self.capabilities:
            if (
                cap.type == CAPABILITY_TEMPERATURE_SETTING
                and cap.instance == INSTANCE_TARGET_TEMPERATURE
            ):
                for f in cap.parameters.get("fields", []):
                    if f.get("fieldName") == "temperature":
                        range_data = f.get("range", {})
                        return (
                            int(range_data.get("min", 16)),
                            int(range_data.get("max", 35)),
                        )
        return (16, 35)

    def get_fan_speed_options(self) -> list[dict[str, Any]]:
        """Extract fan speed options from work_mode capability.

        Parses both workMode and modeValue fields, flattening nested
        sub-options. For example, H7131 has gearMode containing
        Low/Medium/High sub-options in the modeValue field.

        Returns list of {"name": "Low", "work_mode": 1, "mode_value": 0} dicts.
        """
        for cap in self.capabilities:
            if cap.type == CAPABILITY_WORK_MODE and cap.instance == INSTANCE_WORK_MODE:
                work_mode_field: dict[str, Any] | None = None
                mode_value_field: dict[str, Any] | None = None
                for f in cap.parameters.get("fields", []):
                    if f.get("fieldName") == "workMode":
                        work_mode_field = f
                    elif f.get("fieldName") == "modeValue":
                        mode_value_field = f

                if not work_mode_field:
                    return []

                # Build modeValue lookup by name
                mv_lookup: dict[str, dict[str, Any]] = {}
                if mode_value_field:
                    for mv_opt in mode_value_field.get("options", []):
                        name = mv_opt.get("name", "")
                        if name:
                            mv_lookup[name] = mv_opt

                result: list[dict[str, Any]] = []
                for wm_opt in work_mode_field.get("options", []):
                    wm_name = wm_opt.get("name", "")
                    wm_value = wm_opt.get("value")
                    if not wm_name or wm_value is None:
                        continue

                    # Check for nested sub-options (e.g., gearMode → Low/Medium/High)
                    mv_entry = mv_lookup.get(wm_name, {})
                    sub_options: list[dict[str, Any]] = mv_entry.get("options", [])

                    if sub_options:
                        for sub_opt in sub_options:
                            sub_name = sub_opt.get("name", "")
                            sub_value = sub_opt.get("value")
                            if sub_value is not None:
                                if not sub_name:
                                    sub_name = f"Speed {sub_value}"
                                result.append(
                                    {
                                        "name": sub_name,
                                        "work_mode": wm_value,
                                        "mode_value": sub_value,
                                    }
                                )
                    else:
                        default_mv: int = mv_entry.get("defaultValue", 0)
                        result.append(
                            {
                                "name": wm_name,
                                "work_mode": wm_value,
                                "mode_value": default_mv,
                            }
                        )

                return result
        return []

    def get_purifier_mode_options(self) -> list[dict[str, Any]]:
        """Extract purifier mode options from capability.

        Supports two patterns:
        1. Simple CAPABILITY_MODE with options (e.g., H6006)
        2. Complex CAPABILITY_WORK_MODE with nested modeValue options (e.g., H7127)

        Returns list of {"name": "Sleep", "value": 1} dicts.
        For work_mode capabilities, extracts gearMode options.
        """
        # Pattern 1: Simple CAPABILITY_MODE (e.g., H6006)
        for cap in self.capabilities:
            if cap.type == CAPABILITY_MODE and cap.instance == INSTANCE_PURIFIER_MODE:
                options: list[dict[str, Any]] = cap.parameters.get("options", [])
                return options

        # Pattern 2: Complex CAPABILITY_WORK_MODE (e.g., H7127)
        # Some purifiers use work_mode like fans, with modeValue options
        for cap in self.capabilities:
            if cap.type == CAPABILITY_WORK_MODE and cap.instance == "workMode":
                # Extract gear mode options from modeValue field in STRUCT
                fields = cap.parameters.get("fields", [])
                for f in fields:
                    if f.get("fieldName") == "modeValue":
                        options = f.get("options", [])
                        # Find the gearMode options within the nested structure
                        for opt in options:
                            if opt.get("name") == "gearMode":
                                gear_options: list[dict[str, Any]] = opt.get(
                                    "options", []
                                )
                                if gear_options:
                                    return gear_options
        return []

    @property
    def is_light_device(self) -> bool:
        """Check if device is a light (not a plain plug, fan, or other appliance).

        An outlet extender that exposes an RGB / tunable-white nightlight
        (e.g. H5089) IS a light for that nightlight, so a plug is only
        excluded when it has no color capability. Plain plugs (on/off only)
        stay switch-only. Fixes #59 — the #54 appliance filter over-reached
        and removed the H5089's color light entity.
        """
        if (
            self.is_fan
            or self.is_heater
            or self.is_purifier
            or self.is_humidifier
            or self.is_kettle
        ):
            return False
        if self.is_plug and not (self.supports_rgb or self.supports_color_temp):
            return False
        return (
            self.device_type == DEVICE_TYPE_LIGHT
            or self.supports_rgb
            or self.supports_color_temp
        )

    @property
    def brightness_range(self) -> tuple[int, int]:
        """Get brightness range from capability. Default (0, 100)."""
        for cap in self.capabilities:
            if cap.is_brightness:
                return cap.brightness_range
        return (0, 100)

    @property
    def color_temp_range(self) -> ColorTempRange | None:
        """Get color temperature range if supported."""
        for cap in self.capabilities:
            if cap.is_color_temp:
                return ColorTempRange.from_capability({"parameters": cap.parameters})
        return None

    @property
    def segment_count(self) -> int:
        """Get number of segments for RGBIC devices."""
        for cap in self.capabilities:
            if cap.is_segment_color:
                seg = SegmentCapability.from_capability({"parameters": cap.parameters})
                return seg.segment_count if seg else 0
        return 0

    def get_capability(self, cap_type: str, instance: str) -> GoveeCapability | None:
        """Get a specific capability by type and instance."""
        for cap in self.capabilities:
            if cap.type == cap_type and cap.instance == instance:
                return cap
        return None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> GoveeDevice:
        """Create GoveeDevice from API response data.

        Args:
            data: Device dict from /user/devices endpoint.

        Returns:
            GoveeDevice instance.
        """
        device_id = data.get("device", "")
        sku = data.get("sku", "")
        if not device_id or not sku:
            raise ValueError(
                f"Device missing required fields: device_id={device_id!r}, sku={sku!r}"
            )
        name = data.get("deviceName", sku)
        device_type = data.get("type", "devices.types.light")

        # Check for group device types
        # Groups can be identified by:
        # 1. Explicit group device types
        # 2. Numeric-only device IDs (no colons like MAC addresses)
        is_group = device_type in (
            "devices.types.group",
            "devices.types.same_mode_group",
            "devices.types.scenic_group",
        ) or (device_id.isdigit())

        # Parse capabilities
        raw_caps = data.get("capabilities", [])
        capabilities = []
        for raw_cap in raw_caps:
            cap = GoveeCapability(
                type=raw_cap.get("type", ""),
                instance=raw_cap.get("instance", ""),
                parameters=raw_cap.get("parameters", {}),
            )
            capabilities.append(cap)

        return cls(
            device_id=device_id,
            sku=sku,
            name=name,
            device_type=device_type,
            capabilities=tuple(capabilities),
            is_group=is_group,
        )


@dataclass(frozen=True)
class GoveeLeakSensor:
    """Represents a Govee leak sensor sub-device (e.g., H5058).

    These sensors communicate via LoRa to a hub (e.g., H5043) which relays
    events to the Govee cloud. They are discovered via the BFF device API
    and receive real-time updates via MQTT multiSync messages.
    """

    device_id: str  # Sensor MAC (e.g., "01:32:7A:C4:06:03:0D:0C")
    name: str  # User-assigned name (e.g., "Kitchen sink")
    sku: str  # Model (e.g., "H5058")
    hub_device_id: str  # Hub device ID (e.g., "09:C2:60:74:F4:64:AB:FA")
    sno: int  # Sensor slot on hub (0-14), maps to MQTT packet byte 2
    hw_version: str = ""  # Hardware version
    sw_version: str = ""  # Software version


@dataclass
class GoveeLeakSensorState:
    """Mutable state for a leak sensor, updated from BFF API and MQTT."""

    is_wet: bool = False
    battery: int | None = None  # 0-100%
    online: bool = True  # Sensor connected to gateway
    gateway_online: bool = True  # Gateway hub connected to cloud
    last_wet_time: int | None = None  # Epoch ms of last leak event
    read: bool = True  # Alert acknowledged in Govee app
    last_mqtt_wet_at: float = 0.0  # time.time() when MQTT last set is_wet=True


def leak_sensor_device_info(sensor: GoveeLeakSensor, domain: str) -> DeviceInfo:
    """Build the HA DeviceInfo for a leak sensor.

    Shared across binary_sensor, sensor, and event platforms.
    """
    info = DeviceInfo(
        identifiers={(domain, sensor.device_id)},
        name=sensor.name,
        manufacturer="Govee",
        model=sensor.sku,
        via_device=(domain, sensor.hub_device_id),
    )
    if sensor.hw_version:
        info["hw_version"] = sensor.hw_version
    if sensor.sw_version:
        info["sw_version"] = sensor.sw_version
    return info
