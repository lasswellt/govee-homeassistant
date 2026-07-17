"""Test Govee fan platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.fan import (
    GoveeFanEntity,
    PRESET_MODE_NORMAL,
    PRESET_MODE_AUTO,
    WORK_MODE_GEAR,
    WORK_MODE_AUTO,
)
from custom_components.govee.models import (
    OscillationCommand,
    PowerCommand,
    WorkModeCommand,
)

# ==============================================================================
# Fan Entity Property Tests
# ==============================================================================


class TestGoveeFanEntity:
    """Test GoveeFanEntity class."""

    @pytest.fixture
    def mock_coordinator(self, mock_fan_device, mock_fan_device_state):
        """Create a mock coordinator for testing."""
        coordinator = MagicMock()
        coordinator.devices = {mock_fan_device.device_id: mock_fan_device}
        coordinator.get_state = MagicMock(return_value=mock_fan_device_state)
        coordinator.async_control_device = AsyncMock(return_value=True)
        return coordinator

    @pytest.fixture
    def fan_entity(self, mock_coordinator, mock_fan_device):
        """Create a fan entity for testing."""
        return GoveeFanEntity(mock_coordinator, mock_fan_device)

    def test_init(self, fan_entity, mock_fan_device):
        """Test fan entity initialization."""
        assert fan_entity._device == mock_fan_device
        assert fan_entity._device_id == mock_fan_device.device_id

    def test_supported_features(self, fan_entity):
        """Test supported features are correctly set."""
        from homeassistant.components.fan import FanEntityFeature

        features = fan_entity.supported_features
        assert features & FanEntityFeature.TURN_ON
        assert features & FanEntityFeature.TURN_OFF
        assert features & FanEntityFeature.SET_SPEED
        assert features & FanEntityFeature.OSCILLATE
        assert features & FanEntityFeature.PRESET_MODE

    def test_speed_count(self, fan_entity):
        """Test speed count is correctly set."""
        assert fan_entity.speed_count == 3

    def test_preset_modes(self, fan_entity):
        """Test preset modes are correctly set."""
        assert fan_entity.preset_modes == [PRESET_MODE_NORMAL, PRESET_MODE_AUTO]

    def test_is_on(self, fan_entity):
        """Test is_on property."""
        assert fan_entity.is_on is True

    def test_is_on_off(self, fan_entity, mock_coordinator, mock_fan_device_state):
        """Test is_on property when off."""
        mock_fan_device_state.power_state = False
        mock_coordinator.get_state.return_value = mock_fan_device_state
        assert fan_entity.is_on is False

    def test_percentage_medium(self, fan_entity):
        """Test percentage property for medium speed."""
        # Mock state has mode_value=2 (medium) in gear mode
        # With 3 speeds, HA's ordered_list_item_to_percentage returns:
        # Low=33, Medium=66, High=100 (evenly divided)
        percentage = fan_entity.percentage
        assert percentage is not None
        assert percentage == 66  # Medium = 66% (2/3 of range)

    def test_percentage_low(self, fan_entity, mock_coordinator, mock_fan_device_state):
        """Test percentage property for low speed."""
        mock_fan_device_state.mode_value = 1  # Low
        mock_coordinator.get_state.return_value = mock_fan_device_state
        assert fan_entity.percentage == 33  # Low = ~33%

    def test_percentage_high(self, fan_entity, mock_coordinator, mock_fan_device_state):
        """Test percentage property for high speed."""
        mock_fan_device_state.mode_value = 3  # High
        mock_coordinator.get_state.return_value = mock_fan_device_state
        assert fan_entity.percentage == 100  # High = 100%

    def test_percentage_auto_mode(
        self, fan_entity, mock_coordinator, mock_fan_device_state
    ):
        """Test percentage returns None in auto mode."""
        mock_fan_device_state.work_mode = WORK_MODE_AUTO
        mock_coordinator.get_state.return_value = mock_fan_device_state
        # In auto mode, percentage is not applicable
        assert fan_entity.percentage is None

    def test_preset_mode_normal(self, fan_entity):
        """Test preset mode returns Normal for gear mode."""
        assert fan_entity.preset_mode == PRESET_MODE_NORMAL

    def test_preset_mode_auto(
        self, fan_entity, mock_coordinator, mock_fan_device_state
    ):
        """Test preset mode returns Auto for auto mode."""
        mock_fan_device_state.work_mode = WORK_MODE_AUTO
        mock_coordinator.get_state.return_value = mock_fan_device_state
        assert fan_entity.preset_mode == PRESET_MODE_AUTO

    def test_oscillating(self, fan_entity):
        """Test oscillating property."""
        assert fan_entity.oscillating is True

    def test_oscillating_off(self, fan_entity, mock_coordinator, mock_fan_device_state):
        """Test oscillating property when off."""
        mock_fan_device_state.oscillating = False
        mock_coordinator.get_state.return_value = mock_fan_device_state
        assert fan_entity.oscillating is False


# ==============================================================================
# Fan Entity Control Tests
# ==============================================================================


class TestGoveeFanEntityControls:
    """Test GoveeFanEntity control methods."""

    @pytest.fixture
    def mock_coordinator(self, mock_fan_device, mock_fan_device_state):
        """Create a mock coordinator for testing."""
        coordinator = MagicMock()
        coordinator.devices = {mock_fan_device.device_id: mock_fan_device}
        coordinator.get_state = MagicMock(return_value=mock_fan_device_state)
        coordinator.async_control_device = AsyncMock(return_value=True)
        return coordinator

    @pytest.fixture
    def fan_entity(self, mock_coordinator, mock_fan_device):
        """Create a fan entity for testing."""
        return GoveeFanEntity(mock_coordinator, mock_fan_device)

    @pytest.mark.asyncio
    async def test_turn_on(self, fan_entity, mock_coordinator):
        """Test turning on the fan."""
        await fan_entity.async_turn_on()

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        assert call_args[0][0] == fan_entity._device_id
        assert isinstance(call_args[0][1], PowerCommand)
        assert call_args[0][1].power_on is True

    @pytest.mark.asyncio
    async def test_turn_on_with_percentage(self, fan_entity, mock_coordinator):
        """Test turning on with speed percentage."""
        await fan_entity.async_turn_on(percentage=100)

        # Should call set_percentage then power on
        assert mock_coordinator.async_control_device.call_count == 2

        # First call: WorkModeCommand for speed
        first_call = mock_coordinator.async_control_device.call_args_list[0]
        assert isinstance(first_call[0][1], WorkModeCommand)
        assert first_call[0][1].work_mode == WORK_MODE_GEAR
        assert first_call[0][1].mode_value == 3  # High

        # Second call: PowerCommand
        second_call = mock_coordinator.async_control_device.call_args_list[1]
        assert isinstance(second_call[0][1], PowerCommand)
        assert second_call[0][1].power_on is True

    @pytest.mark.asyncio
    async def test_turn_on_with_preset_mode(self, fan_entity, mock_coordinator):
        """Test turning on with preset mode."""
        await fan_entity.async_turn_on(preset_mode=PRESET_MODE_AUTO)

        # Should call set_preset_mode then power on
        assert mock_coordinator.async_control_device.call_count == 2

        # First call: WorkModeCommand for auto mode
        first_call = mock_coordinator.async_control_device.call_args_list[0]
        assert isinstance(first_call[0][1], WorkModeCommand)
        assert first_call[0][1].work_mode == WORK_MODE_AUTO

        # Second call: PowerCommand
        second_call = mock_coordinator.async_control_device.call_args_list[1]
        assert isinstance(second_call[0][1], PowerCommand)

    @pytest.mark.asyncio
    async def test_turn_off(self, fan_entity, mock_coordinator):
        """Test turning off the fan."""
        await fan_entity.async_turn_off()

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        assert isinstance(call_args[0][1], PowerCommand)
        assert call_args[0][1].power_on is False

    @pytest.mark.asyncio
    async def test_set_percentage_low(self, fan_entity, mock_coordinator):
        """Test setting low speed."""
        await fan_entity.async_set_percentage(33)

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        assert isinstance(call_args[0][1], WorkModeCommand)
        assert call_args[0][1].work_mode == WORK_MODE_GEAR
        assert call_args[0][1].mode_value == 1  # Low

    @pytest.mark.asyncio
    async def test_set_percentage_medium(self, fan_entity, mock_coordinator):
        """Test setting medium speed."""
        await fan_entity.async_set_percentage(50)

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        assert isinstance(call_args[0][1], WorkModeCommand)
        assert call_args[0][1].work_mode == WORK_MODE_GEAR
        assert call_args[0][1].mode_value == 2  # Medium

    @pytest.mark.asyncio
    async def test_set_percentage_high(self, fan_entity, mock_coordinator):
        """Test setting high speed."""
        await fan_entity.async_set_percentage(100)

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        assert isinstance(call_args[0][1], WorkModeCommand)
        assert call_args[0][1].work_mode == WORK_MODE_GEAR
        assert call_args[0][1].mode_value == 3  # High

    @pytest.mark.asyncio
    async def test_set_percentage_zero_turns_off(self, fan_entity, mock_coordinator):
        """Test setting 0% turns off the fan."""
        await fan_entity.async_set_percentage(0)

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        assert isinstance(call_args[0][1], PowerCommand)
        assert call_args[0][1].power_on is False

    @pytest.mark.asyncio
    async def test_set_preset_mode_auto(self, fan_entity, mock_coordinator):
        """Test setting auto preset mode."""
        await fan_entity.async_set_preset_mode(PRESET_MODE_AUTO)

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        assert isinstance(call_args[0][1], WorkModeCommand)
        assert call_args[0][1].work_mode == WORK_MODE_AUTO

    @pytest.mark.asyncio
    async def test_set_preset_mode_normal(self, fan_entity, mock_coordinator):
        """Test setting normal preset mode."""
        await fan_entity.async_set_preset_mode(PRESET_MODE_NORMAL)

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        assert isinstance(call_args[0][1], WorkModeCommand)
        assert call_args[0][1].work_mode == WORK_MODE_GEAR
        # Should preserve current mode_value (2 = medium from fixture)
        assert call_args[0][1].mode_value == 2

    @pytest.mark.asyncio
    async def test_oscillate_on(self, fan_entity, mock_coordinator):
        """Test turning oscillation on."""
        await fan_entity.async_oscillate(True)

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        assert isinstance(call_args[0][1], OscillationCommand)
        assert call_args[0][1].oscillating is True

    @pytest.mark.asyncio
    async def test_oscillate_off(self, fan_entity, mock_coordinator):
        """Test turning oscillation off."""
        await fan_entity.async_oscillate(False)

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        assert isinstance(call_args[0][1], OscillationCommand)
        assert call_args[0][1].oscillating is False


# ==============================================================================
# 8-Speed Fan Entity Tests
# ==============================================================================


class TestGoveeFanEntity8Speed:
    """Test GoveeFanEntity with 8-speed device (H7101)."""

    @pytest.fixture
    def mock_coordinator(self, mock_fan_8speed_device):
        """Create a mock coordinator for testing."""
        coordinator = MagicMock()
        coordinator.devices = {mock_fan_8speed_device.device_id: mock_fan_8speed_device}
        state = MagicMock()
        state.power_state = True
        state.oscillating = False
        state.work_mode = WORK_MODE_GEAR
        state.mode_value = 1
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.async_control_device = AsyncMock(return_value=True)
        return coordinator

    @pytest.fixture
    def fan_entity(self, mock_coordinator, mock_fan_8speed_device):
        """Create an 8-speed fan entity for testing."""
        return GoveeFanEntity(mock_coordinator, mock_fan_8speed_device)

    def test_speed_count_8(self, fan_entity):
        """Test speed count is 8 for H7101."""
        assert fan_entity.speed_count == 8

    def test_percentage_speed_1(self, fan_entity):
        """Test percentage for speed 1 (lowest)."""
        # speed 1 of 8 => 12% (1*100//8 = 12)
        assert fan_entity.percentage == 12

    def test_percentage_speed_4(self, fan_entity, mock_coordinator):
        """Test percentage for speed 4 (mid)."""
        mock_coordinator.get_state.return_value.mode_value = 4
        assert fan_entity.percentage == 50

    def test_percentage_speed_8(self, fan_entity, mock_coordinator):
        """Test percentage for speed 8 (max)."""
        mock_coordinator.get_state.return_value.mode_value = 8
        assert fan_entity.percentage == 100

    @pytest.mark.asyncio
    async def test_set_percentage_sends_correct_mode_value(
        self, fan_entity, mock_coordinator
    ):
        """Test that 50% maps to mode_value=4 for 8-speed fan."""
        await fan_entity.async_set_percentage(50)

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        assert isinstance(call_args[0][1], WorkModeCommand)
        assert call_args[0][1].work_mode == WORK_MODE_GEAR
        assert call_args[0][1].mode_value == 4


# ==============================================================================
# Ceiling Fan (H1310) Tests — issue #74
# ==============================================================================


def _h1310_device():
    """Build an H1310-shaped ceiling-fan-with-light device."""
    from custom_components.govee.models import GoveeDevice, GoveeCapability
    from custom_components.govee.models.device import (
        CAPABILITY_ON_OFF,
        CAPABILITY_TOGGLE,
        CAPABILITY_MODE,
        INSTANCE_POWER,
        INSTANCE_FAN_TOGGLE,
        INSTANCE_FAN_SPEED_MODE,
        INSTANCE_REVERSE_AIRFLOW,
    )

    on_off = {"name": "on", "value": 1}
    off = {"name": "off", "value": 0}
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:13:10",
        sku="H1310",
        name="Room1 Ceiling Fan",
        device_type="devices.types.light",
        capabilities=(
            GoveeCapability(
                type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
            ),
            GoveeCapability(
                type=CAPABILITY_TOGGLE,
                instance=INSTANCE_FAN_TOGGLE,
                parameters={"dataType": "ENUM", "options": [on_off, off]},
            ),
            GoveeCapability(
                type=CAPABILITY_MODE,
                instance=INSTANCE_FAN_SPEED_MODE,
                parameters={
                    "dataType": "ENUM",
                    "options": [
                        {"name": f"Speed {i}", "value": i} for i in range(1, 7)
                    ],
                },
            ),
            GoveeCapability(
                type=CAPABILITY_TOGGLE,
                instance=INSTANCE_REVERSE_AIRFLOW,
                parameters={"dataType": "ENUM", "options": [on_off, off]},
            ),
        ),
        is_group=False,
    )


def _h1370_device():
    """Build an H1370-shaped ceiling-fan-with-light device (issue #105).

    Like the H1310 but its oscillation uses ``fanOscillateToggle`` (not the
    standalone fan's ``oscillationToggle``) and it adds main/background light
    zone toggles.
    """
    from custom_components.govee.models import GoveeDevice, GoveeCapability
    from custom_components.govee.models.device import (
        CAPABILITY_ON_OFF,
        CAPABILITY_TOGGLE,
        CAPABILITY_MODE,
        INSTANCE_POWER,
        INSTANCE_FAN_TOGGLE,
        INSTANCE_FAN_SPEED_MODE,
        INSTANCE_REVERSE_AIRFLOW,
        INSTANCE_FAN_OSCILLATE,
    )

    on_off = {"name": "on", "value": 1}
    off = {"name": "off", "value": 0}
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:13:70",
        sku="H1370",
        name="Office Fan",
        device_type="devices.types.light",
        capabilities=(
            GoveeCapability(
                type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
            ),
            GoveeCapability(
                type=CAPABILITY_TOGGLE,
                instance=INSTANCE_FAN_TOGGLE,
                parameters={"dataType": "ENUM", "options": [on_off, off]},
            ),
            GoveeCapability(
                type=CAPABILITY_MODE,
                instance=INSTANCE_FAN_SPEED_MODE,
                parameters={
                    "dataType": "ENUM",
                    "options": [
                        {"name": f"Speed {i}", "value": i} for i in range(1, 7)
                    ],
                },
            ),
            GoveeCapability(
                type=CAPABILITY_TOGGLE,
                instance=INSTANCE_REVERSE_AIRFLOW,
                parameters={"dataType": "ENUM", "options": [on_off, off]},
            ),
            GoveeCapability(
                type=CAPABILITY_TOGGLE,
                instance=INSTANCE_FAN_OSCILLATE,
                parameters={"dataType": "ENUM", "options": [on_off, off]},
            ),
        ),
        is_group=False,
    )


class TestGoveeCeilingFanEntity:
    """Test GoveeCeilingFanEntity (H1310) — issue #74."""

    @pytest.fixture
    def device(self):
        return _h1310_device()

    @pytest.fixture
    def mock_coordinator(self, device):
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        coordinator.get_state = MagicMock(return_value=MagicMock(online=True))
        coordinator.async_control_device = AsyncMock(return_value=True)
        return coordinator

    @pytest.fixture
    def fan_entity(self, mock_coordinator, device):
        from custom_components.govee.fan import GoveeCeilingFanEntity

        return GoveeCeilingFanEntity(mock_coordinator, device)

    def test_detection(self, device):
        """H1310 is a ceiling fan and NOT a standalone fan, but IS a light."""
        assert device.supports_ceiling_fan is True
        assert device.supports_reverse_airflow is True
        assert device.is_fan is False
        assert device.is_light_device is True

    def test_speed_options(self, device):
        """fanSpeedMode exposes 6 speed values."""
        opts = device.get_ceiling_fan_speed_options()
        assert [o["value"] for o in opts] == [1, 2, 3, 4, 5, 6]

    def test_unique_id_suffixed(self, fan_entity, device):
        """Fan unique_id must differ from the light entity (bare device_id)."""
        assert fan_entity.unique_id == f"{device.device_id}_fan"

    def test_supported_features(self, fan_entity):
        from homeassistant.components.fan import FanEntityFeature

        features = fan_entity.supported_features
        assert features & FanEntityFeature.TURN_ON
        assert features & FanEntityFeature.TURN_OFF
        assert features & FanEntityFeature.SET_SPEED
        assert features & FanEntityFeature.DIRECTION

    def test_speed_count(self, fan_entity):
        assert fan_entity.speed_count == 6

    @pytest.mark.asyncio
    async def test_turn_on_sends_fan_toggle(self, fan_entity, mock_coordinator):
        from custom_components.govee.models import ToggleCommand
        from custom_components.govee.models.device import INSTANCE_FAN_TOGGLE

        fan_entity.async_write_ha_state = MagicMock()
        await fan_entity.async_turn_on()

        call_args = mock_coordinator.async_control_device.call_args
        cmd = call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.toggle_instance == INSTANCE_FAN_TOGGLE
        assert cmd.enabled is True
        assert fan_entity.is_on is True

    @pytest.mark.asyncio
    async def test_turn_off_sends_fan_toggle(self, fan_entity, mock_coordinator):
        from custom_components.govee.models import ToggleCommand

        fan_entity.async_write_ha_state = MagicMock()
        await fan_entity.async_turn_off()

        cmd = mock_coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.enabled is False
        assert fan_entity.is_on is False

    @pytest.mark.asyncio
    async def test_set_percentage_sends_mode_command(
        self, fan_entity, mock_coordinator
    ):
        from custom_components.govee.models import ModeCommand
        from custom_components.govee.models.device import INSTANCE_FAN_SPEED_MODE

        fan_entity.async_write_ha_state = MagicMock()
        await fan_entity.async_set_percentage(100)

        cmd = mock_coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ModeCommand)
        assert cmd.mode_instance == INSTANCE_FAN_SPEED_MODE
        assert cmd.value == 6  # 100% -> top speed of 6
        assert fan_entity.is_on is True
        assert fan_entity.percentage == 100

    @pytest.mark.asyncio
    async def test_set_percentage_zero_turns_off(self, fan_entity, mock_coordinator):
        from custom_components.govee.models import ToggleCommand

        fan_entity.async_write_ha_state = MagicMock()
        await fan_entity.async_set_percentage(0)

        cmd = mock_coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.enabled is False
        assert fan_entity.is_on is False

    @pytest.mark.asyncio
    async def test_set_direction_reverse(self, fan_entity, mock_coordinator):
        from homeassistant.components.fan import DIRECTION_REVERSE
        from custom_components.govee.models import ToggleCommand
        from custom_components.govee.models.device import INSTANCE_REVERSE_AIRFLOW

        fan_entity.async_write_ha_state = MagicMock()
        await fan_entity.async_set_direction(DIRECTION_REVERSE)

        cmd = mock_coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.toggle_instance == INSTANCE_REVERSE_AIRFLOW
        assert cmd.enabled is True
        assert fan_entity.current_direction == DIRECTION_REVERSE


class TestGoveeCeilingFanOscillation:
    """H1370 ceiling-fan oscillation via fanOscillateToggle (issue #105)."""

    @pytest.fixture
    def device(self):
        return _h1370_device()

    @pytest.fixture
    def mock_coordinator(self, device):
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        coordinator.get_state = MagicMock(return_value=MagicMock(online=True))
        coordinator.async_control_device = AsyncMock(return_value=True)
        return coordinator

    @pytest.fixture
    def fan_entity(self, mock_coordinator, device):
        from custom_components.govee.fan import GoveeCeilingFanEntity

        entity = GoveeCeilingFanEntity(mock_coordinator, device)
        entity.async_write_ha_state = MagicMock()
        return entity

    def test_detection(self, device):
        """H1370 is a ceiling fan that also oscillates and reverses."""
        assert device.supports_ceiling_fan is True
        assert device.supports_fan_oscillation is True
        assert device.supports_reverse_airflow is True
        # Must NOT be confused with the standalone-fan oscillationToggle.
        assert device.supports_oscillation is False

    def test_h1310_has_no_fan_oscillation(self):
        """The H1310 (no fanOscillateToggle) must not report oscillation."""
        device = _h1310_device()
        assert device.supports_fan_oscillation is False

    def test_supported_features_includes_oscillate(self, fan_entity):
        from homeassistant.components.fan import FanEntityFeature

        assert fan_entity.supported_features & FanEntityFeature.OSCILLATE

    def test_h1310_entity_has_no_oscillate_feature(self, mock_coordinator):
        from homeassistant.components.fan import FanEntityFeature
        from custom_components.govee.fan import GoveeCeilingFanEntity

        device = _h1310_device()
        mock_coordinator.devices = {device.device_id: device}
        entity = GoveeCeilingFanEntity(mock_coordinator, device)
        assert not (entity.supported_features & FanEntityFeature.OSCILLATE)
        assert entity.oscillating is None

    @pytest.mark.asyncio
    async def test_oscillate_on_sends_toggle(self, fan_entity, mock_coordinator):
        from custom_components.govee.models import ToggleCommand
        from custom_components.govee.models.device import INSTANCE_FAN_OSCILLATE

        await fan_entity.async_oscillate(True)

        cmd = mock_coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.toggle_instance == INSTANCE_FAN_OSCILLATE
        assert cmd.enabled is True
        assert fan_entity.oscillating is True

    @pytest.mark.asyncio
    async def test_oscillate_off_sends_toggle(self, fan_entity, mock_coordinator):
        from custom_components.govee.models import ToggleCommand

        fan_entity._oscillating = True
        await fan_entity.async_oscillate(False)

        cmd = mock_coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.enabled is False
        assert fan_entity.oscillating is False


# ==============================================================================
# Issue #120 — duplicate "Auto" preset must not be emitted (HomeKit IID crash)
# ==============================================================================


def _h7106_device():
    """H7106-shaped fan where FanSpeed is manual and Auto is not value 3."""
    from custom_components.govee.models import GoveeDevice, GoveeCapability
    from custom_components.govee.models.device import (
        CAPABILITY_ON_OFF,
        CAPABILITY_WORK_MODE,
        INSTANCE_POWER,
        INSTANCE_WORK_MODE,
    )

    workmode = {
        "dataType": "STRUCT",
        "fields": [
            {
                "fieldName": "workMode",
                "options": [
                    {"name": "FanSpeed", "value": 1},
                    {"name": "Auto", "value": 2},
                    {"name": "Sleep", "value": 5},
                    {"name": "Nature", "value": 6},
                    {"name": "Custom", "value": 7},
                ],
            },
            {
                "fieldName": "modeValue",
                "options": [
                    {
                        "name": "FanSpeed",
                        "options": [
                            {"value": 1},
                            {"value": 2},
                            {"value": 3},
                            {"value": 4},
                            {"value": 5},
                            {"value": 6},
                            {"value": 7},
                            {"value": 8},
                        ],
                    },
                    {"defaultValue": 0, "name": "Auto"},
                    {
                        "name": "Sleep",
                        "options": [{"value": 1}, {"value": 2}, {"value": 3}],
                    },
                    {
                        "name": "Nature",
                        "options": [{"value": 1}, {"value": 2}, {"value": 3}],
                    },
                    {"defaultValue": 0, "name": "Custom"},
                ],
            },
        ],
    }
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:71:06",
        sku="H7106",
        name="Living Room Fan",
        device_type="devices.types.fan",
        capabilities=(
            GoveeCapability(
                type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}
            ),
            GoveeCapability(
                type=CAPABILITY_WORK_MODE,
                instance=INSTANCE_WORK_MODE,
                parameters=workmode,
            ),
        ),
    )


