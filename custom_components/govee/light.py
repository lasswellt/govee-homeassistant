"""Light platform for Govee integration.

Provides light entities with support for:
- On/Off control
- Brightness control
- RGB color
- Color temperature
"""

from __future__ import annotations

import asyncio
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoredExtraData, RestoreEntity

from .const import (
    CONF_ENABLE_SCENES,
    CONF_SEGMENT_MODE_BY_DEVICE,
    DEFAULT_ENABLE_SCENES,
    DEFAULT_SEGMENT_MODE,
    DOMAIN,
    MAIN_LIGHT_TOGGLE_SKUS,
    PTREAL_LIGHT_ZONE_SKUS,
    PTREAL_MAIN_PANEL_BIT,
    PTREAL_ZONE_MAX_KELVIN,
    PTREAL_ZONE_MIN_KELVIN,
    SEGMENT_MODE_BOTH,
    SEGMENT_MODE_GROUPED,
    SEGMENT_MODE_INDIVIDUAL,
    SUFFIX_MAIN_LIGHT_TOGGLE,
    SUFFIX_MAIN_PANEL,
    SUFFIX_RING,
)
from .brightness import device_to_ha_brightness, ha_to_device_brightness
from .coordinator import GoveeConfigEntry, GoveeCoordinator
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
from .models.device import INSTANCE_BACKGROUND_LIGHT_TOGGLE, INSTANCE_MAIN_LIGHT_TOGGLE, INSTANCE_NIGHT_LIGHT
from .platforms.grouped_segment import GoveeGroupedSegmentEntity
from .platforms.segment import GoveeSegmentEntity
from .scene_cache import scenes_refreshed_signal

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# Colour temperature the main-panel entity falls back to when switched on with
# nothing to go on — no service-call attributes and no restored previous value.
# Mid-range neutral white rather than either extreme.
MAIN_LIGHT_ON_KELVIN = 4000

