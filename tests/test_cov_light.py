"""Coverage tests for the light platform.

Fills the gaps the feature-focused suites leave in ``light.py``: the plain
``GoveeLightEntity`` control path (brightness / colour / colour temperature /
power and the raise-on-rejection contract), its state properties without
device state, group-state restoration, the ``GoveeMainLightEntity``
constructor and restore-across-restart plumbing (issue #131), the whole
``GoveeNightLightEntity`` property surface (issue #114), and the platform
setup branch that adds the main-panel entity for ``MAIN_LIGHT_TOGGLE_SKUS``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.light import ColorMode, LightEntityFeature
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoredExtraData

from custom_components.govee import light as light_mod
from custom_components.govee.entity import GoveeEntity
from custom_components.govee.light import (
    MAIN_LIGHT_ON_KELVIN,
    GoveeLightEntity,
    GoveeMainLightEntity,
    GoveeMainPanelLight,
    GoveeRingLight,
    GoveeNightLightEntity,
)
from custom_components.govee.models import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    PowerCommand,
    RGBColor,
    ToggleCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    CAPABILITY_TOGGLE,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_PURIFIER,
    INSTANCE_BACKGROUND_LIGHT_TOGGLE,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_MAIN_LIGHT_TOGGLE,
    INSTANCE_NIGHT_LIGHT,
    INSTANCE_POWER,
    INSTANCE_SEGMENT_COLOR,
)


def _cap(cap_type: str, instance: str, params: dict | None = None) -> GoveeCapability:
    return GoveeCapability(type=cap_type, instance=instance, parameters=params or {})


_POWER = _cap(CAPABILITY_ON_OFF, INSTANCE_POWER)
_BRIGHTNESS = _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS, {"range": {"min": 0, "max": 100}})
_RGB = _cap(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB)
_CT = _cap(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_TEMP, {"range": {"min": 2700, "max": 6500}})
_NIGHTLIGHT = _cap(CAPABILITY_TOGGLE, INSTANCE_NIGHT_LIGHT)


def _device(device_id: str, sku: str, device_type: str, *caps: GoveeCapability, name: str = "Test") -> GoveeDevice:
    return GoveeDevice(device_id=device_id, sku=sku, name=name, device_type=device_type, capabilities=tuple(caps))


def _coordinator(state: GoveeDeviceState | None, *devices: GoveeDevice) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = {d.device_id: d for d in devices}
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.async_get_scenes = AsyncMock(return_value=[])
    coordinator.async_reassert_segments = AsyncMock()
    coordinator.is_power_off_pending = MagicMock(return_value=False)
    coordinator.restore_group_state = MagicMock()
    return coordinator


def _commands(coordinator: MagicMock) -> list:
    return [c[0][1] for c in coordinator.async_control_device.await_args_list]


# --------------------------------------------------------------------------- #
# GoveeLightEntity
# --------------------------------------------------------------------------- #


@pytest.fixture
def light(mock_light_device, mock_device_state):
    entity = GoveeLightEntity(_coordinator(mock_device_state, mock_light_device), mock_light_device)
    entity.async_write_ha_state = MagicMock()
    return entity


class TestLightProperties:
    def test_properties_are_none_without_state(self, light):
        light.coordinator.get_state.return_value = None
        assert light.is_on is None
        assert light.brightness is None
        assert light.rgb_color is None
        assert light.color_temp_kelvin is None

    def test_properties_reflect_state(self, light, mock_device_state):
        assert light.is_on is True
        assert light.brightness == 191  # 75 of 0-100 on the 0-255 scale
        assert light.rgb_color == (255, 128, 64)
        assert light.color_temp_kelvin is None

    def test_color_temp_kelvin_from_state(self, light, mock_device_state):
        mock_device_state.color = None
        mock_device_state.color_temp_kelvin = 4500
        assert light.color_temp_kelvin == 4500
        assert light.rgb_color is None

    def test_color_temp_range_defaults_without_capability(self, mock_plug_device):
        entity = GoveeLightEntity(_coordinator(None, mock_plug_device), mock_plug_device)
        assert entity.min_color_temp_kelvin == 2000
        assert entity.max_color_temp_kelvin == 9000

    def test_color_temp_range_from_capability(self, light):
        assert light.min_color_temp_kelvin == 2000
        assert light.max_color_temp_kelvin == 9000

    def test_degenerate_brightness_range_reads_as_zero(self):
        device = _device(
            "AA:BB:CC:DD:EE:FF:01:01",
            "H6000",
            DEVICE_TYPE_LIGHT,
            _POWER,
            _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS, {"range": {"min": 100, "max": 100}}),
        )
        state = GoveeDeviceState(device_id=device.device_id, brightness=100)
        entity = GoveeLightEntity(_coordinator(state, device), device)
        # A zero-width range cannot be scaled; must not divide by zero.
        assert entity.brightness == 0


def _ranged_light(low: int, high: int, device_brightness: int) -> GoveeLightEntity:
    device = _device(
        "AA:BB:CC:DD:EE:FF:01:02",
        "H6000",
        DEVICE_TYPE_LIGHT,
        _POWER,
        _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS, {"range": {"min": low, "max": high}}),
    )
    state = GoveeDeviceState(device_id=device.device_id, power_state=True, brightness=device_brightness)
    entity = GoveeLightEntity(_coordinator(state, device), device)
    entity.async_write_ha_state = MagicMock()
    return entity


class TestBrightnessConversion:
    @pytest.mark.parametrize(("low", "high"), [(1, 100), (0, 100), (1, 254), (10, 100), (1, 10)])
    def test_reapplying_the_reported_brightness_never_drifts(self, low, high):
        entity = _ranged_light(low, high, high)
        for device_value in range(max(low, 1), high + 1):
            reported = entity._device_to_ha_brightness(device_value)
            assert entity._ha_to_device_brightness(reported) == device_value

    def test_half_brightness_reads_back_as_half(self):
        entity = _ranged_light(1, 100, 100)
        device_value = entity._ha_to_device_brightness(128)
        assert device_value == 50
        assert entity._device_to_ha_brightness(device_value) == 128

    @pytest.mark.parametrize(("low", "high"), [(1, 100), (0, 100)])
    def test_lowest_level_is_on_and_lowest_ha_level_is_not_dark(self, low, high):
        entity = _ranged_light(low, high, max(low, 1))
        # Device at its lowest lit level must not read as brightness 0.
        assert entity.brightness is not None and entity.brightness >= 1
        # A non-zero HA brightness must never send 0 (dark) to the device.
        assert entity._ha_to_device_brightness(1) >= 1

    def test_nightlight_uses_the_same_conversion(self):
        device = _nightlight_device(_cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS, {"range": {"min": 1, "max": 100}}))
        entity = _nightlight(device, _nl_state(brightness=1))
        assert entity.brightness >= 1
        assert entity._ha_to_device_brightness(128) == 50
        assert entity._device_to_ha_brightness(50) == 128


class TestLightTurnOn:
    async def test_brightness_only_when_already_on(self, light):
        await light.async_turn_on(brightness=128)

        cmds = _commands(light.coordinator)
        assert cmds == [BrightnessCommand(brightness=50)]

    async def test_brightness_also_powers_on_when_off(self, light, mock_device_state):
        mock_device_state.power_state = False

        await light.async_turn_on(brightness=255)

        cmds = _commands(light.coordinator)
        assert cmds == [BrightnessCommand(brightness=100), PowerCommand(power_on=True)]

    async def test_rgb_color(self, light):
        await light.async_turn_on(rgb_color=(10, 20, 30))

        assert _commands(light.coordinator) == [ColorCommand(color=RGBColor(r=10, g=20, b=30))]

    async def test_color_temp(self, light):
        await light.async_turn_on(color_temp_kelvin=3500)

        assert _commands(light.coordinator) == [ColorTempCommand(kelvin=3500)]

    async def test_all_attributes_in_one_call(self, light):
        await light.async_turn_on(brightness=255, rgb_color=(1, 2, 3), color_temp_kelvin=4000)

        cmds = _commands(light.coordinator)
        assert [type(c) for c in cmds] == [BrightnessCommand, ColorCommand, ColorTempCommand]

    async def test_no_attributes_sends_power_on(self, light):
        await light.async_turn_on()

        assert _commands(light.coordinator) == [PowerCommand(power_on=True)]

    async def test_turn_off_sends_power_off(self, light):
        await light.async_turn_off()

        assert _commands(light.coordinator) == [PowerCommand(power_on=False)]

    async def test_rejected_brightness_raises_before_power(self, light, mock_device_state):
        mock_device_state.power_state = False
        light.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await light.async_turn_on(brightness=10)

        # The failing command stops the sequence — no follow-up power command.
        assert len(_commands(light.coordinator)) == 1

    async def test_rejected_turn_off_raises(self, light):
        light.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await light.async_turn_off()


class TestLightGroupRestore:
    async def _add(self, entity: GoveeLightEntity, last_state: State | None) -> None:
        with (
            patch.object(GoveeEntity, "async_added_to_hass", new_callable=AsyncMock),
            patch.object(entity, "async_get_last_state", new_callable=AsyncMock, return_value=last_state),
        ):
            await entity.async_added_to_hass()

    async def test_group_restores_power_and_brightness(self, mock_group_device):
        entity = GoveeLightEntity(_coordinator(None, mock_group_device), mock_group_device)

        await self._add(entity, State("light.all_lights", "on", {"brightness": 255}))

        entity.coordinator.restore_group_state.assert_called_once_with(mock_group_device.device_id, True, 100)
        # Groups have no scene API.
        entity.coordinator.async_get_scenes.assert_not_awaited()

    async def test_group_restores_off_without_brightness(self, mock_group_device):
        entity = GoveeLightEntity(_coordinator(None, mock_group_device), mock_group_device)

        await self._add(entity, State("light.all_lights", "off"))

        entity.coordinator.restore_group_state.assert_called_once_with(mock_group_device.device_id, False, None)

    async def test_group_without_previous_state_restores_nothing(self, mock_group_device):
        entity = GoveeLightEntity(_coordinator(None, mock_group_device), mock_group_device)

        await self._add(entity, None)

        entity.coordinator.restore_group_state.assert_not_called()

    async def test_regular_light_never_touches_group_restore(self, light):
        light.hass = MagicMock()
        light.hass.data = {}
        await self._add(light, State("light.living_room_light", "on", {"brightness": 255}))

        light.coordinator.restore_group_state.assert_not_called()


# --------------------------------------------------------------------------- #
# GoveeMainLightEntity (issue #131)
# --------------------------------------------------------------------------- #

H1270_ID = "AA:BB:CC:DD:EE:FF:12:70"


def _h1270() -> GoveeDevice:
    return _device(
        H1270_ID,
        "H1270",
        DEVICE_TYPE_LIGHT,
        _POWER,
        _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS, {"range": {"min": 1, "max": 100}}),
        _RGB,
        _CT,
        _cap(
            CAPABILITY_SEGMENT_COLOR,
            INSTANCE_SEGMENT_COLOR,
            {
                "fields": [
                    {"fieldName": "segment", "elementRange": {"min": 0, "max": 11}, "size": {"min": 1, "max": 12}}
                ]
            },
        ),
        name="Ceiling Light Pro",
    )


def _main_light(
    *, color: tuple[int, int, int] | None = (0, 0, 0), color_temp_kelvin: int | None = None
) -> GoveeMainLightEntity:
    device = _h1270()
    state = GoveeDeviceState(device_id=H1270_ID, power_state=True, brightness=100)
    state.color = RGBColor(*color) if color else None
    state.color_temp_kelvin = color_temp_kelvin
    entity = GoveeMainLightEntity(_coordinator(state, device), device)
    entity.async_write_ha_state = MagicMock()
    return entity


class TestMainLightConstruction:
    def test_unique_id_scenes_and_defaults(self):
        entity = _main_light()
        assert entity.unique_id == f"{H1270_ID}_main_light_toggle"
        # Scenes are whole-fixture and belong to the master entity.
        assert not (entity.supported_features & LightEntityFeature.EFFECT)
        assert entity._last_on_kelvin is None
        assert entity._last_on_rgb is None
        # Inherits the device's brightness scaling.
        assert entity._ha_to_device_brightness(255) == 100

    def test_name_comes_from_translation_not_device_name(self):
        # The master light uses the bare device name; the panel must not, or
        # both entities show up as just "<Device>".
        entity = _main_light()
        # Older HA reads the translation key's prefix from platform, newer
        # from platform_data; set both so the test runs on either.
        entity.platform = entity.platform_data = MagicMock(platform_name="govee", domain="light")
        translations = {"component.govee.entity.light.govee_main_light_panel.name": "Main light"}

        assert entity._name_internal(None, translations) == "Main light"
        # HA returns _attr_name before the translation; use_device_name, which
        # also read it, is gone in newer HA.
        assert not hasattr(entity, "_attr_name")

    def test_is_on_reads_black_as_off(self):
        assert _main_light(color=(0, 0, 0)).is_on is False
        assert _main_light(color=(255, 0, 0)).is_on is True


class TestMainLightTurnOn:
    async def test_brightness_on_lit_panel_only_sends_brightness(self):
        entity = _main_light(color=(255, 0, 0))

        await entity.async_turn_on(brightness=255)

        assert _commands(entity.coordinator) == [BrightnessCommand(brightness=100)]
        entity.coordinator.async_reassert_segments.assert_awaited_once_with(H1270_ID)
        entity.async_write_ha_state.assert_called_once()

    async def test_brightness_on_dark_panel_also_restores_colour(self):
        entity = _main_light(color=(0, 0, 0))

        await entity.async_turn_on(brightness=128)

        cmds = _commands(entity.coordinator)
        assert cmds[0] == BrightnessCommand(brightness=50)
        assert cmds[1] == ColorTempCommand(kelvin=MAIN_LIGHT_ON_KELVIN)

    async def test_from_black_restores_last_rgb(self):
        entity = _main_light(color=(0, 0, 0))
        entity._last_on_rgb = (12, 34, 56)

        await entity.async_turn_on()

        assert _commands(entity.coordinator) == [ColorCommand(color=RGBColor(r=12, g=34, b=56))]

    async def test_turn_off_remembers_rgb_for_next_on(self):
        entity = _main_light(color=(200, 100, 50))

        await entity.async_turn_off()

        assert entity._last_on_rgb == (200, 100, 50)
        assert entity._last_on_kelvin is None
        assert _commands(entity.coordinator) == [ColorCommand(color=RGBColor(r=0, g=0, b=0))]


class TestMainLightRestore:
    """The return-to colour must survive a restart while the panel is off."""

    def test_extra_restore_state_data_with_rgb(self):
        entity = _main_light()
        entity._last_on_rgb = (1, 2, 3)

        assert entity.extra_restore_state_data.as_dict() == {"last_on_kelvin": None, "last_on_rgb": [1, 2, 3]}

    def test_extra_restore_state_data_with_kelvin(self):
        entity = _main_light()
        entity._last_on_kelvin = 3000

        assert entity.extra_restore_state_data.as_dict() == {"last_on_kelvin": 3000, "last_on_rgb": None}

    async def _add(self, entity: GoveeMainLightEntity, extra: dict | None) -> None:
        stored = RestoredExtraData(extra) if extra is not None else None
        with (
            patch.object(GoveeEntity, "async_added_to_hass", new_callable=AsyncMock),
            patch.object(entity, "async_get_last_extra_data", new_callable=AsyncMock, return_value=stored),
        ):
            await entity.async_added_to_hass()

    async def test_restores_kelvin(self):
        entity = _main_light()

        await self._add(entity, {"last_on_kelvin": 2700, "last_on_rgb": None})

        assert entity._last_on_kelvin == 2700
        assert entity._last_on_rgb is None

    async def test_restores_rgb(self):
        entity = _main_light()

        await self._add(entity, {"last_on_kelvin": None, "last_on_rgb": [10, 20, 30]})

        assert entity._last_on_rgb == (10, 20, 30)
        assert entity._last_on_kelvin is None

    async def test_kelvin_wins_over_rgb(self):
        entity = _main_light()

        await self._add(entity, {"last_on_kelvin": 4000, "last_on_rgb": [10, 20, 30]})

        assert entity._last_on_kelvin == 4000
        assert entity._last_on_rgb is None

    async def test_black_rgb_is_not_restored(self):
        """Black is "off", never a colour to return to."""
        entity = _main_light()

        await self._add(entity, {"last_on_kelvin": None, "last_on_rgb": [0, 0, 0]})

        assert entity._last_on_rgb is None
        assert entity._last_on_kelvin is None

    async def test_no_stored_data_keeps_defaults(self):
        entity = _main_light()

        await self._add(entity, None)

        assert entity._last_on_rgb is None
        assert entity._last_on_kelvin is None

    async def test_round_trip(self):
        """What extra_restore_state_data writes, async_added_to_hass reads back."""
        source = _main_light()
        source._last_on_rgb = (7, 8, 9)
        restored = _main_light()

        await self._add(restored, source.extra_restore_state_data.as_dict())

        assert restored._last_on_rgb == (7, 8, 9)


# --------------------------------------------------------------------------- #
# GoveeNightLightEntity (issue #114)
# --------------------------------------------------------------------------- #

NL_ID = "AA:BB:CC:DD:EE:FF:50:89"


def _nightlight_device(*caps: GoveeCapability) -> GoveeDevice:
    return _device(NL_ID, "H5089", DEVICE_TYPE_PLUG, _POWER, _NIGHTLIGHT, *caps, name="Outlet Extender")


def _nightlight(device: GoveeDevice, state: GoveeDeviceState | None) -> GoveeNightLightEntity:
    entity = GoveeNightLightEntity(_coordinator(state, device), device)
    entity.async_write_ha_state = MagicMock()
    return entity


def _nl_state(**kwargs) -> GoveeDeviceState:
    state = GoveeDeviceState(device_id=NL_ID, online=True, brightness=50)
    state.toggles = {INSTANCE_NIGHT_LIGHT: True}
    for key, value in kwargs.items():
        setattr(state, key, value)
    return state


class TestNightLightColorModes:
    def test_rgb_and_color_temp(self):
        entity = _nightlight(_nightlight_device(_BRIGHTNESS, _RGB, _CT), None)
        assert entity.supported_color_modes == {ColorMode.RGB, ColorMode.COLOR_TEMP}

    def test_color_temp_only(self):
        entity = _nightlight(_nightlight_device(_BRIGHTNESS, _CT), None)
        assert entity.supported_color_modes == {ColorMode.COLOR_TEMP}
        assert entity.color_mode == ColorMode.COLOR_TEMP

    def test_brightness_only(self):
        entity = _nightlight(_nightlight_device(_BRIGHTNESS), None)
        assert entity.supported_color_modes == {ColorMode.BRIGHTNESS}
        assert entity.color_mode == ColorMode.BRIGHTNESS

    def test_toggle_only(self):
        entity = _nightlight(_nightlight_device(), None)
        assert entity.supported_color_modes == {ColorMode.ONOFF}
        assert entity.color_mode == ColorMode.ONOFF

    def test_color_mode_follows_state(self):
        device = _nightlight_device(_BRIGHTNESS, _RGB, _CT)
        state = _nl_state(color=RGBColor(r=1, g=2, b=3), color_temp_kelvin=None)
        entity = _nightlight(device, state)
        assert entity.color_mode == ColorMode.RGB

        state.color_temp_kelvin = 3000
        assert entity.color_mode == ColorMode.COLOR_TEMP

        # No state at all: RGB is the preferred default when supported.
        entity.coordinator.get_state.return_value = None
        assert entity.color_mode == ColorMode.RGB

    def test_color_in_state_on_ct_only_device_does_not_claim_rgb(self):
        entity = _nightlight(_nightlight_device(_BRIGHTNESS, _CT), _nl_state(color=RGBColor(r=1, g=2, b=3)))
        assert entity.color_mode == ColorMode.COLOR_TEMP


class TestNightLightProperties:
    def test_none_without_state(self):
        entity = _nightlight(_nightlight_device(_BRIGHTNESS, _RGB), None)
        assert entity.is_on is None
        assert entity.brightness is None
        assert entity.rgb_color is None
        assert entity.color_temp_kelvin is None

    def test_values_from_state(self):
        state = _nl_state(color=RGBColor(r=9, g=8, b=7), color_temp_kelvin=None)
        entity = _nightlight(_nightlight_device(_BRIGHTNESS, _RGB, _CT), state)
        assert entity.is_on is True
        assert entity.brightness == 128
        assert entity.rgb_color == (9, 8, 7)
        assert entity.color_temp_kelvin is None

        state.color = None
        state.color_temp_kelvin = 5000
        assert entity.rgb_color is None
        assert entity.color_temp_kelvin == 5000

    def test_is_on_unknown_when_toggle_never_reported(self):
        state = _nl_state()
        state.toggles = {}
        entity = _nightlight(_nightlight_device(_BRIGHTNESS), state)
        assert entity.is_on is None

    def test_color_temp_range(self):
        assert _nightlight(_nightlight_device(_CT), None).min_color_temp_kelvin == 2700
        assert _nightlight(_nightlight_device(_CT), None).max_color_temp_kelvin == 6500
        assert _nightlight(_nightlight_device(), None).min_color_temp_kelvin == 2000
        assert _nightlight(_nightlight_device(), None).max_color_temp_kelvin == 9000

    def test_degenerate_brightness_range_reads_as_zero(self):
        device = _nightlight_device(_cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS, {"range": {"min": 5, "max": 5}}))
        entity = _nightlight(device, _nl_state(brightness=5))
        assert entity.brightness == 0


class TestNightLightTurnOn:
    async def test_rgb_on_lit_nightlight_sends_only_colour(self):
        entity = _nightlight(_nightlight_device(_BRIGHTNESS, _RGB), _nl_state())

        await entity.async_turn_on(rgb_color=(10, 20, 30))

        assert _commands(entity.coordinator) == [ColorCommand(color=RGBColor(r=10, g=20, b=30))]

    async def test_color_temp_on_dark_nightlight_also_toggles_on(self):
        state = _nl_state()
        state.toggles = {INSTANCE_NIGHT_LIGHT: False}
        entity = _nightlight(_nightlight_device(_BRIGHTNESS, _CT), state)

        await entity.async_turn_on(color_temp_kelvin=4000)

        cmds = _commands(entity.coordinator)
        assert cmds == [
            ColorTempCommand(kelvin=4000),
            ToggleCommand(toggle_instance=INSTANCE_NIGHT_LIGHT, enabled=True),
        ]
        # Written back to live state so the entity reads "on" immediately.
        assert entity.is_on is True
        entity.async_write_ha_state.assert_called_once()

    async def test_plain_turn_on_only_toggles(self):
        entity = _nightlight(_nightlight_device(_BRIGHTNESS, _RGB), _nl_state())

        await entity.async_turn_on()

        assert _commands(entity.coordinator) == [ToggleCommand(toggle_instance=INSTANCE_NIGHT_LIGHT, enabled=True)]

    async def test_rejected_colour_raises_and_skips_toggle(self):
        state = _nl_state()
        state.toggles = {INSTANCE_NIGHT_LIGHT: False}
        entity = _nightlight(_nightlight_device(_BRIGHTNESS, _RGB), state)
        entity.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on(rgb_color=(1, 1, 1))

        assert len(_commands(entity.coordinator)) == 1
        assert entity.is_on is False

    async def test_rejected_toggle_raises_without_flipping_state(self):
        entity = _nightlight(_nightlight_device(_BRIGHTNESS), _nl_state())
        entity.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_off()

        assert entity.is_on is True
        entity.async_write_ha_state.assert_not_called()

    async def test_toggle_without_state_still_writes(self):
        entity = _nightlight(_nightlight_device(_BRIGHTNESS), None)

        await entity.async_turn_off()

        assert _commands(entity.coordinator) == [ToggleCommand(toggle_instance=INSTANCE_NIGHT_LIGHT, enabled=False)]
        entity.async_write_ha_state.assert_called_once()


# --------------------------------------------------------------------------- #
# async_setup_entry
# --------------------------------------------------------------------------- #


class TestLightPlatformSetup:
    async def _setup(self, *devices: GoveeDevice, options: dict | None = None) -> list:
        coordinator = _coordinator(None, *devices)
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = options or {}
        added: list = []
        await light_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
        return added

    async def test_main_light_toggle_sku_gets_master_and_panel_entities(self):
        added = await self._setup(_h1270(), options={"segment_mode_by_device": {H1270_ID: "disabled"}})

        assert [type(e).__name__ for e in added] == ["GoveeLightEntity", "GoveeMainLightEntity"]
        assert added[0].unique_id == H1270_ID
        assert added[1].unique_id == f"{H1270_ID}_main_light_toggle"

    async def test_sku_match_is_case_insensitive(self):
        device = GoveeDevice(
            device_id=H1270_ID,
            sku="h1270",
            name="Ceiling",
            device_type=DEVICE_TYPE_LIGHT,
            capabilities=(_POWER, _BRIGHTNESS, _RGB),
        )

        added = await self._setup(device)

        assert "GoveeMainLightEntity" in [type(e).__name__ for e in added]

    async def test_ordinary_light_gets_no_panel_entity(self, mock_light_device):
        added = await self._setup(mock_light_device)

        assert [type(e).__name__ for e in added] == ["GoveeLightEntity"]
        assert added[0]._enable_scenes is True

    async def test_scenes_option_propagates(self, mock_light_device):
        added = await self._setup(mock_light_device, options={"enable_scenes": False})

        assert added[0]._enable_scenes is False

    async def test_appliance_and_nightlight_wiring(self):
        purifier = _device("AA:BB:CC:DD:EE:FF:71:24", "H7124", DEVICE_TYPE_PURIFIER, _POWER, _NIGHTLIGHT, _BRIGHTNESS)
        plain_plug = _device("AA:BB:CC:DD:EE:FF:50:80", "H5080", DEVICE_TYPE_PLUG, _POWER)

        added = await self._setup(purifier, plain_plug)

        # The purifier is not a light, but its nightlight is; a plain plug is neither.
        assert [type(e).__name__ for e in added] == ["GoveeNightLightEntity"]
        assert added[0].unique_id == f"{purifier.device_id}_nightlight"

    async def test_group_devices_get_no_nightlight_entity(self):
        group = GoveeDevice(
            device_id="12345678",
            sku="GROUP",
            name="Nightlights",
            device_type="devices.types.group",
            capabilities=(_POWER, _NIGHTLIGHT, _BRIGHTNESS),
            is_group=True,
        )

        added = await self._setup(group)

        assert "GoveeNightLightEntity" not in [type(e).__name__ for e in added]

    async def test_default_segment_mode_is_individual(self, mock_rgbic_device):
        added = await self._setup(mock_rgbic_device)

        names = [type(e).__name__ for e in added]
        assert names.count("GoveeSegmentEntity") == mock_rgbic_device.segment_count
        assert "GoveeGroupedSegmentEntity" not in names


# --------------------------------------------------------------------------- #
# GoveeMainPanelLight (issue #223)
# --------------------------------------------------------------------------- #

H1232_ID = "AA:BB:CC:DD:EE:FF:12:32"


def _h1232() -> GoveeDevice:
    return _device(H1232_ID, "H1232", DEVICE_TYPE_LIGHT, _POWER, _BRIGHTNESS, _RGB, name="Ceiling Light Pro")


def _panel(*, toggled: bool | None = True, state: bool = True) -> GoveeMainPanelLight:
    device = _h1232()
    device_state = GoveeDeviceState(device_id=H1232_ID, power_state=True, brightness=100) if state else None
    if device_state is not None and toggled is not None:
        device_state.toggles[INSTANCE_MAIN_LIGHT_TOGGLE] = toggled
    coordinator = _coordinator(device_state, device)
    coordinator.async_set_main_panel = AsyncMock(return_value=True)
    entity = GoveeMainPanelLight(coordinator, device)
    entity.async_write_ha_state = MagicMock()
    return entity


class TestMainPanelProperties:
    def test_unique_id_translation_key_and_defaults(self):
        entity = _panel()
        assert entity.unique_id == f"{H1232_ID}_main_panel"
        assert entity.translation_key == "govee_main_light_panel"
        assert entity.supported_color_modes == {ColorMode.COLOR_TEMP, ColorMode.RGB}
        assert entity.color_mode == ColorMode.RGB
        assert (entity.min_color_temp_kelvin, entity.max_color_temp_kelvin) == (2700, 6500)
        assert entity.rgb_color == (255, 255, 255)
        assert entity.brightness == 255

    def test_is_on_follows_main_light_toggle(self):
        assert _panel(toggled=True).is_on is True
        assert _panel(toggled=False).is_on is False
        assert _panel(toggled=None).is_on is None

    def test_is_on_none_without_state(self):
        assert _panel(state=False).is_on is None


class TestMainPanelControl:
    async def test_turn_on_from_off_sends_toggle_only(self):
        entity = _panel(toggled=False)

        await entity.async_turn_on()

        assert _commands(entity.coordinator) == [
            ToggleCommand(toggle_instance=INSTANCE_MAIN_LIGHT_TOGGLE, enabled=True)
        ]
        entity.coordinator.async_set_main_panel.assert_not_awaited()
        entity.async_write_ha_state.assert_called_once()

    async def test_colour_and_brightness_go_over_ptreal(self):
        entity = _panel(toggled=True)

        await entity.async_turn_on(rgb_color=(10, 20, 30), brightness=128)

        assert _commands(entity.coordinator) == []
        entity.coordinator.async_set_main_panel.assert_awaited_once_with(
            H1232_ID, rgb=RGBColor(r=10, g=20, b=30), brightness=50, kelvin=None
        )
        assert entity.rgb_color == (10, 20, 30)
        assert entity.brightness == 128

    async def test_lowest_brightness_never_sends_a_dark_panel(self):
        entity = _panel(toggled=True)

        await entity.async_turn_on(brightness=1)

        entity.coordinator.async_set_main_panel.assert_awaited_once_with(H1232_ID, rgb=None, brightness=1, kelvin=None)

    async def test_ptreal_failure_keeps_the_previous_colour_and_brightness(self):
        entity = _panel(toggled=True)
        entity.coordinator.async_set_main_panel = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on(rgb_color=(1, 2, 3), brightness=10)

        assert entity.rgb_color == (255, 255, 255)
        assert entity.brightness == 255

    async def test_ptreal_failure_keeps_the_previous_colour_temperature(self):
        entity = _panel(toggled=True)
        mode, kelvin = entity.color_mode, entity.color_temp_kelvin
        entity.coordinator.async_set_main_panel = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on(color_temp_kelvin=3000)

        assert entity.color_mode == mode
        assert entity.color_temp_kelvin == kelvin

    async def test_colour_temperature_goes_over_ptreal_clamped(self):
        entity = _panel(toggled=True)

        await entity.async_turn_on(color_temp_kelvin=9000)

        entity.coordinator.async_set_main_panel.assert_awaited_once_with(
            H1232_ID, rgb=None, brightness=None, kelvin=6500
        )
        assert entity.color_mode == ColorMode.COLOR_TEMP
        assert entity.color_temp_kelvin == 6500

        await entity.async_turn_on(rgb_color=(1, 2, 3))
        assert entity.color_mode == ColorMode.RGB

    async def test_ptreal_failure_raises(self):
        entity = _panel(toggled=True)
        entity.coordinator.async_set_main_panel = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on(rgb_color=(1, 2, 3))

        entity.async_write_ha_state.assert_not_called()

    async def test_toggle_rejection_raises(self):
        entity = _panel(toggled=False)
        entity.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on()

    async def test_turn_off_sends_toggle_off(self):
        entity = _panel(toggled=True)

        await entity.async_turn_off()

        assert _commands(entity.coordinator) == [
            ToggleCommand(toggle_instance=INSTANCE_MAIN_LIGHT_TOGGLE, enabled=False)
        ]
        entity.async_write_ha_state.assert_called_once()


class TestMainPanelRestore:
    async def _add(self, entity: GoveeMainPanelLight, last: State | None) -> None:
        with (
            patch.object(GoveeEntity, "async_added_to_hass", new_callable=AsyncMock),
            patch.object(entity, "async_get_last_state", new_callable=AsyncMock, return_value=last),
        ):
            await entity.async_added_to_hass()

    async def test_restores_colour_and_brightness(self):
        entity = _panel()

        await self._add(entity, State("light.panel", "on", {"brightness": 77, "rgb_color": [4, 5, 6]}))

        assert entity.brightness == 77
        assert entity.rgb_color == (4, 5, 6)

    async def test_restores_colour_temperature_mode(self):
        entity = _panel()

        await self._add(
            entity, State("light.panel", "on", {"color_temp_kelvin": 3000, "color_mode": ColorMode.COLOR_TEMP})
        )

        assert entity.color_temp_kelvin == 3000
        assert entity.color_mode == ColorMode.COLOR_TEMP

    async def test_off_state_without_attributes_keeps_defaults(self):
        entity = _panel()

        await self._add(entity, State("light.panel", "off", {}))

        assert entity.brightness == 255
        assert entity.rgb_color == (255, 255, 255)

    async def test_no_last_state_keeps_defaults(self):
        entity = _panel()

        await self._add(entity, None)

        assert entity.brightness == 255


class TestMainPanelSetup:
    async def test_h1232_gets_the_panel_entity(self):
        coordinator = _coordinator(None, _h1232())
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {"segment_mode_by_device": {H1232_ID: "disabled"}}
        added: list = []

        await light_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))

        assert [type(e).__name__ for e in added] == ["GoveeLightEntity", "GoveeMainPanelLight"]
        assert added[1].unique_id == f"{H1232_ID}_main_panel"

    async def test_h1252_gets_panel_and_ring_entities(self):
        device = _device(H1252_ID, "H1252", DEVICE_TYPE_LIGHT, _POWER, _BRIGHTNESS, _RGB, name="Ceiling Light Pro")
        coordinator = _coordinator(None, device)
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {"segment_mode_by_device": {H1252_ID: "disabled"}}
        added: list = []

        await light_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))

        assert [type(e).__name__ for e in added] == ["GoveeLightEntity", "GoveeMainPanelLight", "GoveeRingLight"]
        assert added[2].unique_id == f"{H1252_ID}_ring"
        assert added[2].translation_key == "govee_ring_light"


H1252_ID = "AA:BB:CC:DD:EE:FF:12:52"


def _ring(*, toggled: bool | None = True) -> GoveeRingLight:
    device = _device(H1252_ID, "H1252", DEVICE_TYPE_LIGHT, _POWER, _BRIGHTNESS, _RGB, name="Ceiling Light Pro")
    device_state = GoveeDeviceState(device_id=H1252_ID, power_state=True, brightness=100)
    if toggled is not None:
        device_state.toggles[INSTANCE_BACKGROUND_LIGHT_TOGGLE] = toggled
    coordinator = _coordinator(device_state, device)
    coordinator.async_set_ring = AsyncMock(return_value=True)
    coordinator.async_set_main_panel = AsyncMock(return_value=True)
    entity = GoveeRingLight(coordinator, device)
    entity.async_write_ha_state = MagicMock()
    return entity


class TestRingLight:
    def test_is_on_follows_background_light_toggle(self):
        assert _ring(toggled=True).is_on is True
        assert _ring(toggled=False).is_on is False

    async def test_turn_on_from_off_sends_ring_toggle(self):
        entity = _ring(toggled=False)

        await entity.async_turn_on()

        assert _commands(entity.coordinator) == [
            ToggleCommand(toggle_instance=INSTANCE_BACKGROUND_LIGHT_TOGGLE, enabled=True)
        ]

    async def test_colour_temperature_and_brightness_go_to_the_ring(self):
        entity = _ring(toggled=True)

        await entity.async_turn_on(color_temp_kelvin=4000, brightness=255)

        entity.coordinator.async_set_ring.assert_awaited_once_with(H1252_ID, rgb=None, brightness=100, kelvin=4000)
        entity.coordinator.async_set_main_panel.assert_not_awaited()

    async def test_turn_off_sends_ring_toggle_off(self):
        entity = _ring(toggled=True)

        await entity.async_turn_off()

        assert _commands(entity.coordinator) == [
            ToggleCommand(toggle_instance=INSTANCE_BACKGROUND_LIGHT_TOGGLE, enabled=False)
        ]
