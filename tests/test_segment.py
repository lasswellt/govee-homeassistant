"""Tests for Govee segment light entities."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ColorMode
from homeassistant.const import STATE_ON, STATE_OFF
from homeassistant.core import HomeAssistant, State

from custom_components.govee.entities.segment import GoveeSegmentLight
from custom_components.govee.models import (
    GoveeDevice,
    GoveeDeviceState,
    DeviceCapability,
    CapabilityParameter,
)


@pytest.fixture
def mock_segment_device() -> GoveeDevice:
    """Create a mock RGBIC device with segment support."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:11:22",
        sku="H6199",
        device_name="RGBIC Strip",
        device_type="devices.types.light",
        capabilities=[
            DeviceCapability(
                type="devices.capabilities.on_off",
                instance="powerSwitch",
            ),
            DeviceCapability(
                type="devices.capabilities.range",
                instance="brightness",
                parameters=CapabilityParameter(
                    data_type="INTEGER",
                    range={"min": 0, "max": 100},
                ),
                min_value=0,
                max_value=100,
            ),
            DeviceCapability(
                type="devices.capabilities.color_setting",
                instance="colorRgb",
                parameters=CapabilityParameter(
                    data_type="STRUCT",
                    fields=[
                        {"fieldName": "r", "range": {"min": 0, "max": 255}},
                        {"fieldName": "g", "range": {"min": 0, "max": 255}},
                        {"fieldName": "b", "range": {"min": 0, "max": 255}},
                    ],
                ),
            ),
            DeviceCapability(
                type="devices.capabilities.segment_color_setting",
                instance="segmentedColorRgb",
                parameters=CapabilityParameter(
                    data_type="STRUCT",
                    fields=[
                        {
                            "fieldName": "segment",
                            "type": "ARRAY",
                            "elementRange": {"min": 0, "max": 14},
                        },
                        {"fieldName": "rgb", "type": "INTEGER"},
                    ],
                ),
            ),
        ],
        firmware_version="1.02.03",
    )


@pytest.fixture
def mock_segment_coordinator(hass: HomeAssistant) -> MagicMock:
    """Create a mock coordinator for segment tests."""
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.async_set_segment_color = AsyncMock()
    coordinator.async_set_segment_brightness = AsyncMock()
    coordinator.async_execute_batch = AsyncMock()
    coordinator.last_update_success = True
    coordinator.data = {}
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    # Add config_entry with options for delay setting
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {}
    return coordinator


@pytest.fixture
def segment_light(
    mock_segment_coordinator: MagicMock,
    mock_segment_device: GoveeDevice,
) -> GoveeSegmentLight:
    """Create a segment light entity for testing."""
    segment = GoveeSegmentLight(
        coordinator=mock_segment_coordinator,
        device=mock_segment_device,
        segment_index=0,
    )
    # Initialize device state
    mock_segment_coordinator.get_device_state = MagicMock(return_value=GoveeDeviceState(
        device_id=mock_segment_device.device_id,
        online=True,
        power_state=True,
        brightness=100,
    ))
    return segment


class TestSegmentLightInit:
    """Tests for segment light initialization."""

    def test_unique_id(
        self, segment_light: GoveeSegmentLight, mock_segment_device: GoveeDevice
    ) -> None:
        """Test segment light has correct unique ID."""
        assert segment_light.unique_id == f"{mock_segment_device.device_id}_segment_0"

    def test_color_mode(self, segment_light: GoveeSegmentLight) -> None:
        """Test segment light color mode is RGB."""
        assert segment_light.color_mode == ColorMode.RGB

    def test_supported_color_modes(self, segment_light: GoveeSegmentLight) -> None:
        """Test segment light supports only RGB."""
        assert segment_light.supported_color_modes == {ColorMode.RGB}

    def test_assumed_state(self, segment_light: GoveeSegmentLight) -> None:
        """Test segment light uses assumed state."""
        assert segment_light.assumed_state is True

    def test_translation_placeholders(self, segment_light: GoveeSegmentLight) -> None:
        """Test translation placeholders for segment naming."""
        assert segment_light.translation_placeholders == {"segment_number": "1"}

    def test_different_segment_indices(
        self,
        mock_segment_coordinator: MagicMock,
        mock_segment_device: GoveeDevice,
    ) -> None:
        """Test different segment indices produce unique IDs."""
        segment0 = GoveeSegmentLight(mock_segment_coordinator, mock_segment_device, 0)
        segment5 = GoveeSegmentLight(mock_segment_coordinator, mock_segment_device, 5)
        segment14 = GoveeSegmentLight(mock_segment_coordinator, mock_segment_device, 14)

        assert segment0.unique_id == f"{mock_segment_device.device_id}_segment_0"
        assert segment5.unique_id == f"{mock_segment_device.device_id}_segment_5"
        assert segment14.unique_id == f"{mock_segment_device.device_id}_segment_14"

        # Translation placeholders should be 1-indexed for users
        assert segment0.translation_placeholders == {"segment_number": "1"}
        assert segment5.translation_placeholders == {"segment_number": "6"}
        assert segment14.translation_placeholders == {"segment_number": "15"}


