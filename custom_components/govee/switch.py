"""Switch platform for Govee integration.

Provides switch entities for:
- Smart plugs (on/off control)
- Night light toggle (for lights with night light mode)
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    SUFFIX_BACKGROUND_LIGHT,
    SUFFIX_BOTTOM_LIGHT,
    SUFFIX_DREAMVIEW,
    SUFFIX_HEATER_AUTO_STOP,
    SUFFIX_LIGHT_ZONE,
    SUFFIX_MAIN_LIGHT,
    SUFFIX_MUSIC_MODE,
    SUFFIX_NEBULA_LIGHT,
    SUFFIX_NIGHT_LIGHT,
    SUFFIX_SIDE_LIGHT,
    SUFFIX_MQTT_OUTLET,
    SUFFIX_SOCKET,
)
from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import (
    GoveeDevice,
    MusicModeCommand,
    PowerCommand,
    TemperatureSettingCommand,
    ToggleCommand,
    create_night_light_command,
)
from .models.device import (
    INSTANCE_BACKGROUND_LIGHT_TOGGLE,
    INSTANCE_MAIN_LIGHT_TOGGLE,
    INSTANCE_THERMOSTAT_TOGGLE,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# Named per-part light toggles (issues #114, #126): capability instance ->
# (translation_key, unique_id suffix, icon). Devices covered so far: ceiling-fan
# lights (H1310/H1370: main/background) and the H60B3 uplighter floor lamp
# (nebula/side/bottom). Unknown ``<name>LightToggle`` instances are logged and
# skipped so a new SKU surfaces in debug logs instead of silently missing.
NAMED_LIGHT_TOGGLE_SPECS: dict[str, tuple[str, str, str]] = {
    INSTANCE_MAIN_LIGHT_TOGGLE: (
        "govee_main_light",
        SUFFIX_MAIN_LIGHT,
        "mdi:ceiling-light",
    ),
    INSTANCE_BACKGROUND_LIGHT_TOGGLE: (
        "govee_background_light",
        SUFFIX_BACKGROUND_LIGHT,
        "mdi:wall-sconce-flat",
    ),
    "nebulaLightToggle": (
        "govee_nebula_light",
        SUFFIX_NEBULA_LIGHT,
        "mdi:shimmer",
    ),
    "sideLightToggle": (
        "govee_side_light",
        SUFFIX_SIDE_LIGHT,
        "mdi:wall-sconce",
    ),
    "bottomLightToggle": (
        "govee_bottom_light",
        SUFFIX_BOTTOM_LIGHT,
        "mdi:floor-lamp",
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee switches from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[SwitchEntity] = []

    for device in coordinator.devices.values():
        # Probe thermometers only report when polled, so the poll needs an
        # explicit, restorable on/off that the owner controls.
        if device.is_probe_thermometer:
            entities.append(GoveeProbeLivePollingSwitch(coordinator, device))
            continue

        # Create switch for smart plugs (power on/off)
        if device.is_plug and device.supports_power:
            entities.append(GoveePlugSwitchEntity(coordinator, device))

        # Create switch for night light toggle (lights with night light mode).
        # Appliances whose nightlight has brightness/colour get a richer
        # nightlight *light* entity instead, so skip the plain switch for them
        # to avoid duplicate on/off controls (issue #114).
        if device.supports_night_light and not device.has_nightlight_light:
            entities.append(GoveeNightLightSwitchEntity(coordinator, device))

        # Create switch for music mode toggle
        # STRUCT-based devices use REST API (no MQTT required)
        # Legacy devices use BLE passthrough via MQTT
        # Skip for group devices - groups don't support music mode (no MQTT topic)
        if device.is_group:
            _LOGGER.debug(
                "Skipping music mode/DreamView switches for group device %s "
                "(groups don't support these features)",
                device.name,
            )
        elif device.has_struct_music_mode:
            # STRUCT-based music mode - uses REST API, no MQTT required
            entities.append(
                GoveeMusicModeSwitchEntity(coordinator, device, use_rest_api=True)
            )
            _LOGGER.debug("Created STRUCT music mode switch entity for %s", device.name)
        elif device.supports_music_mode:
            # Legacy BLE-based music mode - availability gated on MQTT at runtime
            entities.append(
                GoveeMusicModeSwitchEntity(coordinator, device, use_rest_api=False)
            )
            _LOGGER.debug("Created BLE music mode switch entity for %s", device.name)

        # Create switch for heater auto-stop toggle. Two shapes exist:
        #  - H7130 exposes a dedicated ``thermostatToggle`` capability, or
        #  - H713C / similar carry ``autoStop`` as a field of the
        #    ``targetTemperature`` STRUCT. Both get the same switch entity;
        #    the entity picks the right command at turn_on/off time
        #    (issue #29).
        if (
            device.supports_thermostat_toggle
            or device.supports_temperature_setting_auto_stop
        ):
            entities.append(GoveeAutoStopSwitchEntity(coordinator, device))
            _LOGGER.debug("Created auto-stop switch entity for %s", device.name)

        # Minimal power control for heaters that don't yet have a
        # climate platform. Humidifiers/dehumidifiers now have their own
        # dedicated platform (issue #54); a climate platform for heaters
        # is still pending.
        if (
            device.supports_power
            and not device.is_group
            and (device.is_heater or device.is_kettle or device.is_aroma_diffuser)
        ):
            entities.append(GoveeAppliancePowerSwitchEntity(coordinator, device))

        # Create switch for DreamView (Movie Mode) toggle
        # Skip for group devices - groups don't support DreamView
        # DreamView uses BLE passthrough via MQTT (REST API returns 400 for some devices)
        if device.supports_dreamview and not device.is_group:
            entities.append(GoveeDreamViewSwitchEntity(coordinator, device))
            _LOGGER.debug(
                "Created DreamView switch entity for %s (REST-first with BLE fallback)",
                device.name,
            )

        # Per-zone on/off switches for multi-zone lamps (e.g. H60B2's three
        # light{1,2,3}Toggle zones — issue #104). Distinct from RGBIC color
        # segments, which the segment platform owns.
        if not device.is_group:
            for zone_index, instance in enumerate(device.light_toggle_instances):
                entities.append(
                    GoveeLightZoneSwitchEntity(
                        coordinator, device, instance, zone_index
                    )
                )
                _LOGGER.debug(
                    "Created light zone switch %d (%s) for %s",
                    zone_index + 1,
                    instance,
                    device.name,
                )

            # Per-outlet switches for multi-socket plugs (e.g. H5089's two
            # socketToggle{N} outlets). These return live state on poll, unlike
            # the optimistic light zones (issue #114).
            for socket_index, instance in enumerate(device.socket_toggle_instances):
                entities.append(
                    GoveeSocketSwitchEntity(
                        coordinator, device, instance, socket_index
                    )
                )
                _LOGGER.debug(
                    "Created outlet switch %d (%s) for %s",
                    socket_index + 1,
                    instance,
                    device.name,
                )

            # Outlets the Developer API does not expose individually but the
            # AWS IoT `turn` bitmask can drive (H5160/H5161, issue #184).
            for outlet_index in range(device.mqtt_outlet_count):
                entities.append(GoveeMqttOutletSwitchEntity(coordinator, device, outlet_index))
                _LOGGER.debug(
                    "Created MQTT outlet switch %d for %s", outlet_index + 1, device.name
                )

            # Named per-part light toggles — main/background on ceiling-fan
            # lights (H1310/H1370, issue #114), nebula/side/bottom on the
            # H60B3 uplighter (issue #126). Govee returns "" for these on
            # poll, so they are optimistic + RestoreEntity like the zones.
            for instance in device.named_light_toggle_instances:
                spec = NAMED_LIGHT_TOGGLE_SPECS.get(instance)
                if spec is None:
                    _LOGGER.debug(
                        "Unknown named light toggle %s on %s (%s) — skipping",
                        instance,
                        device.name,
                        device.sku,
                    )
                    continue
                translation_key, suffix, icon = spec
                entities.append(
                    GoveeNamedLightSwitchEntity(
                        coordinator, device, instance, translation_key, suffix, icon
                    )
                )
                _LOGGER.debug(
                    "Created named light switch %s for %s", instance, device.name
                )

    async_add_entities(entities)
    _LOGGER.debug("Set up %d Govee switch entities", len(entities))


class GoveeProbeLivePollingSwitch(GoveeEntity, SwitchEntity, RestoreEntity):
    """Arms live polling for a probe thermometer (H5192).

    Probe thermometers answer only when asked, so this switch is what makes the
    entities update at all. It is off by default and restores across restarts:
    polling every 30 seconds around the clock would drain a battery device that
    is only interesting while something is cooking.

    Turning it on fires one immediate read so the entities populate without
    waiting for the first tick.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "probe_live_polling"

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        """Initialize the live polling switch."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_probe_live_polling"

    async def async_added_to_hass(self) -> None:
        """Restore the previous polling state and re-arm the timer if needed."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state == STATE_ON:
            self.coordinator.set_probe_polling(self._device_id, True)

    @property
    def is_on(self) -> bool:
        """Return True while polling is armed."""
        return self.coordinator.is_probe_polling(self._device_id)

    @property
    def available(self) -> bool:
        """Always available: this is a local toggle, not a device capability."""
        return True

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Arm polling and read once immediately."""
        self.coordinator.set_probe_polling(self._device_id, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disarm polling."""
        self.coordinator.set_probe_polling(self._device_id, False)
        self.async_write_ha_state()


