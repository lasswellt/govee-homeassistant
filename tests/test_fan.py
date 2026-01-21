"""Test Govee fan platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.fan import (
    GoveeFanEntity,
    ORDERED_NAMED_FAN_SPEEDS,
    PRESET_MODE_NORMAL,
    PRESET_MODE_AUTO,
    WORK_MODE_GEAR,
    WORK_MODE_AUTO,
)
from custom_components.govee.models import (
    GoveeDeviceState,
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
        assert fan_entity.speed_count == len(ORDERED_NAMED_FAN_SPEEDS)
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

    def test_percentage_auto_mode(self, fan_entity, mock_coordinator, mock_fan_device_state):
        """Test percentage returns None in auto mode."""
        mock_fan_device_state.work_mode = WORK_MODE_AUTO
        mock_coordinator.get_state.return_value = mock_fan_device_state
        # In auto mode, percentage is not applicable
        assert fan_entity.percentage is None

    def test_preset_mode_normal(self, fan_entity):
        """Test preset mode returns Normal for gear mode."""
        assert fan_entity.preset_mode == PRESET_MODE_NORMAL

    def test_preset_mode_auto(self, fan_entity, mock_coordinator, mock_fan_device_state):
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