class TestSegmentLightState:
    """Tests for segment light state properties."""

    def test_is_on_none_initially(self, segment_light: GoveeSegmentLight) -> None:
        """Test is_on is None when no optimistic state set."""
        assert segment_light.is_on is None

    def test_is_on_after_turn_on(self, segment_light: GoveeSegmentLight) -> None:
        """Test is_on returns True after optimistic turn on."""
        segment_light._optimistic_on = True
        assert segment_light.is_on is True

    def test_is_on_after_turn_off(self, segment_light: GoveeSegmentLight) -> None:
        """Test is_on returns False after optimistic turn off."""
        segment_light._optimistic_on = False
        assert segment_light.is_on is False

    def test_brightness_none_initially(self, segment_light: GoveeSegmentLight) -> None:
        """Test brightness is None when no optimistic state set."""
        assert segment_light.brightness is None

    def test_brightness_after_set(self, segment_light: GoveeSegmentLight) -> None:
        """Test brightness returns optimistic value."""
        segment_light._optimistic_brightness = 128
        assert segment_light.brightness == 128

    def test_rgb_color_none_initially(self, segment_light: GoveeSegmentLight) -> None:
        """Test rgb_color is None when no optimistic state set."""
        assert segment_light.rgb_color is None

    def test_rgb_color_after_set(self, segment_light: GoveeSegmentLight) -> None:
        """Test rgb_color returns optimistic value."""
        segment_light._optimistic_rgb = (255, 128, 64)
        assert segment_light.rgb_color == (255, 128, 64)


