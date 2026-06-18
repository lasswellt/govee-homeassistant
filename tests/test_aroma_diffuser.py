"""Test Govee aroma diffuser support (H7161, issue #99)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    ModeCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_EVENT,
    CAPABILITY_MODE,
    CAPABILITY_ON_OFF,
    DEVICE_TYPE_AROMA_DIFFUSER,
    INSTANCE_POWER,
    INSTANCE_PRESET_SCENE,
)

# The 5 real scene options from the issue-#99 diagnostics (German locale —
# names are localized, the control payload must use the integer id).
PRESET_SCENE_OPTIONS = [
    {"name": "Bach", "value": 171396},
    {"name": "Wärme am Kamin", "value": 171397},
    {"name": "Morgen", "value": 171398},
    {"name": "Gutenachtkuss", "value": 171399},
    {"name": "Nachtlicht", "value": 171400},
]


# ==============================================================================
# Aroma Diffuser Device Fixtures
# ==============================================================================


@pytest.fixture
def aroma_diffuser_capabilities() -> tuple[GoveeCapability, ...]:
    """Capabilities for an H7161 aroma diffuser (matches real diagnostics)."""
    return (
        GoveeCapability(
            type=CAPABILITY_ON_OFF,
            instance=INSTANCE_POWER,
            parameters={},
        ),
        GoveeCapability(
            type=CAPABILITY_MODE,
            instance=INSTANCE_PRESET_SCENE,
            parameters={"options": list(PRESET_SCENE_OPTIONS)},
        ),
        # Present on the device but no transport delivers it (mqtt=false, absent
        # from REST) — intentionally NOT surfaced as an entity (Tier 2 deferral).
        GoveeCapability(
            type=CAPABILITY_EVENT,
            instance="lackWaterEvent",
            parameters={},
        ),
    )


@pytest.fixture
def mock_aroma_diffuser_device(aroma_diffuser_capabilities) -> GoveeDevice:
    """Create a mock H7161 aroma diffuser device."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:99",
        sku="H7161",
        name="Aroma Diffuser Pro",
        device_type=DEVICE_TYPE_AROMA_DIFFUSER,
        capabilities=aroma_diffuser_capabilities,
        is_group=False,
    )


# ==============================================================================
# Device Type Detection
# ==============================================================================


class TestAromaDiffuserDetection:
    """Aroma diffuser device-type detection (issue #99)."""

    def test_is_aroma_diffuser(self, mock_aroma_diffuser_device):
        assert mock_aroma_diffuser_device.is_aroma_diffuser is True

    def test_is_aroma_diffuser_false_for_light(self, mock_light_device):
        assert mock_light_device.is_aroma_diffuser is False

    def test_not_other_appliance_types(self, mock_aroma_diffuser_device):
        # Must not accidentally pick up heater/kettle/purifier/fan branches.
        assert mock_aroma_diffuser_device.is_heater is False
        assert mock_aroma_diffuser_device.is_kettle is False
        assert mock_aroma_diffuser_device.is_purifier is False
        assert mock_aroma_diffuser_device.is_fan is False

    def test_not_a_light_device(self, mock_aroma_diffuser_device):
        # No light entity should be created for the diffuser.
        assert mock_aroma_diffuser_device.is_light_device is False

    def test_supports_power(self, mock_aroma_diffuser_device):
        assert mock_aroma_diffuser_device.supports_power is True


# ==============================================================================
# Preset Scene Capability Parsing
# ==============================================================================


class TestPresetSceneParsing:
    """get_preset_scene_options extraction."""

    def test_get_preset_scene_options(self, mock_aroma_diffuser_device):
        options = mock_aroma_diffuser_device.get_preset_scene_options()
        assert len(options) == 5
        assert options[0] == {"name": "Bach", "value": 171396}
        assert options[4] == {"name": "Nachtlicht", "value": 171400}

    def test_get_preset_scene_options_empty(self):
        device = GoveeDevice(
            device_id="test",
            sku="H7161",
            name="Test Diffuser",
            device_type=DEVICE_TYPE_AROMA_DIFFUSER,
            capabilities=(
                GoveeCapability(
                    type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
                ),
            ),
        )
        assert device.get_preset_scene_options() == []


# ==============================================================================
# State Parsing
# ==============================================================================


class TestPresetSceneState:
    """presetScene value parsed into state (parallel to hdmiSource)."""

    def test_update_from_api_sets_preset_scene(self):
        state = GoveeDeviceState.create_empty("AA:BB:CC:DD:EE:FF:00:99")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": CAPABILITY_MODE,
                        "instance": "presetScene",
                        "state": {"value": 171397},
                    }
                ]
            }
        )
        assert state.preset_scene == 171397

    def test_update_from_api_empty_preset_scene_is_none(self):
        # Govee returns "" for an unset/offline mode — must not crash or coerce.
        state = GoveeDeviceState.create_empty("AA:BB:CC:DD:EE:FF:00:99")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": CAPABILITY_MODE,
                        "instance": "presetScene",
                        "state": {"value": ""},
                    }
                ]
            }
        )
        assert state.preset_scene is None


