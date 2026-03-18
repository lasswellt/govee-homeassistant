"""Test Govee heater platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    TemperatureSettingCommand,
    ToggleCommand,
    WorkModeCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_TEMPERATURE_SETTING,
    CAPABILITY_TOGGLE,
    CAPABILITY_WORK_MODE,
    DEVICE_TYPE_HEATER,
    INSTANCE_POWER,
    INSTANCE_TARGET_TEMPERATURE,
    INSTANCE_THERMOSTAT_TOGGLE,
    INSTANCE_WORK_MODE,
)

# ==============================================================================
# Heater Device Fixtures
# ==============================================================================


@pytest.fixture
def heater_capabilities() -> tuple[GoveeCapability, ...]:
    """Create capabilities for a heater device (H7130).

    Uses real API shapes: STRUCT-based temperature_setting (range 5-30 with
    autoStop field) and work_mode, plus thermostatToggle capability.
    """
    return (
        GoveeCapability(
            type=CAPABILITY_ON_OFF,
            instance=INSTANCE_POWER,
            parameters={},
        ),
        GoveeCapability(
            type=CAPABILITY_TEMPERATURE_SETTING,
            instance=INSTANCE_TARGET_TEMPERATURE,
            parameters={
                "fields": [
                    {
                        "fieldName": "autoStop",
                        "defaultValue": 0,
                        "options": [
                            {"name": "Maintain", "value": 0},
                            {"name": "Auto stop", "value": 1},
                        ],
                    },
                    {
                        "fieldName": "temperature",
                        "range": {"min": 5, "max": 30},
                    },
                    {
                        "fieldName": "unit",
                        "defaultValue": "Celsius",
                    },
                ],
            },
        ),
        GoveeCapability(
            type=CAPABILITY_WORK_MODE,
            instance=INSTANCE_WORK_MODE,
            parameters={
                "fields": [
                    {
                        "fieldName": "workMode",
                        "options": [
                            {"name": "Low", "value": 1},
                            {"name": "Medium", "value": 2},
                            {"name": "High", "value": 3},
                        ],
                    },
                    {
                        "fieldName": "modeValue",
                        "options": [
                            {"defaultValue": 0, "name": "Low"},
                            {"defaultValue": 0, "name": "Medium"},
                            {"defaultValue": 0, "name": "High"},
                        ],
                    },
                ],
            },
        ),
        GoveeCapability(
            type=CAPABILITY_TOGGLE,
            instance=INSTANCE_THERMOSTAT_TOGGLE,
            parameters={},
        ),
    )


@pytest.fixture
def mock_heater_device(heater_capabilities) -> GoveeDevice:
    """Create a mock heater device (H7130)."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:77",
        sku="H7130",
        name="Living Room Heater",
        device_type=DEVICE_TYPE_HEATER,
        capabilities=heater_capabilities,
        is_group=False,
    )


# ==============================================================================
# Device Type Detection Tests
# ==============================================================================


class TestHeaterDeviceDetection:
    """Test heater device type detection."""

    def test_is_heater(self, mock_heater_device):
        """Test is_heater property."""
        assert mock_heater_device.is_heater is True

    def test_is_heater_false_for_light(self, mock_light_device):
        """Test is_heater is False for light devices."""
        assert mock_light_device.is_heater is False

    def test_device_type(self, mock_heater_device):
        """Test device type is correct."""
        assert mock_heater_device.device_type == DEVICE_TYPE_HEATER

    def test_supports_power(self, mock_heater_device):
        """Test heater supports power control."""
        assert mock_heater_device.supports_power is True

    def test_supports_thermostat_toggle(self, mock_heater_device):
        """Test H7130 supports thermostat toggle."""
        assert mock_heater_device.supports_thermostat_toggle is True


# ==============================================================================
# Heater Capability Parsing Tests
# ==============================================================================


