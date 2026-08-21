"""Light platform for Govee integration.

Provides light entities with support for:
- On/Off control
- Brightness control
- RGB color
- Color temperature
"""

from __future__ import annotations

import logging
from typing import Any

# mypy --strict: HA's `light` module re-exports without __all__, so
# `--no-implicit-reexport` raises attr-defined for each member. The
# suppression is upstream-stub-bound, not a real type error here.
from homeassistant.components.light import (  # type: ignore[attr-defined]
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_ENABLE_SCENES,
    DEFAULT_ENABLE_SCENES,
    DEFAULT_SEGMENT_MODE,
    SEGMENT_MODE_BOTH,
    SEGMENT_MODE_GROUPED,
    SEGMENT_MODE_INDIVIDUAL,
)
from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    GoveeDevice,
    PowerCommand,
    RGBColor,
    SceneCommand,
    ToggleCommand,
)
from .models.device import INSTANCE_NIGHT_LIGHT
from .platforms.grouped_segment import GoveeGroupedSegmentEntity
from .platforms.segment import GoveeSegmentEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# Home Assistant brightness range
HA_BRIGHTNESS_MAX = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee lights from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[LightEntity] = []

    # Get per-device segment modes
    device_modes = entry.options.get("segment_mode_by_device", {})

    # Check if scenes are enabled in options
    enable_scenes = entry.options.get(CONF_ENABLE_SCENES, DEFAULT_ENABLE_SCENES)

    for device in coordinator.devices.values():
        # Only create light entities for actual lights — appliances (heaters,
        # fans, dehumidifiers, purifiers, plugs) are surfaced by their own
        # platforms. Without this filter, e.g. an H7150 dehumidifier would
        # appear as a light bulb (issue #54).
        if device.is_light_device and device.supports_power:
            entities.append(GoveeLightEntity(coordinator, device, enable_scenes))

        # Appliances whose only light is the nightlight (e.g. H5089 outlet
        # extender, H7124 purifier) get a dedicated nightlight light entity —
        # on/off via nightlightToggle, brightness + colour from the shared
        # range/colour capabilities (issue #114).
        if device.has_nightlight_light and not device.is_group:
            entities.append(GoveeNightLightEntity(coordinator, device))

        # Create segment entities for RGBIC devices based on per-device mode
        if device.supports_segments and device.segment_count > 0:
            # Use per-device mode if set, otherwise default to individual
            segment_mode = device_modes.get(device.device_id, DEFAULT_SEGMENT_MODE)

            _LOGGER.debug(
                "Segment check for %s: device_mode=%s, supports_segments=%s, segment_count=%d",
                device.name,
                device_modes.get(device.device_id, "default (individual)"),
                device.supports_segments,
                device.segment_count,
            )

            # SEGMENT_MODE_BOTH creates every entity SEGMENT_MODE_GROUPED and
            # SEGMENT_MODE_INDIVIDUAL would create on their own — the grouped
            # entity is a convenience "all segments" light layered on top,
            # never a replacement for the individual per-segment entities.
            if segment_mode in (SEGMENT_MODE_GROUPED, SEGMENT_MODE_BOTH):
                _LOGGER.debug(
                    "Creating grouped segment entity for %s",
                    device.name,
                )
                entities.append(
                    GoveeGroupedSegmentEntity(
                        coordinator=coordinator,
                        device=device,
                    )
                )
            if segment_mode in (SEGMENT_MODE_INDIVIDUAL, SEGMENT_MODE_BOTH):
                _LOGGER.debug(
                    "Creating %d individual segment entities for %s",
                    device.segment_count,
                    device.name,
                )
                for segment_index in range(device.segment_count):
                    entities.append(
                        GoveeSegmentEntity(
                            coordinator=coordinator,
                            device=device,
                            segment_index=segment_index,
                        )
                    )

    async_add_entities(entities)
    _LOGGER.debug("Set up %d Govee light entities", len(entities))