class GoveePlugSwitchEntity(GoveeEntity, SwitchEntity):
    """Govee smart plug switch entity.

    Controls power state for Govee smart plugs.
    """

    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_translation_key = "govee_plug"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the plug switch entity."""
        super().__init__(coordinator, device)

        # Use device name as entity name
        self._attr_name = None

    @property
    def is_on(self) -> bool | None:
        """Return True if plug is on."""
        state = self.device_state
        return state.power_state if state else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the plug on."""
        await self.coordinator.async_control_device(
            self._device_id,
            PowerCommand(power_on=True),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the plug off."""
        await self.coordinator.async_control_device(
            self._device_id,
            PowerCommand(power_on=False),
        )


class GoveeNightLightSwitchEntity(GoveeEntity, SwitchEntity, RestoreEntity):
    """Govee night light toggle switch entity.

    Controls night light mode for devices that support it.
    Uses optimistic state since API may not return night light status.
    Uses RestoreEntity to persist state across HA restarts.
    """

    _attr_translation_key = "govee_night_light"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the night light switch entity."""
        super().__init__(coordinator, device)

        # Unique ID for night light switch
        self._attr_unique_id = f"{device.device_id}{SUFFIX_NIGHT_LIGHT}"

        # Optimistic state
        self._is_on = False

    async def async_added_to_hass(self) -> None:
        """Restore state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            self._is_on = last_state.state == "on"

    @property
    def is_on(self) -> bool:
        """Return True if night light is on."""
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn night light on."""
        success = await self.coordinator.async_control_device(
            self._device_id,
            create_night_light_command(enabled=True),
        )
        if success:
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn night light off."""
        success = await self.coordinator.async_control_device(
            self._device_id,
            create_night_light_command(enabled=False),
        )
        if success:
            self._is_on = False
            self.async_write_ha_state()


class GoveeLightZoneSwitchEntity(GoveeEntity, SwitchEntity, RestoreEntity):
    """On/off switch for one independently switchable light zone (issue #104).

    Multi-zone fixtures like the H60B2 3-segment lamp expose each zone as a
    ``light{N}Toggle`` capability. Govee doesn't reliably report the per-zone
    state on poll, so state is optimistic and restored across restarts via
    RestoreEntity — the same approach as the night-light switch.
    """

    _attr_translation_key = "govee_light_zone"
    _attr_icon = "mdi:lightbulb-multiple"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        toggle_instance: str,
        zone_index: int,
    ) -> None:
        """Initialize the light zone switch entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this switch controls.
            toggle_instance: The ``light{N}Toggle`` capability instance.
            zone_index: Zero-based zone index (for unique_id + name).
        """
        super().__init__(coordinator, device)

        self._toggle_instance = toggle_instance
        self._zone_index = zone_index
        self._attr_unique_id = f"{device.device_id}{SUFFIX_LIGHT_ZONE}{zone_index}"
        self._attr_translation_placeholders = {"zone": str(zone_index + 1)}

        # Optimistic state — Govee does not report per-zone state on poll.
        self._is_on = False

    async def async_added_to_hass(self) -> None:
        """Restore optimistic state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            self._is_on = last_state.state == "on"

    @property
    def is_on(self) -> bool:
        """Return True if the zone is on (optimistic)."""
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light zone on."""
        success = await self.coordinator.async_control_device(
            self._device_id,
            ToggleCommand(toggle_instance=self._toggle_instance, enabled=True),
        )
        if success:
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light zone off."""
        success = await self.coordinator.async_control_device(
            self._device_id,
            ToggleCommand(toggle_instance=self._toggle_instance, enabled=False),
        )
        if success:
            self._is_on = False
            self.async_write_ha_state()


class GoveeSocketSwitchEntity(GoveeEntity, SwitchEntity):
    """One independently switchable outlet on a multi-socket plug (issue #114).

    Outlet extenders like the H5089 expose each socket as a ``socketToggle{N}``
    capability and return a live 0/1 value on poll, so this switch reads real
    state (unlike the optimistic light-zone switch). The command result is
    written back to coordinator state for immediate UI feedback; the next poll
    reconfirms it.
    """

    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_translation_key = "govee_socket"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        toggle_instance: str,
        socket_index: int,
    ) -> None:
        """Initialize the outlet switch entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this switch controls.
            toggle_instance: The ``socketToggle{N}`` capability instance.
            socket_index: Zero-based outlet index (for unique_id + name).
        """
        super().__init__(coordinator, device)

        self._toggle_instance = toggle_instance
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SOCKET}{socket_index}"
        self._attr_translation_placeholders = {"socket": str(socket_index + 1)}

    @property
    def is_on(self) -> bool | None:
        """Return True if the outlet is on (live state)."""
        state = self.device_state
        return state.toggles.get(self._toggle_instance) if state else None

    async def _set(self, enabled: bool) -> None:
        success = await self.coordinator.async_control_device(
            self._device_id,
            ToggleCommand(toggle_instance=self._toggle_instance, enabled=enabled),
        )
        if success:
            state = self.device_state
            if state is not None:
                state.toggles[self._toggle_instance] = enabled
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on."""
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the outlet off."""
        await self._set(False)