class TestFanDuplicatePreset:
    """Capability-driven fan mode handling for H7106-like devices."""

    @pytest.fixture
    def h7106_entity(self):
        device = _h7106_device()
        state = MagicMock()
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.async_control_device = AsyncMock(return_value=True)
        return GoveeFanEntity(coordinator, device)

    def test_no_duplicate_auto_preset(self, h7106_entity):
        modes = h7106_entity.preset_modes
        # Presets come directly from capabilities with no duplicates.
        assert modes.count(PRESET_MODE_AUTO) == 1
        assert PRESET_MODE_NORMAL in modes
        assert "Sleep" in modes
        assert "Nature" in modes
        assert "Custom" in modes
        assert len(modes) == len(set(modes))

    def test_preset_modes_map_to_discovered_work_modes(self, h7106_entity):
        assert h7106_entity._auto_work_mode == 2
        assert h7106_entity._preset_work_modes.get("Auto") == 2
        assert h7106_entity._manual_work_mode == 1
        assert h7106_entity._manual_preset_name == PRESET_MODE_NORMAL
        assert h7106_entity._preset_work_modes.get("Sleep") == 5

    def test_speed_count_and_percentage_step_from_fanspeed_modevalue(self, h7106_entity):
        assert h7106_entity.speed_count == 8
        assert h7106_entity.percentage_step == pytest.approx(12.5)

    @pytest.mark.asyncio
    async def test_set_preset_auto_uses_discovered_work_mode(self, h7106_entity):
        await h7106_entity.async_set_preset_mode("Auto")
        cmd = h7106_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("preset_mode", "work_mode"),
        [("Sleep", 5), ("Nature", 6)],
    )
    async def test_set_non_manual_preset_restores_last_mode_value_after_mode_switch(
        self, h7106_entity, preset_mode, work_mode
    ):
        state = h7106_entity.coordinator.get_state.return_value
        state.work_mode = work_mode
        state.mode_value = 3

        await h7106_entity.async_set_preset_mode(PRESET_MODE_NORMAL)
        state.work_mode = 1
        state.mode_value = 4

        await h7106_entity.async_set_preset_mode(preset_mode)

        cmd = h7106_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == work_mode
        assert cmd.mode_value == 3

    @pytest.mark.asyncio
    async def test_set_percentage_in_sleep_uses_sleep_speed_range(self, h7106_entity):
        state = h7106_entity.coordinator.get_state.return_value
        state.work_mode = 5
        state.mode_value = 2

        await h7106_entity.async_set_percentage(100)

        cmd = h7106_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 5
        assert cmd.mode_value == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("percentage", "expected_mode_value"),
        [(1, 1), (50, 2), (100, 3)],
    )
    async def test_set_percentage_in_sleep_maps_across_sleep_speed_range(
        self, h7106_entity, percentage, expected_mode_value
    ):
        state = h7106_entity.coordinator.get_state.return_value
        state.work_mode = 5
        state.mode_value = 2

        await h7106_entity.async_set_percentage(percentage)

        cmd = h7106_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 5
        assert cmd.mode_value == expected_mode_value


