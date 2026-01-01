"""Test Govee sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest
from homeassistant.core import HomeAssistant

from custom_components.govee.sensor import async_setup_entry
from custom_components.govee.entities.sensor import GoveeRateLimitSensor
from custom_components.govee.entity_descriptions.sensor import SENSOR_DESCRIPTIONS


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_setup_entry_creates_sensors(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_coordinator,
    ):
        """Test setup creates rate limit sensor entities."""
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add sensor entities
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # Should have 2 rate limit sensors (minute and day)
        assert len(entities) == 2
        for entity in entities:
            assert isinstance(entity, GoveeRateLimitSensor)


class TestGoveeRateLimitSensor:
    """Test GoveeRateLimitSensor class."""

    def test_sensor_initialization(self, mock_coordinator):
        """Test sensor entity initializes correctly."""
        description = SENSOR_DESCRIPTIONS["rate_limit_remaining_minute"]
        entity = GoveeRateLimitSensor(mock_coordinator, description)

        assert entity.entity_description == description
        assert entity._attr_unique_id == "govee_rate_limit_remaining_minute"
        assert entity._attr_device_info is None  # Integration-level sensor

    def test_sensor_available(self, mock_coordinator):
        """Test sensor availability."""
        description = SENSOR_DESCRIPTIONS["rate_limit_remaining_minute"]
        entity = GoveeRateLimitSensor(mock_coordinator, description)

        mock_coordinator.last_update_success = True
        assert entity.available is True

        mock_coordinator.last_update_success = False
        assert entity.available is False

    def test_sensor_native_value_minute(self, mock_coordinator):
        """Test native_value for minute sensor."""
        mock_coordinator.rate_limit_remaining_minute = 75

        description = SENSOR_DESCRIPTIONS["rate_limit_remaining_minute"]
        entity = GoveeRateLimitSensor(mock_coordinator, description)

        assert entity.native_value == 75

    def test_sensor_native_value_day(self, mock_coordinator):
        """Test native_value for day sensor."""
        mock_coordinator.rate_limit_remaining = 9500

        description = SENSOR_DESCRIPTIONS["rate_limit_remaining_day"]
        entity = GoveeRateLimitSensor(mock_coordinator, description)

        assert entity.native_value == 9500

    def test_sensor_native_value_unknown_key(self, mock_coordinator):
        """Test native_value returns None for unknown key (entities/sensor.py line 57)."""
        from dataclasses import dataclass
        from homeassistant.components.sensor import SensorEntityDescription

        # Create a description with an unknown key
        @dataclass(frozen=True, kw_only=True)
        class UnknownSensorDescription(SensorEntityDescription):
            """Sensor description for testing unknown key."""
            key: str = "unknown_key"

        unknown_description = UnknownSensorDescription(
            key="unknown_key",
            name="Unknown Sensor",
        )
        entity = GoveeRateLimitSensor(mock_coordinator, unknown_description)

        assert entity.native_value is None

    @pytest.mark.asyncio
    async def test_async_added_to_hass(self, hass: HomeAssistant, mock_coordinator):
        """Test async_added_to_hass subscribes to coordinator."""
        description = SENSOR_DESCRIPTIONS["rate_limit_remaining_minute"]
        entity = GoveeRateLimitSensor(mock_coordinator, description)

        entity.hass = hass
        entity.async_write_ha_state = MagicMock()

        await entity.async_added_to_hass()

        # Should have registered a listener
        mock_coordinator.async_add_listener.assert_called_once()