class GoveeMqttOutletSwitchEntity(GoveeEntity, SwitchEntity, RestoreEntity):
    """One outlet of a multi-outlet plug driven over AWS IoT only (issue #184).

    The H5160/H5161 advertise a single ``powerSwitch`` to the Developer API,
    so the only per-outlet control is homebridge-govee's bitmask ``turn`` on
    the account MQTT session. Optimistic and restored across restarts until
    the plug's own per-outlet readback is decoded; unavailable without the
    AWS IoT session because nothing else can carry the command.
    """

    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_translation_key = "govee_socket"
    _attr_assumed_state = True

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice, outlet_index: int) -> None:
        """Initialize the outlet switch entity."""
        super().__init__(coordinator, device)
        self._outlet_index = outlet_index
        self._toggle_key = f"outlet{outlet_index + 1}"
        self._attr_unique_id = f"{device.device_id}{SUFFIX_MQTT_OUTLET}{outlet_index}"
        self._attr_translation_placeholders = {"socket": str(outlet_index + 1)}
        self._is_on = False

    async def async_added_to_hass(self) -> None:
        """Restore optimistic state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            self._is_on = last_state.state == "on"

    @property
    def available(self) -> bool:
        """Only the AWS IoT session can carry the command."""
        return super().available and self.coordinator.mqtt_connected

    @property
    def is_on(self) -> bool:
        """Return True if the outlet is on (optimistic, shared via state.toggles)."""
        state = self.device_state
        if state is not None:
            live = state.toggles.get(self._toggle_key)
            if live is not None:
                return live
        return self._is_on

    async def _set(self, enabled: bool) -> None:
        if await self.coordinator.async_set_mqtt_outlet(self._device_id, self._outlet_index, enabled):
            self._is_on = enabled
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on."""
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the outlet off."""
        await self._set(False)


class GoveeNamedLightSwitchEntity(GoveeEntity, SwitchEntity, RestoreEntity):
    """On/off switch for a named light part — main/background on ceiling-fan
    lights (H1310/H1370, issue #114), nebula/side/bottom on the H60B3
    uplighter floor lamp (issue #126).

    Govee returns "" for these toggles on poll, so state is optimistic and
    restored across restarts via RestoreEntity (the same approach as the
    light-zone and night-light switches).
    """

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        toggle_instance: str,
        translation_key: str,
        unique_suffix: str,
        icon: str,
    ) -> None:
        """Initialize the named light toggle switch entity."""
        super().__init__(coordinator, device)

        self._toggle_instance = toggle_instance
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{device.device_id}{unique_suffix}"
        self._attr_icon = icon

        # Optimistic state — Govee does not report these toggles on poll.
        self._is_on = False

    async def async_added_to_hass(self) -> None:
        """Restore optimistic state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            self._is_on = last_state.state == "on"

    @property
    def is_on(self) -> bool:
        """Return True if the light zone is on.

        Prefers the shared state when the device has reported the toggle —
        ceiling-fan combos push their lights as ``aa 42`` / ``aa 36`` frames
        over AWS IoT (issue #181) — and falls back to the restored optimistic
        value otherwise.
        """
        state = self.device_state
        if state is not None:
            live = state.toggles.get(self._toggle_instance)
            if live is not None:
                return live
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light zone on."""
        success = await self.coordinator.async_control_device(
            self._device_id,
            ToggleCommand(toggle_instance=self._toggle_instance, enabled=True),
        )
        if success:
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light zone off."""
        success = await self.coordinator.async_control_device(
            self._device_id,
            ToggleCommand(toggle_instance=self._toggle_instance, enabled=False),
        )
        if success:
            self._is_on = False
            self.async_write_ha_state()


class GoveeMusicModeSwitchEntity(GoveeEntity, SwitchEntity):
    """Govee music mode toggle switch entity.

    Controls music reactive mode for devices that support it.

    For STRUCT-based devices (use_rest_api=True):
    - Uses REST API with structured payload
    - No MQTT required
    - Sends musicMode command with mode and sensitivity

    For legacy devices (use_rest_api=False):
    - Uses BLE passthrough via MQTT
    - Requires MQTT connection
    - Sends simple on/off toggle

    Uses optimistic state since API may not return music mode status.
    """

    _attr_translation_key = "govee_music_mode"
    _attr_icon = "mdi:music"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        use_rest_api: bool = False,
    ) -> None:
        """Initialize the music mode switch entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this switch controls.
            use_rest_api: True to use REST API (STRUCT), False for BLE passthrough.
        """
        super().__init__(coordinator, device)

        self._use_rest_api = use_rest_api

        # Unique ID for music mode switch
        self._attr_unique_id = f"{device.device_id}{SUFFIX_MUSIC_MODE}"

        # Optimistic state
        self._is_on = False

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        For BLE passthrough, requires MQTT connection.
        For REST API, only requires device to be online.
        """
        if not self._use_rest_api and not self.coordinator.mqtt_connected:
            return False
        return super().available

    @property
    def is_on(self) -> bool:
        """Return True if music mode is on."""
        state = self.device_state
        if state and state.music_mode_enabled is not None:
            return state.music_mode_enabled
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn music mode on."""
        if self._use_rest_api:
            # Use REST API with STRUCT payload
            # Get current sensitivity and mode from state, or use defaults
            state = self.device_state
            sensitivity = 50
            # Default to the first mode the device advertises. The old
            # hard-coded 1 ("Rhythm" on most strips) is not a valid value on
            # every SKU — the H6022 offers 3/4/5/6 and rejects 1 with
            # "Parameter value out of range" (issue #186).
            valid_modes = [
                int(opt["value"])
                for opt in self._device.get_music_mode_options()
                if isinstance(opt.get("value"), int)
            ]
            music_mode = valid_modes[0] if valid_modes else 1
            if state:
                if state.music_sensitivity is not None:
                    sensitivity = state.music_sensitivity
                if state.music_mode_value is not None and (
                    not valid_modes or state.music_mode_value in valid_modes
                ):
                    music_mode = state.music_mode_value

            command = MusicModeCommand(
                music_mode=music_mode,
                sensitivity=sensitivity,
                auto_color=1,  # Use automatic colors
            )
            success = await self.coordinator.async_control_device(
                self._device_id,
                command,
            )
        else:
            # Use BLE passthrough via MQTT
            success = await self.coordinator.async_send_music_mode(
                self._device_id,
                enabled=True,
            )

        if success:
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn music mode off.

        Delegates to coordinator which handles REST-first with BLE fallback.
        For STRUCT devices, the coordinator restores the last scene or sends
        a brightness command to cleanly exit music mode.
        For BLE devices, the coordinator sends the explicit off command.
        """
        state = self.device_state
        success = await self.coordinator.async_send_music_mode(
            self._device_id,
            enabled=False,
            last_scene_id=state.last_scene_id if state else None,
            last_scene_name=state.last_scene_name if state else None,
        )
        if success:
            self._is_on = False
            self.async_write_ha_state()


class GoveeDreamViewSwitchEntity(GoveeEntity, SwitchEntity):
    """Govee DreamView (Movie Mode) toggle switch entity.

    Controls DreamView mode for devices that support it (e.g., Immersion TV backlights).
    Uses REST API first, with BLE passthrough via MQTT as fallback for devices
    where REST returns 400 (e.g., H6199).

    DreamView, Music Mode, and Scenes are mutually exclusive on the device.
    When DreamView is turned on, music mode and scene states are cleared.
    """

    _attr_translation_key = "govee_dreamview"
    _attr_icon = "mdi:movie-open"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the DreamView switch entity."""
        super().__init__(coordinator, device)

        # Unique ID for DreamView switch
        self._attr_unique_id = f"{device.device_id}{SUFFIX_DREAMVIEW}"

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        DreamView uses REST API first, with BLE fallback via MQTT.
        Entity is available as long as device is online.
        """
        return super().available

    @property
    def is_on(self) -> bool:
        """Return True if DreamView is on.

        Reads from device state for proper mutual exclusion tracking.
        """
        state = self.device_state
        if state and state.dreamview_enabled is not None:
            return state.dreamview_enabled
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn DreamView on via BLE passthrough.

        This clears music mode and scene states due to mutual exclusion.
        """
        success = await self.coordinator.async_send_dreamview(
            self._device_id,
            enabled=True,
        )
        if success:
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn DreamView off via BLE passthrough."""
        success = await self.coordinator.async_send_dreamview(
            self._device_id,
            enabled=False,
        )
        if success:
            self.async_write_ha_state()


class GoveeAutoStopSwitchEntity(GoveeEntity, SwitchEntity, RestoreEntity):
    """Govee heater auto-stop toggle switch entity.

    Controls the thermostat auto-stop feature on heaters that support it
    (e.g., H7130 with thermostatToggle capability).
    Uses RestoreEntity since API may not reliably return auto-stop status.
    """

    _attr_translation_key = "govee_heater_auto_stop"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the auto-stop switch entity."""
        super().__init__(coordinator, device)

        self._attr_unique_id = f"{device.device_id}{SUFFIX_HEATER_AUTO_STOP}"
        self._is_on = False

    async def async_added_to_hass(self) -> None:
        """Restore state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            self._is_on = last_state.state == "on"

    @property
    def is_on(self) -> bool:
        """Return True if auto-stop is on."""
        state = self.device_state
        if state and state.heater_auto_stop is not None:
            return state.heater_auto_stop == 1
        return self._is_on

    async def _send_auto_stop(self, enabled: bool) -> bool:
        """Dispatch auto-stop using whichever shape the device supports.

        Devices with a dedicated ``thermostatToggle`` capability use a
        ToggleCommand. Devices that carry ``autoStop`` inside the
        ``targetTemperature`` STRUCT need a TemperatureSettingCommand — we
        must send the current target temperature alongside so the write
        doesn't clobber it (issue #29).
        """
        if self._device.supports_thermostat_toggle:
            return await self.coordinator.async_control_device(
                self._device_id,
                ToggleCommand(
                    toggle_instance=INSTANCE_THERMOSTAT_TOGGLE, enabled=enabled
                ),
            )

        state = self.coordinator.get_state(self._device_id)
        current_temp = (
            state.heater_temperature
            if state and state.heater_temperature is not None
            else 20
        )
        return await self.coordinator.async_control_device(
            self._device_id,
            TemperatureSettingCommand(
                temperature=int(current_temp),
                auto_stop=1 if enabled else 0,
            ),
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn auto-stop on."""
        if await self._send_auto_stop(True):
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn auto-stop off."""
        if await self._send_auto_stop(False):
            self._is_on = False
            self.async_write_ha_state()


class GoveeAppliancePowerSwitchEntity(GoveeEntity, SwitchEntity):
    """Power switch for appliances without a dedicated platform.

    Heaters, kettles, and (de)humidifiers no longer appear as lights
    after the issue-#54 filter change; this entity restores basic
    on/off control until the full climate and humidifier platforms
    land. Kettles use this for the on/off regression in issue #63.
    """

    _attr_translation_key = "govee_appliance_power"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the appliance power switch entity."""
        super().__init__(coordinator, device)
        # Use the device's own name as the switch name.
        self._attr_name = None

    @property
    def is_on(self) -> bool | None:
        """Return True if the appliance is on."""
        state = self.device_state
        return state.power_state if state else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the appliance on."""
        await self.coordinator.async_control_device(
            self._device_id,
            PowerCommand(power_on=True),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the appliance off."""
        await self.coordinator.async_control_device(
            self._device_id,
            PowerCommand(power_on=False),
        )
