"""Test grouped segment entity.

Verifies that GoveeGroupedSegmentEntity controls all segments as a single entity
with color and brightness control, similar to individual segments but for all at once.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.models import (
    GoveeDeviceState,
    PowerCommand,
    RGBColor,
    SegmentColorCommand,
)
from custom_components.govee.platforms.grouped_segment import GoveeGroupedSegmentEntity


def _make_grouped_segment_entity(
    *,
    power_state: bool = True,
    power_off_pending: bool = False,
    state_exists: bool = True,
    segment_count: int = 8,
) -> GoveeGroupedSegmentEntity:
    """Create a GoveeGroupedSegmentEntity with a mocked coordinator.

    Args:
        power_state: Device power state returned by get_state().
        power_off_pending: Value returned by is_power_off_pending().
        state_exists: Whether get_state() returns a state or None.
        segment_count: Number of segments in the device.
    """
    coordinator = MagicMock()
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.is_power_off_pending = MagicMock(return_value=power_off_pending)
    coordinator.last_update_success = True

    if state_exists:
        state = GoveeDeviceState.create_empty("AA:BB:CC:DD:EE:FF:00:11")
        state.power_state = power_state
        coordinator.get_state = MagicMock(return_value=state)
    else:
        coordinator.get_state = MagicMock(return_value=None)

    device = MagicMock()
    device.device_id = "AA:BB:CC:DD:EE:FF:00:11"
    device.sku = "H60A1"
    device.name = "RGBIC Strip"
    device.segment_count = segment_count

    # Bypass GoveeGroupedSegmentEntity.__init__ which requires a real coordinator
    with patch.object(
        GoveeGroupedSegmentEntity, "__init__", lambda self, *a, **kw: None
    ):
        entity = GoveeGroupedSegmentEntity.__new__(GoveeGroupedSegmentEntity)

    # Set the attributes that __init__ would normally set
    entity.coordinator = coordinator
    entity._device_id = device.device_id
    entity._device = device
    entity._segment_indices = tuple(range(segment_count))
    entity._is_on = True
    entity._brightness = 255
    entity._rgb_color = (255, 255, 255)
    entity.async_write_ha_state = MagicMock()
    # async_turn_on/off now also fire a dispatcher signal (SEGMENT_MODE_BOTH
    # sync, #164-adjacent) — real async_dispatcher_send() needs hass.data to
    # be an actual dict to safely no-op against zero listeners.
    entity.hass = MagicMock()
    entity.hass.data = {}

    return entity


class TestGroupedSegmentEntity:
    """Test grouped segment entity functionality."""

    @pytest.mark.asyncio
    async def test_turn_on_sends_command_to_all_segments(self):
        """async_turn_on sends command with all segment indices."""
        entity = _make_grouped_segment_entity(segment_count=8)

        await entity.async_turn_on()

        entity.coordinator.async_control_device.assert_called_once()
        args = entity.coordinator.async_control_device.call_args
        assert args[0][0] == "AA:BB:CC:DD:EE:FF:00:11"
        cmd = args[0][1]
        assert isinstance(cmd, SegmentColorCommand)
        assert cmd.segment_indices == tuple(range(8))

    @pytest.mark.asyncio
    async def test_turn_on_with_color(self):
        """async_turn_on sets color from ATTR_RGB_COLOR."""
        entity = _make_grouped_segment_entity()

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        args = entity.coordinator.async_control_device.call_args
        cmd = args[0][1]
        assert cmd.color == RGBColor(r=255, g=0, b=0)
        assert entity._rgb_color == (255, 0, 0)

    @pytest.mark.asyncio
    async def test_turn_on_with_brightness(self):
        """async_turn_on updates brightness from ATTR_BRIGHTNESS."""
        entity = _make_grouped_segment_entity()

        await entity.async_turn_on(brightness=128)

        assert entity._brightness == 128
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off_sends_black_color_to_all_segments(self):
        """async_turn_off sends black color command with all segment indices."""
        entity = _make_grouped_segment_entity(power_state=True, power_off_pending=False)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_called_once()
        args = entity.coordinator.async_control_device.call_args
        cmd = args[0][1]
        assert isinstance(cmd, SegmentColorCommand)
        assert cmd.color == RGBColor(r=0, g=0, b=0)
        assert cmd.segment_indices == tuple(range(8))

    @pytest.mark.asyncio
    async def test_turn_off_skipped_when_power_off_pending(self):
        """async_turn_off skips command when power_off_pending=True."""
        entity = _make_grouped_segment_entity(power_state=True, power_off_pending=True)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_skipped_when_device_already_off(self):
        """async_turn_off skips command when device is already off."""
        entity = _make_grouped_segment_entity(
            power_state=False, power_off_pending=False
        )

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_on_property(self):
        """is_on property returns correct state."""
        entity = _make_grouped_segment_entity()
        assert entity.is_on is True

        entity._is_on = False
        assert entity.is_on is False

    @pytest.mark.asyncio
    async def test_brightness_property(self):
        """brightness property returns correct value."""
        entity = _make_grouped_segment_entity()
        assert entity.brightness == 255

        entity._brightness = 128
        assert entity.brightness == 128

    @pytest.mark.asyncio
    async def test_rgb_color_property(self):
        """rgb_color property returns correct tuple."""
        entity = _make_grouped_segment_entity()
        assert entity.rgb_color == (255, 255, 255)

        entity._rgb_color = (255, 0, 0)
        assert entity.rgb_color == (255, 0, 0)

    @pytest.mark.asyncio
    async def test_available_property(self):
        """available property reflects coordinator health."""
        entity = _make_grouped_segment_entity()
        assert entity.available is True

        entity.coordinator.last_update_success = False
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_different_segment_counts(self):
        """Works correctly with different numbers of segments."""
        for segment_count in [1, 4, 8, 16]:
            entity = _make_grouped_segment_entity(segment_count=segment_count)
            assert entity._segment_indices == tuple(range(segment_count))

    @pytest.mark.asyncio
    async def test_turn_off_yields_before_flag_check(self):
        """asyncio.sleep(0) is called before checking the power-off flag."""
        entity = _make_grouped_segment_entity(power_state=True, power_off_pending=False)

        call_order: list[str] = []
        original_sleep = asyncio.sleep

        async def tracking_sleep(delay: float, *args: object) -> None:
            if delay == 0:
                call_order.append("sleep_0")
            await original_sleep(delay)

        entity.coordinator.is_power_off_pending = MagicMock(
            side_effect=lambda _: (call_order.append("flag_check"), False)[1]
        )

        with patch("asyncio.sleep", side_effect=tracking_sleep):
            await entity.async_turn_off()

        assert call_order == ["sleep_0", "flag_check"]

    @pytest.mark.asyncio
    async def test_concurrent_turn_off_with_main_entity(self):
        """Concurrent area turn_off: grouped segment defers to main entity's PowerCommand.

        Simulates asyncio.gather(main_turn_off, grouped_segment_turn_off) and verifies
        the grouped segment skips its SegmentColorCommand because the main entity sets
        the power-off flag first.
        """
        coordinator = MagicMock()
        coordinator.last_update_success = True

        state = GoveeDeviceState.create_empty("AA:BB:CC:DD:EE:FF:00:11")
        state.power_state = True
        coordinator.get_state = MagicMock(return_value=state)

        # Track commands sent and implement real pending-power-off logic
        pending_power_off: set[str] = set()
        commands_sent: list[object] = []

        # Use an event so the mock API call holds the flag until the
        # segment has had a chance to check it (mirrors real API latency).
        api_done = asyncio.Event()

        async def mock_control(device_id: str, command: object) -> bool:
            is_power_off = isinstance(command, PowerCommand) and not command.power_on
            if is_power_off:
                pending_power_off.add(device_id)
            commands_sent.append(command)
            if is_power_off:
                await api_done.wait()
                pending_power_off.discard(device_id)
            return True

        coordinator.async_control_device = mock_control
        coordinator.is_power_off_pending = lambda did: did in pending_power_off

        # Build grouped segment entity
        with patch.object(
            GoveeGroupedSegmentEntity, "__init__", lambda self, *a, **kw: None
        ):
            entity = GoveeGroupedSegmentEntity.__new__(GoveeGroupedSegmentEntity)
        entity.coordinator = coordinator
        entity._device_id = "AA:BB:CC:DD:EE:FF:00:11"
        entity._segment_indices = tuple(range(8))
        entity._is_on = True
        entity._brightness = 255
        entity._rgb_color = (255, 255, 255)
        entity.async_write_ha_state = MagicMock()
        entity.hass = MagicMock()
        entity.hass.data = {}

        # Simulate main entity turn_off (sends PowerCommand directly)
        async def main_turn_off() -> None:
            await coordinator.async_control_device(
                "AA:BB:CC:DD:EE:FF:00:11",
                PowerCommand(power_on=False),
            )

        async def run_both() -> None:
            await asyncio.gather(main_turn_off(), entity.async_turn_off())

        # Let both coroutines run, then release the API mock
        task = asyncio.create_task(run_both())
        await asyncio.sleep(0)  # let gather start both coroutines
        await asyncio.sleep(0)  # let segment's sleep(0) yield
        api_done.set()
        await task

        # Only PowerCommand should have been sent, not SegmentColorCommand
        assert len(commands_sent) == 1
        assert isinstance(commands_sent[0], PowerCommand)


class TestGroupedSegmentOptimisticSync:
    """SEGMENT_MODE_BOTH: the grouped entity broadcasts its state so the 12
    individual segment entities don't drift from what the group last set."""

    @pytest.mark.asyncio
    async def test_turn_on_broadcasts_the_signal_for_this_device(self):
        entity = _make_grouped_segment_entity()
        with patch(
            "custom_components.govee.platforms.grouped_segment.async_dispatcher_send"
        ) as mock_send:
            await entity.async_turn_on()

        mock_send.assert_called_once_with(
            entity.hass,
            "govee_segments_optimistic_AA:BB:CC:DD:EE:FF:00:11",
            True,
            255,
            (255, 255, 255),
        )

    @pytest.mark.asyncio
    async def test_turn_off_broadcasts_off_state(self):
        entity = _make_grouped_segment_entity()
        with patch(
            "custom_components.govee.platforms.grouped_segment.async_dispatcher_send"
        ) as mock_send:
            await entity.async_turn_off()

        args = mock_send.call_args[0]
        assert args[1] == "govee_segments_optimistic_AA:BB:CC:DD:EE:FF:00:11"
        assert args[2] is False

    @pytest.mark.asyncio
    async def test_turn_off_still_broadcasts_when_api_call_skipped(self):
        """Even the already-off/power-off-pending skip path must broadcast —
        the individual entities need to hear "off" regardless of whether a
        fresh command was actually sent (issue #164-adjacent)."""
        entity = _make_grouped_segment_entity(
            power_state=True, power_off_pending=True
        )
        with patch(
            "custom_components.govee.platforms.grouped_segment.async_dispatcher_send"
        ) as mock_send:
            await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_not_called()
        mock_send.assert_called_once()
        assert mock_send.call_args[0][2] is False
