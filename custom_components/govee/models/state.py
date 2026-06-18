"""Device state models.

Mutable state that changes with device updates from API or MQTT.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# Candidate keys a thermometer/hygrometer reading may hide behind. The Govee
# state shape varies by SKU and transport: REST returns either a plain number
# under ``value`` or a STRUCT; AWS IoT pushes use flat keys whose naming is not
# documented, so accept the known spellings defensively.
#
# The ``*_STRUCT_KEYS`` sets include the generic ``value`` for unwrapping a
# nested object. The ``*_MQTT_KEYS`` sets deliberately omit ``value`` so a
# top-level scan of a light's push (onOff/brightness/color/...) can't false-
# match an unrelated ``value`` field.
_SENSOR_TEMPERATURE_STRUCT_KEYS = (
    "sensorTemperature",
    "currentTemperature",
    "temperature",
    "value",
    "tem",
)
_SENSOR_HUMIDITY_STRUCT_KEYS = (
    "sensorHumidity",
    "currentHumidity",
    "humidity",
    "value",
    "hum",
)
_SENSOR_TEMPERATURE_MQTT_KEYS = (
    "sensorTemperature",
    "currentTemperature",
    "temperature",
    "tem",
)
_SENSOR_HUMIDITY_MQTT_KEYS = (
    "sensorHumidity",
    "currentHumidity",
    "humidity",
    "hum",
)


def _coerce_int(value: Any) -> int | None:
    """int(value), or None when value is empty/None/non-numeric.

    The Govee cloud returns ``""`` for a capability's value when the device is
    offline or hasn't reported (e.g. ``brightness``/``hdmiSource`` on an offline
    device, #83). An unguarded ``int("")`` raises ValueError and fails the whole
    device-state parse, so a single bad field would stop the device updating.
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_sensor_value(value: Any, keys: tuple[str, ...]) -> float | None:
    """Extract a float reading from a number or a nested STRUCT.

    Accepts a plain ``int``/``float`` or a dict where the reading lives under
    one of ``keys``. Returns ``None`` when nothing usable is present so callers
    can preserve the last-known value instead of clobbering it.
    """
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in keys:
            sub = value.get(key)
            if isinstance(sub, bool):
                continue
            if isinstance(sub, (int, float)):
                return float(sub)
    return None


@dataclass(frozen=True)
class RGBColor:
    """Immutable RGB color representation."""

    r: int
    g: int
    b: int

    def __post_init__(self) -> None:
        """Validate color values are in range."""
        # Use object.__setattr__ because dataclass is frozen
        object.__setattr__(self, "r", max(0, min(255, self.r)))
        object.__setattr__(self, "g", max(0, min(255, self.g)))
        object.__setattr__(self, "b", max(0, min(255, self.b)))

    @property
    def as_tuple(self) -> tuple[int, int, int]:
        """Return as (r, g, b) tuple."""
        return (self.r, self.g, self.b)

    @property
    def as_packed_int(self) -> int:
        """Return as packed integer for Govee API: (R << 16) + (G << 8) + B."""
        return (self.r << 16) + (self.g << 8) + self.b

    @classmethod
    def from_packed_int(cls, value: int) -> RGBColor:
        """Create from Govee API packed integer."""
        r = (value >> 16) & 0xFF
        g = (value >> 8) & 0xFF
        b = value & 0xFF
        return cls(r=r, g=g, b=b)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RGBColor:
        """Create from dict with r, g, b keys."""
        return cls(
            r=int(data.get("r", 0)),
            g=int(data.get("g", 0)),
            b=int(data.get("b", 0)),
        )


@dataclass(frozen=True)
class SegmentState:
    """State of a single segment in RGBIC device."""

    index: int
    color: RGBColor
    brightness: int = 100

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int) -> SegmentState:
        """Create from segment dict."""
        color = RGBColor.from_dict(data.get("color", {}))
        brightness = data.get("brightness", 100)
        return cls(index=index, color=color, brightness=brightness)


