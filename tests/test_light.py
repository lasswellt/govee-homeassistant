"""Test Govee light entity effect and color mode support."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.light import ColorMode, LightEntityFeature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.govee.light import DIY_EFFECT_PREFIX, GoveeLightEntity
from custom_components.govee.models import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    RGBColor,
    SceneCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    DEVICE_TYPE_LIGHT,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_DIY,
    INSTANCE_POWER,
    INSTANCE_SCENE,
)


@pytest.fixture
def mock_coordinator(mock_light_device, mock_device_state, mock_scenes):
    """Create a mock coordinator for light entity tests."""
    coordinator = MagicMock()
    coordinator.devices = {mock_light_device.device_id: mock_light_device}
    coordinator.get_state.return_value = mock_device_state
    coordinator.async_get_scenes = AsyncMock(return_value=mock_scenes)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.data = {mock_light_device.device_id: mock_device_state}
    return coordinator


@pytest.fixture
def mock_coordinator_no_scenes(mock_light_device, mock_device_state):
    """Create a mock coordinator with no scenes."""
    coordinator = MagicMock()
    coordinator.devices = {mock_light_device.device_id: mock_light_device}
    coordinator.get_state.return_value = mock_device_state
    coordinator.async_get_scenes = AsyncMock(return_value=[])
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.data = {mock_light_device.device_id: mock_device_state}
    return coordinator


class TestLightEffectSupport:
    """Test effect support on the light entity."""

    def test_effect_feature_enabled_when_scenes_supported_and_enabled(self, mock_coordinator, mock_light_device):
        """Test EFFECT feature flag is set when device supports scenes and scenes enabled."""
        from homeassistant.components.light import LightEntityFeature

        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        assert entity.supported_features & LightEntityFeature.EFFECT

    def test_effect_feature_disabled_when_scenes_disabled(self, mock_coordinator, mock_light_device):
        """Test EFFECT feature flag is NOT set when scenes are disabled in config."""
        from homeassistant.components.light import LightEntityFeature

        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=False)
        assert not (entity.supported_features & LightEntityFeature.EFFECT)

    def test_effect_feature_disabled_for_device_without_scenes(self, mock_coordinator, mock_plug_device):
        """Test EFFECT feature flag is NOT set for devices without scene support."""
        from homeassistant.components.light import LightEntityFeature

        entity = GoveeLightEntity(mock_coordinator, mock_plug_device, enable_scenes=True)
        assert not (entity.supported_features & LightEntityFeature.EFFECT)

    def test_effect_list_empty_before_added_to_hass(self, mock_coordinator, mock_light_device):
        """Test effect_list is None before async_added_to_hass populates it."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        assert entity.effect_list is None

    def test_build_effect_mapping(self, mock_coordinator, mock_light_device, mock_scenes):
        """Test _build_effect_mapping populates effect names and mappings."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        entity._build_effect_mapping(mock_scenes)

        assert entity.effect_list == ["Sunrise", "Sunset", "Party", "Movie"]
        assert entity._effect_to_scene["Sunrise"] == (1, "Sunrise")
        assert entity._scene_id_to_effect["1"] == "Sunrise"
        assert entity._scene_id_to_effect["4"] == "Movie"

    def test_build_effect_mapping_handles_duplicates(self, mock_coordinator, mock_light_device):
        """Test duplicate scene names get deduped with counter."""
        scenes = [
            {"name": "Rainbow", "value": {"id": 1}},
            {"name": "Rainbow", "value": {"id": 2}},
            {"name": "Rainbow", "value": {"id": 3}},
        ]
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        entity._build_effect_mapping(scenes)

        assert entity.effect_list == ["Rainbow", "Rainbow (1)", "Rainbow (2)"]
        assert entity._effect_to_scene["Rainbow"] == (1, "Rainbow")
        assert entity._effect_to_scene["Rainbow (1)"] == (2, "Rainbow")
        assert entity._effect_to_scene["Rainbow (2)"] == (3, "Rainbow")

    def test_effect_returns_active_scene_name(
        self, mock_coordinator, mock_light_device, mock_scenes, mock_device_state
    ):
        """Test effect property returns active scene name from mapping."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        entity._build_effect_mapping(mock_scenes)

        # Set active scene
        mock_device_state.active_scene = "2"
        mock_device_state.active_scene_name = "Sunset"

        # Mock device_state property
        with patch.object(
            type(entity),
            "device_state",
            new_callable=lambda: property(lambda self: mock_device_state),
        ):
            assert entity.effect == "Sunset"

    def test_effect_returns_none_when_no_scene_active(
        self, mock_coordinator, mock_light_device, mock_scenes, mock_device_state
    ):
        """Test effect property returns None when no scene is active."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        entity._build_effect_mapping(mock_scenes)

        mock_device_state.active_scene = None
        mock_device_state.active_scene_name = None

        with patch.object(
            type(entity),
            "device_state",
            new_callable=lambda: property(lambda self: mock_device_state),
        ):
            assert entity.effect is None

    def test_effect_falls_back_to_scene_name(
        self, mock_coordinator, mock_light_device, mock_scenes, mock_device_state
    ):
        """Test effect falls back to active_scene_name if ID not in mapping."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        entity._build_effect_mapping(mock_scenes)

        # Set active scene to an ID not in our mapping
        mock_device_state.active_scene = "999"
        mock_device_state.active_scene_name = "Unknown Scene"

        with patch.object(
            type(entity),
            "device_state",
            new_callable=lambda: property(lambda self: mock_device_state),
        ):
            assert entity.effect == "Unknown Scene"

    @pytest.mark.asyncio
    async def test_turn_on_with_effect_sends_scene_command(self, mock_coordinator, mock_light_device, mock_scenes):
        """Test async_turn_on with effect sends SceneCommand."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        entity._build_effect_mapping(mock_scenes)

        await entity.async_turn_on(effect="Sunrise")

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        assert call_args[0][0] == mock_light_device.device_id
        cmd = call_args[0][1]
        assert isinstance(cmd, SceneCommand)
        assert cmd.scene_id == 1
        assert cmd.scene_name == "Sunrise"

    @pytest.mark.asyncio
    async def test_turn_on_with_unknown_effect_raises(self, mock_coordinator, mock_light_device, mock_scenes):
        """Test async_turn_on with an unknown effect raises a validation error."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        entity._build_effect_mapping(mock_scenes)

        with pytest.raises(ServiceValidationError):
            await entity.async_turn_on(effect="NonExistent")

        # No command should be sent
        mock_coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_on_with_effect_returns_early(self, mock_coordinator, mock_light_device, mock_scenes):
        """Test async_turn_on with effect returns early without power command."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        entity._build_effect_mapping(mock_scenes)

        # Entity says light is off
        with patch.object(type(entity), "is_on", new_callable=lambda: property(lambda self: False)):
            await entity.async_turn_on(effect="Sunset")

        # Only one call: the scene command. No separate power command.
        assert mock_coordinator.async_control_device.call_count == 1
        cmd = mock_coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, SceneCommand)

    @pytest.mark.asyncio
    async def test_turn_on_with_effect_and_brightness(self, mock_coordinator, mock_light_device, mock_scenes):
        """The brightness is set after the scene, which leaves it unchanged."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        entity._build_effect_mapping(mock_scenes)

        with patch.object(type(entity), "is_on", new_callable=lambda: property(lambda self: False)):
            await entity.async_turn_on(effect="Sunset", brightness=255)

        commands = [call[0][1] for call in mock_coordinator.async_control_device.call_args_list]
        assert [type(cmd) for cmd in commands] == [SceneCommand, BrightnessCommand]
        assert commands[1].brightness == entity._ha_to_device_brightness(255)

    @pytest.mark.asyncio
    async def test_async_added_to_hass_loads_scenes(self, mock_coordinator, mock_light_device, mock_scenes):
        """Test async_added_to_hass loads scenes and builds effect mapping."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)

        # Patch super().async_added_to_hass and async_get_last_state
        with (
            patch.object(
                GoveeLightEntity.__bases__[0],
                "async_added_to_hass",
                new_callable=AsyncMock,
            ),
            patch.object(
                entity,
                "async_get_last_state",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            await entity.async_added_to_hass()

        mock_coordinator.async_get_scenes.assert_called_once_with(mock_light_device.device_id)
        assert entity.effect_list == ["Sunrise", "Sunset", "Party", "Movie"]

    @pytest.mark.asyncio
    async def test_async_added_to_hass_skips_scenes_when_disabled(self, mock_coordinator, mock_light_device):
        """Test async_added_to_hass does NOT load scenes when disabled."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=False)

        with (
            patch.object(
                GoveeLightEntity.__bases__[0],
                "async_added_to_hass",
                new_callable=AsyncMock,
            ),
            patch.object(
                entity,
                "async_get_last_state",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            await entity.async_added_to_hass()

        mock_coordinator.async_get_scenes.assert_not_called()
        assert entity.effect_list is None

    @pytest.mark.asyncio
    async def test_async_added_to_hass_skips_scenes_for_group(self, mock_coordinator, mock_group_device):
        """Test async_added_to_hass does NOT load scenes for group devices."""
        entity = GoveeLightEntity(mock_coordinator, mock_group_device, enable_scenes=True)

        with (
            patch.object(
                GoveeLightEntity.__bases__[0],
                "async_added_to_hass",
                new_callable=AsyncMock,
            ),
            patch.object(
                entity,
                "async_get_last_state",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            await entity.async_added_to_hass()

        mock_coordinator.async_get_scenes.assert_not_called()
        assert entity.effect_list is None


@pytest.fixture
def diy_capability() -> GoveeCapability:
    """The DIY scene capability as the device list reports it."""
    return GoveeCapability(type=CAPABILITY_DYNAMIC_SCENE, instance=INSTANCE_DIY, parameters={})


@pytest.fixture
def mock_diy_light_device(light_capabilities, diy_capability) -> GoveeDevice:
    """A light with both catalog scenes and DIY scenes (like the H1630)."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:44",
        sku="H1630",
        name="Lantern",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=light_capabilities + (diy_capability,),
        is_group=False,
    )


@pytest.fixture
def mock_diy_only_device(light_capabilities, diy_capability) -> GoveeDevice:
    """A light with DIY scenes but no catalog scene capability."""
    capabilities = tuple(cap for cap in light_capabilities if cap.instance != INSTANCE_SCENE)
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:55",
        sku="H6000",
        name="DIY Only",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=capabilities + (diy_capability,),
        is_group=False,
    )


@pytest.fixture
def mock_diy_scenes() -> list[dict[str, Any]]:
    """DIY scene data: the ID sits directly in "value"."""
    return [
        {"name": "Campfire", "value": 101},
        {"name": "Sunset", "value": 102},
    ]


@pytest.fixture
def diy_state(mock_diy_light_device) -> GoveeDeviceState:
    """A real state object, so the optimistic transitions are exercised."""
    state = GoveeDeviceState.create_empty(mock_diy_light_device.device_id)
    state.power_state = True
    state.color = RGBColor(255, 128, 64)
    return state


@pytest.fixture
def diy_coordinator(mock_diy_light_device, diy_state, mock_scenes, mock_diy_scenes):
    """Coordinator mock whose DIY send applies the optimistic state like the real one."""
    coordinator = MagicMock()
    coordinator.devices = {mock_diy_light_device.device_id: mock_diy_light_device}
    coordinator.get_state.return_value = diy_state
    coordinator.async_get_scenes = AsyncMock(return_value=mock_scenes)
    coordinator.async_get_diy_scenes = AsyncMock(return_value=mock_diy_scenes)

    async def control(device_id, command):
        if isinstance(command, SceneCommand):
            diy_state.apply_optimistic_scene(str(command.scene_id), command.scene_name)
        elif isinstance(command, ColorCommand):
            diy_state.apply_optimistic_color(command.color)
        elif isinstance(command, ColorTempCommand):
            diy_state.apply_optimistic_color_temp(command.kelvin)
        return True

    async def send_diy(device_id, scene_id, scene_name=""):
        diy_state.apply_optimistic_diy_scene(str(scene_id))
        return True

    coordinator.async_control_device = AsyncMock(side_effect=control)
    coordinator.async_send_diy_scene = AsyncMock(side_effect=send_diy)
    return coordinator


async def _add_to_hass(entity: GoveeLightEntity) -> None:
    """Run async_added_to_hass without the HA base-class machinery."""
    with (
        patch.object(GoveeLightEntity.__bases__[0], "async_added_to_hass", new_callable=AsyncMock),
        patch.object(entity, "async_get_last_state", new_callable=AsyncMock, return_value=None),
    ):
        await entity.async_added_to_hass()


class TestLightDIYEffects:
    """DIY scenes exposed as light effects next to the catalog scenes."""

    def test_effect_feature_for_device_with_only_diy_scenes(self, diy_coordinator, mock_diy_only_device):
        """A DIY scene capability alone is enough for an effect list."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_only_device, enable_scenes=True)
        assert entity.supported_features & LightEntityFeature.EFFECT

    def test_no_effect_feature_when_diy_option_disabled(self, diy_coordinator, mock_diy_only_device):
        """The enable_diy_scenes option turns the DIY effects off."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_only_device, enable_scenes=True, enable_diy_scenes=False)
        assert not entity.supported_features & LightEntityFeature.EFFECT

    async def test_effect_list_has_catalog_then_prefixed_diy(self, diy_coordinator, mock_diy_light_device):
        """DIY names get the prefix, so "Sunset" and "DIY: Sunset" are both listed."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device)
        await _add_to_hass(entity)

        diy_coordinator.async_get_diy_scenes.assert_called_once_with(mock_diy_light_device.device_id)
        assert entity.effect_list == [
            "Sunrise",
            "Sunset",
            "Party",
            "Movie",
            f"{DIY_EFFECT_PREFIX}Campfire",
            f"{DIY_EFFECT_PREFIX}Sunset",
        ]

    async def test_diy_only_device_skips_catalog_fetch(self, diy_coordinator, mock_diy_only_device):
        """Without the catalog capability only DIY scenes are fetched."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_only_device)
        await _add_to_hass(entity)

        diy_coordinator.async_get_scenes.assert_not_called()
        assert entity.effect_list == [f"{DIY_EFFECT_PREFIX}Campfire", f"{DIY_EFFECT_PREFIX}Sunset"]

    async def test_diy_scenes_not_loaded_when_option_disabled(self, diy_coordinator, mock_diy_light_device):
        """With the DIY option off, only the catalog scenes are effects."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device, enable_diy_scenes=False)
        await _add_to_hass(entity)

        diy_coordinator.async_get_diy_scenes.assert_not_called()
        assert entity.effect_list == ["Sunrise", "Sunset", "Party", "Movie"]

    async def test_diy_scenes_not_loaded_for_group(self, diy_coordinator, light_capabilities, diy_capability):
        """Groups have no DIY scene API, same as catalog scenes."""
        group = GoveeDevice(
            device_id="GROUP:AA:BB",
            sku="GROUP",
            name="All Lights",
            device_type="devices.types.group",
            capabilities=light_capabilities + (diy_capability,),
            is_group=True,
        )
        entity = GoveeLightEntity(diy_coordinator, group)
        await _add_to_hass(entity)

        diy_coordinator.async_get_diy_scenes.assert_not_called()
        assert entity.effect_list is None

    def test_diy_names_deduplicated(self, diy_coordinator, mock_diy_light_device):
        """Duplicate DIY names, and a catalog scene that already uses the prefix, get a counter."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device)
        entity._build_effect_mapping([{"name": f"{DIY_EFFECT_PREFIX}Glow", "value": {"id": 1}}])
        entity._build_diy_effect_mapping(
            [
                {"name": "Glow", "value": 201},
                {"name": "Glow", "value": 202},
            ]
        )

        assert entity.effect_list == [
            f"{DIY_EFFECT_PREFIX}Glow",
            f"{DIY_EFFECT_PREFIX}Glow (1)",
            f"{DIY_EFFECT_PREFIX}Glow (2)",
        ]
        assert entity._effect_to_scene[f"{DIY_EFFECT_PREFIX}Glow"] == (1, f"{DIY_EFFECT_PREFIX}Glow")
        assert entity._effect_to_diy_scene[f"{DIY_EFFECT_PREFIX}Glow (1)"] == (201, "Glow")
        assert entity._effect_to_diy_scene[f"{DIY_EFFECT_PREFIX}Glow (2)"] == (202, "Glow")

    async def test_turn_on_diy_effect_uses_diy_scene_path(self, diy_coordinator, mock_diy_light_device):
        """A DIY effect goes through the coordinator's DIY call, not a SceneCommand."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device)
        await _add_to_hass(entity)

        await entity.async_turn_on(effect=f"{DIY_EFFECT_PREFIX}Sunset")

        diy_coordinator.async_send_diy_scene.assert_awaited_once_with(
            mock_diy_light_device.device_id, scene_id=102, scene_name="Sunset"
        )
        diy_coordinator.async_control_device.assert_not_called()

    async def test_turn_on_same_named_catalog_scene_is_not_diy(self, diy_coordinator, mock_diy_light_device):
        """The unprefixed name still selects the catalog scene."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device)
        await _add_to_hass(entity)

        await entity.async_turn_on(effect="Sunset")

        diy_coordinator.async_send_diy_scene.assert_not_called()
        cmd = diy_coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, SceneCommand)
        assert cmd.scene_id == 2

    async def test_turn_on_diy_effect_with_brightness(self, diy_coordinator, mock_diy_light_device):
        """Brightness is applied after the DIY scene, like for catalog scenes."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device)
        await _add_to_hass(entity)

        await entity.async_turn_on(effect=f"{DIY_EFFECT_PREFIX}Campfire", brightness=255)

        diy_coordinator.async_send_diy_scene.assert_awaited_once()
        commands = [call[0][1] for call in diy_coordinator.async_control_device.call_args_list]
        assert [type(cmd) for cmd in commands] == [BrightnessCommand]

    async def test_turn_on_diy_effect_failure_raises(self, diy_coordinator, mock_diy_light_device):
        """A rejected DIY scene surfaces as an error instead of passing silently."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device)
        await _add_to_hass(entity)
        diy_coordinator.async_send_diy_scene = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on(effect=f"{DIY_EFFECT_PREFIX}Campfire", brightness=255)

        # Nothing else is sent after the failed scene
        diy_coordinator.async_control_device.assert_not_called()

    async def test_unknown_diy_effect_raises(self, diy_coordinator, mock_diy_light_device):
        """A prefixed name that matches no DIY scene is a validation error."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device)
        await _add_to_hass(entity)

        with pytest.raises(ServiceValidationError):
            await entity.async_turn_on(effect=f"{DIY_EFFECT_PREFIX}Nope")

        diy_coordinator.async_send_diy_scene.assert_not_called()

    async def test_effect_reports_active_diy_scene(self, diy_coordinator, mock_diy_light_device):
        """After activation the light reports the DIY effect."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device)
        await _add_to_hass(entity)

        await entity.async_turn_on(effect=f"{DIY_EFFECT_PREFIX}Campfire")

        assert entity.effect == f"{DIY_EFFECT_PREFIX}Campfire"

    async def test_effect_reports_diy_scene_set_by_select(self, diy_coordinator, mock_diy_light_device, diy_state):
        """A DIY scene started from the select shows up as the light's effect too."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device)
        await _add_to_hass(entity)

        diy_state.apply_optimistic_diy_scene("102")

        assert entity.effect == f"{DIY_EFFECT_PREFIX}Sunset"

    def test_effect_is_none_before_first_state(self, diy_coordinator, mock_diy_light_device):
        """No state from the coordinator yet means no effect."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device)
        diy_coordinator.get_state.return_value = None

        assert entity.effect is None

    async def test_unknown_active_diy_id_reports_no_effect(self, diy_coordinator, mock_diy_light_device, diy_state):
        """A DIY ID missing from the cached list (deleted in the app) is not reported."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device)
        await _add_to_hass(entity)

        diy_state.apply_optimistic_diy_scene("999")

        assert entity.effect is None

    @pytest.mark.parametrize(
        "change",
        [
            {"rgb_color": (0, 255, 0)},
            {"color_temp_kelvin": 3000},
            {"effect": "Party"},
        ],
    )
    async def test_diy_effect_cleared_by_other_modes(self, diy_coordinator, mock_diy_light_device, change):
        """Color, color temperature, or a catalog scene end the DIY effect."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device)
        await _add_to_hass(entity)
        await entity.async_turn_on(effect=f"{DIY_EFFECT_PREFIX}Campfire")

        await entity.async_turn_on(**change)

        assert entity.effect == change.get("effect")

    async def test_diy_effect_cleared_by_music_mode(self, diy_coordinator, mock_diy_light_device, diy_state):
        """Music mode ends the DIY effect."""
        entity = GoveeLightEntity(diy_coordinator, mock_diy_light_device)
        await _add_to_hass(entity)
        await entity.async_turn_on(effect=f"{DIY_EFFECT_PREFIX}Campfire")

        diy_state.apply_optimistic_music_mode_struct(music_mode=1, sensitivity=50)

        assert entity.effect is None

    async def test_setup_entry_passes_diy_option(self, mock_diy_light_device):
        """The enable_diy_scenes option reaches the light entity."""
        from custom_components.govee import light as light_mod

        coordinator = MagicMock()
        coordinator.devices = {mock_diy_light_device.device_id: mock_diy_light_device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []

        entry.options = {"enable_diy_scenes": False}
        await light_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
        entry.options = {}
        await light_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))

        lights = [e for e in added if type(e) is GoveeLightEntity]
        assert [e._enable_diy_scenes for e in lights] == [False, True]


@pytest.fixture
def brightness_only_capabilities():
    """Create capabilities for a brightness-only device (no color)."""
    return (
        GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
        GoveeCapability(
            type=CAPABILITY_RANGE,
            instance=INSTANCE_BRIGHTNESS,
            parameters={"range": {"min": 0, "max": 100}},
        ),
    )


@pytest.fixture
def mock_brightness_device(brightness_only_capabilities):
    """Create a mock brightness-only device."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:99",
        sku="H6000",
        name="Simple Light",
        device_type="devices.types.light",
        capabilities=brightness_only_capabilities,
        is_group=False,
    )


class TestColorMode:
    """Test color_mode property returns valid modes."""

    def test_color_mode_rgb_when_color_in_state(self, mock_coordinator, mock_light_device, mock_device_state):
        """Test color_mode returns RGB when state has color set."""
        mock_device_state.color = RGBColor(r=255, g=0, b=0)
        mock_device_state.color_temp_kelvin = None

        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        assert entity.color_mode == ColorMode.RGB

    def test_color_mode_color_temp_when_temp_in_state(self, mock_coordinator, mock_light_device, mock_device_state):
        """Test color_mode returns COLOR_TEMP when state has color_temp set."""
        mock_device_state.color = None
        mock_device_state.color_temp_kelvin = 4000

        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        assert entity.color_mode == ColorMode.COLOR_TEMP

    def test_color_mode_color_temp_takes_priority(self, mock_coordinator, mock_light_device, mock_device_state):
        """Test COLOR_TEMP takes priority when both color and color_temp are set."""
        mock_device_state.color = RGBColor(r=0, g=0, b=0)
        mock_device_state.color_temp_kelvin = 6667

        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        assert entity.color_mode == ColorMode.COLOR_TEMP

    def test_color_mode_valid_when_no_state(self, mock_coordinator, mock_light_device):
        """Test color_mode returns a valid mode when device_state is None."""
        mock_coordinator.get_state.return_value = None

        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        assert entity.color_mode in entity.supported_color_modes

    def test_color_mode_valid_when_empty_state(self, mock_coordinator, mock_light_device, mock_device_state_off):
        """Test color_mode returns valid mode when color and color_temp are both None."""
        mock_coordinator.get_state.return_value = mock_device_state_off

        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        assert entity.color_mode in entity.supported_color_modes

    def test_color_mode_onoff_for_onoff_only_device(self, mock_coordinator, mock_plug_device):
        """Test color_mode returns ONOFF for device with only power control."""
        mock_coordinator.get_state.return_value = None

        entity = GoveeLightEntity(mock_coordinator, mock_plug_device, enable_scenes=False)
        assert entity.color_mode == ColorMode.ONOFF
        assert entity.supported_color_modes == {ColorMode.ONOFF}

    def test_color_mode_brightness_for_brightness_only_device(self, mock_coordinator, mock_brightness_device):
        """Test color_mode returns BRIGHTNESS for brightness-only device."""
        mock_coordinator.get_state.return_value = None

        entity = GoveeLightEntity(mock_coordinator, mock_brightness_device, enable_scenes=False)
        assert entity.color_mode == ColorMode.BRIGHTNESS
        assert entity.supported_color_modes == {ColorMode.BRIGHTNESS}

    def test_color_mode_always_in_supported_modes(self, mock_coordinator, mock_light_device, mock_device_state):
        """Test color_mode is always in supported_color_modes across state transitions."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)

        # With RGB color
        mock_device_state.color = RGBColor(r=255, g=0, b=0)
        mock_device_state.color_temp_kelvin = None
        assert entity.color_mode in entity.supported_color_modes

        # With color temp
        mock_device_state.color = None
        mock_device_state.color_temp_kelvin = 5000
        assert entity.color_mode in entity.supported_color_modes

        # With no state
        mock_coordinator.get_state.return_value = None
        assert entity.color_mode in entity.supported_color_modes

        # With empty state
        mock_device_state.color = None
        mock_device_state.color_temp_kelvin = None
        mock_coordinator.get_state.return_value = mock_device_state
        assert entity.color_mode in entity.supported_color_modes


class TestBrightnessConversion:
    """Test brightness conversion between HA (0-255) and device scales."""

    def test_device_to_ha_normal(self, mock_coordinator, mock_light_device):
        """Test normal brightness conversion from device (0-100) to HA (0-255)."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=False)
        # Device range is (0, 100); device=50 → 50% → 127
        assert entity._device_to_ha_brightness(50) == 127
        assert entity._device_to_ha_brightness(100) == 255
        assert entity._device_to_ha_brightness(0) == 0

    def test_device_to_ha_clamped_when_exceeding_range(self, mock_coordinator, mock_light_device):
        """Test brightness is clamped to 255 when device value exceeds declared range.

        Regression test for GitHub issue #24: H6104 returns brightness=254
        from API despite declaring range (0, 100), causing HA to show 255%.
        """
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=False)
        # Device claims range (0, 100) but API returns 254 → unclamped would be 647
        assert entity._device_to_ha_brightness(254) == 255

    def test_device_to_ha_clamped_at_zero(self, mock_coordinator, mock_light_device):
        """Test brightness is clamped to 0 for negative device values."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=False)
        assert entity._device_to_ha_brightness(-10) == 0

    def test_ha_to_device_clamped_to_device_range(self, mock_coordinator, mock_light_device):
        """Test HA-to-device conversion is clamped to device range."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=False)
        # Device range is (0, 100)
        assert entity._ha_to_device_brightness(255) == 100
        assert entity._ha_to_device_brightness(0) == 0

    def test_device_to_ha_with_254_range(self, mock_coordinator):
        """Test brightness conversion with 0-254 device range."""
        device = GoveeDevice(
            device_id="TEST:ID:00:00:00:00:00:00",
            sku="H6104",
            name="Test Light",
            device_type="devices.types.light",
            capabilities=(
                GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
                GoveeCapability(
                    type=CAPABILITY_RANGE,
                    instance=INSTANCE_BRIGHTNESS,
                    parameters={"range": {"min": 0, "max": 254}},
                ),
                GoveeCapability(
                    type=CAPABILITY_COLOR_SETTING,
                    instance=INSTANCE_COLOR_RGB,
                    parameters={},
                ),
                GoveeCapability(
                    type=CAPABILITY_COLOR_SETTING,
                    instance=INSTANCE_COLOR_TEMP,
                    parameters={"range": {"min": 2000, "max": 9000}},
                ),
            ),
            is_group=False,
        )
        entity = GoveeLightEntity(mock_coordinator, device, enable_scenes=False)
        assert entity._device_to_ha_brightness(254) == 255
        assert entity._device_to_ha_brightness(127) == 127
        assert entity._device_to_ha_brightness(0) == 0


class TestSegmentModeSetup:
    """async_setup_entry's per-device segment_mode dispatch, incl. SEGMENT_MODE_BOTH."""

    async def _setup(self, device, segment_mode):
        from custom_components.govee import light as light_mod

        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {"segment_mode_by_device": {device.device_id: segment_mode}}
        added: list = []
        await light_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
        return added

    def _counts(self, added):
        grouped = sum(type(e).__name__ == "GoveeGroupedSegmentEntity" for e in added)
        individual = sum(type(e).__name__ == "GoveeSegmentEntity" for e in added)
        return grouped, individual

    async def test_grouped_mode_creates_only_the_grouped_entity(self, mock_rgbic_device):
        added = await self._setup(mock_rgbic_device, "grouped")
        assert self._counts(added) == (1, 0)

    async def test_individual_mode_creates_only_individual_entities(self, mock_rgbic_device):
        added = await self._setup(mock_rgbic_device, "individual")
        assert self._counts(added) == (0, mock_rgbic_device.segment_count)

    async def test_both_mode_creates_grouped_and_individual_entities(self, mock_rgbic_device):
        """SEGMENT_MODE_BOTH: the grouped entity is additive, not a replacement."""
        added = await self._setup(mock_rgbic_device, "both")
        assert self._counts(added) == (1, mock_rgbic_device.segment_count)

    async def test_disabled_mode_creates_no_segment_entities(self, mock_rgbic_device):
        added = await self._setup(mock_rgbic_device, "disabled")
        assert self._counts(added) == (0, 0)
