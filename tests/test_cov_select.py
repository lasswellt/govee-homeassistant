"""Coverage tests for the select platform.

The scene, DIY-scene, HDMI-source and music-mode selects had no direct tests
at all; this file covers their option building, ``current_option`` lookup,
``async_select_option`` (including the "None" clear path, the
``ServiceValidationError`` for an unknown option and the ``HomeAssistantError``
for a rejected command), the snapshot id normaliser, the nightlight / snapshot
fallbacks, and every entity-creation branch of ``async_setup_entry``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.govee import select as select_mod
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    ModeCommand,
    MusicModeCommand,
    SceneCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_MODE,
    CAPABILITY_MUSIC_MODE,
    CAPABILITY_ON_OFF,
    CAPABILITY_TOGGLE,
    CAPABILITY_WORK_MODE,
    DEVICE_TYPE_HEATER,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    INSTANCE_DIY,
    INSTANCE_HDMI_SOURCE,
    INSTANCE_MUSIC_MODE,
    INSTANCE_NIGHT_LIGHT,
    INSTANCE_NIGHTLIGHT_SCENE,
    INSTANCE_POWER,
    INSTANCE_SCENE,
    INSTANCE_SNAPSHOT,
    INSTANCE_WORK_MODE,
)
from custom_components.govee.select import (
    SCENE_NONE,
    GoveeDIYSceneSelectEntity,
    GoveeHdmiSourceSelectEntity,
    GoveeMusicModeSelectEntity,
    GoveeNightlightSceneSelectEntity,
    GoveeSceneSelectEntity,
    GoveeSnapshotSelectEntity,
    _snapshot_id,
)
from .coordinator_mocks import follow_online_flag


def _cap(cap_type: str, instance: str, params: dict | None = None) -> GoveeCapability:
    return GoveeCapability(type=cap_type, instance=instance, parameters=params or {})


def _device(device_id: str, sku: str, device_type: str, *caps: GoveeCapability, is_group: bool = False) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name=f"{sku} device",
        device_type=device_type,
        capabilities=tuple(caps),
        is_group=is_group,
    )


_POWER = _cap(CAPABILITY_ON_OFF, INSTANCE_POWER)
_SCENES = _cap(CAPABILITY_DYNAMIC_SCENE, INSTANCE_SCENE)
_DIY = _cap(CAPABILITY_DYNAMIC_SCENE, INSTANCE_DIY)
_MUSIC_OPTIONS = [{"name": "Rhythm", "value": 1}, {"name": "Spectrum", "value": 2}, {"name": "Rolling", "value": 3}]
_MUSIC = _cap(
    CAPABILITY_MUSIC_MODE,
    INSTANCE_MUSIC_MODE,
    {
        "dataType": "STRUCT",
        "fields": [
            {"fieldName": "musicMode", "dataType": "ENUM", "options": list(_MUSIC_OPTIONS)},
            {"fieldName": "sensitivity", "dataType": "INTEGER", "range": {"min": 0, "max": 100}},
        ],
    },
)
_HEATER_WORK_MODE = _cap(
    CAPABILITY_WORK_MODE,
    INSTANCE_WORK_MODE,
    {
        "fields": [
            {"fieldName": "workMode", "options": [{"name": "Low", "value": 1}, {"name": "High", "value": 3}]},
            {
                "fieldName": "modeValue",
                "options": [{"defaultValue": 0, "name": "Low"}, {"defaultValue": 0, "name": "High"}],
            },
        ],
    },
)
_NIGHTLIGHT_SCENE = _cap(
    CAPABILITY_MODE,
    INSTANCE_NIGHTLIGHT_SCENE,
    {"options": [{"name": "Forest", "value": 0}, {"name": "Ocean", "value": 1}]},
)
_SNAPSHOT = _cap(
    CAPABILITY_DYNAMIC_SCENE,
    INSTANCE_SNAPSHOT,
    {"options": [{"name": "Ambient", "value": 3862070}, {"name": "Movie Night", "value": {"paramId": 77}}]},
)

DIY_SCENES = [{"name": "Lava", "value": 101}, {"name": "Waves", "value": 102}]


def _coordinator(device: GoveeDevice, state: GoveeDeviceState | None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device}
    coordinator.last_update_success = True
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.async_clear_scene = AsyncMock()
    coordinator.async_send_diy_scene = AsyncMock(return_value=True)
    coordinator.async_get_scenes = AsyncMock(return_value=[])
    coordinator.async_get_diy_scenes = AsyncMock(return_value=[])
    return follow_online_flag(coordinator)


def _state(device: GoveeDevice, **kwargs) -> GoveeDeviceState:
    state = GoveeDeviceState(device_id=device.device_id, online=True, power_state=True)
    for key, value in kwargs.items():
        setattr(state, key, value)
    return state


def _sent(coordinator: MagicMock) -> list:
    return [c[0][1] for c in coordinator.async_control_device.await_args_list]


# --------------------------------------------------------------------------- #
# Scene select
# --------------------------------------------------------------------------- #


@pytest.fixture
def scene_select(mock_light_device, mock_scenes):
    state = _state(mock_light_device)
    entity = GoveeSceneSelectEntity(_coordinator(mock_light_device, state), mock_light_device, mock_scenes)
    entity.async_write_ha_state = MagicMock()
    return entity


class TestSceneSelect:
    def test_options_and_unique_id(self, scene_select, mock_light_device):
        assert scene_select.options == [SCENE_NONE, "Sunrise", "Sunset", "Party", "Movie"]
        assert scene_select.unique_id == f"{mock_light_device.device_id}_scene_select"

    def test_duplicate_names_are_suffixed_and_keep_their_own_ids(self, mock_light_device):
        scenes = [
            {"name": "Rainbow", "value": {"id": 1}},
            {"name": "Rainbow", "value": {"id": 2}},
            {"name": "Rainbow", "value": {"id": 3}},
        ]
        entity = GoveeSceneSelectEntity(_coordinator(mock_light_device, None), mock_light_device, scenes)

        assert entity.options == [SCENE_NONE, "Rainbow", "Rainbow (1)", "Rainbow (2)"]
        assert entity._scene_map["Rainbow (2)"] == (3, "Rainbow")
        assert entity._scene_id_to_option["3"] == "Rainbow (2)"

    def test_missing_fields_get_defaults(self, mock_light_device):
        scenes = [{"value": {"id": 5}}, {"name": "Nameless id"}]
        entity = GoveeSceneSelectEntity(_coordinator(mock_light_device, None), mock_light_device, scenes)

        assert entity.options == [SCENE_NONE, "Scene 5", "Nameless id"]
        assert entity._scene_map["Nameless id"] == (0, "Nameless id")

    def test_current_option_none_without_state_or_scene(self, scene_select):
        assert scene_select.current_option == SCENE_NONE
        scene_select.coordinator.get_state.return_value = None
        assert scene_select.current_option == SCENE_NONE

    def test_current_option_maps_active_scene_id(self, scene_select):
        scene_select.coordinator.get_state.return_value.active_scene = "2"
        assert scene_select.current_option == "Sunset"

    def test_current_option_unknown_id_reads_none(self, scene_select):
        scene_select.coordinator.get_state.return_value.active_scene = "999"
        assert scene_select.current_option == SCENE_NONE

    def test_current_option_none_while_light_off_and_back_when_on(self, scene_select):
        """The scene is hidden while off but kept, so it shows again once on."""
        state = scene_select.coordinator.get_state.return_value
        state.active_scene = "2"

        state.power_state = False
        assert scene_select.current_option == SCENE_NONE
        assert state.active_scene == "2"

        state.power_state = True
        assert scene_select.current_option == "Sunset"

    def test_current_option_shown_when_power_unknown(self, scene_select):
        """Only a known-off light hides the scene."""
        state = scene_select.coordinator.get_state.return_value
        state.active_scene = "2"
        state.power_state = None
        assert scene_select.current_option == "Sunset"

    async def test_select_none_while_off_sends_nothing(self, scene_select):
        """Clearing would switch the light on, so "None" while off is a no-op."""
        state = scene_select.coordinator.get_state.return_value
        state.active_scene = "2"
        state.power_state = False

        await scene_select.async_select_option(SCENE_NONE)

        scene_select.coordinator.async_clear_scene.assert_not_awaited()
        scene_select.coordinator.async_control_device.assert_not_awaited()
        assert state.active_scene == "2"
        scene_select.async_write_ha_state.assert_called_once()

    async def test_select_scene_sends_scene_command(self, scene_select, mock_light_device):
        await scene_select.async_select_option("Party")

        assert _sent(scene_select.coordinator) == [SceneCommand(scene_id=3, scene_name="Party")]
        assert scene_select.coordinator.async_control_device.await_args[0][0] == mock_light_device.device_id
        scene_select.async_write_ha_state.assert_called_once()
        scene_select.coordinator.async_clear_scene.assert_not_awaited()

    async def test_select_suffixed_duplicate_uses_original_name(self, mock_light_device):
        scenes = [{"name": "Rainbow", "value": {"id": 1}}, {"name": "Rainbow", "value": {"id": 2}}]
        entity = GoveeSceneSelectEntity(_coordinator(mock_light_device, None), mock_light_device, scenes)
        entity.async_write_ha_state = MagicMock()

        await entity.async_select_option("Rainbow (1)")

        assert _sent(entity.coordinator) == [SceneCommand(scene_id=2, scene_name="Rainbow")]

    async def test_select_none_clears_scene_through_coordinator(self, scene_select, mock_light_device):
        await scene_select.async_select_option(SCENE_NONE)

        scene_select.coordinator.async_clear_scene.assert_awaited_once_with(mock_light_device.device_id)
        scene_select.coordinator.async_control_device.assert_not_awaited()
        scene_select.async_write_ha_state.assert_called_once()

    async def test_unknown_option_raises_validation_error(self, scene_select):
        with pytest.raises(ServiceValidationError):
            await scene_select.async_select_option("Disco")

        scene_select.coordinator.async_control_device.assert_not_awaited()
        scene_select.async_write_ha_state.assert_not_called()

    async def test_rejected_command_raises(self, scene_select):
        scene_select.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await scene_select.async_select_option("Movie")

        scene_select.async_write_ha_state.assert_not_called()


# --------------------------------------------------------------------------- #
# DIY scene select
# --------------------------------------------------------------------------- #


@pytest.fixture
def diy_device():
    return _device("AA:BB:CC:DD:EE:FF:0D:1Y", "H6167", DEVICE_TYPE_LIGHT, _POWER, _SCENES, _DIY)


@pytest.fixture
def diy_select(diy_device):
    entity = GoveeDIYSceneSelectEntity(_coordinator(diy_device, _state(diy_device)), diy_device, DIY_SCENES)
    entity.async_write_ha_state = MagicMock()
    return entity


class TestDIYSceneSelect:
    def test_options_and_unique_id(self, diy_select, diy_device):
        assert diy_select.options == [SCENE_NONE, "Lava", "Waves"]
        assert diy_select.unique_id == f"{diy_device.device_id}_diy_scene_select"
        # DIY values are bare ints, not {"id": ...} dicts.
        assert diy_select._scene_map["Waves"] == (102, "Waves")
        assert diy_select._scene_id_to_option["102"] == "Waves"

    def test_duplicates_and_defaults(self, diy_device):
        scenes = [{"name": "Glow", "value": 1}, {"name": "Glow", "value": 2}, {"value": 3}, {"name": "No id"}]
        entity = GoveeDIYSceneSelectEntity(_coordinator(diy_device, None), diy_device, scenes)

        assert entity.options == [SCENE_NONE, "Glow", "Glow (1)", "DIY 3", "No id"]
        assert entity._scene_map["Glow (1)"] == (2, "Glow")
        assert entity._scene_map["No id"] == (0, "No id")

    def test_available_follows_coordinator_and_device_state(self, diy_select):
        assert diy_select.available is True
        diy_select.coordinator.get_state.return_value.online = False
        assert diy_select.available is False
        diy_select.coordinator.get_state.return_value.online = True
        diy_select.coordinator.last_update_success = False
        assert diy_select.available is False

    def test_available_false_without_state(self, diy_select):
        diy_select.coordinator.get_state.return_value = None
        assert diy_select.available is False

    def test_current_option(self, diy_select):
        assert diy_select.current_option == SCENE_NONE
        diy_select.coordinator.get_state.return_value.active_diy_scene = "101"
        assert diy_select.current_option == "Lava"
        diy_select.coordinator.get_state.return_value.active_diy_scene = "555"
        assert diy_select.current_option == SCENE_NONE
        diy_select.coordinator.get_state.return_value = None
        assert diy_select.current_option == SCENE_NONE

    def test_current_option_none_while_light_off_and_back_when_on(self, diy_select):
        """The DIY scene is hidden while off but kept, so it shows again once on."""
        state = diy_select.coordinator.get_state.return_value
        state.active_diy_scene = "101"

        state.power_state = False
        assert diy_select.current_option == SCENE_NONE
        assert state.active_diy_scene == "101"

        state.power_state = True
        assert diy_select.current_option == "Lava"

    async def test_select_none_while_off_sends_nothing(self, diy_select):
        """Clearing would switch the light on, so "None" while off is a no-op."""
        state = diy_select.coordinator.get_state.return_value
        state.active_diy_scene = "101"
        state.power_state = False

        await diy_select.async_select_option(SCENE_NONE)

        diy_select.coordinator.async_clear_scene.assert_not_awaited()
        assert state.active_diy_scene == "101"
        diy_select.async_write_ha_state.assert_called_once()

    async def test_select_sends_through_diy_path(self, diy_select, diy_device):
        await diy_select.async_select_option("Waves")

        diy_select.coordinator.async_send_diy_scene.assert_awaited_once_with(
            diy_device.device_id, scene_id=102, scene_name="Waves"
        )
        # DIY scenes never go through the generic control path.
        diy_select.coordinator.async_control_device.assert_not_awaited()
        diy_select.async_write_ha_state.assert_called_once()

    async def test_select_none_clears_scene(self, diy_select, diy_device):
        await diy_select.async_select_option(SCENE_NONE)

        diy_select.coordinator.async_clear_scene.assert_awaited_once_with(diy_device.device_id)
        diy_select.coordinator.async_send_diy_scene.assert_not_awaited()
        diy_select.async_write_ha_state.assert_called_once()

    async def test_unknown_option_raises_validation_error(self, diy_select):
        with pytest.raises(ServiceValidationError):
            await diy_select.async_select_option("Nope")

        diy_select.coordinator.async_send_diy_scene.assert_not_awaited()

    async def test_rejected_diy_scene_raises(self, diy_select):
        diy_select.coordinator.async_send_diy_scene = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await diy_select.async_select_option("Lava")

        diy_select.async_write_ha_state.assert_not_called()


# --------------------------------------------------------------------------- #
# HDMI source select
# --------------------------------------------------------------------------- #


@pytest.fixture
def hdmi_select(mock_hdmi_device, mock_hdmi_device_state):
    entity = GoveeHdmiSourceSelectEntity(
        _coordinator(mock_hdmi_device, mock_hdmi_device_state),
        mock_hdmi_device,
        mock_hdmi_device.get_hdmi_source_options(),
    )
    entity.async_write_ha_state = MagicMock()
    return entity


class TestHdmiSourceSelect:
    def test_options_and_unique_id(self, hdmi_select, mock_hdmi_device):
        assert hdmi_select.options == ["HDMI 1", "HDMI 2", "HDMI 3", "HDMI 4"]
        assert hdmi_select.unique_id == f"{mock_hdmi_device.device_id}_hdmi_source_select"
        assert hdmi_select._option_map["HDMI 3"] == 3

    def test_malformed_options_are_skipped(self, mock_hdmi_device):
        options = [{"name": "", "value": 1}, {"name": "No value"}, {"value": 2}, {"name": "HDMI 9", "value": 9}]
        entity = GoveeHdmiSourceSelectEntity(_coordinator(mock_hdmi_device, None), mock_hdmi_device, options)

        assert entity.options == ["HDMI 9"]

    def test_current_option_from_state(self, hdmi_select, mock_hdmi_device_state):
        assert hdmi_select.current_option == "HDMI 1"
        mock_hdmi_device_state.hdmi_source = 3
        assert hdmi_select.current_option == "HDMI 3"

    def test_current_option_falls_back_to_first(self, hdmi_select, mock_hdmi_device_state):
        mock_hdmi_device_state.hdmi_source = None
        assert hdmi_select.current_option == "HDMI 1"
        mock_hdmi_device_state.hdmi_source = 9  # not one of the offered ports
        assert hdmi_select.current_option == "HDMI 1"
        hdmi_select.coordinator.get_state.return_value = None
        assert hdmi_select.current_option == "HDMI 1"

    def test_current_option_none_without_options(self, mock_hdmi_device):
        entity = GoveeHdmiSourceSelectEntity(_coordinator(mock_hdmi_device, None), mock_hdmi_device, [])
        assert entity.current_option is None

    async def test_select_sends_mode_command(self, hdmi_select, mock_hdmi_device):
        await hdmi_select.async_select_option("HDMI 4")

        assert _sent(hdmi_select.coordinator) == [ModeCommand(mode_instance=INSTANCE_HDMI_SOURCE, value=4)]
        assert hdmi_select.coordinator.async_control_device.await_args[0][0] == mock_hdmi_device.device_id
        hdmi_select.async_write_ha_state.assert_called_once()

    async def test_unknown_option_raises_validation_error(self, hdmi_select):
        with pytest.raises(ServiceValidationError):
            await hdmi_select.async_select_option("HDMI 7")

        hdmi_select.coordinator.async_control_device.assert_not_awaited()

    async def test_rejected_command_raises(self, hdmi_select):
        hdmi_select.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await hdmi_select.async_select_option("HDMI 2")

        hdmi_select.async_write_ha_state.assert_not_called()


# --------------------------------------------------------------------------- #
# Music mode select
# --------------------------------------------------------------------------- #


@pytest.fixture
def music_device():
    return _device("AA:BB:CC:DD:EE:FF:0F:00", "H6199", DEVICE_TYPE_LIGHT, _POWER, _MUSIC)


@pytest.fixture
def music_select(music_device):
    state = _state(music_device, music_mode_name="Spectrum", music_sensitivity=80)
    entity = GoveeMusicModeSelectEntity(
        _coordinator(music_device, state), music_device, music_device.get_music_mode_options()
    )
    entity.async_write_ha_state = MagicMock()
    return entity


class TestMusicModeSelect:
    def test_options_and_unique_id(self, music_select, music_device):
        assert music_device.has_struct_music_mode is True
        assert music_select.options == ["Rhythm", "Spectrum", "Rolling"]
        assert music_select.unique_id == f"{music_device.device_id}_music_mode_select"

    def test_malformed_options_are_skipped(self, music_device):
        options = [{"name": "Rhythm"}, {"value": 2}, {"name": "Rolling", "value": 3}]
        entity = GoveeMusicModeSelectEntity(_coordinator(music_device, None), music_device, options)
        assert entity.options == ["Rolling"]

    def test_current_option(self, music_select):
        assert music_select.current_option == "Spectrum"
        music_select.coordinator.get_state.return_value.music_mode_name = "Energic"  # not offered
        assert music_select.current_option == "Rhythm"
        music_select.coordinator.get_state.return_value.music_mode_name = None
        assert music_select.current_option == "Rhythm"
        music_select.coordinator.get_state.return_value = None
        assert music_select.current_option == "Rhythm"

    def test_current_option_none_without_options(self, music_device):
        entity = GoveeMusicModeSelectEntity(_coordinator(music_device, None), music_device, [])
        assert entity.current_option is None

    async def test_select_keeps_current_sensitivity(self, music_select, music_device):
        await music_select.async_select_option("Rolling")

        assert _sent(music_select.coordinator) == [MusicModeCommand(music_mode=3, sensitivity=80, auto_color=1)]
        assert music_select.coordinator.async_control_device.await_args[0][0] == music_device.device_id
        music_select.async_write_ha_state.assert_called_once()

    async def test_select_defaults_sensitivity_when_unknown(self, music_select):
        music_select.coordinator.get_state.return_value.music_sensitivity = None

        await music_select.async_select_option("Rhythm")

        assert _sent(music_select.coordinator) == [MusicModeCommand(music_mode=1, sensitivity=50, auto_color=1)]

    async def test_select_without_state_uses_default_sensitivity(self, music_select):
        music_select.coordinator.get_state.return_value = None

        await music_select.async_select_option("Spectrum")

        assert _sent(music_select.coordinator) == [MusicModeCommand(music_mode=2, sensitivity=50, auto_color=1)]

    async def test_unknown_option_raises_validation_error(self, music_select):
        with pytest.raises(ServiceValidationError):
            await music_select.async_select_option("Disco")

        music_select.coordinator.async_control_device.assert_not_awaited()

    async def test_rejected_command_raises(self, music_select):
        music_select.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await music_select.async_select_option("Rhythm")

        music_select.async_write_ha_state.assert_not_called()


# --------------------------------------------------------------------------- #
# Nightlight scene + snapshot fallbacks
# --------------------------------------------------------------------------- #


@pytest.fixture
def nightlight_device():
    return _device(
        "AA:BB:CC:DD:EE:FF:50:89",
        "H5089",
        DEVICE_TYPE_PLUG,
        _POWER,
        _cap(CAPABILITY_TOGGLE, INSTANCE_NIGHT_LIGHT),
        _NIGHTLIGHT_SCENE,
    )


class TestNightlightSceneSelectFallbacks:
    def _entity(self, device, state):
        entity = GoveeNightlightSceneSelectEntity(
            _coordinator(device, state), device, device.get_nightlight_scene_options()
        )
        entity.async_write_ha_state = MagicMock()
        return entity

    def test_current_option_falls_back_to_first(self, nightlight_device):
        entity = self._entity(nightlight_device, _state(nightlight_device, nightlight_scene=None))
        assert entity.current_option == "Forest"
        entity.coordinator.get_state.return_value.nightlight_scene = 42
        assert entity.current_option == "Forest"
        entity.coordinator.get_state.return_value = None
        assert entity.current_option == "Forest"

    def test_current_option_none_without_options(self, nightlight_device):
        entity = GoveeNightlightSceneSelectEntity(_coordinator(nightlight_device, None), nightlight_device, [])
        assert entity.current_option is None

    async def test_unknown_option_raises_validation_error(self, nightlight_device):
        entity = self._entity(nightlight_device, _state(nightlight_device))

        with pytest.raises(ServiceValidationError):
            await entity.async_select_option("Volcano")

        entity.coordinator.async_control_device.assert_not_awaited()

    async def test_rejected_command_raises(self, nightlight_device):
        entity = self._entity(nightlight_device, _state(nightlight_device))
        entity.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_select_option("Ocean")

        entity.async_write_ha_state.assert_not_called()


class TestSnapshotId:
    def test_scalar_values(self):
        assert _snapshot_id(7) == 7
        assert _snapshot_id("42") == 42
        assert _snapshot_id(None) is None
        assert _snapshot_id("not-a-number") is None

    def test_struct_values(self):
        assert _snapshot_id({"id": 5}) == 5
        assert _snapshot_id({"paramId": "9"}) == 9
        assert _snapshot_id({"id": 5, "paramId": 9}) == 5
        assert _snapshot_id({"name": "no id here"}) is None
        assert _snapshot_id({"id": "abc"}) is None


class TestSnapshotSelectEdges:
    @pytest.fixture
    def snapshot_device(self):
        return _device("AA:BB:CC:DD:EE:FF:13:10", "H1310", DEVICE_TYPE_LIGHT, _POWER, _SNAPSHOT)

    @pytest.fixture
    def entity(self, snapshot_device):
        e = GoveeSnapshotSelectEntity(
            _coordinator(snapshot_device, _state(snapshot_device)),
            snapshot_device,
            snapshot_device.get_snapshot_options(),
        )
        e.async_write_ha_state = MagicMock()
        return e

    async def test_unknown_option_raises_validation_error(self, entity):
        with pytest.raises(ServiceValidationError):
            await entity.async_select_option("Nonexistent")

        entity.coordinator.async_control_device.assert_not_awaited()

    def test_struct_valued_option_matches_state(self, entity):
        entity.coordinator.get_state.return_value.active_snapshot = 77
        assert entity.current_option == "Movie Night"
        entity.coordinator.get_state.return_value.active_snapshot = 1
        assert entity.current_option is None

    async def test_struct_valued_option_is_tracked_after_select(self, entity):
        await entity.async_select_option("Movie Night")

        assert entity.coordinator.async_control_device.await_args[0][1].snapshot_value == {"paramId": 77}
        assert entity.current_option == "Movie Night"

    async def test_select_without_state_still_sends(self, entity):
        entity.coordinator.get_state.return_value = None

        await entity.async_select_option("Ambient")

        entity.coordinator.async_control_device.assert_awaited_once()
        entity.async_write_ha_state.assert_called_once()

    async def test_rejected_command_raises_and_does_not_track(self, entity):
        entity.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_select_option("Ambient")

        assert entity.current_option is None


# --------------------------------------------------------------------------- #
# async_setup_entry
# --------------------------------------------------------------------------- #


async def _setup(
    *devices: GoveeDevice,
    options: dict | None = None,
    scenes: list | None = None,
    diy_scenes: list | None = None,
) -> tuple[list, MagicMock]:
    coordinator = MagicMock()
    coordinator.devices = {d.device_id: d for d in devices}
    coordinator.async_get_scenes = AsyncMock(return_value=scenes or [])
    coordinator.async_get_diy_scenes = AsyncMock(return_value=diy_scenes or [])
    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.options = options or {}
    added: list = []
    await select_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
    return added, coordinator


def _names(added: list) -> list[str]:
    return [type(e).__name__ for e in added]


class TestSelectPlatformSetup:
    async def test_group_devices_get_nothing_and_are_never_queried(self, mock_group_device, mock_scenes):
        added, coordinator = await _setup(mock_group_device, scenes=mock_scenes)

        assert added == []
        coordinator.async_get_scenes.assert_not_awaited()
        coordinator.async_get_diy_scenes.assert_not_awaited()

    async def test_scene_select_created_from_fetched_scenes(self, mock_light_device, mock_scenes):
        added, coordinator = await _setup(mock_light_device, scenes=mock_scenes)

        assert _names(added) == ["GoveeSceneSelectEntity"]
        coordinator.async_get_scenes.assert_awaited_once_with(mock_light_device.device_id)
        assert added[0].options == [SCENE_NONE, "Sunrise", "Sunset", "Party", "Movie"]

    async def test_no_scene_select_without_scenes(self, mock_light_device):
        added, _ = await _setup(mock_light_device, scenes=[])

        assert added == []

    async def test_scene_select_gated_by_option(self, mock_light_device, mock_scenes):
        added, coordinator = await _setup(mock_light_device, scenes=mock_scenes, options={"enable_scenes": False})

        assert added == []
        coordinator.async_get_scenes.assert_not_awaited()

    async def test_diy_select_created_from_fetched_diy_scenes(self, diy_device):
        added, coordinator = await _setup(diy_device, diy_scenes=DIY_SCENES)

        assert _names(added) == ["GoveeDIYSceneSelectEntity"]
        coordinator.async_get_diy_scenes.assert_awaited_once_with(diy_device.device_id)
        assert added[0].options == [SCENE_NONE, "Lava", "Waves"]

    async def test_diy_select_gated_by_option_and_empty_list(self, diy_device):
        added, coordinator = await _setup(diy_device, diy_scenes=DIY_SCENES, options={"enable_diy_scenes": False})
        assert added == []
        coordinator.async_get_diy_scenes.assert_not_awaited()

        added, _ = await _setup(diy_device, diy_scenes=[])
        assert added == []

    async def test_hdmi_select_created(self, mock_hdmi_device):
        added, _ = await _setup(mock_hdmi_device)

        assert _names(added) == ["GoveeHdmiSourceSelectEntity"]
        assert added[0].options == ["HDMI 1", "HDMI 2", "HDMI 3", "HDMI 4"]

    async def test_hdmi_select_skipped_without_options(self):
        device = _device(
            "AA:BB:CC:DD:EE:FF:66:04", "H6604", DEVICE_TYPE_LIGHT, _POWER, _cap(CAPABILITY_MODE, INSTANCE_HDMI_SOURCE)
        )

        added, _ = await _setup(device)

        assert added == []

    async def test_music_mode_select_created(self, music_device):
        added, _ = await _setup(music_device)

        assert _names(added) == ["GoveeMusicModeSelectEntity"]
        assert added[0].options == ["Rhythm", "Spectrum", "Rolling"]

    async def test_music_mode_select_skipped_without_options(self):
        struct_without_modes = _cap(CAPABILITY_MUSIC_MODE, INSTANCE_MUSIC_MODE, {"fields": []})
        device = _device("AA:BB:CC:DD:EE:FF:61:99", "H6199", DEVICE_TYPE_LIGHT, _POWER, struct_without_modes)

        added, _ = await _setup(device)

        assert added == []

    async def test_heater_fan_speed_select_created(self):
        heater = _device("AA:BB:CC:DD:EE:FF:71:30", "H7130", DEVICE_TYPE_HEATER, _POWER, _HEATER_WORK_MODE)

        added, _ = await _setup(heater)

        assert _names(added) == ["GoveeFanSpeedSelectEntity"]
        assert added[0].options == ["Low", "High"]

    async def test_heater_without_work_mode_gets_no_fan_speed_select(self):
        heater = _device("AA:BB:CC:DD:EE:FF:71:31", "H7130", DEVICE_TYPE_HEATER, _POWER)

        added, _ = await _setup(heater)

        assert added == []

    async def test_purifier_mode_select_created(self, mock_air_purifier_device):
        added, _ = await _setup(mock_air_purifier_device)

        assert _names(added) == ["GoveePurifierModeSelectEntity"]
        assert added[0].options == ["Sleep", "Low", "High"]

    async def test_nightlight_scene_select_created(self, nightlight_device):
        added, _ = await _setup(nightlight_device)

        assert _names(added) == ["GoveeNightlightSceneSelectEntity"]
        assert added[0].options == ["Forest", "Ocean"]

    async def test_one_device_can_get_several_selects(self, mock_scenes):
        device = _device(
            "AA:BB:CC:DD:EE:FF:AA:AA", "H6199", DEVICE_TYPE_LIGHT, _POWER, _SCENES, _DIY, _MUSIC, _SNAPSHOT
        )

        added, _ = await _setup(device, scenes=mock_scenes, diy_scenes=DIY_SCENES)

        assert _names(added) == [
            "GoveeSceneSelectEntity",
            "GoveeSnapshotSelectEntity",
            "GoveeDIYSceneSelectEntity",
            "GoveeMusicModeSelectEntity",
        ]
        assert len({e.unique_id for e in added}) == 4
