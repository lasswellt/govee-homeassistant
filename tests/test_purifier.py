"""Test Govee air purifier platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.models import GoveeCapability, GoveeDevice
from custom_components.govee.models.device import (
    CAPABILITY_MODE,
    CAPABILITY_ON_OFF,
    DEVICE_TYPE_PURIFIER,
    INSTANCE_POWER,
    INSTANCE_PURIFIER_MODE,
)

# ==============================================================================
# Purifier Device Fixtures
# ==============================================================================


@pytest.fixture
def purifier_capabilities() -> tuple[GoveeCapability, ...]:
    """Create capabilities for a purifier device (H6006)."""
    return (
        GoveeCapability(
            type=CAPABILITY_ON_OFF,
            instance=INSTANCE_POWER,
            parameters={},
        ),
        GoveeCapability(
            type=CAPABILITY_MODE,
            instance=INSTANCE_PURIFIER_MODE,
            parameters={
                "options": [
                    {"name": "Sleep", "value": 1},
                    {"name": "Low", "value": 2},
                    {"name": "High", "value": 3},
                    {"name": "Custom", "value": 4},
                ],
            },
        ),
    )


@pytest.fixture
def mock_purifier_device(purifier_capabilities) -> GoveeDevice:
    """Create a mock purifier device (H6006)."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:88",
        sku="H6006",
        name="Living Room Air Purifier",
        device_type=DEVICE_TYPE_PURIFIER,
        capabilities=purifier_capabilities,
        is_group=False,
    )


# ==============================================================================
# Device Type Detection Tests
# ==============================================================================


class TestPurifierDeviceDetection:
    """Test purifier device type detection."""

    def test_is_purifier(self, mock_purifier_device):
        """Test is_purifier property."""
        assert mock_purifier_device.is_purifier is True

    def test_is_purifier_false_for_light(self, mock_light_device):
        """Test is_purifier is False for light devices."""
        assert mock_light_device.is_purifier is False

    def test_device_type(self, mock_purifier_device):
        """Test device type is correct."""
        assert mock_purifier_device.device_type == DEVICE_TYPE_PURIFIER

    def test_supports_power(self, mock_purifier_device):
        """Test purifier supports power control."""
        assert mock_purifier_device.supports_power is True


# ==============================================================================
# Purifier Capability Parsing Tests
# ==============================================================================


class TestPurifierCapabilityParsing:
    """Test purifier capability parsing."""

    def test_get_purifier_mode_options(self, mock_purifier_device):
        """Test purifier mode options extraction."""
        options = mock_purifier_device.get_purifier_mode_options()
        assert len(options) == 4
        assert options[0]["name"] == "Sleep"
        assert options[0]["value"] == 1
        assert options[1]["name"] == "Low"
        assert options[1]["value"] == 2
        assert options[2]["name"] == "High"
        assert options[2]["value"] == 3
        assert options[3]["name"] == "Custom"
        assert options[3]["value"] == 4

    def test_get_purifier_mode_options_empty(self):
        """Test purifier mode options return empty list if not available."""
        device = GoveeDevice(
            device_id="test",
            sku="H6006",
            name="Test Purifier",
            device_type=DEVICE_TYPE_PURIFIER,
            capabilities=(
                GoveeCapability(
                    type=CAPABILITY_ON_OFF,
                    instance=INSTANCE_POWER,
                    parameters={},
                ),
            ),
        )
        options = device.get_purifier_mode_options()
        assert options == []


# ==============================================================================
# Purifier Mode Select Entity Tests
# ==============================================================================