class TestHeaterCapabilityParsing:
    """Test heater capability parsing."""

    def test_get_temperature_range(self, mock_heater_device):
        """Test temperature range extraction from STRUCT capability."""
        min_temp, max_temp = mock_heater_device.get_temperature_range()
        assert min_temp == 5
        assert max_temp == 30

    def test_get_temperature_range_default(self):
        """Test temperature range defaults to 16-35."""
        device = GoveeDevice(
            device_id="test",
            sku="H7130",
            name="Test Heater",
            device_type=DEVICE_TYPE_HEATER,
            capabilities=(
                GoveeCapability(
                    type=CAPABILITY_ON_OFF,
                    instance=INSTANCE_POWER,
                    parameters={},
                ),
            ),
        )
        min_temp, max_temp = device.get_temperature_range()
        assert min_temp == 16
        assert max_temp == 35

    def test_get_fan_speed_options(self, mock_heater_device):
        """Test fan speed options extraction from work_mode capability."""
        options = mock_heater_device.get_fan_speed_options()
        assert len(options) == 3
        assert options[0] == {"name": "Low", "work_mode": 1, "mode_value": 0}
        assert options[1] == {"name": "Medium", "work_mode": 2, "mode_value": 0}
        assert options[2] == {"name": "High", "work_mode": 3, "mode_value": 0}

    def test_get_fan_speed_options_empty(self):
        """Test fan speed options return empty list if not available."""
        device = GoveeDevice(
            device_id="test",
            sku="H7130",
            name="Test Heater",
            device_type=DEVICE_TYPE_HEATER,
            capabilities=(
                GoveeCapability(
                    type=CAPABILITY_ON_OFF,
                    instance=INSTANCE_POWER,
                    parameters={},
                ),
            ),
        )
        options = device.get_fan_speed_options()
        assert options == []


# ==============================================================================
# Temperature Command Tests
# ==============================================================================


class TestTemperatureSettingCommand:
    """Test TemperatureSettingCommand includes autoStop in payload."""

    def test_command_includes_auto_stop(self):
        """Test command value includes autoStop field."""
        cmd = TemperatureSettingCommand(temperature=22, auto_stop=0)
        value = cmd.get_value()
        assert value == {"autoStop": 0, "temperature": 22, "unit": "Celsius"}

    def test_command_auto_stop_enabled(self):
        """Test command value with autoStop enabled."""
        cmd = TemperatureSettingCommand(temperature=25, auto_stop=1)
        value = cmd.get_value()
        assert value == {"autoStop": 1, "temperature": 25, "unit": "Celsius"}

    def test_command_default_auto_stop_is_zero(self):
        """Test auto_stop defaults to 0."""
        cmd = TemperatureSettingCommand(temperature=20)
        assert cmd.auto_stop == 0
        value = cmd.get_value()
        assert value["autoStop"] == 0


# ==============================================================================
# Heater Entity Tests
# ==============================================================================


