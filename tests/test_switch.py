"""Test Govee switch platform."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.components.switch import SwitchDeviceClass

from custom_components.govee.switch import (
    async_setup_entry,
    GoveeSwitchEntity,
    GoveeNightLightSwitch,
)
from custom_components.govee.api.const import (
    CAPABILITY_ON_OFF,
    CAPABILITY_TOGGLE,
    INSTANCE_NIGHTLIGHT_TOGGLE,
    INSTANCE_POWER_SWITCH,
)


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_setup_entry_with_socket_device(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_coordinator,
        mock_device_switch,
    ):
        """Test setup creates switch entity for socket device."""
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data.devices = {
            mock_device_switch.device_id: mock_device_switch
        }

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add one switch entity
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], GoveeSwitchEntity)

    @pytest.mark.asyncio
    async def test_setup_entry_with_nightlight_device(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_coordinator,
    ):
        """Test setup creates nightlight switch for light with nightlight capability."""
        from custom_components.govee.models import GoveeDevice, DeviceCapability
        from custom_components.govee.const import DEVICE_TYPE_LIGHT

        # Create device with nightlight capability
        nightlight_device = GoveeDevice(
            device_id="TEST_NIGHTLIGHT",
            sku="H6199",
            device_name="Light with Nightlight",
            device_type=DEVICE_TYPE_LIGHT,
            capabilities=[
                DeviceCapability(
                    type=CAPABILITY_TOGGLE,
                    instance=INSTANCE_NIGHTLIGHT_TOGGLE,
                ),
            ],
        )

        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data.devices = {
            nightlight_device.device_id: nightlight_device
        }

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add one nightlight switch entity
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], GoveeNightLightSwitch)

    @pytest.mark.asyncio
    async def test_setup_entry_with_no_switch_devices(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_coordinator,
        mock_device_light,
    ):
        """Test setup with only light devices (no switches)."""
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data.devices = {
            mock_device_light.device_id: mock_device_light
        }

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add no entities (light has no nightlight capability)
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 0


class TestGoveeSwitchEntity:
    """Test GoveeSwitchEntity class."""

    def test_switch_entity_initialization(
        self,
        mock_coordinator,
        mock_device_switch,
    ):
        """Test switch entity initializes correctly."""
        entity = GoveeSwitchEntity(mock_coordinator, mock_device_switch)

        assert entity._device == mock_device_switch
        assert entity._attr_unique_id == f"govee_{mock_device_switch.device_id}_switch"
        assert entity._attr_device_class == SwitchDeviceClass.OUTLET
        assert entity._attr_name is None  # Uses device name

    def test_switch_is_on_true(
        self,
        mock_coordinator,
        mock_device_switch,
    ):
        """Test is_on returns True when switch is on."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(online=True, power_state=True, brightness=None)
        mock_coordinator.get_state.return_value = state

        entity = GoveeSwitchEntity(mock_coordinator, mock_device_switch)

        assert entity.is_on is True

    def test_switch_is_on_false(
        self,
        mock_coordinator,
        mock_device_switch,
    ):
        """Test is_on returns False when switch is off."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(online=True, power_state=False, brightness=None)
        mock_coordinator.get_state.return_value = state

        entity = GoveeSwitchEntity(mock_coordinator, mock_device_switch)

        assert entity.is_on is False

    def test_switch_is_on_none_when_no_state(
        self,
        mock_coordinator,
        mock_device_switch,
    ):
        """Test is_on returns None when no state available."""
        mock_coordinator.get_state.return_value = None

        entity = GoveeSwitchEntity(mock_coordinator, mock_device_switch)

        assert entity.is_on is None

    @pytest.mark.asyncio
    async def test_async_turn_on_success(
        self,
        mock_coordinator,
        mock_device_switch,
    ):
        """Test turning switch on."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeSwitchEntity(mock_coordinator, mock_device_switch)

        await entity.async_turn_on()

        mock_coordinator.async_control_device.assert_called_once_with(
            mock_device_switch.device_id,
            CAPABILITY_ON_OFF,
            INSTANCE_POWER_SWITCH,
            1,
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_with_error(
        self,
        mock_coordinator,
        mock_device_switch,
        caplog,
    ):
        """Test turning switch on handles errors."""
        mock_coordinator.async_control_device = AsyncMock(
            side_effect=Exception("API error")
        )
        entity = GoveeSwitchEntity(mock_coordinator, mock_device_switch)

        await entity.async_turn_on()

        # Should log error but not raise
        assert "Failed to turn on" in caplog.text
        assert "API error" in caplog.text

    @pytest.mark.asyncio
    async def test_async_turn_off_success(
        self,
        mock_coordinator,
        mock_device_switch,
    ):
        """Test turning switch off."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeSwitchEntity(mock_coordinator, mock_device_switch)

        await entity.async_turn_off()

        mock_coordinator.async_control_device.assert_called_once_with(
            mock_device_switch.device_id,
            CAPABILITY_ON_OFF,
            INSTANCE_POWER_SWITCH,
            0,
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_with_error(
        self,
        mock_coordinator,
        mock_device_switch,
        caplog,
    ):
        """Test turning switch off handles errors."""
        mock_coordinator.async_control_device = AsyncMock(
            side_effect=Exception("API error")
        )
        entity = GoveeSwitchEntity(mock_coordinator, mock_device_switch)

        await entity.async_turn_off()

        # Should log error but not raise
        assert "Failed to turn off" in caplog.text
        assert "API error" in caplog.text


class TestGoveeNightLightSwitch:
    """Test GoveeNightLightSwitch class."""

    @pytest.fixture
    def nightlight_device(self):
        """Create a device with nightlight capability."""
        from custom_components.govee.models import GoveeDevice, DeviceCapability
        from custom_components.govee.const import DEVICE_TYPE_LIGHT

        return GoveeDevice(
            device_id="TEST_NIGHTLIGHT",
            sku="H6199",
            device_name="Bedroom Light",
            device_type=DEVICE_TYPE_LIGHT,
            capabilities=[
                DeviceCapability(
                    type=CAPABILITY_TOGGLE,
                    instance=INSTANCE_NIGHTLIGHT_TOGGLE,
                ),
            ],
        )

    def test_nightlight_switch_initialization(
        self,
        mock_coordinator,
        nightlight_device,
    ):
        """Test nightlight switch initializes correctly."""
        entity = GoveeNightLightSwitch(mock_coordinator, nightlight_device)

        assert entity._device == nightlight_device
        assert entity._attr_unique_id == f"govee_{nightlight_device.device_id}_nightlight"
        assert entity._attr_device_class == SwitchDeviceClass.SWITCH
        assert entity._attr_translation_key == "nightlight"

    def test_nightlight_name(
        self,
        mock_coordinator,
        nightlight_device,
    ):
        """Test nightlight switch name."""
        entity = GoveeNightLightSwitch(mock_coordinator, nightlight_device)

        assert entity.name == "Bedroom Light Night Light"

    def test_nightlight_is_on_true(
        self,
        mock_coordinator,
        nightlight_device,
    ):
        """Test is_on returns True when nightlight is on."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            online=True, power_state=True, brightness=100, nightlight_on=True
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeNightLightSwitch(mock_coordinator, nightlight_device)

        assert entity.is_on is True

    def test_nightlight_is_on_false(
        self,
        mock_coordinator,
        nightlight_device,
    ):
        """Test is_on returns False when nightlight is off."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            online=True, power_state=True, brightness=100, nightlight_on=False
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeNightLightSwitch(mock_coordinator, nightlight_device)

        assert entity.is_on is False

    def test_nightlight_is_on_none_when_no_state(
        self,
        mock_coordinator,
        nightlight_device,
    ):
        """Test is_on returns None when no state available."""
        mock_coordinator.get_state.return_value = None

        entity = GoveeNightLightSwitch(mock_coordinator, nightlight_device)

        assert entity.is_on is None

    @pytest.mark.asyncio
    async def test_async_turn_on_success(
        self,
        mock_coordinator,
        nightlight_device,
    ):
        """Test turning nightlight on."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeNightLightSwitch(mock_coordinator, nightlight_device)

        await entity.async_turn_on()

        mock_coordinator.async_control_device.assert_called_once_with(
            nightlight_device.device_id,
            CAPABILITY_TOGGLE,
            INSTANCE_NIGHTLIGHT_TOGGLE,
            1,
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_with_error(
        self,
        mock_coordinator,
        nightlight_device,
        caplog,
    ):
        """Test turning nightlight on handles errors."""
        mock_coordinator.async_control_device = AsyncMock(
            side_effect=Exception("API error")
        )
        entity = GoveeNightLightSwitch(mock_coordinator, nightlight_device)

        await entity.async_turn_on()

        # Should log error but not raise
        assert "Failed to turn on nightlight" in caplog.text
        assert "API error" in caplog.text

    @pytest.mark.asyncio
    async def test_async_turn_off_success(
        self,
        mock_coordinator,
        nightlight_device,
    ):
        """Test turning nightlight off."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeNightLightSwitch(mock_coordinator, nightlight_device)

        await entity.async_turn_off()

        mock_coordinator.async_control_device.assert_called_once_with(
            nightlight_device.device_id,
            CAPABILITY_TOGGLE,
            INSTANCE_NIGHTLIGHT_TOGGLE,
            0,
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_with_error(
        self,
        mock_coordinator,
        nightlight_device,
        caplog,
    ):
        """Test turning nightlight off handles errors."""
        mock_coordinator.async_control_device = AsyncMock(
            side_effect=Exception("API error")
        )
        entity = GoveeNightLightSwitch(mock_coordinator, nightlight_device)

        await entity.async_turn_off()

        # Should log error but not raise
        assert "Failed to turn off nightlight" in caplog.text
        assert "API error" in caplog.text