@dataclass
class GoveeDeviceState:
    """Mutable device state updated from API or MQTT.

    Unlike GoveeDevice (frozen), state changes frequently and needs
    to be updated in-place for performance.
    """

    device_id: str
    online: bool = True
    power_state: bool = False
    brightness: int = 100
    color: RGBColor | None = None
    color_temp_kelvin: int | None = None
    active_scene: str | None = None
    active_scene_name: str | None = None  # Display name of active scene
    active_diy_scene: str | None = None  # DIY scene ID (separate from regular scenes)
    segments: list[SegmentState] = field(default_factory=list)
    diy_style: str | None = None  # DIY animation style (Fade, Jumping, etc.)
    diy_style_value: int | None = None  # DIY animation style numeric value (0-4)
    music_mode_enabled: bool | None = None  # Music mode on/off state (legacy BLE)

    # STRUCT-based music mode state (for devices with music_setting capability)
    music_mode_value: int | None = None  # Music mode value (1-11)
    music_mode_name: str | None = None  # Music mode display name (e.g., "Rhythm")
    music_sensitivity: int | None = None  # Microphone sensitivity (0-100)

    # Fan state
    oscillating: bool | None = None  # Fan oscillation on/off
    work_mode: int | None = None  # Fan work mode: 1=gearMode, 3=Auto, 9=Fan
    mode_value: int | None = None  # Fan speed for gearMode: 1=Low, 2=Medium, 3=High

    # HDMI source state (for devices like AI Sync Box H6604)
    hdmi_source: int | None = None  # HDMI port: 1, 2, 3, 4

    # Aroma diffuser preset scene (H7161, issue #99): integer id of the active
    # named scene. Often "" on poll, so the select is optimistic like HDMI/scene.
    preset_scene: int | None = None

    # DreamView (Movie Mode) state
    dreamview_enabled: bool | None = None  # DreamView on/off

    # Heater state
    heater_temperature: int | None = None  # Target temperature in Celsius
    heater_auto_stop: int | None = None  # Auto-stop setting (0=Maintain, 1=Auto-stop)
    fan_speed: int | None = None  # Fan speed mode value (1=Low, 2=Medium, 3=High)

    # Purifier state
    purifier_mode: int | None = (
        None  # Purifier mode value (1=Sleep, 2=Low, 3=High, etc.)
    )

    # Humidifier / dehumidifier state.
    # Target humidity (Auto mode) and manual speed are both carried in
    # ``mode_value`` above; the humidifier entity interprets it based on
    # ``work_mode``. Only the event flag needs its own field.
    water_full: bool | None = None  # Dehumidifier water-tank-full event

    # Standalone water-leak detector trip (H5054, issue #62). True when water
    # is detected. Arrives via the bodyAppearedEvent event capability — the
    # developer-API device-state poll only returns `online`, so the trip
    # normally lands via MQTT push.
    water_leak: bool | None = None

    # Read-only sensor properties (devices.capabilities.property) for
    # stand-alone sensors like H5109/H5179. None until first poll lands.
    sensor_temperature: float | None = (
        None  # Raw from API (°C or °F; entity may normalize)
    )
    sensor_humidity: float | None = None  # Relative humidity 0-100 %
    battery: int | None = None  # Battery level 0-100 % (BFF thermo-hygrometers)

    # Last activated scene (for restoring after music mode off)
    last_scene_id: str | None = None
    last_scene_name: str | None = None

    # Last color/color_temp (for restoring when scene is cleared)
    last_color: RGBColor | None = None
    last_color_temp_kelvin: int | None = None

    # Source tracking for state management
    # "api" = from REST poll, "mqtt" = from push, "optimistic" = from command
    source: str = "api"

    # Monotonic timestamp of the most recent optimistic write. Used by the
    # coordinator to apply a short grace period during which API polls don't
    # overwrite optimistic power/brightness — covers out-of-range BLE devices
    # and slow cloud round-trips. Reset by MQTT push confirmations.
    last_optimistic_update: float | None = None

    def _stamp_optimistic(self) -> None:
        """Record that an optimistic write just happened."""
        self.source = "optimistic"
        self.last_optimistic_update = time.monotonic()

    def clear_optimistic_window(self) -> None:
        """End the optimistic grace period (e.g. on confirmed MQTT push)."""
        self.last_optimistic_update = None

    def update_from_api(self, data: dict[str, Any]) -> None:
        """Update state from API response.

        Args:
            data: Device state dict from /device/state endpoint.
        """
        self.source = "api"

        # Parse capabilities array for state values
        capabilities = data.get("capabilities", [])
        for cap in capabilities:
            cap_type = cap.get("type", "")
            instance = cap.get("instance", "")
            state = cap.get("state") or {}
            value = state.get("value")

            if cap_type == "devices.capabilities.online":
                self.online = bool(value)

            elif cap_type == "devices.capabilities.on_off":
                if instance == "powerSwitch":
                    self.power_state = bool(value)

            elif cap_type == "devices.capabilities.range":
                if instance == "brightness":
                    parsed_brightness = _coerce_int(value)
                    self.brightness = (
                        parsed_brightness if parsed_brightness is not None else 100
                    )

            elif cap_type == "devices.capabilities.color_setting":
                if instance == "colorRgb":
                    if isinstance(value, int):
                        self.color = RGBColor.from_packed_int(value)
                    elif isinstance(value, dict):
                        self.color = RGBColor.from_dict(value)
                elif instance == "colorTemperatureK":
                    self.color_temp_kelvin = int(value) if value else None

            elif cap_type == "devices.capabilities.toggle":
                if instance == "oscillationToggle":
                    self.oscillating = bool(value)
                elif instance == "dreamViewToggle":
                    self.dreamview_enabled = bool(value)

            elif cap_type == "devices.capabilities.work_mode":
                if instance == "workMode" and isinstance(value, dict):
                    self.work_mode = value.get("workMode")
                    self.mode_value = value.get("modeValue")

            elif cap_type == "devices.capabilities.mode":
                if instance == "hdmiSource":
                    self.hdmi_source = _coerce_int(value)
                elif instance == "presetScene":
                    self.preset_scene = _coerce_int(value)

            elif cap_type == "devices.capabilities.property":
                # Read-only sensor properties on devices like H5109 and
                # H5179 (issue #62). The state shape varies by SKU — some
                # return a plain number under "value", others return a
                # STRUCT. Accept both, plus the legacy "currentX" field
                # naming used by older WiFi sensors.
                if instance == "sensorTemperature":
                    parsed = _coerce_sensor_value(
                        value, _SENSOR_TEMPERATURE_STRUCT_KEYS
                    )
                    if parsed is not None:
                        self.sensor_temperature = parsed
                elif instance == "sensorHumidity":
                    parsed = _coerce_sensor_value(value, _SENSOR_HUMIDITY_STRUCT_KEYS)
                    if parsed is not None:
                        self.sensor_humidity = parsed

            elif cap_type == "devices.capabilities.event":
                # Event capabilities (e.g. waterFullEvent) report a boolean-
                # ish value when the event is active. Some backends also
                # nest it under a STRUCT; accept either shape.
                if instance == "waterFullEvent":
                    if isinstance(value, dict):
                        self.water_full = bool(value.get("state") or value.get("value"))
                    elif value is not None:
                        self.water_full = bool(value)
                elif instance == "bodyAppearedEvent":
                    # H5054 water-leak detector trip (issue #62).
                    if isinstance(value, dict):
                        self.water_leak = bool(value.get("state") or value.get("value"))
                    elif value is not None:
                        self.water_leak = bool(value)

            elif cap_type == "devices.capabilities.temperature_setting":
                # Heaters report target temperature + autoStop in a STRUCT.
                # Capturing autoStop here lets temperature-change commands
                # preserve the user's choice instead of resetting it to 0
                # (issue #29).
                if instance == "targetTemperature" and isinstance(value, dict):
                    temp_val = value.get("temperature")
                    if temp_val is not None:
                        try:
                            self.heater_temperature = int(temp_val)
                        except (TypeError, ValueError):
                            pass
                    auto_stop = value.get("autoStop")
                    if auto_stop is not None:
                        try:
                            self.heater_auto_stop = int(auto_stop)
                        except (TypeError, ValueError):
                            pass

    def update_from_mqtt(self, data: dict[str, Any]) -> None:
        """Update state from MQTT push message.

        MQTT format differs from REST API - uses onOff/brightness/color keys.

        Args:
            data: State dict from MQTT message.
        """
        self.source = "mqtt"

        # Receiving an AWS IoT push from the device is direct proof of life.
        # The Govee cloud's `online` flag can lag for many minutes after a
        # power-cycle (issue #68); using the MQTT signal lets HA recover
        # availability immediately without waiting for the cloud to catch up.
        self.online = True

        if "onOff" in data:
            self.power_state = bool(data["onOff"])

        if "brightness" in data:
            parsed_brightness = _coerce_int(data["brightness"])
            if parsed_brightness is not None:
                self.brightness = parsed_brightness

        if "color" in data:
            color_data = data["color"]
            if isinstance(color_data, dict):
                self.color = RGBColor.from_dict(color_data)
            elif isinstance(color_data, int):
                self.color = RGBColor.from_packed_int(color_data)

        if "colorTemInKelvin" in data:
            temp = data["colorTemInKelvin"]
            self.color_temp_kelvin = int(temp) if temp else None

        # Stand-alone thermometer/hygrometer readings (H5179, H5109, H5110,
        # HS5108, HS5106). The mqtt.py docstring notes AWS IoT pushes carry
        # temperature, but the flat-key spelling is undocumented — accept the
        # known instance/short names. Without this the push was silently
        # dropped and the entity only ever showed its first REST read (#83).
        for key in _SENSOR_TEMPERATURE_MQTT_KEYS:
            if key in data:
                parsed = _coerce_sensor_value(
                    data[key], _SENSOR_TEMPERATURE_STRUCT_KEYS
                )
                if parsed is not None:
                    self.sensor_temperature = parsed
                    break
        for key in _SENSOR_HUMIDITY_MQTT_KEYS:
            if key in data:
                parsed = _coerce_sensor_value(data[key], _SENSOR_HUMIDITY_STRUCT_KEYS)
                if parsed is not None:
                    self.sensor_humidity = parsed
                    break

        # A confirmed push ends the optimistic grace window — from this point
        # on API polls are authoritative again for power/brightness.
        self.clear_optimistic_window()

    def apply_optimistic_power(self, power_on: bool) -> None:
        """Apply optimistic power state update."""
        self.power_state = power_on
        self._stamp_optimistic()

    def apply_optimistic_brightness(self, brightness: int) -> None:
        """Apply optimistic brightness update."""
        self.brightness = brightness
        self._stamp_optimistic()

    def apply_optimistic_color(self, color: RGBColor) -> None:
        """Apply optimistic color update."""
        self.color = color
        self.color_temp_kelvin = None  # RGB mode
        self._stamp_optimistic()
        # Setting a color overrides any running scene
        self.active_scene = None
        self.active_scene_name = None

    def apply_optimistic_color_temp(self, kelvin: int) -> None:
        """Apply optimistic color temperature update."""
        self.color_temp_kelvin = kelvin
        self.color = None  # Color temp mode
        self._stamp_optimistic()
        # Setting color temp overrides any running scene
        self.active_scene = None
        self.active_scene_name = None

    def apply_optimistic_scene(
        self, scene_id: str, scene_name: str | None = None
    ) -> None:
        """Apply optimistic scene activation.

        Scenes, Music Mode, and DreamView are mutually exclusive.
        When a Scene is activated, DreamView, music mode, and DIY scene are cleared.
        """
        self.active_scene = scene_id
        self.active_scene_name = scene_name
        self.last_scene_id = scene_id
        self.last_scene_name = scene_name
        self._stamp_optimistic()
        # Save current color/color_temp before clearing so we can restore on scene clear.
        # Only save when a value exists so scene A → scene B → clear restores pre-A color.
        # Skip RGBColor(0,0,0) — the API returns colorRgb=0 when a scene is running,
        # which is not a meaningful color to restore.
        if self.color is not None and self.color.as_packed_int != 0:
            self.last_color = self.color
            self.last_color_temp_kelvin = None
        elif self.color_temp_kelvin is not None:
            self.last_color_temp_kelvin = self.color_temp_kelvin
            self.last_color = None
        # Clear stale color — scenes run dynamic patterns, no single color is accurate
        self.color = None
        self.color_temp_kelvin = None
        # Mutual exclusion: clear other modes when activating scene
        self.dreamview_enabled = False
        self.music_mode_enabled = False
        self.music_mode_value = None
        self.music_mode_name = None
        self.active_diy_scene = None

    def apply_optimistic_diy_scene(self, scene_id: str) -> None:
        """Apply optimistic DIY scene activation.

        DIY Scenes, regular Scenes, Music Mode, and DreamView are mutually exclusive.
        When a DIY Scene is activated, DreamView, music mode, and regular scene are cleared.
        """
        self.active_diy_scene = scene_id
        self._stamp_optimistic()
        # Save current color/color_temp before clearing (same logic as regular scenes)
        if self.color is not None and self.color.as_packed_int != 0:
            self.last_color = self.color
            self.last_color_temp_kelvin = None
        elif self.color_temp_kelvin is not None:
            self.last_color_temp_kelvin = self.color_temp_kelvin
            self.last_color = None
        # Mutual exclusion: clear other modes when activating DIY scene
        self.dreamview_enabled = False
        self.music_mode_enabled = False
        self.music_mode_value = None
        self.music_mode_name = None
        self.active_scene = None
        self.active_scene_name = None

    def apply_optimistic_diy_style(
        self, style: str, style_value: int | None = None
    ) -> None:
        """Apply optimistic DIY style update.

        Args:
            style: Style name (Fade, Jumping, Flicker, Marquee, Music).
            style_value: Style numeric value (0-4). If None, will be looked up.
        """
        self.diy_style = style
        self.diy_style_value = style_value
        self._stamp_optimistic()

    def apply_optimistic_music_mode(self, enabled: bool) -> None:
        """Apply optimistic music mode update (legacy BLE).

        Music Mode, DreamView, and Scenes are mutually exclusive.
        When Music Mode is enabled, DreamView and scenes are cleared.
        """
        self.music_mode_enabled = enabled
        self._stamp_optimistic()
        # Mutual exclusion: clear other modes when enabling music mode
        if enabled:
            self.dreamview_enabled = False
            self.active_scene = None
            self.active_scene_name = None
            self.active_diy_scene = None

    def apply_optimistic_music_mode_struct(
        self,
        music_mode: int,
        sensitivity: int,
        mode_name: str | None = None,
    ) -> None:
        """Apply optimistic music mode update (STRUCT-based REST API).

        Music Mode, DreamView, and Scenes are mutually exclusive.
        When Music Mode is enabled, DreamView and active scene are cleared.

        Args:
            music_mode: Music mode value (1-11).
            sensitivity: Microphone sensitivity (0-100).
            mode_name: Optional display name for the mode.
        """
        self.music_mode_value = music_mode
        self.music_sensitivity = sensitivity
        self.music_mode_name = mode_name
        self.music_mode_enabled = True  # Also set enabled for switch state
        self._stamp_optimistic()
        # Mutual exclusion: clear other modes when enabling music mode
        self.dreamview_enabled = False
        self.active_scene = None
        self.active_scene_name = None
        self.active_diy_scene = None

    def apply_optimistic_oscillation(self, oscillating: bool) -> None:
        """Apply optimistic oscillation update (fans)."""
        self.oscillating = oscillating
        self._stamp_optimistic()

    def apply_optimistic_work_mode(self, work_mode: int, mode_value: int) -> None:
        """Apply optimistic work mode update (fans)."""
        self.work_mode = work_mode
        self.mode_value = mode_value
        self._stamp_optimistic()

    def apply_optimistic_hdmi_source(self, source: int) -> None:
        """Apply optimistic HDMI source update."""
        self.hdmi_source = source
        self._stamp_optimistic()

    def apply_optimistic_dreamview(self, enabled: bool) -> None:
        """Apply optimistic DreamView (Movie Mode) update.

        DreamView, Music Mode, and Scenes are mutually exclusive.
        When DreamView is enabled, music mode and scenes are cleared.
        """
        self.dreamview_enabled = enabled
        self._stamp_optimistic()
        # Mutual exclusion: clear other modes when enabling DreamView
        if enabled:
            self.music_mode_enabled = False
            self.music_mode_value = None
            self.music_mode_name = None
            self.active_scene = None
            self.active_scene_name = None
            self.active_diy_scene = None

    @classmethod
    def create_empty(cls, device_id: str) -> GoveeDeviceState:
        """Create empty state for a device."""
        return cls(device_id=device_id)