class TestHeaterTemperatureNumberEntity:
    """Test heater temperature number entity."""

    @pytest.fixture
    def mock_coordinator(self, mock_heater_device):
        """Create a mock coordinator for testing."""
        coordinator = MagicMock()
        coordinator.devices = {mock_heater_device.device_id: mock_heater_device}

        # Create state with heater-specific fields
        state = GoveeDeviceState(
            device_id=mock_heater_device.device_id,
            online=True,
            power_state=True,
            brightness=100,
            source="api",
        )
        state.heater_temperature = 22
        state.heater_auto_stop = 0

        coordinator.get_state = MagicMock(return_value=state)
        coordinator.async_control_device = AsyncMock(return_value=True)
        return coordinator

    @pytest.fixture
    def heater_temp_entity(self, mock_coordinator, mock_heater_device):
        """Create a heater temperature entity for testing."""
        from custom_components.govee.number import GoveeHeaterTemperatureNumber

        entity = GoveeHeaterTemperatureNumber(
            coordinator=mock_coordinator,
            device=mock_heater_device,
            temp_range=(5, 30),
        )
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
        return entity

    def test_temp_entity_init(self, heater_temp_entity, mock_heater_device):
        """Test temperature entity initialization."""
        assert heater_temp_entity._device == mock_heater_device
        assert heater_temp_entity._device_id == mock_heater_device.device_id

    def test_temp_entity_range(self, heater_temp_entity):
        """Test temperature range is correctly set."""
        assert heater_temp_entity._attr_native_min_value == 5.0
        assert heater_temp_entity._attr_native_max_value == 30.0
        assert heater_temp_entity._attr_native_step == 1

    def test_temp_entity_unique_id(self, heater_temp_entity, mock_heater_device):
        """Test unique ID is correct."""
        from custom_components.govee.const import SUFFIX_HEATER_TEMPERATURE

        expected_id = f"{mock_heater_device.device_id}{SUFFIX_HEATER_TEMPERATURE}"
        assert heater_temp_entity._attr_unique_id == expected_id

    def test_temp_entity_available_online(self, heater_temp_entity):
        """Test entity is available when device is online."""
        assert heater_temp_entity.available is True

    def test_temp_entity_unavailable_offline(
        self, heater_temp_entity, mock_coordinator
    ):
        """Test entity is unavailable when device is offline."""
        offline_state = GoveeDeviceState(
            device_id=heater_temp_entity._device_id,
            online=False,
            power_state=False,
            source="api",
        )
        mock_coordinator.get_state.return_value = offline_state
        assert heater_temp_entity.available is False

    async def test_set_temperature(self, heater_temp_entity, mock_coordinator):
        """Test setting heater temperature sends TemperatureSettingCommand with autoStop."""
        await heater_temp_entity.async_set_native_value(25.0)

        # Verify command was sent
        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        device_id, command = call_args[0]

        assert device_id == heater_temp_entity._device_id
        assert isinstance(command, TemperatureSettingCommand)
        assert command.temperature == 25
        assert command.auto_stop == 0  # Preserved from state
        assert command.unit == "Celsius"

        # Verify state was updated
        assert heater_temp_entity._attr_native_value == 25.0

    async def test_set_temperature_preserves_auto_stop(
        self, heater_temp_entity, mock_coordinator
    ):
        """Test setting temperature preserves current auto_stop setting."""
        # Set auto_stop to 1 in state
        state = mock_coordinator.get_state.return_value
        state.heater_auto_stop = 1

        await heater_temp_entity.async_set_native_value(20.0)

        call_args = mock_coordinator.async_control_device.call_args
        _, command = call_args[0]

        assert isinstance(command, TemperatureSettingCommand)
        assert command.temperature == 20
        assert command.auto_stop == 1  # Preserved from state

    async def test_set_temperature_boundary_low(
        self, heater_temp_entity, mock_coordinator
    ):
        """Test setting temperature at minimum boundary."""
        await heater_temp_entity.async_set_native_value(5.0)

        call_args = mock_coordinator.async_control_device.call_args
        device_id, command = call_args[0]

        assert isinstance(command, TemperatureSettingCommand)
        assert command.temperature == 5

    async def test_set_temperature_boundary_high(
        self, heater_temp_entity, mock_coordinator
    ):
        """Test setting temperature at maximum boundary."""
        await heater_temp_entity.async_set_native_value(30.0)

        call_args = mock_coordinator.async_control_device.call_args
        device_id, command = call_args[0]

        assert isinstance(command, TemperatureSettingCommand)
        assert command.temperature == 30

    async def test_set_temperature_failure(self, heater_temp_entity, mock_coordinator):
        """Test temperature setting failure."""
        mock_coordinator.async_control_device.return_value = False
        initial_value = heater_temp_entity._attr_native_value

        await heater_temp_entity.async_set_native_value(28.0)

        # Value should not change on failure
        assert heater_temp_entity._attr_native_value == initial_value


