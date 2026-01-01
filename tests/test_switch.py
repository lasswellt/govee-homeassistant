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
    GoveeOscillationSwitch,
    GoveeThermostatSwitch,
    GoveeGradientSwitch,
    GoveeWarmMistSwitch,
    GoveeAirDeflectorSwitch,
)
from custom_components.govee.api.const import (
    CAPABILITY_TOGGLE,
    INSTANCE_NIGHTLIGHT_TOGGLE,
    INSTANCE_OSCILLATION_TOGGLE,
    INSTANCE_THERMOSTAT_TOGGLE,
    INSTANCE_GRADIENT_TOGGLE,
    INSTANCE_WARM_MIST_TOGGLE,
    INSTANCE_AIR_DEFLECTOR_TOGGLE,
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
        assert entity._attr_unique_id == f"{mock_device_switch.device_id}_switch"
        assert entity.entity_description is not None

    def test_switch_is_on_true(
        self,
        mock_coordinator,
        mock_device_switch,
    ):
        """Test is_on returns True when switch is on."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            device_id=mock_device_switch.device_id,
            online=True, power_state=True, brightness=None
        )
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

        state = GoveeDeviceState(
            device_id=mock_device_switch.device_id,
            online=True, power_state=False, brightness=None
        )
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
        mock_coordinator.async_set_power_state = AsyncMock()
        entity = GoveeSwitchEntity(mock_coordinator, mock_device_switch)

        await entity.async_turn_on()

        mock_coordinator.async_set_power_state.assert_called_once_with(
            mock_device_switch.device_id,
            True,
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_with_error(
        self,
        mock_coordinator,
        mock_device_switch,
        caplog,
    ):
        """Test turning switch on handles errors."""
        mock_coordinator.async_set_power_state = AsyncMock(
            side_effect=Exception("API error")
        )
        entity = GoveeSwitchEntity(mock_coordinator, mock_device_switch)

        with pytest.raises(Exception, match="API error"):
            await entity.async_turn_on()

    @pytest.mark.asyncio
    async def test_async_turn_off_success(
        self,
        mock_coordinator,
        mock_device_switch,
    ):
        """Test turning switch off."""
        mock_coordinator.async_set_power_state = AsyncMock()
        entity = GoveeSwitchEntity(mock_coordinator, mock_device_switch)

        await entity.async_turn_off()

        mock_coordinator.async_set_power_state.assert_called_once_with(
            mock_device_switch.device_id,
            False,
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_with_error(
        self,
        mock_coordinator,
        mock_device_switch,
        caplog,
    ):
        """Test turning switch off handles errors."""
        mock_coordinator.async_set_power_state = AsyncMock(
            side_effect=Exception("API error")
        )
        entity = GoveeSwitchEntity(mock_coordinator, mock_device_switch)

        with pytest.raises(Exception, match="API error"):
            await entity.async_turn_off()


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
        assert entity._attr_unique_id == f"{nightlight_device.device_id}_nightlight"
        assert entity.entity_description is not None

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
            device_id=nightlight_device.device_id,
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
            device_id=nightlight_device.device_id,
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


class TestGoveeOscillationSwitch:
    """Test GoveeOscillationSwitch class."""

    @pytest.fixture
    def oscillation_device(self):
        """Create a device with oscillation capability."""
        from custom_components.govee.models import GoveeDevice, DeviceCapability

        return GoveeDevice(
            device_id="TEST_OSCILLATION",
            sku="H7130",
            device_name="Fan",
            device_type="devices.types.air_purifier",
            capabilities=[
                DeviceCapability(
                    type=CAPABILITY_TOGGLE,
                    instance=INSTANCE_OSCILLATION_TOGGLE,
                ),
            ],
        )

    def test_oscillation_switch_initialization(
        self,
        mock_coordinator,
        oscillation_device,
    ):
        """Test oscillation switch initializes correctly."""
        entity = GoveeOscillationSwitch(mock_coordinator, oscillation_device)

        assert entity._device == oscillation_device
        assert entity._attr_unique_id == f"{oscillation_device.device_id}_oscillation"
        assert entity.entity_description is not None

    def test_oscillation_is_on_true(
        self,
        mock_coordinator,
        oscillation_device,
    ):
        """Test is_on returns True when oscillation is on."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            device_id=oscillation_device.device_id,
            online=True, power_state=True, brightness=None, oscillation_on=True
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeOscillationSwitch(mock_coordinator, oscillation_device)

        assert entity.is_on is True

    def test_oscillation_is_on_false(
        self,
        mock_coordinator,
        oscillation_device,
    ):
        """Test is_on returns False when oscillation is off."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            device_id=oscillation_device.device_id,
            online=True, power_state=True, brightness=None, oscillation_on=False
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeOscillationSwitch(mock_coordinator, oscillation_device)

        assert entity.is_on is False

    def test_oscillation_is_on_none_when_no_state(
        self,
        mock_coordinator,
        oscillation_device,
    ):
        """Test is_on returns None when no state available."""
        mock_coordinator.get_state.return_value = None

        entity = GoveeOscillationSwitch(mock_coordinator, oscillation_device)

        assert entity.is_on is None

    @pytest.mark.asyncio
    async def test_async_turn_on_success(
        self,
        mock_coordinator,
        oscillation_device,
    ):
        """Test turning oscillation on."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeOscillationSwitch(mock_coordinator, oscillation_device)

        await entity.async_turn_on()

        mock_coordinator.async_control_device.assert_called_once_with(
            oscillation_device.device_id,
            CAPABILITY_TOGGLE,
            INSTANCE_OSCILLATION_TOGGLE,
            1,
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_success(
        self,
        mock_coordinator,
        oscillation_device,
    ):
        """Test turning oscillation off."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeOscillationSwitch(mock_coordinator, oscillation_device)

        await entity.async_turn_off()

        mock_coordinator.async_control_device.assert_called_once_with(
            oscillation_device.device_id,
            CAPABILITY_TOGGLE,
            INSTANCE_OSCILLATION_TOGGLE,
            0,
        )


class TestGoveeThermostatSwitch:
    """Test GoveeThermostatSwitch class."""

    @pytest.fixture
    def thermostat_device(self):
        """Create a device with thermostat capability."""
        from custom_components.govee.models import GoveeDevice, DeviceCapability

        return GoveeDevice(
            device_id="TEST_THERMOSTAT",
            sku="H7130",
            device_name="Heater",
            device_type="devices.types.heater",
            capabilities=[
                DeviceCapability(
                    type=CAPABILITY_TOGGLE,
                    instance=INSTANCE_THERMOSTAT_TOGGLE,
                ),
            ],
        )

    def test_thermostat_switch_initialization(
        self,
        mock_coordinator,
        thermostat_device,
    ):
        """Test thermostat switch initializes correctly."""
        entity = GoveeThermostatSwitch(mock_coordinator, thermostat_device)

        assert entity._device == thermostat_device
        assert entity._attr_unique_id == f"{thermostat_device.device_id}_thermostat"

    def test_thermostat_is_on_true(
        self,
        mock_coordinator,
        thermostat_device,
    ):
        """Test is_on returns True when thermostat is on."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            device_id=thermostat_device.device_id,
            online=True, power_state=True, brightness=None, thermostat_on=True
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeThermostatSwitch(mock_coordinator, thermostat_device)

        assert entity.is_on is True

    def test_thermostat_is_on_false(
        self,
        mock_coordinator,
        thermostat_device,
    ):
        """Test is_on returns False when thermostat is off."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            device_id=thermostat_device.device_id,
            online=True, power_state=True, brightness=None, thermostat_on=False
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeThermostatSwitch(mock_coordinator, thermostat_device)

        assert entity.is_on is False

    @pytest.mark.asyncio
    async def test_async_turn_on_success(
        self,
        mock_coordinator,
        thermostat_device,
    ):
        """Test turning thermostat on."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeThermostatSwitch(mock_coordinator, thermostat_device)

        await entity.async_turn_on()

        mock_coordinator.async_control_device.assert_called_once_with(
            thermostat_device.device_id,
            CAPABILITY_TOGGLE,
            INSTANCE_THERMOSTAT_TOGGLE,
            1,
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_success(
        self,
        mock_coordinator,
        thermostat_device,
    ):
        """Test turning thermostat off."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeThermostatSwitch(mock_coordinator, thermostat_device)

        await entity.async_turn_off()

        mock_coordinator.async_control_device.assert_called_once_with(
            thermostat_device.device_id,
            CAPABILITY_TOGGLE,
            INSTANCE_THERMOSTAT_TOGGLE,
            0,
        )


class TestGoveeGradientSwitch:
    """Test GoveeGradientSwitch class."""

    @pytest.fixture
    def gradient_device(self):
        """Create a device with gradient capability."""
        from custom_components.govee.models import GoveeDevice, DeviceCapability
        from custom_components.govee.const import DEVICE_TYPE_LIGHT

        return GoveeDevice(
            device_id="TEST_GRADIENT",
            sku="H6199",
            device_name="LED Strip",
            device_type=DEVICE_TYPE_LIGHT,
            capabilities=[
                DeviceCapability(
                    type=CAPABILITY_TOGGLE,
                    instance=INSTANCE_GRADIENT_TOGGLE,
                ),
            ],
        )

    def test_gradient_switch_initialization(
        self,
        mock_coordinator,
        gradient_device,
    ):
        """Test gradient switch initializes correctly."""
        entity = GoveeGradientSwitch(mock_coordinator, gradient_device)

        assert entity._device == gradient_device
        assert entity._attr_unique_id == f"{gradient_device.device_id}_gradient"

    def test_gradient_is_on_true(
        self,
        mock_coordinator,
        gradient_device,
    ):
        """Test is_on returns True when gradient is on."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            device_id=gradient_device.device_id,
            online=True, power_state=True, brightness=100, gradient_on=True
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeGradientSwitch(mock_coordinator, gradient_device)

        assert entity.is_on is True

    def test_gradient_is_on_false(
        self,
        mock_coordinator,
        gradient_device,
    ):
        """Test is_on returns False when gradient is off."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            device_id=gradient_device.device_id,
            online=True, power_state=True, brightness=100, gradient_on=False
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeGradientSwitch(mock_coordinator, gradient_device)

        assert entity.is_on is False

    @pytest.mark.asyncio
    async def test_async_turn_on_success(
        self,
        mock_coordinator,
        gradient_device,
    ):
        """Test turning gradient on."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeGradientSwitch(mock_coordinator, gradient_device)

        await entity.async_turn_on()

        mock_coordinator.async_control_device.assert_called_once_with(
            gradient_device.device_id,
            CAPABILITY_TOGGLE,
            INSTANCE_GRADIENT_TOGGLE,
            1,
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_success(
        self,
        mock_coordinator,
        gradient_device,
    ):
        """Test turning gradient off."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeGradientSwitch(mock_coordinator, gradient_device)

        await entity.async_turn_off()

        mock_coordinator.async_control_device.assert_called_once_with(
            gradient_device.device_id,
            CAPABILITY_TOGGLE,
            INSTANCE_GRADIENT_TOGGLE,
            0,
        )


class TestGoveeWarmMistSwitch:
    """Test GoveeWarmMistSwitch class."""

    @pytest.fixture
    def warm_mist_device(self):
        """Create a device with warm mist capability."""
        from custom_components.govee.models import GoveeDevice, DeviceCapability

        return GoveeDevice(
            device_id="TEST_WARM_MIST",
            sku="H7141",
            device_name="Humidifier",
            device_type="devices.types.humidifier",
            capabilities=[
                DeviceCapability(
                    type=CAPABILITY_TOGGLE,
                    instance=INSTANCE_WARM_MIST_TOGGLE,
                ),
            ],
        )

    def test_warm_mist_switch_initialization(
        self,
        mock_coordinator,
        warm_mist_device,
    ):
        """Test warm mist switch initializes correctly."""
        entity = GoveeWarmMistSwitch(mock_coordinator, warm_mist_device)

        assert entity._device == warm_mist_device
        assert entity._attr_unique_id == f"{warm_mist_device.device_id}_warm_mist"

    def test_warm_mist_is_on_true(
        self,
        mock_coordinator,
        warm_mist_device,
    ):
        """Test is_on returns True when warm mist is on."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            device_id=warm_mist_device.device_id,
            online=True, power_state=True, brightness=None, warm_mist_on=True
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeWarmMistSwitch(mock_coordinator, warm_mist_device)

        assert entity.is_on is True

    def test_warm_mist_is_on_false(
        self,
        mock_coordinator,
        warm_mist_device,
    ):
        """Test is_on returns False when warm mist is off."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            device_id=warm_mist_device.device_id,
            online=True, power_state=True, brightness=None, warm_mist_on=False
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeWarmMistSwitch(mock_coordinator, warm_mist_device)

        assert entity.is_on is False

    @pytest.mark.asyncio
    async def test_async_turn_on_success(
        self,
        mock_coordinator,
        warm_mist_device,
    ):
        """Test turning warm mist on."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeWarmMistSwitch(mock_coordinator, warm_mist_device)

        await entity.async_turn_on()

        mock_coordinator.async_control_device.assert_called_once_with(
            warm_mist_device.device_id,
            CAPABILITY_TOGGLE,
            INSTANCE_WARM_MIST_TOGGLE,
            1,
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_success(
        self,
        mock_coordinator,
        warm_mist_device,
    ):
        """Test turning warm mist off."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeWarmMistSwitch(mock_coordinator, warm_mist_device)

        await entity.async_turn_off()

        mock_coordinator.async_control_device.assert_called_once_with(
            warm_mist_device.device_id,
            CAPABILITY_TOGGLE,
            INSTANCE_WARM_MIST_TOGGLE,
            0,
        )


class TestGoveeAirDeflectorSwitch:
    """Test GoveeAirDeflectorSwitch class."""

    @pytest.fixture
    def air_deflector_device(self):
        """Create a device with air deflector capability."""
        from custom_components.govee.models import GoveeDevice, DeviceCapability

        return GoveeDevice(
            device_id="TEST_AIR_DEFLECTOR",
            sku="H7120",
            device_name="Air Purifier",
            device_type="devices.types.air_purifier",
            capabilities=[
                DeviceCapability(
                    type=CAPABILITY_TOGGLE,
                    instance=INSTANCE_AIR_DEFLECTOR_TOGGLE,
                ),
            ],
        )

    def test_air_deflector_switch_initialization(
        self,
        mock_coordinator,
        air_deflector_device,
    ):
        """Test air deflector switch initializes correctly."""
        entity = GoveeAirDeflectorSwitch(mock_coordinator, air_deflector_device)

        assert entity._device == air_deflector_device
        assert entity._attr_unique_id == f"{air_deflector_device.device_id}_air_deflector"

    def test_air_deflector_is_on_true(
        self,
        mock_coordinator,
        air_deflector_device,
    ):
        """Test is_on returns True when air deflector is on."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            device_id=air_deflector_device.device_id,
            online=True, power_state=True, brightness=None, air_deflector_on=True
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeAirDeflectorSwitch(mock_coordinator, air_deflector_device)

        assert entity.is_on is True

    def test_air_deflector_is_on_false(
        self,
        mock_coordinator,
        air_deflector_device,
    ):
        """Test is_on returns False when air deflector is off."""
        from custom_components.govee.models import GoveeDeviceState

        state = GoveeDeviceState(
            device_id=air_deflector_device.device_id,
            online=True, power_state=True, brightness=None, air_deflector_on=False
        )
        mock_coordinator.get_state.return_value = state

        entity = GoveeAirDeflectorSwitch(mock_coordinator, air_deflector_device)

        assert entity.is_on is False

    @pytest.mark.asyncio
    async def test_async_turn_on_success(
        self,
        mock_coordinator,
        air_deflector_device,
    ):
        """Test turning air deflector on."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeAirDeflectorSwitch(mock_coordinator, air_deflector_device)

        await entity.async_turn_on()

        mock_coordinator.async_control_device.assert_called_once_with(
            air_deflector_device.device_id,
            CAPABILITY_TOGGLE,
            INSTANCE_AIR_DEFLECTOR_TOGGLE,
            1,
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_success(
        self,
        mock_coordinator,
        air_deflector_device,
    ):
        """Test turning air deflector off."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeAirDeflectorSwitch(mock_coordinator, air_deflector_device)

        await entity.async_turn_off()

        mock_coordinator.async_control_device.assert_called_once_with(
            air_deflector_device.device_id,
            CAPABILITY_TOGGLE,
            INSTANCE_AIR_DEFLECTOR_TOGGLE,
            0,
        )