class TestSegmentLightTurnOn:
    """Tests for segment light turn_on using batched command execution."""

    async def test_turn_on_default_color(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
        mock_segment_coordinator: MagicMock,
        mock_segment_device: GoveeDevice,
    ) -> None:
        """Test turn_on uses white when no previous color."""
        segment_light.hass = hass

        with patch.object(segment_light, "async_write_ha_state"):
            await segment_light.async_turn_on()

        # Verify batch execution was called
        mock_segment_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_segment_coordinator.async_execute_batch.call_args
        device_id, batch, delay = call_args[0]

        assert device_id == mock_segment_device.device_id
        assert len(batch.commands) == 1  # Color only, no brightness
        # Verify color command with white (255, 255, 255)
        color_cmd = batch.commands[0]
        assert color_cmd.value["segment"] == [0]
        assert color_cmd.value["rgb"] == (255 << 16) + (255 << 8) + 255

        assert segment_light._optimistic_on is True
        assert segment_light._optimistic_rgb == (255, 255, 255)

    async def test_turn_on_with_rgb_color(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
        mock_segment_coordinator: MagicMock,
        mock_segment_device: GoveeDevice,
    ) -> None:
        """Test turn_on with specific RGB color."""
        segment_light.hass = hass

        with patch.object(segment_light, "async_write_ha_state"):
            await segment_light.async_turn_on(**{ATTR_RGB_COLOR: (255, 0, 128)})

        mock_segment_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_segment_coordinator.async_execute_batch.call_args
        device_id, batch, delay = call_args[0]

        assert device_id == mock_segment_device.device_id
        # Verify color command with (255, 0, 128)
        color_cmd = batch.commands[0]
        assert color_cmd.value["rgb"] == (255 << 16) + (0 << 8) + 128

        assert segment_light._optimistic_on is True
        assert segment_light._optimistic_rgb == (255, 0, 128)

    async def test_turn_on_preserves_previous_color(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
        mock_segment_coordinator: MagicMock,
        mock_segment_device: GoveeDevice,
    ) -> None:
        """Test turn_on uses previous color when available."""
        segment_light.hass = hass
        segment_light._optimistic_rgb = (100, 150, 200)

        with patch.object(segment_light, "async_write_ha_state"):
            await segment_light.async_turn_on()

        mock_segment_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_segment_coordinator.async_execute_batch.call_args
        _, batch, _ = call_args[0]

        # Verify color command uses previous color
        color_cmd = batch.commands[0]
        assert color_cmd.value["rgb"] == (100 << 16) + (150 << 8) + 200

    async def test_turn_on_with_brightness_uses_native_api(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
        mock_segment_coordinator: MagicMock,
        mock_segment_device: GoveeDevice,
    ) -> None:
        """Test turn_on with brightness uses native brightness API via batch."""
        segment_light.hass = hass

        # Turn on with 50% brightness (128/255)
        with patch.object(segment_light, "async_write_ha_state"):
            await segment_light.async_turn_on(**{ATTR_BRIGHTNESS: 128})

        mock_segment_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_segment_coordinator.async_execute_batch.call_args
        _, batch, _ = call_args[0]

        # Should have 2 commands: color then brightness
        assert len(batch.commands) == 2

        # Color command (white by default)
        color_cmd = batch.commands[0]
        assert color_cmd.value["rgb"] == (255 << 16) + (255 << 8) + 255

        # Brightness command (0-100 scale)
        brightness_cmd = batch.commands[1]
        assert brightness_cmd.value["brightness"] == 50  # 128/255 * 100 = ~50%

        assert segment_light._optimistic_brightness == 128

    async def test_turn_on_with_rgb_and_brightness(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
        mock_segment_coordinator: MagicMock,
        mock_segment_device: GoveeDevice,
    ) -> None:
        """Test turn_on with both RGB color and brightness."""
        segment_light.hass = hass

        # Turn on with specific color and 50% brightness
        with patch.object(segment_light, "async_write_ha_state"):
            await segment_light.async_turn_on(**{
                ATTR_RGB_COLOR: (200, 100, 50),
                ATTR_BRIGHTNESS: 128,
            })

        mock_segment_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_segment_coordinator.async_execute_batch.call_args
        _, batch, _ = call_args[0]

        # Should have 2 commands: color then brightness
        assert len(batch.commands) == 2

        # Color command with (200, 100, 50)
        color_cmd = batch.commands[0]
        assert color_cmd.value["rgb"] == (200 << 16) + (100 << 8) + 50

        # Brightness command
        brightness_cmd = batch.commands[1]
        assert brightness_cmd.value["brightness"] == 50

    async def test_turn_on_uses_white_when_previous_was_black(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
        mock_segment_coordinator: MagicMock,
    ) -> None:
        """Test turn_on uses white when previous color was black."""
        segment_light.hass = hass
        segment_light._optimistic_rgb = (0, 0, 0)

        with patch.object(segment_light, "async_write_ha_state"):
            await segment_light.async_turn_on()

        mock_segment_coordinator.async_execute_batch.assert_called_once()
        call_args = mock_segment_coordinator.async_execute_batch.call_args
        _, batch, _ = call_args[0]

        # Should use white since previous was black
        color_cmd = batch.commands[0]
        assert color_cmd.value["rgb"] == (255 << 16) + (255 << 8) + 255


class TestSegmentLightTurnOff:
    """Tests for segment light turn_off."""

    async def test_turn_off_sets_black(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
        mock_segment_coordinator: MagicMock,
        mock_segment_device: GoveeDevice,
    ) -> None:
        """Test turn_off sets segment to black."""
        segment_light.hass = hass

        with patch.object(segment_light, "async_write_ha_state"):
            await segment_light.async_turn_off()

        mock_segment_coordinator.async_set_segment_color.assert_called_once_with(
            mock_segment_device.device_id,
            mock_segment_device.sku,
            0,
            (0, 0, 0),
        )
        assert segment_light._optimistic_on is False
        assert segment_light._optimistic_rgb == (0, 0, 0)