class TestFanSpeedSelectEntity:
    """Test heater fan speed select entity."""

    @pytest.fixture
    def mock_coordinator(self, mock_heater_device):
        """Create a mock coordinator for testing."""
        coordinator = MagicMock()
        coordinator.devices = {mock_heater_device.device_id: mock_heater_device}

        state = GoveeDeviceState(
            device_id=mock_heater_device.device_id,
            online=True,
            power_state=True,
            source="api",
        )
        state.work_mode = 2  # Medium
        state.mode_value = 0

        coordinator.get_state = MagicMock(return_value=state)
        coordinator.async_control_device = AsyncMock(return_value=True)
        return coordinator

    @pytest.fixture
    def fan_speed_entity(self, mock_coordinator, mock_heater_device):
        """Create a fan speed select entity for testing."""
        from custom_components.govee.select import GoveeFanSpeedSelectEntity

        options = mock_heater_device.get_fan_speed_options()
        entity = GoveeFanSpeedSelectEntity(
            coordinator=mock_coordinator,
            device=mock_heater_device,
            options=options,
        )
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
        return entity

    def test_fan_speed_entity_init(self, fan_speed_entity, mock_heater_device):
        """Test fan speed entity initialization."""
        assert fan_speed_entity._device == mock_heater_device
        assert fan_speed_entity._device_id == mock_heater_device.device_id

    def test_fan_speed_options(self, fan_speed_entity):
        """Test fan speed options are correctly set."""
        assert fan_speed_entity._attr_options == ["Low", "Medium", "High"]

    def test_fan_speed_option_map(self, fan_speed_entity):
        """Test option map is correctly built."""
        assert fan_speed_entity._option_map["Low"] == (1, 0)
        assert fan_speed_entity._option_map["Medium"] == (2, 0)
        assert fan_speed_entity._option_map["High"] == (3, 0)

    def test_fan_speed_unique_id(self, fan_speed_entity, mock_heater_device):
        """Test unique ID is correct."""
        from custom_components.govee.const import SUFFIX_HEATER_FAN_SPEED

        expected_id = f"{mock_heater_device.device_id}{SUFFIX_HEATER_FAN_SPEED}"
        assert fan_speed_entity._attr_unique_id == expected_id

    def test_current_option_medium(self, fan_speed_entity):
        """Test current option returns Medium (work_mode=2)."""
        assert fan_speed_entity.current_option == "Medium"

    def test_current_option_default_on_none(self, fan_speed_entity, mock_coordinator):
        """Test current option returns first option when work_mode is None."""
        state = GoveeDeviceState(
            device_id=fan_speed_entity._device_id,
            online=True,
            power_state=True,
            source="api",
        )
        # No work_mode set
        mock_coordinator.get_state.return_value = state
        assert fan_speed_entity.current_option == "Low"

    async def test_select_fan_speed(self, fan_speed_entity, mock_coordinator):
        """Test selecting fan speed sends WorkModeCommand."""
        await fan_speed_entity.async_select_option("High")

        # Verify command was sent
        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        device_id, command = call_args[0]

        assert device_id == fan_speed_entity._device_id
        assert isinstance(command, WorkModeCommand)
        assert command.work_mode == 3
        assert command.mode_value == 0

    async def test_select_fan_speed_invalid(self, fan_speed_entity, mock_coordinator):
        """Test selecting invalid fan speed option."""
        await fan_speed_entity.async_select_option("Invalid")

        # Command should not be sent
        mock_coordinator.async_control_device.assert_not_called()

    async def test_select_fan_speed_failure(self, fan_speed_entity, mock_coordinator):
        """Test fan speed selection failure."""
        mock_coordinator.async_control_device.return_value = False

        await fan_speed_entity.async_select_option("Low")

        # Command should still be attempted
        mock_coordinator.async_control_device.assert_called_once()


# ==============================================================================
# H7131 Fan Speed Tests (nested modeValue structure)
# ==============================================================================


@pytest.fixture
def h7131_capabilities() -> tuple[GoveeCapability, ...]:
    """Create capabilities for H7131 heater with nested modeValue.

    H7131 has gearMode as a parent workMode containing Low/Medium/High
    sub-options in the modeValue field. No thermostatToggle capability.
    """
    return (
        GoveeCapability(
            type=CAPABILITY_ON_OFF,
            instance=INSTANCE_POWER,
            parameters={},
        ),
        GoveeCapability(
            type=CAPABILITY_TEMPERATURE_SETTING,
            instance=INSTANCE_TARGET_TEMPERATURE,
            parameters={
                "fields": [
                    {
                        "fieldName": "autoStop",
                        "defaultValue": 0,
                        "options": [
                            {"name": "Maintain", "value": 0},
                            {"name": "Auto stop", "value": 1},
                        ],
                    },
                    {
                        "fieldName": "temperature",
                        "range": {"min": 5, "max": 30},
                    },
                    {
                        "fieldName": "unit",
                        "defaultValue": "Celsius",
                    },
                ],
            },
        ),
        GoveeCapability(
            type=CAPABILITY_WORK_MODE,
            instance=INSTANCE_WORK_MODE,
            parameters={
                "fields": [
                    {
                        "fieldName": "workMode",
                        "options": [
                            {"name": "gearMode", "value": 1},
                            {"name": "Fan", "value": 9},
                            {"name": "Auto", "value": 3},
                        ],
                    },
                    {
                        "fieldName": "modeValue",
                        "options": [
                            {
                                "name": "gearMode",
                                "options": [
                                    {"name": "Low", "value": 1},
                                    {"name": "Medium", "value": 2},
                                    {"name": "High", "value": 3},
                                ],
                            },
                            {"defaultValue": 0, "name": "Fan"},
                            {"defaultValue": 0, "name": "Auto"},
                        ],
                    },
                ],
            },
        ),
    )