class GoveeLightEntity(GoveeEntity, LightEntity, RestoreEntity):
    """Govee light entity.

    Supports:
    - On/Off
    - Brightness (scaled to device range)
    - RGB color
    - Color temperature
    - State restoration for group devices
    """

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        enable_scenes: bool = True,
    ) -> None:
        """Initialize the light entity."""
        super().__init__(coordinator, device)

        # Set name (uses has_entity_name = True)
        self._attr_name = None  # Use device name

        # Determine supported color modes
        self._attr_supported_color_modes = self._determine_color_modes()
        # Get device brightness range
        self._brightness_min, self._brightness_max = device.brightness_range

        # Effect support: only if device has scenes AND scenes are enabled
        self._enable_scenes = device.supports_scenes and enable_scenes
        if self._enable_scenes:
            self._attr_supported_features = LightEntityFeature.EFFECT

        # Scene-to-effect mappings (populated in async_added_to_hass)
        self._effect_to_scene: dict[str, tuple[int, str]] = {}
        self._scene_id_to_effect: dict[str, str] = {}
        self._effect_names: list[str] = []

    def _determine_color_modes(self) -> set[ColorMode]:
        """Determine supported color modes from device capabilities."""
        modes: set[ColorMode] = set()

        if self._device.supports_rgb:
            modes.add(ColorMode.RGB)

        if self._device.supports_color_temp:
            modes.add(ColorMode.COLOR_TEMP)

        if not modes and self._device.supports_brightness:
            modes.add(ColorMode.BRIGHTNESS)

        if not modes:
            modes.add(ColorMode.ONOFF)

        return modes

    @property
    def color_mode(self) -> ColorMode:
        """Return current color mode based on device state.

        Dynamically computed so it always reflects actual state.
        Always returns a value from supported_color_modes to satisfy
        HA Core validation (color_mode must be in supported_color_modes).
        """
        state = self.device_state
        modes = self.supported_color_modes or {ColorMode.ONOFF}

        if state and state.color_temp_kelvin is not None:
            if ColorMode.COLOR_TEMP in modes:
                return ColorMode.COLOR_TEMP

        if state and state.color is not None:
            if ColorMode.RGB in modes:
                return ColorMode.RGB

        # Default to first supported mode (prefer COLOR_TEMP > BRIGHTNESS > any)
        if ColorMode.BRIGHTNESS in modes:
            return ColorMode.BRIGHTNESS
        if ColorMode.COLOR_TEMP in modes:
            return ColorMode.COLOR_TEMP
        return ColorMode(next(iter(modes)))

    @property
    def is_on(self) -> bool | None:
        """Return True if light is on."""
        state = self.device_state
        return state.power_state if state else None

    @property
    def brightness(self) -> int | None:
        """Return brightness (0-255)."""
        state = self.device_state
        if state is None:
            return None

        # Convert device brightness to HA scale
        return self._device_to_ha_brightness(state.brightness)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return RGB color as (r, g, b) tuple."""
        state = self.device_state
        if state and state.color:
            return state.color.as_tuple
        return None

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return color temperature in Kelvin."""
        state = self.device_state
        return state.color_temp_kelvin if state and state.color_temp_kelvin else None

    @property
    def min_color_temp_kelvin(self) -> int:
        """Return minimum color temperature in Kelvin."""
        temp_range = self._device.color_temp_range
        return temp_range.min_kelvin if temp_range else 2000

    @property
    def max_color_temp_kelvin(self) -> int:
        """Return maximum color temperature in Kelvin."""
        temp_range = self._device.color_temp_range
        return temp_range.max_kelvin if temp_range else 9000

    @property
    def effect_list(self) -> list[str] | None:
        """Return list of available effects (scene names)."""
        return self._effect_names if self._effect_names else None

    @property
    def effect(self) -> str | None:
        """Return currently active effect (scene name)."""
        state = self.device_state
        if not state or not state.active_scene:
            return None
        # Look up display name from scene ID mapping
        effect_name = self._scene_id_to_effect.get(state.active_scene)
        if effect_name:
            return effect_name
        # Fall back to stored scene name if ID not in mapping
        return state.active_scene_name

    def _ha_to_device_brightness(self, ha_brightness: int) -> int:
        """Convert HA brightness (0-255) to device range, respecting min."""
        ratio = ha_brightness / HA_BRIGHTNESS_MAX
        result = int(
            self._brightness_min + ratio * (self._brightness_max - self._brightness_min)
        )
        return max(self._brightness_min, min(self._brightness_max, result))

    def _device_to_ha_brightness(self, device_brightness: int) -> int:
        """Convert device brightness to HA range (0-255), respecting min."""
        device_range = self._brightness_max - self._brightness_min
        if device_range <= 0:
            return 0
        result = int(
            (device_brightness - self._brightness_min)
            / device_range
            * HA_BRIGHTNESS_MAX
        )
        return max(0, min(HA_BRIGHTNESS_MAX, result))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on with optional parameters."""
        # Handle effect (scene activation)
        if ATTR_EFFECT in kwargs:
            effect_name = kwargs[ATTR_EFFECT]
            scene_info = self._effect_to_scene.get(effect_name)
            if scene_info:
                scene_id, scene_name = scene_info
                await self.coordinator.async_control_device(
                    self._device_id,
                    SceneCommand(scene_id=scene_id, scene_name=scene_name),
                )
            else:
                _LOGGER.warning(
                    "Unknown effect '%s' for %s", effect_name, self._device.name
                )
            return

        # Handle brightness
        if ATTR_BRIGHTNESS in kwargs:
            ha_brightness = kwargs[ATTR_BRIGHTNESS]
            device_brightness = self._ha_to_device_brightness(ha_brightness)
            if not await self.coordinator.async_control_device(
                self._device_id,
                BrightnessCommand(brightness=device_brightness),
            ):
                _LOGGER.warning("Brightness command failed for %s", self._device_id)

        # Handle RGB color
        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            color = RGBColor(r=r, g=g, b=b)
            if not await self.coordinator.async_control_device(
                self._device_id,
                ColorCommand(color=color),
            ):
                _LOGGER.warning("Color command failed for %s", self._device_id)

        # Handle color temperature
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            if not await self.coordinator.async_control_device(
                self._device_id,
                ColorTempCommand(kelvin=kelvin),
            ):
                _LOGGER.warning("Color temp command failed for %s", self._device_id)

        # Only send power command if light is off or no attributes were set
        has_attribute = any(
            k in kwargs
            for k in (ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ATTR_COLOR_TEMP_KELVIN)
        )
        if not has_attribute or not self.is_on:
            await self.coordinator.async_control_device(
                self._device_id,
                PowerCommand(power_on=True),
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self.coordinator.async_control_device(
            self._device_id,
            PowerCommand(power_on=False),
        )

    def _build_effect_mapping(self, scenes: list[dict[str, Any]]) -> None:
        """Build effect name mappings from scene data.

        Handles duplicate scene names by appending a counter,
        mirroring the logic in GoveeSceneSelectEntity.
        """
        self._effect_to_scene = {}
        self._scene_id_to_effect = {}
        names: list[str] = []

        for scene_data in scenes:
            scene_id = scene_data.get("value", {}).get("id", 0)
            scene_name = scene_data.get("name", f"Scene {scene_id}")

            # Handle duplicate names by appending counter
            unique_name = scene_name
            counter = 1
            while unique_name in self._effect_to_scene:
                unique_name = f"{scene_name} ({counter})"
                counter += 1

            self._effect_to_scene[unique_name] = (scene_id, scene_name)
            self._scene_id_to_effect[str(scene_id)] = unique_name
            names.append(unique_name)

        self._effect_names = names

    async def async_added_to_hass(self) -> None:
        """Restore state for group devices and load scenes for effects."""
        await super().async_added_to_hass()

        if self._device.is_group:
            last_state = await self.async_get_last_state()
            if last_state:
                # Restore state via coordinator
                power = last_state.state == "on"
                brightness = None
                if last_state.attributes.get("brightness"):
                    brightness = self._ha_to_device_brightness(
                        last_state.attributes["brightness"]
                    )
                self.coordinator.restore_group_state(self._device_id, power, brightness)

        # Load scenes for effect support (skip group devices - no scene API support)
        if self._enable_scenes and not self._device.is_group:
            scenes = await self.coordinator.async_get_scenes(self._device_id)
            if scenes:
                self._build_effect_mapping(scenes)


class GoveeNightLightEntity(GoveeEntity, LightEntity):
    """Dedicated light entity for an appliance's nightlight (issue #114).

    Used for appliances whose only light is the nightlight — the H5089 outlet
    extender and H7124 air purifier. On/off uses the ``nightlightToggle``
    capability (NOT the device's ``powerSwitch``, which is the outlet/appliance),
    while brightness and RGB colour come from the shared
    ``range::brightness`` / ``color_setting::colorRgb`` capabilities that, on
    these appliances, belong to the nightlight. The named nightlightScene modes
    are surfaced by a separate select entity.
    """

    _attr_translation_key = "govee_nightlight"
    _attr_icon = "mdi:lightbulb-night"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the nightlight entity."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_nightlight"

        modes: set[ColorMode] = set()
        if device.supports_rgb:
            modes.add(ColorMode.RGB)
        if device.supports_color_temp:
            modes.add(ColorMode.COLOR_TEMP)
        if not modes and device.supports_brightness:
            modes.add(ColorMode.BRIGHTNESS)
        if not modes:
            modes.add(ColorMode.ONOFF)
        self._attr_supported_color_modes = modes

        self._brightness_min, self._brightness_max = device.brightness_range

    def _ha_to_device_brightness(self, ha_brightness: int) -> int:
        ratio = ha_brightness / HA_BRIGHTNESS_MAX
        result = int(
            self._brightness_min + ratio * (self._brightness_max - self._brightness_min)
        )
        return max(self._brightness_min, min(self._brightness_max, result))

    def _device_to_ha_brightness(self, device_brightness: int) -> int:
        device_range = self._brightness_max - self._brightness_min
        if device_range <= 0:
            return 0
        result = int(
            (device_brightness - self._brightness_min)
            / device_range
            * HA_BRIGHTNESS_MAX
        )
        return max(0, min(HA_BRIGHTNESS_MAX, result))

    @property
    def color_mode(self) -> ColorMode:
        """Return current colour mode (always within supported_color_modes)."""
        state = self.device_state
        modes = self.supported_color_modes or {ColorMode.ONOFF}
        if state and state.color_temp_kelvin is not None and ColorMode.COLOR_TEMP in modes:
            return ColorMode.COLOR_TEMP
        if state and state.color is not None and ColorMode.RGB in modes:
            return ColorMode.RGB
        if ColorMode.RGB in modes:
            return ColorMode.RGB
        if ColorMode.BRIGHTNESS in modes:
            return ColorMode.BRIGHTNESS
        if ColorMode.COLOR_TEMP in modes:
            return ColorMode.COLOR_TEMP
        return ColorMode(next(iter(modes)))

    @property
    def is_on(self) -> bool | None:
        """Return True if the nightlight is on (live nightlightToggle state)."""
        state = self.device_state
        if state is None:
            return None
        return state.toggles.get(INSTANCE_NIGHT_LIGHT)

    @property
    def brightness(self) -> int | None:
        """Return nightlight brightness (0-255)."""
        state = self.device_state
        if state is None:
            return None
        return self._device_to_ha_brightness(state.brightness)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return nightlight RGB colour."""
        state = self.device_state
        if state and state.color:
            return state.color.as_tuple
        return None

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return nightlight colour temperature in Kelvin."""
        state = self.device_state
        return state.color_temp_kelvin if state and state.color_temp_kelvin else None

    @property
    def min_color_temp_kelvin(self) -> int:
        """Return minimum colour temperature in Kelvin."""
        temp_range = self._device.color_temp_range
        return temp_range.min_kelvin if temp_range else 2000

    @property
    def max_color_temp_kelvin(self) -> int:
        """Return maximum colour temperature in Kelvin."""
        temp_range = self._device.color_temp_range
        return temp_range.max_kelvin if temp_range else 9000

    async def _set_toggle(self, enabled: bool) -> bool:
        success = await self.coordinator.async_control_device(
            self._device_id,
            ToggleCommand(toggle_instance=INSTANCE_NIGHT_LIGHT, enabled=enabled),
        )
        if success:
            state = self.device_state
            if state is not None:
                state.toggles[INSTANCE_NIGHT_LIGHT] = enabled
            self.async_write_ha_state()
        return success

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the nightlight on with optional brightness/colour."""
        if ATTR_BRIGHTNESS in kwargs:
            device_brightness = self._ha_to_device_brightness(kwargs[ATTR_BRIGHTNESS])
            await self.coordinator.async_control_device(
                self._device_id, BrightnessCommand(brightness=device_brightness)
            )

        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            await self.coordinator.async_control_device(
                self._device_id, ColorCommand(color=RGBColor(r=r, g=g, b=b))
            )

        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            await self.coordinator.async_control_device(
                self._device_id, ColorTempCommand(kelvin=kwargs[ATTR_COLOR_TEMP_KELVIN])
            )

        has_attribute = any(
            k in kwargs
            for k in (ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ATTR_COLOR_TEMP_KELVIN)
        )
        if not has_attribute or not self.is_on:
            await self._set_toggle(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the nightlight off."""
        await self._set_toggle(False)
