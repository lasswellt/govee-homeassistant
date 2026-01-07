"""Test Govee light platform."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.helpers.restore_state import State
from homeassistant.const import STATE_ON, STATE_OFF

from custom_components.govee.light import async_setup_entry
from custom_components.govee.entities import GoveeLightEntity
from custom_components.govee.api.exceptions import GoveeApiError
from custom_components.govee.api.const import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    CAPABILITY_MUSIC_SETTING,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_LIGHT_SCENE,
    INSTANCE_POWER_SWITCH,
    INSTANCE_SEGMENTED_COLOR,
    INSTANCE_SEGMENTED_BRIGHTNESS,
    INSTANCE_MUSIC_MODE,
)
from custom_components.govee.const import (
    COLOR_TEMP_KELVIN_MIN,
    COLOR_TEMP_KELVIN_MAX,
)
from custom_components.govee.models import GoveeDeviceState


# ==============================================================================
# Setup Entry Tests
# ==============================================================================


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_setup_entry_creates_light_entities(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_coordinator,
        mock_device_light,
        mock_device_switch,
    ):
        """Test setup creates light entities for light devices."""
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data.devices = {
            mock_device_light.device_id: mock_device_light,
            mock_device_switch.device_id: mock_device_switch,
        }

        async_add_entities = MagicMock()

        with patch(
            "custom_components.govee.light.async_setup_services",
            new_callable=AsyncMock,
        ):
            await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add light entity for light device and switch device (has on/off)
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 2
        assert all(isinstance(e, GoveeLightEntity) for e in entities)

    @pytest.mark.asyncio
    async def test_setup_entry_registers_services(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_coordinator,
        mock_device_light,
    ):
        """Test setup registers light services."""
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data.devices = {
            mock_device_light.device_id: mock_device_light
        }

        with patch(
            "custom_components.govee.light.async_setup_services",
            new_callable=AsyncMock,
        ) as mock_setup_services:
            await async_setup_entry(hass, mock_config_entry, MagicMock())

            # Should register services
            mock_setup_services.assert_called_once_with(hass)

    @pytest.mark.asyncio
    async def test_setup_entry_creates_segment_entities(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_coordinator,
        mock_device_light_with_segments,
    ):
        """Test setup creates segment entities for RGBIC devices (lines 55-76)."""
        from custom_components.govee.entities.segment import GoveeSegmentLight

        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data.devices = {
            mock_device_light_with_segments.device_id: mock_device_light_with_segments,
        }

        async_add_entities = MagicMock()

        with patch(
            "custom_components.govee.light.async_setup_services",
            new_callable=AsyncMock,
        ):
            await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add main light entity + segment entities
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]

        # Main light + segments
        main_lights = [e for e in entities if isinstance(e, GoveeLightEntity)]
        segment_lights = [e for e in entities if isinstance(e, GoveeSegmentLight)]

        assert len(main_lights) == 1
        assert len(segment_lights) == mock_device_light_with_segments.get_segment_count()

    @pytest.mark.asyncio
    async def test_setup_entry_logs_warning_for_many_segments(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_coordinator,
        mock_device_light_with_segments,
        caplog,
    ):
        """Test setup logs warning for devices with many segments (lines 57-64)."""
        from custom_components.govee.light import MAX_SEGMENTS_WARNING

        # Mock device to have more than MAX_SEGMENTS_WARNING segments
        mock_device_light_with_segments.get_segment_count = MagicMock(
            return_value=MAX_SEGMENTS_WARNING + 5
        )

        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data.devices = {
            mock_device_light_with_segments.device_id: mock_device_light_with_segments,
        }

        with patch(
            "custom_components.govee.light.async_setup_services",
            new_callable=AsyncMock,
        ):
            await async_setup_entry(hass, mock_config_entry, MagicMock())

        # Should log warning about unusually high segment count
        assert "unusually high" in caplog.text


# ==============================================================================
# Entity Initialization Tests
# ==============================================================================


class TestGoveeLightEntityInitialization:
    """Test GoveeLightEntity initialization."""

    def test_entity_initialization(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test entity initializes with correct attributes."""
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity._device == mock_device_light
        assert entity.coordinator == mock_coordinator
        assert entity._entry == mock_config_entry
        assert (
            entity._attr_unique_id
            == f"govee_{mock_config_entry.title}_{mock_device_light.device_id}"
        )
        assert entity._attr_name is None  # Uses device name

    def test_color_modes_rgb_and_temp(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test color modes for RGB+CCT device."""
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert ColorMode.RGB in entity._attr_supported_color_modes
        assert ColorMode.COLOR_TEMP in entity._attr_supported_color_modes
        assert ColorMode.BRIGHTNESS not in entity._attr_supported_color_modes
        assert ColorMode.ONOFF not in entity._attr_supported_color_modes

    def test_color_modes_brightness_only(
        self,
        mock_coordinator,
        mock_config_entry,
        device_capability_brightness,
        device_capability_on_off,
    ):
        """Test color modes for brightness-only device."""
        from custom_components.govee.models import GoveeDevice

        device = GoveeDevice(
            device_id="TEST_BRIGHT",
            sku="H6000",
            device_name="Brightness Only",
            device_type="devices.types.light",
            capabilities=[device_capability_on_off, device_capability_brightness],
        )

        entity = GoveeLightEntity(mock_coordinator, device, mock_config_entry)

        assert ColorMode.BRIGHTNESS in entity._attr_supported_color_modes
        assert ColorMode.RGB not in entity._attr_supported_color_modes
        assert ColorMode.COLOR_TEMP not in entity._attr_supported_color_modes

    def test_color_modes_onoff_only(
        self,
        mock_coordinator,
        mock_config_entry,
        device_capability_on_off,
    ):
        """Test color modes for on/off-only device."""
        from custom_components.govee.models import GoveeDevice

        device = GoveeDevice(
            device_id="TEST_ONOFF",
            sku="H6000",
            device_name="OnOff Only",
            device_type="devices.types.light",
            capabilities=[device_capability_on_off],
        )

        entity = GoveeLightEntity(mock_coordinator, device, mock_config_entry)

        assert ColorMode.ONOFF in entity._attr_supported_color_modes
        assert len(entity._attr_supported_color_modes) == 1

    def test_features_with_scene_support(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
        mock_config_entry,
    ):
        """Test features include EFFECT when device supports scenes."""
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_scenes, mock_config_entry
        )

        assert entity._attr_supported_features & LightEntityFeature.EFFECT

    def test_features_without_scene_support(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test features do not include EFFECT without scene support."""
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert not (entity._attr_supported_features & LightEntityFeature.EFFECT)

    def test_color_temp_range_from_device(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test color temp range is set from device capabilities."""
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        # Default range from const.py
        assert entity._attr_min_color_temp_kelvin == COLOR_TEMP_KELVIN_MIN
        assert entity._attr_max_color_temp_kelvin == COLOR_TEMP_KELVIN_MAX
        assert entity.min_color_temp_kelvin == COLOR_TEMP_KELVIN_MIN
        assert entity.max_color_temp_kelvin == COLOR_TEMP_KELVIN_MAX

    def test_effect_list_built_from_device(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
        mock_config_entry,
    ):
        """Test effect list is built from device scene options."""
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_scenes, mock_config_entry
        )

        # Should have effect list from device
        assert hasattr(entity, "_attr_effect_list")
        assert isinstance(entity._attr_effect_list, list)
        # Effect list should be sorted
        assert entity._attr_effect_list == sorted(entity._attr_effect_list)


# ==============================================================================
# State Restoration Tests (for Group Devices)
# ==============================================================================


class TestStateRestoration:
    """Test state restoration for group devices."""

    @pytest.mark.asyncio
    async def test_async_added_to_hass_regular_device_no_restore(
        self,
        hass: HomeAssistant,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test regular device does not restore state."""
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)
        entity.hass = hass

        with patch.object(entity, "async_get_last_state") as mock_get_last_state:
            await entity.async_added_to_hass()

            # Should not attempt to restore state for regular device
            mock_get_last_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_added_to_hass_group_device_restores_power(
        self,
        hass: HomeAssistant,
        mock_coordinator,
        mock_device_group,
        mock_config_entry,
    ):
        """Test group device restores power state."""
        # Set up state
        mock_coordinator.get_state.return_value = GoveeDeviceState(
            device_id=mock_device_group.device_id,
            online=False, power_state=None, brightness=None
        )

        # Create mock last state
        last_state = State("light.test", STATE_ON)

        # Patch UNSUPPORTED_DEVICE_SKUS to include the group device SKU
        with patch(
            "custom_components.govee.const.UNSUPPORTED_DEVICE_SKUS",
            {mock_device_group.sku},
        ):
            entity = GoveeLightEntity(mock_coordinator, mock_device_group, mock_config_entry)
            entity.hass = hass
            entity.async_write_ha_state = MagicMock()

            with patch.object(entity, "async_get_last_state", return_value=last_state):
                await entity.async_added_to_hass()

                # Should restore power state
                state = mock_coordinator.get_state.return_value
                assert state.power_state is True

    @pytest.mark.asyncio
    async def test_async_added_to_hass_group_device_restores_brightness(
        self,
        hass: HomeAssistant,
        mock_coordinator,
        mock_device_group,
        mock_config_entry,
    ):
        """Test group device restores brightness."""
        # Set up state
        mock_coordinator.get_state.return_value = GoveeDeviceState(
            device_id=mock_device_group.device_id,
            online=False, power_state=None, brightness=None
        )

        # Create mock last state with brightness
        last_state = State(
            "light.test", STATE_ON, attributes={ATTR_BRIGHTNESS: 128}
        )

        # Patch UNSUPPORTED_DEVICE_SKUS to include the group device SKU
        with patch(
            "custom_components.govee.const.UNSUPPORTED_DEVICE_SKUS",
            {mock_device_group.sku},
        ):
            entity = GoveeLightEntity(mock_coordinator, mock_device_group, mock_config_entry)
            entity.hass = hass
            entity.async_write_ha_state = MagicMock()

            with patch.object(entity, "async_get_last_state", return_value=last_state):
                await entity.async_added_to_hass()

                # Should restore brightness (converted from HA 0-255 to API 0-100)
                state = mock_coordinator.get_state.return_value
                expected_brightness = round(128 * 100 / 255)
                assert state.brightness == expected_brightness

    @pytest.mark.asyncio
    async def test_async_added_to_hass_group_device_no_last_state(
        self,
        hass: HomeAssistant,
        mock_coordinator,
        mock_device_group,
        mock_config_entry,
        caplog,
    ):
        """Test group device handles no previous state."""
        mock_coordinator.get_state.return_value = GoveeDeviceState(
            device_id=mock_device_group.device_id,
            online=False, power_state=None, brightness=None
        )

        # Patch UNSUPPORTED_DEVICE_SKUS to include the group device SKU
        with patch(
            "custom_components.govee.const.UNSUPPORTED_DEVICE_SKUS",
            {mock_device_group.sku},
        ):
            entity = GoveeLightEntity(mock_coordinator, mock_device_group, mock_config_entry)
            entity.hass = hass

            with patch.object(entity, "async_get_last_state", return_value=None):
                await entity.async_added_to_hass()

                # Should log about no state to restore
                assert "No previous state to restore" in caplog.text


# ==============================================================================
# State Property Tests
# ==============================================================================


class TestStateProperties:
    """Test light state properties."""

    def test_is_on_true(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
        mock_state_light_on,
    ):
        """Test is_on returns True when light is on."""
        mock_coordinator.get_state.return_value = mock_state_light_on
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity.is_on is True

    def test_is_on_false(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
        mock_state_light_off,
    ):
        """Test is_on returns False when light is off."""
        mock_coordinator.get_state.return_value = mock_state_light_off
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity.is_on is False

    def test_is_on_none_when_no_state(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test is_on returns None when no state available."""
        mock_coordinator.get_state.return_value = None
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity.is_on is None

    def test_is_on_offline_with_offline_is_off_true(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry_with_options,
        mock_state_offline,
    ):
        """Test is_on returns False for offline device when offline_is_off=True."""
        # mock_config_entry_with_options has CONF_OFFLINE_IS_OFF: True
        mock_coordinator.get_state.return_value = mock_state_offline
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry_with_options)

        assert entity.is_on is False

    def test_is_on_offline_with_offline_is_off_false(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
        mock_state_offline,
    ):
        """Test is_on returns None for offline device when offline_is_off=False."""
        # mock_config_entry has CONF_OFFLINE_IS_OFF: False
        mock_coordinator.get_state.return_value = mock_state_offline
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity.is_on is None

    def test_brightness_conversion_from_api_to_ha(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test brightness conversion from API range (0-100) to HA range (0-255)."""
        state = GoveeDeviceState(
            device_id=mock_device_light.device_id,
            online=True, power_state=True, brightness=50  # API: 50/100
        )
        mock_coordinator.get_state.return_value = state
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        # 50/100 -> ~128/255
        expected_brightness = round(50 * 255 / 100)
        assert entity.brightness == expected_brightness

    def test_brightness_none_when_no_state(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test brightness returns None when no state."""
        mock_coordinator.get_state.return_value = None
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity.brightness is None

    def test_rgb_color_returns_tuple(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test rgb_color returns RGB tuple."""
        state = GoveeDeviceState(
            device_id=mock_device_light.device_id,
            online=True,
            power_state=True,
            brightness=100,
            color_rgb=(255, 128, 64),
        )
        mock_coordinator.get_state.return_value = state
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity.rgb_color == (255, 128, 64)

    def test_color_temp_kelvin_returns_value(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test color_temp_kelvin returns Kelvin value."""
        state = GoveeDeviceState(
            device_id=mock_device_light.device_id,
            online=True,
            power_state=True,
            brightness=100,
            color_temp_kelvin=4000,
        )
        mock_coordinator.get_state.return_value = state
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity.color_temp_kelvin == 4000

    def test_color_mode_color_temp(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test color_mode returns COLOR_TEMP when color temp is set."""
        state = GoveeDeviceState(
            device_id=mock_device_light.device_id,
            online=True,
            power_state=True,
            brightness=100,
            color_temp_kelvin=4000,
        )
        mock_coordinator.get_state.return_value = state
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity.color_mode == ColorMode.COLOR_TEMP

    def test_color_mode_rgb(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test color_mode returns RGB when RGB is set."""
        state = GoveeDeviceState(
            device_id=mock_device_light.device_id,
            online=True,
            power_state=True,
            brightness=100,
            color_rgb=(255, 128, 64),
        )
        mock_coordinator.get_state.return_value = state
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity.color_mode == ColorMode.RGB

    def test_effect_returns_scene_name(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
        mock_config_entry,
    ):
        """Test effect returns current scene name."""
        state = GoveeDeviceState(
            device_id=mock_device_light_with_scenes.device_id,
            online=True,
            power_state=True,
            brightness=100,
            current_scene_name="Sunset",
        )
        mock_coordinator.get_state.return_value = state
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_scenes, mock_config_entry
        )

        assert entity.effect == "Sunset"

    def test_assumed_state_true(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry_with_options,
    ):
        """Test assumed_state returns True when configured."""
        # mock_config_entry_with_options has CONF_USE_ASSUMED_STATE: True
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry_with_options)

        assert entity.assumed_state is True

    def test_assumed_state_false(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test assumed_state returns False when disabled."""
        # mock_config_entry has CONF_USE_ASSUMED_STATE: False
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity.assumed_state is False


# ==============================================================================
# Turn On/Off Tests
# ==============================================================================


class TestTurnOnOff:
    """Test turn on/off functionality."""

    @pytest.mark.asyncio
    async def test_async_turn_on_no_kwargs(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test async_turn_on with no kwargs turns light on."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        await entity.async_turn_on()

        mock_coordinator.async_control_device.assert_called_once_with(
            mock_device_light.device_id,
            CAPABILITY_ON_OFF,
            INSTANCE_POWER_SWITCH,
            1,
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_with_error(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
        caplog,
    ):
        """Test async_turn_on handles errors."""
        mock_coordinator.async_control_device = AsyncMock(
            side_effect=GoveeApiError("API error")
        )
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        await entity.async_turn_on()

        # Should log error but not raise
        assert "Failed to turn on" in caplog.text
        assert "API error" in caplog.text

    @pytest.mark.asyncio
    async def test_async_turn_off_success(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test async_turn_off turns light off."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        await entity.async_turn_off()

        mock_coordinator.async_control_device.assert_called_once_with(
            mock_device_light.device_id,
            CAPABILITY_ON_OFF,
            INSTANCE_POWER_SWITCH,
            0,
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_with_error(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
        caplog,
    ):
        """Test async_turn_off handles errors."""
        mock_coordinator.async_control_device = AsyncMock(
            side_effect=GoveeApiError("API error")
        )
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        await entity.async_turn_off()

        # Should log error but not raise
        assert "Failed to turn off" in caplog.text
        assert "API error" in caplog.text


# ==============================================================================
# Brightness Control Tests
# ==============================================================================


class TestBrightnessControl:
    """Test brightness control functionality using batched command execution."""

    @pytest.mark.asyncio
    async def test_async_turn_on_with_brightness(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test async_turn_on sets brightness via batch execution."""
        mock_coordinator.async_execute_batch = AsyncMock()
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        # Set brightness to 128 (HA range 0-255)
        await entity.async_turn_on(brightness=128)

        # Should convert to API range (0-100): 128/255 * 100 = 50
        expected_brightness = round(128 * 100 / 255)
        mock_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_coordinator.async_execute_batch.call_args
        device_id, batch, delay = call_args[0]

        assert device_id == mock_device_light.device_id
        assert len(batch.commands) == 1
        assert batch.commands[0].capability_type == CAPABILITY_RANGE
        assert batch.commands[0].instance == INSTANCE_BRIGHTNESS
        assert batch.commands[0].value == expected_brightness

    @pytest.mark.asyncio
    async def test_brightness_clamping_to_device_range(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test brightness is clamped to device range."""
        mock_coordinator.async_execute_batch = AsyncMock()
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        # Set brightness to 255 (max HA range)
        await entity.async_turn_on(brightness=255)

        # Should clamp to device max (100)
        mock_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_coordinator.async_execute_batch.call_args
        _, batch, _ = call_args[0]

        assert batch.commands[0].value == 100  # Device max

    @pytest.mark.asyncio
    async def test_brightness_error_handling(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
        caplog,
    ):
        """Test brightness control handles errors via batch execution."""
        mock_coordinator.async_execute_batch = AsyncMock(
            side_effect=GoveeApiError("API error")
        )
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        # The exception should propagate from batch execution
        with pytest.raises(GoveeApiError):
            await entity.async_turn_on(brightness=128)


# ==============================================================================
# RGB Color Control Tests
# ==============================================================================


class TestRGBColorControl:
    """Test RGB color control functionality using batched command execution."""

    @pytest.mark.asyncio
    async def test_async_turn_on_with_rgb_color(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test async_turn_on sets RGB color via batch execution."""
        mock_coordinator.async_execute_batch = AsyncMock()
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        await entity.async_turn_on(rgb_color=(255, 128, 64))

        # Should convert to int: (255 << 16) + (128 << 8) + 64 = 16744512
        expected_color = (255 << 16) + (128 << 8) + 64
        mock_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_coordinator.async_execute_batch.call_args
        device_id, batch, delay = call_args[0]

        assert device_id == mock_device_light.device_id
        assert len(batch.commands) == 1
        assert batch.commands[0].capability_type == CAPABILITY_COLOR_SETTING
        assert batch.commands[0].instance == INSTANCE_COLOR_RGB
        assert batch.commands[0].value == expected_color

    @pytest.mark.asyncio
    async def test_rgb_color_conversion_red(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test RGB color conversion for red."""
        mock_coordinator.async_execute_batch = AsyncMock()
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        expected_color = (255 << 16)  # 16711680
        mock_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_coordinator.async_execute_batch.call_args
        _, batch, _ = call_args[0]

        assert batch.commands[0].value == expected_color

    @pytest.mark.asyncio
    async def test_rgb_color_error_handling(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
        caplog,
    ):
        """Test RGB color control handles errors via batch execution."""
        mock_coordinator.async_execute_batch = AsyncMock(
            side_effect=GoveeApiError("API error")
        )
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        # The exception should propagate from batch execution
        with pytest.raises(GoveeApiError):
            await entity.async_turn_on(rgb_color=(255, 128, 64))


# ==============================================================================
# Color Temperature Control Tests
# ==============================================================================


class TestColorTemperatureControl:
    """Test color temperature control functionality using batched command execution."""

    @pytest.mark.asyncio
    async def test_async_turn_on_with_color_temp(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test async_turn_on sets color temperature via batch execution."""
        mock_coordinator.async_execute_batch = AsyncMock()
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        await entity.async_turn_on(color_temp_kelvin=4000)

        mock_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_coordinator.async_execute_batch.call_args
        device_id, batch, delay = call_args[0]

        assert device_id == mock_device_light.device_id
        assert len(batch.commands) == 1
        assert batch.commands[0].capability_type == CAPABILITY_COLOR_SETTING
        assert batch.commands[0].instance == INSTANCE_COLOR_TEMP
        assert batch.commands[0].value == 4000

    @pytest.mark.asyncio
    async def test_color_temp_clamping_to_min(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test color temp is clamped to minimum."""
        mock_coordinator.async_execute_batch = AsyncMock()
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        # Try to set below minimum
        await entity.async_turn_on(color_temp_kelvin=1000)

        # Should clamp to min (2000K)
        mock_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_coordinator.async_execute_batch.call_args
        _, batch, _ = call_args[0]

        assert batch.commands[0].value == COLOR_TEMP_KELVIN_MIN

    @pytest.mark.asyncio
    async def test_color_temp_clamping_to_max(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test color temp is clamped to maximum."""
        mock_coordinator.async_execute_batch = AsyncMock()
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        # Try to set above maximum
        await entity.async_turn_on(color_temp_kelvin=10000)

        # Should clamp to max (9000K)
        mock_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_coordinator.async_execute_batch.call_args
        _, batch, _ = call_args[0]

        assert batch.commands[0].value == COLOR_TEMP_KELVIN_MAX

    @pytest.mark.asyncio
    async def test_color_temp_error_handling(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
        caplog,
    ):
        """Test color temp control handles errors via batch execution."""
        mock_coordinator.async_execute_batch = AsyncMock(
            side_effect=GoveeApiError("API error")
        )
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        # The exception should propagate from batch execution
        with pytest.raises(GoveeApiError):
            await entity.async_turn_on(color_temp_kelvin=4000)


# ==============================================================================
# Effect/Scene Control Tests
# ==============================================================================


class TestEffectControl:
    """Test effect/scene control functionality."""

    @pytest.mark.asyncio
    async def test_async_turn_on_with_effect(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
        mock_config_entry,
    ):
        """Test async_turn_on sets effect (scene)."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_scenes, mock_config_entry
        )

        # Build effect map
        entity._effect_map = {
            "Sunset": {"name": "Sunset", "value": 1},
        }

        await entity.async_turn_on(effect="Sunset")

        # Should set scene with raw value (not wrapped in dict)
        mock_coordinator.async_control_device.assert_called_once_with(
            mock_device_light_with_scenes.device_id,
            CAPABILITY_DYNAMIC_SCENE,
            INSTANCE_LIGHT_SCENE,
            1,  # Raw scene value
        )

    @pytest.mark.asyncio
    async def test_effect_unknown_scene(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
        mock_config_entry,
        caplog,
    ):
        """Test setting unknown effect logs warning."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_scenes, mock_config_entry
        )

        entity._effect_map = {}

        await entity.async_turn_on(effect="Unknown Scene")

        # Should log warning and not call coordinator
        assert "Unknown effect" in caplog.text
        mock_coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_effect_error_handling(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
        mock_config_entry,
        caplog,
    ):
        """Test effect control handles errors."""
        mock_coordinator.async_control_device = AsyncMock(
            side_effect=GoveeApiError("API error")
        )
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_scenes, mock_config_entry
        )

        entity._effect_map = {"Sunset": {"name": "Sunset", "value": 1}}

        await entity.async_turn_on(effect="Sunset")

        # Should log error but not raise
        assert "Failed to set effect" in caplog.text
        assert "API error" in caplog.text


# ==============================================================================
# Segment Control Tests (Service Methods)
# ==============================================================================


class TestSegmentControl:
    """Test segment control service methods."""

    @pytest.mark.asyncio
    async def test_async_set_segment_color_success(
        self,
        mock_coordinator,
        mock_device_light_with_segments,
        mock_config_entry,
    ):
        """Test async_set_segment_color sets segment color."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_segments, mock_config_entry
        )

        await entity.async_set_segment_color(
            segments=[0, 1, 2],
            rgb=(255, 128, 64),
        )

        # Should send segment color command
        expected_color = (255 << 16) + (128 << 8) + 64
        expected_value = {"segment": [0, 1, 2], "rgb": expected_color}
        mock_coordinator.async_control_device.assert_called_once_with(
            mock_device_light_with_segments.device_id,
            CAPABILITY_SEGMENT_COLOR,
            INSTANCE_SEGMENTED_COLOR,
            expected_value,
        )

    @pytest.mark.asyncio
    async def test_async_set_segment_color_unsupported_device(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
        caplog,
    ):
        """Test async_set_segment_color warns for unsupported device."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        await entity.async_set_segment_color(
            segments=[0, 1],
            rgb=(255, 0, 0),
        )

        # Should log warning and not call coordinator
        assert "does not support segment control" in caplog.text
        mock_coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_set_segment_brightness_success(
        self,
        mock_coordinator,
        mock_device_light_with_segments,
        mock_config_entry,
    ):
        """Test async_set_segment_brightness sets segment brightness."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_segments, mock_config_entry
        )

        await entity.async_set_segment_brightness(
            segments=[0, 1, 2],
            brightness=80,
        )

        # Should send segment brightness command
        expected_value = {"segment": [0, 1, 2], "brightness": 80}
        mock_coordinator.async_control_device.assert_called_once_with(
            mock_device_light_with_segments.device_id,
            CAPABILITY_SEGMENT_COLOR,
            INSTANCE_SEGMENTED_BRIGHTNESS,
            expected_value,
        )

    @pytest.mark.asyncio
    async def test_async_set_segment_brightness_error_handling(
        self,
        mock_coordinator,
        mock_device_light_with_segments,
        mock_config_entry,
        caplog,
    ):
        """Test async_set_segment_brightness handles errors."""
        mock_coordinator.async_control_device = AsyncMock(
            side_effect=GoveeApiError("API error")
        )
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_segments, mock_config_entry
        )

        await entity.async_set_segment_brightness(
            segments=[0],
            brightness=50,
        )

        # Should log error but not raise
        assert "Failed to set segment brightness" in caplog.text
        assert "API error" in caplog.text


# ==============================================================================
# Music Mode Tests (Service Method)
# ==============================================================================


class TestMusicMode:
    """Test music mode service method."""

    @pytest.mark.asyncio
    async def test_async_set_music_mode_success(
        self,
        mock_coordinator,
        mock_device_light_with_music_mode,
        mock_config_entry,
    ):
        """Test async_set_music_mode activates music mode."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_music_mode, mock_config_entry
        )

        await entity.async_set_music_mode(
            mode="energic",
            sensitivity=70,
            auto_color=True,
        )

        # Should send music mode command
        expected_value = {
            "musicMode": "energic",
            "sensitivity": 70,
            "autoColor": 1,
        }
        mock_coordinator.async_control_device.assert_called_once_with(
            mock_device_light_with_music_mode.device_id,
            CAPABILITY_MUSIC_SETTING,
            INSTANCE_MUSIC_MODE,
            expected_value,
        )

    @pytest.mark.asyncio
    async def test_async_set_music_mode_with_manual_color(
        self,
        mock_coordinator,
        mock_device_light_with_music_mode,
        mock_config_entry,
    ):
        """Test async_set_music_mode with manual color."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_music_mode, mock_config_entry
        )

        await entity.async_set_music_mode(
            mode="energic",
            sensitivity=50,
            auto_color=False,
            rgb=(255, 0, 0),
        )

        # Should include color in command
        expected_color = (255 << 16)
        expected_value = {
            "musicMode": "energic",
            "sensitivity": 50,
            "autoColor": 0,
            "color": expected_color,
        }
        mock_coordinator.async_control_device.assert_called_once_with(
            mock_device_light_with_music_mode.device_id,
            CAPABILITY_MUSIC_SETTING,
            INSTANCE_MUSIC_MODE,
            expected_value,
        )

    @pytest.mark.asyncio
    async def test_async_set_music_mode_unsupported_device(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
        caplog,
    ):
        """Test async_set_music_mode warns for unsupported device."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        await entity.async_set_music_mode(mode="energic")

        # Should log warning and not call coordinator
        assert "does not support music mode" in caplog.text
        mock_coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_set_music_mode_error_handling(
        self,
        mock_coordinator,
        mock_device_light_with_music_mode,
        mock_config_entry,
        caplog,
    ):
        """Test async_set_music_mode handles errors."""
        mock_coordinator.async_control_device = AsyncMock(
            side_effect=GoveeApiError("API error")
        )
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_music_mode, mock_config_entry
        )

        await entity.async_set_music_mode(mode="energic")

        # Should log error but not raise
        assert "Failed to set music mode" in caplog.text
        assert "API error" in caplog.text


# ==============================================================================
# Multiple Attribute Tests
# ==============================================================================


class TestMultipleAttributes:
    """Test setting multiple attributes at once using batched execution."""

    @pytest.mark.asyncio
    async def test_async_turn_on_with_brightness_and_color(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test async_turn_on batches brightness and color commands."""
        mock_coordinator.async_execute_batch = AsyncMock()
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        await entity.async_turn_on(
            brightness=128,
            rgb_color=(255, 128, 64),
        )

        # Should call batch execution once with 2 commands (color first, brightness last)
        mock_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_coordinator.async_execute_batch.call_args
        _, batch, _ = call_args[0]

        assert len(batch.commands) == 2
        # Color should be first (validated order)
        assert batch.commands[0].capability_type == CAPABILITY_COLOR_SETTING
        assert batch.commands[0].instance == INSTANCE_COLOR_RGB
        # Brightness should be last
        assert batch.commands[1].capability_type == CAPABILITY_RANGE
        assert batch.commands[1].instance == INSTANCE_BRIGHTNESS

    @pytest.mark.asyncio
    async def test_effect_takes_precedence(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
        mock_config_entry,
    ):
        """Test effect parameter takes precedence over other attributes."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_scenes, mock_config_entry
        )

        entity._effect_map = {"Sunset": {"name": "Sunset", "value": 1}}

        await entity.async_turn_on(
            effect="Sunset",
            brightness=128,
            rgb_color=(255, 0, 0),
        )

        # Should only call effect command (bypasses batching), not brightness/color
        assert mock_coordinator.async_control_device.call_count == 1
        call_args = mock_coordinator.async_control_device.call_args
        assert call_args[0][1] == CAPABILITY_DYNAMIC_SCENE


# ==============================================================================
# Additional State Property Tests (Edge Cases)
# ==============================================================================


class TestStatePropertyEdgeCases:
    """Test state property edge cases for coverage."""

    def test_rgb_color_none_when_no_state(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test rgb_color returns None when no state (line 258)."""
        mock_coordinator.get_state.return_value = None
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity.rgb_color is None

    def test_color_temp_kelvin_none_when_no_state(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test color_temp_kelvin returns None when no state (line 266)."""
        mock_coordinator.get_state.return_value = None
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity.color_temp_kelvin is None

    def test_color_mode_none_when_no_state(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test color_mode returns None when no state (line 274)."""
        mock_coordinator.get_state.return_value = None
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        assert entity.color_mode is None

    def test_color_mode_onoff_fallback(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test color_mode falls back to ONOFF (line 293)."""
        state = GoveeDeviceState(
            device_id=mock_device_light.device_id,
            online=True,
            power_state=True,
            brightness=100,
            color_rgb=None,
            color_temp_kelvin=None,
        )
        mock_coordinator.get_state.return_value = state
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        # Force supported_color_modes to only ONOFF
        entity._attr_supported_color_modes = {ColorMode.ONOFF}

        assert entity.color_mode == ColorMode.ONOFF

    def test_color_mode_onoff_when_supported_none(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
    ):
        """Test color_mode returns ONOFF when supported_color_modes is None (line 278)."""
        state = GoveeDeviceState(
            device_id=mock_device_light.device_id,
            online=True,
            power_state=True,
            brightness=100,
        )
        mock_coordinator.get_state.return_value = state
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        # Force supported_color_modes to None
        entity._attr_supported_color_modes = None

        assert entity.color_mode == ColorMode.ONOFF

    def test_color_mode_brightness_fallback(
        self,
        mock_coordinator,
        mock_config_entry,
        device_capability_brightness,
        device_capability_on_off,
    ):
        """Test color_mode falls back to BRIGHTNESS (lines 291-292)."""
        from custom_components.govee.models import GoveeDevice

        device = GoveeDevice(
            device_id="TEST_BRIGHT",
            sku="H6000",
            device_name="Brightness Only",
            device_type="devices.types.light",
            capabilities=[device_capability_on_off, device_capability_brightness],
        )

        state = GoveeDeviceState(
            device_id=device.device_id,
            online=True,
            power_state=True,
            brightness=50,
            color_rgb=None,
            color_temp_kelvin=None,
        )
        mock_coordinator.get_state.return_value = state
        entity = GoveeLightEntity(mock_coordinator, device, mock_config_entry)

        assert entity.color_mode == ColorMode.BRIGHTNESS

    def test_effect_none_when_no_state(
        self,
        mock_coordinator,
        mock_device_light_with_scenes,
        mock_config_entry,
    ):
        """Test effect returns None when no state (line 300)."""
        mock_coordinator.get_state.return_value = None
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_scenes, mock_config_entry
        )

        assert entity.effect is None


# ==============================================================================
# Additional State Restoration Tests (Edge Cases)
# ==============================================================================


class TestStateRestorationEdgeCases:
    """Test state restoration edge cases for coverage."""

    @pytest.mark.asyncio
    async def test_async_added_to_hass_state_exists(
        self,
        hass: HomeAssistant,
        mock_coordinator,
        mock_device_group,
        mock_config_entry,
    ):
        """Test state not restored when coordinator already has state (line 133)."""
        # Set up existing state with power_state
        existing_state = GoveeDeviceState(
            device_id=mock_device_group.device_id,
            online=False,
            power_state=True,  # Already has power_state
            brightness=50,
        )
        mock_coordinator.get_state.return_value = existing_state

        # Create mock last state
        last_state = State("light.test", STATE_OFF)

        with patch(
            "custom_components.govee.const.UNSUPPORTED_DEVICE_SKUS",
            {mock_device_group.sku},
        ):
            entity = GoveeLightEntity(mock_coordinator, mock_device_group, mock_config_entry)
            entity.hass = hass

            with patch.object(entity, "async_get_last_state", return_value=last_state):
                await entity.async_added_to_hass()

                # Should NOT restore state because existing state has power_state
                assert existing_state.power_state is True  # Not changed to False

    @pytest.mark.asyncio
    async def test_async_added_to_hass_restores_rgb_color(
        self,
        hass: HomeAssistant,
        mock_coordinator,
        mock_device_group,
        mock_config_entry,
    ):
        """Test state restoration restores RGB color (line 161)."""
        # Set up state without power_state
        state = GoveeDeviceState(
            device_id=mock_device_group.device_id,
            online=False,
            power_state=None,
            brightness=None,
        )
        mock_coordinator.get_state.return_value = state

        # Create mock last state with RGB color
        last_state = State(
            "light.test",
            STATE_ON,
            attributes={ATTR_RGB_COLOR: (255, 128, 64)},
        )

        with patch(
            "custom_components.govee.const.UNSUPPORTED_DEVICE_SKUS",
            {mock_device_group.sku},
        ):
            entity = GoveeLightEntity(mock_coordinator, mock_device_group, mock_config_entry)
            entity.hass = hass
            entity.async_write_ha_state = MagicMock()

            with patch.object(entity, "async_get_last_state", return_value=last_state):
                await entity.async_added_to_hass()

                # Should restore RGB color
                assert state.color_rgb == (255, 128, 64)

    @pytest.mark.asyncio
    async def test_async_added_to_hass_restores_color_temp(
        self,
        hass: HomeAssistant,
        mock_coordinator,
        mock_device_group,
        mock_config_entry,
    ):
        """Test state restoration restores color temperature (line 165)."""
        # Set up state without power_state
        state = GoveeDeviceState(
            device_id=mock_device_group.device_id,
            online=False,
            power_state=None,
            brightness=None,
        )
        mock_coordinator.get_state.return_value = state

        # Create mock last state with color temp
        last_state = State(
            "light.test",
            STATE_ON,
            attributes={ATTR_COLOR_TEMP_KELVIN: 4500},
        )

        with patch(
            "custom_components.govee.const.UNSUPPORTED_DEVICE_SKUS",
            {mock_device_group.sku},
        ):
            entity = GoveeLightEntity(mock_coordinator, mock_device_group, mock_config_entry)
            entity.hass = hass
            entity.async_write_ha_state = MagicMock()

            with patch.object(entity, "async_get_last_state", return_value=last_state):
                await entity.async_added_to_hass()

                # Should restore color temperature
                assert state.color_temp_kelvin == 4500


# ==============================================================================
# Additional Segment Control Tests (Error Cases)
# ==============================================================================


class TestSegmentControlErrors:
    """Test segment control error handling for coverage."""

    @pytest.mark.asyncio
    async def test_async_set_segment_color_error(
        self,
        mock_coordinator,
        mock_device_light_with_segments,
        mock_config_entry,
        caplog,
    ):
        """Test async_set_segment_color handles API errors (lines 503-504)."""
        mock_coordinator.async_control_device = AsyncMock(
            side_effect=GoveeApiError("API error")
        )
        entity = GoveeLightEntity(
            mock_coordinator, mock_device_light_with_segments, mock_config_entry
        )

        await entity.async_set_segment_color(
            segments=[0, 1],
            rgb=(255, 0, 0),
        )

        # Should log error but not raise
        assert "Failed to set segment color" in caplog.text
        assert "API error" in caplog.text

    @pytest.mark.asyncio
    async def test_async_set_segment_brightness_unsupported(
        self,
        mock_coordinator,
        mock_device_light,
        mock_config_entry,
        caplog,
    ):
        """Test async_set_segment_brightness on unsupported device (lines 520-524)."""
        mock_coordinator.async_control_device = AsyncMock()
        entity = GoveeLightEntity(mock_coordinator, mock_device_light, mock_config_entry)

        await entity.async_set_segment_brightness(
            segments=[0, 1],
            brightness=80,
        )

        # Should log warning and not call coordinator
        assert "does not support segment control" in caplog.text
        mock_coordinator.async_control_device.assert_not_called()
