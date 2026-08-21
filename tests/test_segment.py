"""Test segment entity turn_off logic (issue #16).

Verifies that GoveeSegmentEntity.async_turn_off skips the API call when
a power-off is already in flight or the device is already off, preventing
race conditions that cause RGBIC firmware glitches.
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
from custom_components.govee.platforms.segment import GoveeSegmentEntity


def _make_segment_entity(
    *,
    power_state: bool = True,
    power_off_pending: bool = False,
    state_exists: bool = True,
) -> GoveeSegmentEntity:
    """Create a GoveeSegmentEntity with a mocked coordinator.

    Args:
        power_state: Device power state returned by get_state().
        power_off_pending: Value returned by is_power_off_pending().
        state_exists: Whether get_state() returns a state or None.
    """
    coordinator = MagicMock()
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.is_power_off_pending = MagicMock(return_value=power_off_pending)

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

    # Bypass GoveeEntity.__init__ which requires a real coordinator
    with patch.object(GoveeSegmentEntity, "__init__", lambda self, *a, **kw: None):
        entity = GoveeSegmentEntity.__new__(GoveeSegmentEntity)

    # Set the attributes that __init__ would normally set
    entity.coordinator = coordinator
    entity._device_id = device.device_id
    entity._segment_index = 3
    entity._is_on = True
    entity._brightness = 255
    entity._rgb_color = (255, 255, 255)
    entity.async_write_ha_state = MagicMock()

    return entity


class TestSegmentTurnOffLogic:
    """Test segment async_turn_off race-condition guards."""

    @pytest.mark.asyncio
    async def test_turn_off_sends_command_when_device_on(self):
        """API call is sent when device is on and no power-off pending."""
        entity = _make_segment_entity(power_state=True, power_off_pending=False)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_called_once()
        args = entity.coordinator.async_control_device.call_args
        assert args[0][0] == "AA:BB:CC:DD:EE:FF:00:11"
        cmd = args[0][1]
        assert isinstance(cmd, SegmentColorCommand)
        assert cmd.color == RGBColor(r=0, g=0, b=0)
        assert cmd.segment_indices == (3,)

    @pytest.mark.asyncio
    async def test_turn_off_skipped_when_power_off_pending(self):
        """API call is skipped when power_off_pending=True."""
        entity = _make_segment_entity(power_state=True, power_off_pending=True)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_skipped_when_device_already_off(self):
        """API call is skipped when device is already off."""
        entity = _make_segment_entity(power_state=False, power_off_pending=False)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_skipped_when_both_conditions(self):
        """API call is skipped when both conditions are true."""
        entity = _make_segment_entity(power_state=False, power_off_pending=True)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_local_state_updated_when_command_sent(self):
        """_is_on and async_write_ha_state are always called after sending command."""
        entity = _make_segment_entity(power_state=True, power_off_pending=False)

        await entity.async_turn_off()

        assert entity._is_on is False
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_local_state_updated_when_command_skipped(self):
        """_is_on and async_write_ha_state are always called even when command is skipped."""
        entity = _make_segment_entity(power_state=True, power_off_pending=True)

        await entity.async_turn_off()

        assert entity._is_on is False
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off_when_no_state_exists(self):
        """API call is sent when get_state returns None (device state unknown)."""
        entity = _make_segment_entity(state_exists=False, power_off_pending=False)

        await entity.async_turn_off()

        # When state is None, device_already_off is False, so command should be sent
        entity.coordinator.async_control_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off_yields_before_flag_check(self):
        """asyncio.sleep(0) is called before checking the power-off flag."""
        entity = _make_segment_entity(power_state=True, power_off_pending=False)

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
        """Concurrent area turn_off: segment defers to main entity's PowerCommand.

        Simulates asyncio.gather(main_turn_off, segment_turn_off) and verifies
        the segment skips its SegmentColorCommand because the main entity sets
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

        # Build segment entity
        with patch.object(GoveeSegmentEntity, "__init__", lambda self, *a, **kw: None):
            segment = GoveeSegmentEntity.__new__(GoveeSegmentEntity)
        segment.coordinator = coordinator
        segment._device_id = "AA:BB:CC:DD:EE:FF:00:11"
        segment._segment_index = 0
        segment._is_on = True
        segment._brightness = 255
        segment._rgb_color = (255, 255, 255)
        segment.async_write_ha_state = MagicMock()

        # Simulate main entity turn_off (sends PowerCommand directly)
        async def main_turn_off() -> None:
            await coordinator.async_control_device(
                "AA:BB:CC:DD:EE:FF:00:11",
                PowerCommand(power_on=False),
            )

        async def run_both() -> None:
            await asyncio.gather(main_turn_off(), segment.async_turn_off())

        # Let both coroutines run, then release the API mock
        task = asyncio.create_task(run_both())
        await asyncio.sleep(0)  # let gather start both coroutines
        await asyncio.sleep(0)  # let segment's sleep(0) yield
        api_done.set()
        await task

        # Only PowerCommand should have been sent, not SegmentColorCommand
        assert len(commands_sent) == 1
        assert isinstance(commands_sent[0], PowerCommand)


class TestSegmentOptimisticSync:
    """SEGMENT_MODE_BOTH: a segment mirrors whatever the grouped "all
    segments" entity last broadcast, since Govee gives no real per-segment
    readback to arbitrate between them (issue #164-adjacent)."""

    def test_handle_group_update_mirrors_state_and_writes(self):
        entity = _make_segment_entity()

        entity._handle_group_update(False, 128, (10, 20, 30))

        assert entity._is_on is False
        assert entity._brightness == 128
        assert entity._rgb_color == (10, 20, 30)
        entity.async_write_ha_state.assert_called_once()

    def test_handle_group_update_on(self):
        entity = _make_segment_entity()
        entity._is_on = False
        entity._brightness = 0

        entity._handle_group_update(True, 255, (255, 255, 255))

        assert entity._is_on is True
        assert entity._brightness == 255
        assert entity._rgb_color == (255, 255, 255)