# ==============================================================================
# Preset Scene Select Entity
# ==============================================================================


class TestPresetSceneSelectEntity:
    """GoveePresetSceneSelectEntity behavior."""

    @pytest.fixture
    def mock_coordinator(self, mock_aroma_diffuser_device):
        coordinator = MagicMock()
        coordinator.devices = {
            mock_aroma_diffuser_device.device_id: mock_aroma_diffuser_device
        }
        state = GoveeDeviceState(
            device_id=mock_aroma_diffuser_device.device_id,
            online=True,
            power_state=True,
            source="api",
        )
        state.preset_scene = 171398  # Morgen
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.async_control_device = AsyncMock(return_value=True)
        return coordinator

    @pytest.fixture
    def preset_scene_entity(self, mock_coordinator, mock_aroma_diffuser_device):
        from custom_components.govee.select import GoveePresetSceneSelectEntity

        entity = GoveePresetSceneSelectEntity(
            coordinator=mock_coordinator,
            device=mock_aroma_diffuser_device,
            options=mock_aroma_diffuser_device.get_preset_scene_options(),
        )
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
        return entity

    def test_options(self, preset_scene_entity):
        assert preset_scene_entity._attr_options == [
            "Bach",
            "Wärme am Kamin",
            "Morgen",
            "Gutenachtkuss",
            "Nachtlicht",
        ]

    def test_option_map_uses_integer_ids(self, preset_scene_entity):
        assert preset_scene_entity._option_map["Bach"] == 171396
        assert preset_scene_entity._option_map["Nachtlicht"] == 171400

    def test_unique_id(self, preset_scene_entity, mock_aroma_diffuser_device):
        from custom_components.govee.const import SUFFIX_PRESET_SCENE_SELECT

        expected = (
            f"{mock_aroma_diffuser_device.device_id}{SUFFIX_PRESET_SCENE_SELECT}"
        )
        assert preset_scene_entity._attr_unique_id == expected

    def test_current_option_matches_state(self, preset_scene_entity):
        assert preset_scene_entity.current_option == "Morgen"

    def test_current_option_defaults_when_unset(
        self, preset_scene_entity, mock_coordinator
    ):
        state = GoveeDeviceState(
            device_id=preset_scene_entity._device_id,
            online=True,
            power_state=True,
            source="api",
        )
        mock_coordinator.get_state.return_value = state
        assert preset_scene_entity.current_option == "Bach"

    async def test_select_sends_integer_id_not_name(
        self, preset_scene_entity, mock_coordinator
    ):
        await preset_scene_entity.async_select_option("Morgen")

        mock_coordinator.async_control_device.assert_called_once()
        device_id, command = mock_coordinator.async_control_device.call_args[0]
        assert device_id == preset_scene_entity._device_id
        assert isinstance(command, ModeCommand)
        assert command.mode_instance == INSTANCE_PRESET_SCENE
        # Critical: the localized name must map to the integer id.
        assert command.value == 171398

    async def test_select_invalid_option_no_command(
        self, preset_scene_entity, mock_coordinator
    ):
        await preset_scene_entity.async_select_option("Nope")
        mock_coordinator.async_control_device.assert_not_called()


# ==============================================================================
# Platform Wiring (switch guard + select guard)
# ==============================================================================


class TestAromaDiffuserPlatformWiring:
    """The setup guards create the right entities for a diffuser."""

    async def test_switch_setup_creates_appliance_power_switch(
        self, mock_aroma_diffuser_device
    ):
        from custom_components.govee import switch as switch_mod

        coordinator = MagicMock()
        coordinator.devices = {
            mock_aroma_diffuser_device.device_id: mock_aroma_diffuser_device
        }
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []

        await switch_mod.async_setup_entry(
            MagicMock(), entry, lambda ents: added.extend(ents)
        )

        names = [type(e).__name__ for e in added]
        assert "GoveeAppliancePowerSwitchEntity" in names

    def _select_coordinator(self, device):
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        # Scene/DIY fetches are awaited in async_setup_entry — stub them empty.
        coordinator.async_get_scenes = AsyncMock(return_value=[])
        coordinator.async_get_diy_scenes = AsyncMock(return_value=[])
        return coordinator

    async def test_select_setup_creates_preset_scene_select(
        self, mock_aroma_diffuser_device
    ):
        from custom_components.govee import select as select_mod

        coordinator = self._select_coordinator(mock_aroma_diffuser_device)
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {}
        added: list = []

        await select_mod.async_setup_entry(
            MagicMock(), entry, lambda ents: added.extend(ents)
        )

        names = [type(e).__name__ for e in added]
        assert "GoveePresetSceneSelectEntity" in names

    async def test_light_device_gets_no_preset_scene_select(self, mock_light_device):
        from custom_components.govee import select as select_mod

        coordinator = self._select_coordinator(mock_light_device)
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {}
        added: list = []

        await select_mod.async_setup_entry(
            MagicMock(), entry, lambda ents: added.extend(ents)
        )

        names = [type(e).__name__ for e in added]
        assert "GoveePresetSceneSelectEntity" not in names
