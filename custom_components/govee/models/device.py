from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..api.const import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_MUSIC_SETTING,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    CAPABILITY_TOGGLE,
    INSTANCE_AIR_DEFLECTOR_TOGGLE,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_DIY_SCENE,
    INSTANCE_GRADIENT_TOGGLE,
    INSTANCE_LIGHT_SCENE,
    INSTANCE_MUSIC_MODE,
    INSTANCE_NIGHTLIGHT_TOGGLE,
    INSTANCE_OSCILLATION_TOGGLE,
    INSTANCE_POWER_SWITCH,
    INSTANCE_SEGMENTED_BRIGHTNESS,
    INSTANCE_SEGMENTED_COLOR,
    INSTANCE_SNAPSHOT,
    INSTANCE_THERMOSTAT_TOGGLE,
    INSTANCE_WARM_MIST_TOGGLE,
)
from .capability import DeviceCapability


@dataclass
class GoveeDevice:
    device_id: str
    sku: str
    device_name: str
    device_type: str
    capabilities: list[DeviceCapability] = field(default_factory=list)
    firmware_version: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> GoveeDevice:
        capabilities = [
            DeviceCapability.from_api(cap) for cap in data.get("capabilities", [])
        ]

        return cls(
            device_id=data.get("device", ""),
            sku=data.get("sku", ""),
            device_name=data.get("deviceName", data.get("device", "")),
            device_type=data.get("type", ""),
            capabilities=capabilities,
            firmware_version=data.get("version"),
        )

    def has_capability(self, cap_type: str, instance: str | None = None) -> bool:
        for cap in self.capabilities:
            if cap.type == cap_type:
                if instance is None or cap.instance == instance:
                    return True
        return False

    def get_capability(
        self, cap_type: str, instance: str | None = None
    ) -> DeviceCapability | None:
        for cap in self.capabilities:
            if cap.type == cap_type:
                if instance is None or cap.instance == instance:
                    return cap
        return None

    def get_capability_by_instance(self, instance: str) -> DeviceCapability | None:
        for cap in self.capabilities:
            if cap.instance == instance:
                return cap
        return None

    @property
    def supports_on_off(self) -> bool:
        return self.has_capability(CAPABILITY_ON_OFF, INSTANCE_POWER_SWITCH)

    @property
    def supports_brightness(self) -> bool:
        return self.has_capability(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS)

    @property
    def supports_color(self) -> bool:
        return self.has_capability(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB)

    @property
    def supports_color_temp(self) -> bool:
        return self.has_capability(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_TEMP)

    @property
    def supports_scenes(self) -> bool:
        return self.has_capability(CAPABILITY_DYNAMIC_SCENE, INSTANCE_LIGHT_SCENE)

    @property
    def supports_diy_scenes(self) -> bool:
        return self.has_capability(CAPABILITY_DYNAMIC_SCENE, INSTANCE_DIY_SCENE)

    @property
    def supports_segments(self) -> bool:
        return self.has_capability(CAPABILITY_SEGMENT_COLOR)

    @property
    def supports_music_mode(self) -> bool:
        return self.has_capability(CAPABILITY_MUSIC_SETTING, INSTANCE_MUSIC_MODE)

    @property
    def supports_nightlight(self) -> bool:
        return self.has_capability(CAPABILITY_TOGGLE, INSTANCE_NIGHTLIGHT_TOGGLE)

    @property
    def supports_snapshots(self) -> bool:
        return self.has_capability(CAPABILITY_DYNAMIC_SCENE, INSTANCE_SNAPSHOT)

    @property
    def supports_oscillation_toggle(self) -> bool:
        return self.has_capability(CAPABILITY_TOGGLE, INSTANCE_OSCILLATION_TOGGLE)

    @property
    def supports_thermostat_toggle(self) -> bool:
        return self.has_capability(CAPABILITY_TOGGLE, INSTANCE_THERMOSTAT_TOGGLE)

    @property
    def supports_gradient_toggle(self) -> bool:
        return self.has_capability(CAPABILITY_TOGGLE, INSTANCE_GRADIENT_TOGGLE)

    @property
    def supports_warm_mist_toggle(self) -> bool:
        return self.has_capability(CAPABILITY_TOGGLE, INSTANCE_WARM_MIST_TOGGLE)

    @property
    def supports_air_deflector_toggle(self) -> bool:
        return self.has_capability(CAPABILITY_TOGGLE, INSTANCE_AIR_DEFLECTOR_TOGGLE)

    def get_brightness_range(self) -> tuple[int, int]:
        cap = self.get_capability(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS)
        if cap and cap.min_value is not None and cap.max_value is not None:
            return (cap.min_value, cap.max_value)
        return (0, 100)

    def get_color_temp_range(self) -> tuple[int, int]:
        cap = self.get_capability(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_TEMP)
        if cap and cap.min_value is not None and cap.max_value is not None:
            return (cap.min_value, cap.max_value)
        return (2000, 9000)

    def get_scene_options(self) -> list[dict[str, Any]]:
        cap = self.get_capability(CAPABILITY_DYNAMIC_SCENE, INSTANCE_LIGHT_SCENE)
        if cap and cap.parameters and cap.parameters.options:
            return cap.parameters.options
        return []

    def get_segment_count(self) -> int:
        cap = self.get_capability(CAPABILITY_SEGMENT_COLOR, INSTANCE_SEGMENTED_COLOR)
        if cap and cap.parameters and cap.parameters.fields:
            for fld in cap.parameters.fields:
                if fld.get("fieldName") == "segment":
                    elem_range: dict[str, int] = fld.get("elementRange", {})
                    max_val: int = elem_range.get("max", 0)
                    return max_val + 1
        return 0