class TestPurifierModeSelectEntity:
    """Test purifier mode select entity."""

    @pytest.fixture
    def mock_coordinator(self, mock_purifier_device):
        """Create a mock coordinator for testing."""
        from custom_components.govee.models import GoveeDeviceState

        coordinator = MagicMock()
        coordinator.devices = {mock_purifier_device.device_id: mock_purifier_device}

        state = GoveeDeviceState(
            device_id=mock_purifier_device.device_id,
            online=True,
            power_state=True,
            source="api",
        )
        state.purifier_mode = 2  # Low

        coordinator.get_state = MagicMock(return_value=state)
        coordinator.async_control_device = AsyncMock(return_value=True)
        return coordinator

    @pytest.fixture
    def purifier_mode_entity(self, mock_coordinator, mock_purifier_device):
        """Create a purifier mode select entity for testing."""
        from custom_components.govee.select import GoveePurifierModeSelectEntity

        options = mock_purifier_device.get_purifier_mode_options()
        entity = GoveePurifierModeSelectEntity(
            coordinator=mock_coordinator,
            device=mock_purifier_device,
            options=options,
        )
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
        return entity

    def test_purifier_mode_entity_init(self, purifier_mode_entity, mock_purifier_device):
        """Test purifier mode entity initialization."""
        assert purifier_mode_entity._device == mock_purifier_device
        assert purifier_mode_entity._device_id == mock_purifier_device.device_id

    def test_purifier_mode_options(self, purifier_mode_entity):
        """Test purifier mode options are correctly set."""
        assert purifier_mode_entity._attr_options == ["Sleep", "Low", "High", "Custom"]

    def test_purifier_mode_option_map(self, purifier_mode_entity):
        """Test option map is correctly built."""
        assert purifier_mode_entity._option_map["Sleep"] == 1
        assert purifier_mode_entity._option_map["Low"] == 2
        assert purifier_mode_entity._option_map["High"] == 3
        assert purifier_mode_entity._option_map["Custom"] == 4

    def test_purifier_mode_unique_id(self, purifier_mode_entity, mock_purifier_device):
        """Test unique ID is correct."""
        from custom_components.govee.const import SUFFIX_PURIFIER_MODE_SELECT

        expected_id = f"{mock_purifier_device.device_id}{SUFFIX_PURIFIER_MODE_SELECT}"
        assert purifier_mode_entity._attr_unique_id == expected_id

    def test_current_option_low(self, purifier_mode_entity):
        """Test current option returns Low."""
        assert purifier_mode_entity.current_option == "Low"

    def test_current_option_unknown_on_none(self, purifier_mode_entity, mock_coordinator):
        """Test current option is unknown (None) when no purifier mode is reported."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            device_id=purifier_mode_entity._device_id,
            online=True,
            power_state=True,
            source="api",
        )
        # No purifier_mode set
        mock_coordinator.get_state.return_value = state
        assert purifier_mode_entity.current_option is None

    async def test_select_purifier_mode(self, purifier_mode_entity, mock_coordinator):
        """Test selecting purifier mode."""
        await purifier_mode_entity.async_select_option("High")

        # Verify command was sent
        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        device_id, command = call_args[0]

        assert device_id == purifier_mode_entity._device_id
        from custom_components.govee.models import ModeCommand

        assert isinstance(command, ModeCommand)
        assert command.mode_instance == INSTANCE_PURIFIER_MODE
        assert command.value == 3

    async def test_select_purifier_mode_custom(self, purifier_mode_entity, mock_coordinator):
        """Test selecting Custom purifier mode."""
        await purifier_mode_entity.async_select_option("Custom")

        call_args = mock_coordinator.async_control_device.call_args
        device_id, command = call_args[0]

        from custom_components.govee.models import ModeCommand

        assert isinstance(command, ModeCommand)
        assert command.value == 4

    async def test_select_purifier_mode_invalid(self, purifier_mode_entity, mock_coordinator):
        """Test selecting invalid purifier mode option."""
        from homeassistant.exceptions import ServiceValidationError

        with pytest.raises(ServiceValidationError):
            await purifier_mode_entity.async_select_option("Invalid")

        # Command should not be sent
        mock_coordinator.async_control_device.assert_not_called()

    async def test_select_purifier_mode_failure(self, purifier_mode_entity, mock_coordinator):
        """Test purifier mode selection failure raises."""
        from homeassistant.exceptions import HomeAssistantError

        mock_coordinator.async_control_device.return_value = False

        with pytest.raises(HomeAssistantError):
            await purifier_mode_entity.async_select_option("Sleep")

        # Command should still be attempted
        mock_coordinator.async_control_device.assert_called_once()


class TestPurifierGearWorkModeSelectEntity:
    """Test the Mode select on workMode/gearMode purifiers (H7126, #221)."""

    @pytest.fixture
    def mock_coordinator(self, mock_air_purifier_device):
        """Create a mock coordinator for a gearMode purifier."""
        from custom_components.govee.models import GoveeDeviceState

        coordinator = MagicMock()
        coordinator.devices = {mock_air_purifier_device.device_id: mock_air_purifier_device}

        state = GoveeDeviceState(
            device_id=mock_air_purifier_device.device_id,
            online=True,
            power_state=True,
            source="api",
        )
        state.work_mode = 1  # gearMode
        state.mode_value = 2  # Low

        coordinator.get_state = MagicMock(return_value=state)
        coordinator.async_control_device = AsyncMock(return_value=True)
        return coordinator

    @pytest.fixture
    def gear_mode_entity(self, mock_coordinator, mock_air_purifier_device):
        """Create a purifier mode select entity for the gearMode fixture."""
        from custom_components.govee.select import GoveePurifierModeSelectEntity

        options = mock_air_purifier_device.get_purifier_mode_options()
        entity = GoveePurifierModeSelectEntity(
            coordinator=mock_coordinator,
            device=mock_air_purifier_device,
            options=options,
        )
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
        return entity

    def test_gear_work_mode_resolved(self, gear_mode_entity):
        """Entity resolves the gearMode workMode value from the device."""
        assert gear_mode_entity._gear_work_mode == 1

    async def test_select_high_sends_work_mode_command(self, gear_mode_entity, mock_coordinator):
        """Selecting High sends WorkModeCommand, not ModeCommand (#221)."""
        from custom_components.govee.models import ModeCommand, WorkModeCommand

        await gear_mode_entity.async_select_option("High")

        call_args = mock_coordinator.async_control_device.call_args
        device_id, command = call_args[0]

        assert device_id == gear_mode_entity._device_id
        assert isinstance(command, WorkModeCommand)
        assert not isinstance(command, ModeCommand)
        assert command.work_mode == 1
        assert command.mode_value == 3

    def test_current_option_matches_state(self, gear_mode_entity):
        """current_option reads work_mode/mode_value for the gear path."""
        assert gear_mode_entity.current_option == "Low"

    def test_current_option_other_work_mode_is_unknown(self, gear_mode_entity, mock_coordinator):
        """A non-gear work_mode (Auto) means no gear option is active."""
        mock_coordinator.get_state.return_value.work_mode = 3  # Auto
        assert gear_mode_entity.current_option is None