# The H1232 main panel takes a 1-100 brightness over ptReal.
MAIN_PANEL_BRIGHTNESS_RANGE = (1, 100)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee lights from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[LightEntity] = []

    # Get per-device segment modes
    device_modes = entry.options.get(CONF_SEGMENT_MODE_BY_DEVICE, {})

    # Check if scenes are enabled in options
    enable_scenes = entry.options.get(CONF_ENABLE_SCENES, DEFAULT_ENABLE_SCENES)

    for device in coordinator.devices.values():
        # Only create light entities for actual lights — appliances (heaters,
        # fans, dehumidifiers, purifiers, plugs) are surfaced by their own
        # platforms. Without this filter, e.g. an H7150 dehumidifier would
        # appear as a light bulb (issue #54).
        if device.is_light_device and device.supports_power:
            entities.append(GoveeLightEntity(coordinator, device, enable_scenes))
            if device.sku.upper() in MAIN_LIGHT_TOGGLE_SKUS:
                # The entity above stays as the whole-fixture control —
                # powerSwitch is all-or-nothing and kills the ring too. This
                # second entity drives JUST the main downlight panel,
                # switching it off by writing black to the whole-device colour
                # channel rather than powerSwitch, so the ring can stay lit.
                # See GoveeMainLightEntity in this file (issue #131).
                entities.append(GoveeMainLightEntity(coordinator, device))
            if device.sku.upper() in PTREAL_MAIN_PANEL_BIT:
                # H1232 Ceiling Light Pro: a separate main-panel entity driven
                # over ptReal, alongside the ring segments created elsewhere
                # in this function (issue #223).
                entities.append(GoveeMainPanelLight(coordinator, device))
            if device.sku.upper() in PTREAL_LIGHT_ZONE_SKUS:
                # H1252: the ring as one light, next to the main panel above
                # (issue #223).
                entities.append(GoveeRingLight(coordinator, device))

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

    # Auxiliary subclasses that need non-LAN commands opt out below.
    _allow_lan_availability = True

    @property
    def available(self) -> bool:
        """Keep the whole-device light usable while its LAN transport is healthy."""
        if self._allow_lan_availability and not self._device.is_group:
            lan = self.coordinator.get_transport_health(self._device_id, "lan")
            if lan is not None and lan.is_available and self.device_state is not None:
                return True
        return super().available

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
        return ha_to_device_brightness((self._brightness_min, self._brightness_max), ha_brightness)

    def _device_to_ha_brightness(self, device_brightness: int) -> int:
        """Convert device brightness to HA range (0-255), respecting min."""
        return device_to_ha_brightness((self._brightness_min, self._brightness_max), device_brightness)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on with optional parameters."""
        # Handle effect (scene activation)
        if ATTR_EFFECT in kwargs:
            effect_name = kwargs[ATTR_EFFECT]
            scene_info = self._effect_to_scene.get(effect_name)
            if scene_info is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_effect",
                    translation_placeholders={
                        "effect": str(effect_name),
                        "device": self._device.name,
                    },
                )
            scene_id, scene_name = scene_info
            await self._async_send_command(SceneCommand(scene_id=scene_id, scene_name=scene_name))
            # The scene turns the light on and sets its colours, but not the
            # brightness, so a requested brightness goes after it
            if ATTR_BRIGHTNESS in kwargs:
                device_brightness = self._ha_to_device_brightness(kwargs[ATTR_BRIGHTNESS])
                await self._async_send_command(BrightnessCommand(brightness=device_brightness))
            return

        # Handle brightness
        if ATTR_BRIGHTNESS in kwargs:
            ha_brightness = kwargs[ATTR_BRIGHTNESS]
            device_brightness = self._ha_to_device_brightness(ha_brightness)
            await self._async_send_command(BrightnessCommand(brightness=device_brightness))

        # Handle RGB color
        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            color = RGBColor(r=r, g=g, b=b)
            await self._async_send_command(ColorCommand(color=color))

        # Handle color temperature
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            await self._async_send_command(ColorTempCommand(kelvin=kelvin))

        # Only send power command if light is off or no attributes were set
        has_attribute = any(k in kwargs for k in (ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ATTR_COLOR_TEMP_KELVIN))
        if not has_attribute or not self.is_on:
            await self._async_send_command(PowerCommand(power_on=True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._async_send_command(PowerCommand(power_on=False))

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
                    brightness = self._ha_to_device_brightness(last_state.attributes["brightness"])
                self.coordinator.restore_group_state(self._device_id, power, brightness)

        # Load scenes for effect support (skip group devices - no scene API support)
        if self._enable_scenes and not self._device.is_group:
            scenes = await self.coordinator.async_get_scenes(self._device_id)
            if scenes:
                self._build_effect_mapping(scenes)
            # A "Refresh scenes" press rebuilds the effect list too.
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    scenes_refreshed_signal(self._device_id),
                    self._handle_scenes_refreshed,
                )
            )

    @callback
    def _handle_scenes_refreshed(self, scenes: list[dict[str, Any]], diy_scenes: list[dict[str, Any]]) -> None:
        """Rebuild the effect list from a refreshed catalog; an empty one keeps it."""
        if not scenes:
            return
        self._build_effect_mapping(scenes)
        self.async_write_ha_state()


class GoveeMainLightEntity(GoveeLightEntity):
    """The main downlight panel of a two-zone Ceiling Light Pro (issue #131).

    These fixtures have a central downlight panel plus an RGBIC ring, and the
    obvious capabilities can't separate them: ``mainLightToggle`` is inert, and
    ``powerSwitch`` is whole-fixture — it kills the ring too, and leaves the
    firmware in a state where any later light command silently wakes the panel
    back up (the "segments turn the main light on" coupling).

    The two zones actually sit on different channels:

    * **Ring** — ``segmentedColorRgb``, an overlay that wins for its segments.
    * **Main panel** — the whole-device colour/CCT/brightness channel, which
      drives the ENTIRE fixture and so wipes that overlay.

    So this entity switches the panel via the colour channel: black darkens it
    while leaving ``powerSwitch`` on, and the ring can then be lit from its own
    channel with the panel staying genuinely dark. Since every main-channel
    write clobbers the ring, each one re-asserts the ring afterwards.

    Everything else — brightness scaling, colour properties, colour-mode
    selection — is inherited unchanged from ``GoveeLightEntity``. Only on/off
    differs, so only on/off is overridden. Scenes are disabled because they are
    a whole-fixture concept and belong to the master entity.
    """

    # Distinct from the switch platform's ``govee_main_light`` key, which
    # belongs to the (inert on these SKUs) mainLightToggle capability.
    _attr_translation_key = "govee_main_light_panel"
    # Panel actions reassert ring segments, which have no LAN representation.
    _allow_lan_availability = False

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        """Initialize the main-panel entity."""
        super().__init__(coordinator, device, enable_scenes=False)
        # The parent names itself after the device (_attr_name = None), and HA
        # returns _attr_name before it looks at the translation key — without
        # this the panel would carry the bare device name too.
        del self._attr_name
        self._attr_unique_id = f"{device.device_id}{SUFFIX_MAIN_LIGHT_TOGGLE}"

        # What to return to on turn_on. Only ever holds a non-black colour.
        self._last_on_kelvin: int | None = None
        self._last_on_rgb: tuple[int, int, int] | None = None

    @property
    def is_on(self) -> bool | None:
        """Return True when the panel is lit.

        Colour temperature is checked before colour because the two channels
        are mutually exclusive on this firmware: writing a colour clears
        ``colorTemperatureK``, while writing a colour temperature leaves the
        stale RGB behind. Confirmed on an H1270 — a panel lit at 2700K reported
        that colour temperature, and it became ``None`` the instant black was
        written. Without this ordering a CCT-lit panel whose last colour was
        black would wrongly read as off.
        """
        state = self.device_state
        if state is None:
            return None
        if not state.power_state:
            return False
        if state.color_temp_kelvin:
            return True
        # Black on the colour channel is how this entity switches the panel off.
        return state.color is None or state.color.as_tuple != (0, 0, 0)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Light the panel, then restore the ring the write just clobbered.

        Whichever path is taken must end on a non-black colour, or the panel
        would stay dark while the entity claimed to be on.
        """
        if ATTR_BRIGHTNESS in kwargs:
            device_brightness = self._ha_to_device_brightness(kwargs[ATTR_BRIGHTNESS])
            await self._async_send_command(BrightnessCommand(brightness=device_brightness))

        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            if (r, g, b) == (0, 0, 0):
                # Asking for black is asking for off.
                await self.async_turn_off()
                return
            await self._async_send_command(ColorCommand(color=RGBColor(r=r, g=g, b=b)))
            self._last_on_rgb = (r, g, b)
            self._last_on_kelvin = None
        elif ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            await self._async_send_command(ColorTempCommand(kelvin=kelvin))
            self._last_on_kelvin = kelvin
            self._last_on_rgb = None
        elif not self.is_on:
            # Coming back from black with nothing specified — restore whatever
            # it was last lit with, falling back to neutral white.
            if self._last_on_rgb is not None:
                r, g, b = self._last_on_rgb
                await self._async_send_command(ColorCommand(color=RGBColor(r=r, g=g, b=b)))
            else:
                await self._async_send_command(
                    ColorTempCommand(kelvin=self._last_on_kelvin or MAIN_LIGHT_ON_KELVIN),
                )

        await self.coordinator.async_reassert_segments(self._device_id)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Darken the panel via the colour channel, never ``powerSwitch``.

        Skips the write entirely if the fixture is already off or a power-off
        is in flight, mirroring the segment entities' guard (issue #16). This
        matters more here than it does for a segment: an area-targeted
        turn_off fans out to the master entity (which sends powerSwitch off)
        and to this one at the same time, and a colour write plus the ring
        re-assert landing *after* that power-off would wake the panel straight
        back up — turning the room off would leave the light on.
        """
        # Best-effort ordering, not a guarantee. The yield gives a concurrent
        # PowerCommand a chance to set the pending flag first, and
        # _pending_power_off.add() does run before the first await in
        # async_control_device — but HA promises nothing about which entity's
        # coroutine is scheduled first on an area-targeted turn_off. If this
        # one wins the race, the guard below sees no pending power-off and the
        # panel can come back on. This narrows an existing race; it does not
        # close it.
        await asyncio.sleep(0)

        state = self.device_state
        device_already_off = state is not None and not state.power_state
        if device_already_off or self.coordinator.is_power_off_pending(self._device_id):
            _LOGGER.debug(
                "Skipping main light turn_off for %s (already off or power-off pending)",
                self._device_id,
            )
            self.async_write_ha_state()
            return

        if state is not None:
            if state.color_temp_kelvin:
                self._last_on_kelvin = state.color_temp_kelvin
                self._last_on_rgb = None
            elif state.color is not None and state.color.as_tuple != (0, 0, 0):
                self._last_on_rgb = state.color.as_tuple
                self._last_on_kelvin = None

        await self._async_send_command(ColorCommand(color=RGBColor(r=0, g=0, b=0)))

        # Black wipes the ring too, so put it back — this is what leaves the
        # ring lit while the panel stays dark. The write was black, so an
        # all-black ring needs no replay: it is already dark either way.
        await self.coordinator.async_reassert_segments(self._device_id, wrote_black=True)
        self.async_write_ha_state()

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Persist the colour to return to, independently of the state attrs.

        This cannot ride on the restored state attributes: Home Assistant
        strips ``color_temp_kelvin``/``rgb_color`` from a light whose state is
        ``off``, which is precisely when this value is needed. Storing it as
        extra data is the only way it survives a restart while the panel is
        off — otherwise every restart-then-turn-on would fall back to
        ``MAIN_LIGHT_ON_KELVIN`` and quietly lose the user's colour.
        """
        return RestoredExtraData(
            {
                "last_on_kelvin": self._last_on_kelvin,
                "last_on_rgb": list(self._last_on_rgb) if self._last_on_rgb else None,
            }
        )

    async def async_added_to_hass(self) -> None:
        """Restore the colour to return to when switched back on."""
        await super().async_added_to_hass()
        last_extra = await self.async_get_last_extra_data()
        if not last_extra:
            return
        stored = last_extra.as_dict()
        kelvin = stored.get("last_on_kelvin")
        rgb = stored.get("last_on_rgb")
        if kelvin:
            self._last_on_kelvin = int(kelvin)
        elif rgb and tuple(rgb) != (0, 0, 0):
            self._last_on_rgb = (int(rgb[0]), int(rgb[1]), int(rgb[2]))


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
        return ha_to_device_brightness((self._brightness_min, self._brightness_max), ha_brightness)

    def _device_to_ha_brightness(self, device_brightness: int) -> int:
        return device_to_ha_brightness((self._brightness_min, self._brightness_max), device_brightness)

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

    async def _set_toggle(self, enabled: bool) -> None:
        """Switch the nightlight toggle; raise if the command is not accepted."""
        await self._async_send_command(
            ToggleCommand(toggle_instance=INSTANCE_NIGHT_LIGHT, enabled=enabled),
        )
        state = self.device_state
        if state is not None:
            state.toggles[INSTANCE_NIGHT_LIGHT] = enabled
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the nightlight on with optional brightness/colour."""
        if ATTR_BRIGHTNESS in kwargs:
            device_brightness = self._ha_to_device_brightness(kwargs[ATTR_BRIGHTNESS])
            await self._async_send_command(BrightnessCommand(brightness=device_brightness))

        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            await self._async_send_command(ColorCommand(color=RGBColor(r=r, g=g, b=b)))

        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            await self._async_send_command(ColorTempCommand(kelvin=kwargs[ATTR_COLOR_TEMP_KELVIN]))

        has_attribute = any(k in kwargs for k in (ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ATTR_COLOR_TEMP_KELVIN))
        if not has_attribute or not self.is_on:
            await self._set_toggle(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the nightlight off."""
        await self._set_toggle(False)


class GoveeMainPanelLight(GoveeEntity, LightEntity, RestoreEntity):
    """The Ceiling Light Pro's main downlight panel (H1232/H1252, issue #223).

    This fixture's 17th internal segment is a separate main panel, not part
    of the RGBIC ring, and the Platform API cannot reach it at all — colour,
    colour temperature and brightness only land through the masked ptReal
    frames in ``coordinator.async_set_main_panel``. On/off uses the
    ``mainLightToggle`` capability over the normal command path (on the
    H1252 the coordinator sends it as a ptReal zone frame). The API never
    reports the panel's colour or brightness, so like the ring segments
    (``GoveeSegmentEntity``) this entity keeps purely optimistic local state
    persisted via ``RestoreEntity``.
    """

    _attr_translation_key = "govee_main_light_panel"
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.RGB}
    _attr_min_color_temp_kelvin = PTREAL_ZONE_MIN_KELVIN
    _attr_max_color_temp_kelvin = PTREAL_ZONE_MAX_KELVIN

    _toggle_instance = INSTANCE_MAIN_LIGHT_TOGGLE
    _unique_suffix = SUFFIX_MAIN_PANEL

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        """Initialize the zone entity."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}{self._unique_suffix}"
        self._rgb_color: tuple[int, int, int] = (255, 255, 255)
        self._color_temp_kelvin: int | None = None
        self._color_mode = ColorMode.RGB
        self._brightness: int = 255

    async def _async_set_zone(self, *, rgb: RGBColor | None, brightness: int | None, kelvin: int | None) -> bool:
        """Send the zone's ptReal frames through the coordinator."""
        return await self.coordinator.async_set_main_panel(
            self._device_id, rgb=rgb, brightness=brightness, kelvin=kelvin
        )

    @property
    def is_on(self) -> bool | None:
        """Return True when the zone's toggle is on."""
        state = self.device_state
        if state is None:
            return None
        return state.toggles.get(self._toggle_instance)

    @property
    def brightness(self) -> int:
        """Return the optimistic zone brightness (0-255)."""
        return self._brightness

    @property
    def color_mode(self) -> ColorMode:
        """Return whether the zone was last set to a colour or a colour temperature."""
        return self._color_mode

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        """Return the optimistic zone RGB colour."""
        return self._rgb_color

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the optimistic zone colour temperature."""
        return self._color_temp_kelvin

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Light the zone, sending colour, colour temperature or brightness over ptReal if given."""
        if not self.is_on:
            # The coordinator mirrors the sent toggle into state.toggles.
            await self._async_send_command(
                ToggleCommand(toggle_instance=self._toggle_instance, enabled=True),
            )

        rgb: RGBColor | None = None
        kelvin: int | None = None
        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            rgb = RGBColor(r=r, g=g, b=b)
        elif ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = max(PTREAL_ZONE_MIN_KELVIN, min(PTREAL_ZONE_MAX_KELVIN, int(kwargs[ATTR_COLOR_TEMP_KELVIN])))

        ha_brightness: int | None = kwargs.get(ATTR_BRIGHTNESS)
        device_brightness: int | None = None
        if ha_brightness is not None:
            device_brightness = ha_to_device_brightness(MAIN_PANEL_BRIGHTNESS_RANGE, ha_brightness)

        if rgb is not None or kelvin is not None or device_brightness is not None:
            if not await self._async_set_zone(rgb=rgb, brightness=device_brightness, kelvin=kelvin):
                raise self._command_failed()

        # Only keep the optimistic values once the zone accepted them.
        if rgb is not None:
            self._rgb_color = rgb.as_tuple
            self._color_mode = ColorMode.RGB
        elif kelvin is not None:
            self._color_temp_kelvin = kelvin
            self._color_mode = ColorMode.COLOR_TEMP
        if ha_brightness is not None:
            self._brightness = ha_brightness

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the zone off via its toggle."""
        await self._async_send_command(
            ToggleCommand(toggle_instance=self._toggle_instance, enabled=False),
        )
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore the optimistic colour, colour temperature and brightness from the last known state."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state:
            if last_state.attributes.get("brightness"):
                self._brightness = last_state.attributes["brightness"]
            if last_state.attributes.get("rgb_color"):
                r, g, b = last_state.attributes["rgb_color"]
                self._rgb_color = (r, g, b)
            if last_state.attributes.get("color_temp_kelvin"):
                self._color_temp_kelvin = int(last_state.attributes["color_temp_kelvin"])
            if last_state.attributes.get("color_mode") == ColorMode.COLOR_TEMP:
                self._color_mode = ColorMode.COLOR_TEMP


class GoveeRingLight(GoveeMainPanelLight):
    """The Ceiling Light Pro's RGBIC ring as one light (H1252, issue #223).

    The app only drives the ring together with the panel; ptReal reaches it
    on its own. On/off goes through ``backgroundLightToggle`` (sent as the
    ``33 30 01`` zone frame), colour, colour temperature and brightness as
    one masked frame over all ring segments (``coordinator.async_set_ring``).
    """

    _attr_translation_key = "govee_ring_light"
    _toggle_instance = INSTANCE_BACKGROUND_LIGHT_TOGGLE
    _unique_suffix = SUFFIX_RING

    async def _async_set_zone(self, *, rgb: RGBColor | None, brightness: int | None, kelvin: int | None) -> bool:
        """Send the ring's ptReal frames through the coordinator."""
        return await self.coordinator.async_set_ring(self._device_id, rgb=rgb, brightness=brightness, kelvin=kelvin)