def _h7107_device():
    """H7107-shaped fan where manual FanSpeed work mode is not value 1."""
    from custom_components.govee.models import GoveeDevice, GoveeCapability
    from custom_components.govee.models.device import (
        CAPABILITY_ON_OFF,
        CAPABILITY_WORK_MODE,
        INSTANCE_POWER,
        INSTANCE_WORK_MODE,
    )

    workmode = {
        "dataType": "STRUCT",
        "fields": [
            {
                "fieldName": "workMode",
                "options": [
                    {"name": "Auto", "value": 2},
                    {"name": "Sleep", "value": 3},
                    {"name": "FanSpeed", "value": 4},
                    {"name": "Nature", "value": 5},
                    {"name": "Custom", "value": 6},
                ],
            },
            {
                "fieldName": "modeValue",
                "options": [
                    {
                        "name": "FanSpeed",
                        "options": [{"value": i} for i in range(1, 13)],
                    },
                    {"defaultValue": 0, "name": "Auto"},
                    {"defaultValue": 0, "name": "Sleep"},
                    {"defaultValue": 0, "name": "Nature"},
                    {"defaultValue": 0, "name": "Custom"},
                ],
            },
        ],
    }
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:71:07",
        sku="H7107",
        name="Bedroom Tower Fan",
        device_type="devices.types.fan",
        capabilities=(
            GoveeCapability(
                type=CAPABILITY_ON_OFF,
                instance=INSTANCE_POWER,
                parameters={},
            ),
            GoveeCapability(
                type=CAPABILITY_WORK_MODE,
                instance=INSTANCE_WORK_MODE,
                parameters=workmode,
            ),
        ),
    )