@pytest.fixture
def mock_h7131_device(h7131_capabilities) -> GoveeDevice:
    """Create a mock H7131 heater device."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:88",
        sku="H7131",
        name="Office Heater",
        device_type=DEVICE_TYPE_HEATER,
        capabilities=h7131_capabilities,
        is_group=False,
    )


class TestH7131CapabilityParsing:
    """Test H7131 nested capability parsing."""

    def test_get_fan_speed_options(self, mock_h7131_device):
        """Test H7131 flattens nested gearMode sub-options."""
        options = mock_h7131_device.get_fan_speed_options()
        assert len(options) == 5
        assert options[0] == {"name": "Low", "work_mode": 1, "mode_value": 1}
        assert options[1] == {"name": "Medium", "work_mode": 1, "mode_value": 2}
        assert options[2] == {"name": "High", "work_mode": 1, "mode_value": 3}
        assert options[3] == {"name": "Fan", "work_mode": 9, "mode_value": 0}
        assert options[4] == {"name": "Auto", "work_mode": 3, "mode_value": 0}

    def test_h7131_no_thermostat_toggle(self, mock_h7131_device):
        """Test H7131 does NOT support thermostat toggle."""
        assert mock_h7131_device.supports_thermostat_toggle is False


class TestH7131FanSpeedSelectEntity:
    """Test H7131 fan speed select entity with nested modeValue."""

    @pytest.fixture
    def mock_coordinator(self, mock_h7131_device):
        """Create a mock coordinator for H7131."""
        coordinator = MagicMock()
        coordinator.devices = {mock_h7131_device.device_id: mock_h7131_device}

        state = GoveeDeviceState(
            device_id=mock_h7131_device.device_id,
            online=True,
            power_state=True,
            source="api",
        )
        state.work_mode = 1  # gearMode
        state.mode_value = 2  # Medium

        coordinator.get_state = MagicMock(return_value=state)
        coordinator.async_control_device = AsyncMock(return_value=True)
        return coordinator

    @pytest.fixture
    def fan_speed_entity(self, mock_coordinator, mock_h7131_device):
        """Create a fan speed select entity for H7131."""
        from custom_components.govee.select import GoveeFanSpeedSelectEntity

        options = mock_h7131_device.get_fan_speed_options()
        entity = GoveeFanSpeedSelectEntity(
            coordinator=mock_coordinator,
            device=mock_h7131_device,
            options=options,
        )
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
        return entity

    def test_options_list(self, fan_speed_entity):
        """Test H7131 shows Low/Medium/High/Fan/Auto options."""
        assert fan_speed_entity._attr_options == [
            "Low",
            "Medium",
            "High",
            "Fan",
            "Auto",
        ]

    def test_option_map(self, fan_speed_entity):
        """Test H7131 option map has correct (work_mode, mode_value) tuples."""
        assert fan_speed_entity._option_map["Low"] == (1, 1)
        assert fan_speed_entity._option_map["Medium"] == (1, 2)
        assert fan_speed_entity._option_map["High"] == (1, 3)
        assert fan_speed_entity._option_map["Fan"] == (9, 0)
        assert fan_speed_entity._option_map["Auto"] == (3, 0)

    def test_current_option_medium(self, fan_speed_entity):
        """Test current option matches Medium via work_mode=1, mode_value=2."""
        assert fan_speed_entity.current_option == "Medium"

    def test_current_option_fan(self, fan_speed_entity, mock_coordinator):
        """Test current option matches Fan via work_mode=9."""
        state = GoveeDeviceState(
            device_id=fan_speed_entity._device_id,
            online=True,
            power_state=True,
            source="api",
        )
        state.work_mode = 9
        state.mode_value = 0
        mock_coordinator.get_state.return_value = state
        assert fan_speed_entity.current_option == "Fan"

    def test_current_option_fallback_work_mode_only(
        self, fan_speed_entity, mock_coordinator
    ):
        """Test fallback matching on work_mode when mode_value is None."""
        state = GoveeDeviceState(
            device_id=fan_speed_entity._device_id,
            online=True,
            power_state=True,
            source="api",
        )
        state.work_mode = 1
        # mode_value is None — should match first entry with work_mode=1 (Low)
        mock_coordinator.get_state.return_value = state
        assert fan_speed_entity.current_option == "Low"

    async def test_select_sub_option(self, fan_speed_entity, mock_coordinator):
        """Test selecting Low sends work_mode=1, mode_value=1."""
        await fan_speed_entity.async_select_option("Low")

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        device_id, command = call_args[0]

        assert device_id == fan_speed_entity._device_id
        assert isinstance(command, WorkModeCommand)
        assert command.work_mode == 1
        assert command.mode_value == 1

    async def test_select_top_level_option(self, fan_speed_entity, mock_coordinator):
        """Test selecting Fan sends work_mode=9, mode_value=0."""
        await fan_speed_entity.async_select_option("Fan")

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        device_id, command = call_args[0]

        assert isinstance(command, WorkModeCommand)
        assert command.work_mode == 9
        assert command.mode_value == 0


# ==============================================================================
# Auto-Stop Switch Entity Tests
# ==============================================================================


class TestAutoStopSwitchEntity:
    """Test heater auto-stop switch entity."""

    @pytest.fixture
    def mock_coordinator(self, mock_heater_device):
        """Create a mock coordinator for testing."""
        coordinator = MagicMock()
        coordinator.devices = {mock_heater_device.device_id: mock_heater_device}

        state = GoveeDeviceState(
            device_id=mock_heater_device.device_id,
            online=True,
            power_state=True,
            source="api",
        )
        state.heater_auto_stop = 0

        coordinator.get_state = MagicMock(return_value=state)
        coordinator.async_control_device = AsyncMock(return_value=True)
        return coordinator

    @pytest.fixture
    def auto_stop_entity(self, mock_coordinator, mock_heater_device):
        """Create an auto-stop switch entity for testing."""
        from custom_components.govee.switch import GoveeAutoStopSwitchEntity

        entity = GoveeAutoStopSwitchEntity(
            coordinator=mock_coordinator,
            device=mock_heater_device,
        )
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
        return entity

    def test_unique_id(self, auto_stop_entity, mock_heater_device):
        """Test unique ID is correct."""
        from custom_components.govee.const import SUFFIX_HEATER_AUTO_STOP

        expected_id = f"{mock_heater_device.device_id}{SUFFIX_HEATER_AUTO_STOP}"
        assert auto_stop_entity._attr_unique_id == expected_id

    def test_is_off_when_auto_stop_zero(self, auto_stop_entity):
        """Test switch is off when heater_auto_stop is 0."""
        assert auto_stop_entity.is_on is False

    def test_is_on_when_auto_stop_one(self, auto_stop_entity, mock_coordinator):
        """Test switch is on when heater_auto_stop is 1."""
        state = mock_coordinator.get_state.return_value
        state.heater_auto_stop = 1
        assert auto_stop_entity.is_on is True

    async def test_turn_on(self, auto_stop_entity, mock_coordinator):
        """Test turning auto-stop on sends ToggleCommand."""
        await auto_stop_entity.async_turn_on()

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        device_id, command = call_args[0]

        assert device_id == auto_stop_entity._device_id
        assert isinstance(command, ToggleCommand)
        assert command.toggle_instance == INSTANCE_THERMOSTAT_TOGGLE
        assert command.enabled is True

    async def test_turn_off(self, auto_stop_entity, mock_coordinator):
        """Test turning auto-stop off sends ToggleCommand."""
        await auto_stop_entity.async_turn_off()

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        device_id, command = call_args[0]

        assert isinstance(command, ToggleCommand)
        assert command.toggle_instance == INSTANCE_THERMOSTAT_TOGGLE
        assert command.enabled is False

    async def test_turn_on_failure(self, auto_stop_entity, mock_coordinator):
        """Test auto-stop switch does not update on failure."""
        mock_coordinator.async_control_device.return_value = False

        await auto_stop_entity.async_turn_on()

        # Should not update local state or write HA state
        assert auto_stop_entity._is_on is False
        auto_stop_entity.async_write_ha_state.assert_not_called()


# ==============================================================================
# Optimistic State Preservation Tests
# ==============================================================================


class TestHeaterStatePreservation:
    """Test heater state fields are preserved across API polls."""

    def test_heater_auto_stop_field_exists(self):
        """Test GoveeDeviceState has heater_auto_stop field."""
        state = GoveeDeviceState(device_id="test")
        assert state.heater_auto_stop is None
        state.heater_auto_stop = 1
        assert state.heater_auto_stop == 1

    def test_temperature_command_optimistic_update(self):
        """Test TemperatureSettingCommand sets both temperature and auto_stop."""
        cmd = TemperatureSettingCommand(temperature=22, auto_stop=1)
        state = GoveeDeviceState(device_id="test")
        state.heater_temperature = cmd.temperature
        state.heater_auto_stop = cmd.auto_stop
        assert state.heater_temperature == 22
        assert state.heater_auto_stop == 1