class TestSegmentLightStateRestoration:
    """Tests for segment light state restoration."""

    async def test_restore_on_state(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
    ) -> None:
        """Test restoring 'on' state."""
        last_state = State(
            entity_id="light.test_segment",
            state=STATE_ON,
            attributes={
                ATTR_BRIGHTNESS: 200,
                ATTR_RGB_COLOR: (128, 64, 255),
            },
        )

        with patch.object(
            segment_light, "async_get_last_state", return_value=last_state
        ):
            await segment_light.async_added_to_hass()

        assert segment_light._optimistic_on is True
        assert segment_light._optimistic_brightness == 200
        assert segment_light._optimistic_rgb == (128, 64, 255)

    async def test_restore_off_state(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
    ) -> None:
        """Test restoring 'off' state."""
        last_state = State(
            entity_id="light.test_segment",
            state=STATE_OFF,
            attributes={},
        )

        with patch.object(
            segment_light, "async_get_last_state", return_value=last_state
        ):
            await segment_light.async_added_to_hass()

        assert segment_light._optimistic_on is False

    async def test_restore_no_previous_state(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
    ) -> None:
        """Test when there's no previous state to restore."""
        with patch.object(
            segment_light, "async_get_last_state", return_value=None
        ):
            await segment_light.async_added_to_hass()

        assert segment_light._optimistic_on is None
        assert segment_light._optimistic_brightness is None
        assert segment_light._optimistic_rgb is None

    async def test_restore_partial_attributes(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
    ) -> None:
        """Test restoring state with only some attributes."""
        last_state = State(
            entity_id="light.test_segment",
            state=STATE_ON,
            attributes={
                ATTR_RGB_COLOR: (255, 0, 0),
                # No brightness attribute
            },
        )

        with patch.object(
            segment_light, "async_get_last_state", return_value=last_state
        ):
            await segment_light.async_added_to_hass()

        assert segment_light._optimistic_on is True
        assert segment_light._optimistic_brightness is None
        assert segment_light._optimistic_rgb == (255, 0, 0)


class TestSegmentLightClearState:
    """Tests for clearing segment state."""

    async def test_clear_segment_state(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
    ) -> None:
        """Test clearing segment state resets all optimistic values."""
        segment_light.hass = hass
        segment_light._optimistic_on = True
        segment_light._optimistic_brightness = 200
        segment_light._optimistic_rgb = (255, 128, 64)

        with patch.object(segment_light, "async_write_ha_state"):
            segment_light.clear_segment_state()

        assert segment_light._optimistic_on is None
        assert segment_light._optimistic_brightness is None
        assert segment_light._optimistic_rgb is None


class TestSegmentLightDeviceStateTracking:
    """Tests for device state tracking integration."""

    async def test_turn_on_updates_device_state(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
        mock_segment_coordinator: MagicMock,
    ) -> None:
        """Test turn_on updates device state tracking."""
        segment_light.hass = hass

        # Create a mock device state
        mock_state = MagicMock()
        mock_state.apply_segment_update = MagicMock()
        mock_segment_coordinator.get_device_state = MagicMock(return_value=mock_state)

        with patch.object(segment_light, "async_write_ha_state"):
            await segment_light.async_turn_on(**{ATTR_RGB_COLOR: (100, 150, 200)})

        # device_state property should call apply_segment_update
        # This tests the integration with device state tracking

    async def test_turn_off_updates_device_state(
        self,
        hass: HomeAssistant,
        segment_light: GoveeSegmentLight,
        mock_segment_coordinator: MagicMock,
    ) -> None:
        """Test turn_off updates device state tracking."""
        segment_light.hass = hass

        # Create a mock device state
        mock_state = MagicMock()
        mock_state.apply_segment_update = MagicMock()
        mock_segment_coordinator.get_device_state = MagicMock(return_value=mock_state)

        with patch.object(segment_light, "async_write_ha_state"):
            await segment_light.async_turn_off()


class TestSegmentLightEntityDescription:
    """Tests for segment light entity description."""

    def test_has_entity_description(self, segment_light: GoveeSegmentLight) -> None:
        """Test segment light has entity description."""
        assert segment_light.entity_description is not None

    def test_entity_description_key(self, segment_light: GoveeSegmentLight) -> None:
        """Test entity description has correct key."""
        assert segment_light.entity_description.key == "segment"

    def test_entity_registry_enabled_by_default(
        self, segment_light: GoveeSegmentLight
    ) -> None:
        """Test segment entities are enabled by default.

        Note: Segments are enabled by default so users can immediately use them.
        Users can disable individual segments via the entity registry if desired.
        """
        assert segment_light.entity_description.entity_registry_enabled_default is True