def _h7107_whitespace_device():
    """H7107 variant with whitespace/misaligned option names."""
    from custom_components.govee.models import GoveeDevice, GoveeCapability
    from custom_components.govee.models.device import (
        CAPABILITY_ON_OFF,
        CAPABILITY_WORK_MODE,
        INSTANCE_POWER,
        INSTANCE_WORK_MODE,
    )

    workmode = {
        "dataType": "STRUCT",
        "fields": [
            {
                "fieldName": "workMode",
                "options": [
                    {"name": "Auto ", "value": 2},
                    {"name": "FanSpeed ", "value": 4},
                ],
            },
            {
                "fieldName": "modeValue",
                "options": [
                    {
                        "name": "FanSpeed",
                        # Duplicates are intentional to verify de-duplication.
                        "options": [{"value": 5}, {"value": 1}, {"value": 3}, {"value": 3}, {"value": 2}],
                    },
                    {"defaultValue": 0, "name": "Auto"},
                ],
            },
        ],
    }
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:71:70",
        sku="H7107",
        name="Whitespace Fan",
        device_type="devices.types.fan",
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(type=CAPABILITY_WORK_MODE, instance=INSTANCE_WORK_MODE, parameters=workmode),
        ),
    )


class TestFanSpeedManualModeDiscovery:
    @pytest.fixture
    def h7107_entity(self):
        device = _h7107_device()
        state = MagicMock()
        state.work_mode = 4
        state.mode_value = 6
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.async_control_device = AsyncMock(return_value=True)
        return GoveeFanEntity(coordinator, device)

    def test_speed_count_and_presets_use_capabilities(self, h7107_entity):
        assert h7107_entity.speed_count == 12
        assert 0 not in h7107_entity._fan_speeds
        assert min(h7107_entity._fan_speeds) == 1
        assert h7107_entity.percentage_step == pytest.approx(100 / 12)
        assert h7107_entity.preset_modes == [
            PRESET_MODE_NORMAL,
            "Auto",
            "Sleep",
            "Nature",
            "Custom",
        ]

    @pytest.mark.asyncio
    async def test_set_percentage_uses_detected_manual_work_mode(self, h7107_entity):
        await h7107_entity.async_set_percentage(100)
        cmd = h7107_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 4
        assert cmd.mode_value == 12

    @pytest.mark.asyncio
    async def test_set_percentage_from_auto_uses_manual_mode(self, h7107_entity):
        state = h7107_entity.coordinator.get_state.return_value
        state.work_mode = 2
        state.mode_value = 0

        await h7107_entity.async_set_percentage(100)

        cmd = h7107_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 4
        assert cmd.mode_value == 12

    @pytest.mark.asyncio
    async def test_set_manual_preset_reuses_valid_mode_value_outside_manual_mode(
        self, h7107_entity
    ):
        state = h7107_entity.coordinator.get_state.return_value
        state.work_mode = 2
        state.mode_value = 6

        await h7107_entity.async_set_preset_mode(PRESET_MODE_NORMAL)

        cmd = h7107_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 4
        assert cmd.mode_value == 6

    @pytest.mark.asyncio
    async def test_set_manual_preset_defaults_to_typical_speed_when_mode_value_invalid(
        self, h7107_entity
    ):
        state = h7107_entity.coordinator.get_state.return_value
        state.work_mode = 2
        state.mode_value = 0

        await h7107_entity.async_set_preset_mode(PRESET_MODE_NORMAL)

        cmd = h7107_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 4
        assert cmd.mode_value == 6

    @pytest.mark.asyncio
    async def test_set_auto_preset_uses_zero_mode_value(
        self, h7107_entity
    ):
        state = h7107_entity.coordinator.get_state.return_value
        state.work_mode = 4
        state.mode_value = 8

        await h7107_entity.async_set_preset_mode("Auto")

        cmd = h7107_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 2
        assert cmd.mode_value == 0

    @pytest.mark.asyncio
    async def test_set_auto_preset_uses_zero_mode_value_when_state_invalid(
        self, h7107_entity
    ):
        state = h7107_entity.coordinator.get_state.return_value
        state.work_mode = 2
        state.mode_value = 0

        await h7107_entity.async_set_preset_mode("Auto")

        cmd = h7107_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 2
        assert cmd.mode_value == 0

    @pytest.mark.asyncio
    async def test_set_custom_preset_uses_zero_mode_value(self, h7107_entity):
        await h7107_entity.async_set_preset_mode("Custom")

        cmd = h7107_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 6
        assert cmd.mode_value == 0

    @pytest.mark.asyncio
    async def test_set_manual_preset_restores_last_manual_speed_after_mode_switch(
        self, h7107_entity
    ):
        state = h7107_entity.coordinator.get_state.return_value
        state.work_mode = 4
        state.mode_value = 8

        await h7107_entity.async_set_preset_mode("Auto")
        state.work_mode = 2
        state.mode_value = 1

        await h7107_entity.async_set_preset_mode(PRESET_MODE_NORMAL)

        cmd = h7107_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 4
        assert cmd.mode_value == 8

    @pytest.mark.asyncio
    async def test_set_auto_preset_keeps_zero_mode_value(
        self, h7107_entity
    ):
        h7107_entity._preset_commands["Auto"] = (2, 0)

        await h7107_entity.async_set_preset_mode("Auto")

        cmd = h7107_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 2
        assert cmd.mode_value == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("preset_mode", "work_mode"),
        [("Sleep", 3), ("Nature", 5)],
    )
    async def test_set_non_manual_preset_without_speed_options_keeps_zero_mode_value(
        self, h7107_entity, preset_mode, work_mode
    ):
        state = h7107_entity.coordinator.get_state.return_value
        state.work_mode = work_mode
        state.mode_value = 8

        await h7107_entity.async_set_preset_mode(PRESET_MODE_NORMAL)
        state.work_mode = 4
        state.mode_value = 6

        await h7107_entity.async_set_preset_mode(preset_mode)

        cmd = h7107_entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == work_mode
        assert cmd.mode_value == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "work_mode",
        [3, 5],
    )
    async def test_speed_percentage_returns_none_for_modes_without_speed_options(
        self, h7107_entity, work_mode
    ):
        state = h7107_entity.coordinator.get_state.return_value
        state.work_mode = work_mode
        state.mode_value = 8
        await h7107_entity.async_set_preset_mode(PRESET_MODE_NORMAL)
        assert h7107_entity._last_mode_values[work_mode] == 0
        assert h7107_entity.percentage is None

    def test_percentage_returns_none_for_speedless_mode_without_switch(self, h7107_entity):
        state = h7107_entity.coordinator.get_state.return_value
        state.work_mode = 3
        state.mode_value = 0

        assert h7107_entity.percentage is None

    @pytest.mark.asyncio
    async def test_speed_percentage_returns_none_without_cached_value_for_speed_mode(
        self, h7107_entity
    ):
        state = h7107_entity.coordinator.get_state.return_value
        state.work_mode = 3
        state.mode_value = 0
        h7107_entity._last_mode_values.pop(3, None)

        assert h7107_entity.percentage is None


class TestFanModeNameWhitespaceHandling:
    def test_mode_names_are_stripped_and_manual_speeds_are_sorted_unique(self):
        device = _h7107_whitespace_device()
        state = MagicMock(work_mode=4, mode_value=5)
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.async_control_device = AsyncMock(return_value=True)

        entity = GoveeFanEntity(coordinator, device)

        assert entity._manual_work_mode == 4
        assert entity._auto_work_mode == 2
        assert entity._fan_speeds == [1, 2, 3, 5]
        assert entity._fan_speeds.count(3) == 1
        assert entity.speed_count == 4
