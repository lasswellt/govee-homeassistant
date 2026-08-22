"""Test the dedicated main-panel light entity (issue #131).

Covers the mechanism that makes the two zones of a Ceiling Light Pro
independently controllable: the main panel is switched via the whole-device
colour channel (black == off) rather than ``powerSwitch``, and every
main-channel write is followed by re-asserting the ring's segment colours,
which that write would otherwise wipe.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.light import GoveeMainLightEntity
from custom_components.govee.models import (
    ColorCommand,
    ColorTempCommand,
    GoveeDeviceState,
    RGBColor,
)


def _make_main_light_entity(
    *,
    power_state: bool = True,
    power_off_pending: bool = False,
    color: tuple[int, int, int] | None = (255, 255, 255),
    color_temp_kelvin: int | None = None,
    state_exists: bool = True,
) -> GoveeMainLightEntity:
    """Create a GoveeMainLightEntity with a mocked coordinator.

    Args:
        power_state: Whole-device power state returned by get_state().
        power_off_pending: Value returned by coordinator.is_power_off_pending().
        color: Whole-device colour, or None for "no colour reported".
        color_temp_kelvin: Active colour temperature, if any.
        state_exists: Whether get_state() returns a state or None.
    """
    coordinator = MagicMock()
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.async_reassert_segments = AsyncMock()
    # Explicit: a bare MagicMock would return a truthy sentinel here and make
    # every turn_off silently take the power-off-guard branch.
    coordinator.is_power_off_pending = MagicMock(return_value=power_off_pending)

    if state_exists:
        state = GoveeDeviceState.create_empty("AA:BB:CC:DD:EE:FF:00:11")
        state.power_state = power_state
        state.color = RGBColor(r=color[0], g=color[1], b=color[2]) if color else None
        state.color_temp_kelvin = color_temp_kelvin
        coordinator.get_state = MagicMock(return_value=state)
    else:
        coordinator.get_state = MagicMock(return_value=None)

    device = MagicMock()
    device.device_id = "AA:BB:CC:DD:EE:FF:00:11"
    device.sku = "H1270"
    device.name = "Ceiling Light"
    device.supports_rgb = True
    device.supports_color_temp = True
    device.supports_brightness = True
    device.brightness_range = (1, 100)
    device.color_temp_range = None

    with patch.object(GoveeMainLightEntity, "__init__", lambda self, *a, **kw: None):
        entity = GoveeMainLightEntity.__new__(GoveeMainLightEntity)

    entity.coordinator = coordinator
    entity._device = device
    entity._device_id = device.device_id
    entity._brightness_min, entity._brightness_max = device.brightness_range
    entity._last_on_kelvin = None
    entity._last_on_rgb = None
    entity.async_write_ha_state = MagicMock()

    return entity


class TestMainLightIsOn:
    """is_on is derived from real device state, not tracked optimistically."""

    def test_on_when_color_temp_active(self):
        """A live colour temperature means lit, whatever stale RGB says.

        Writing a colour clears colorTemperatureK on this firmware and writing
        a colour temperature leaves the old RGB behind, so colour temp has to
        win — otherwise a CCT-lit panel whose last RGB was black reads as off.
        """
        entity = _make_main_light_entity(color=(0, 0, 0), color_temp_kelvin=4000)
        assert entity.is_on is True

    def test_off_when_black_and_no_color_temp(self):
        entity = _make_main_light_entity(color=(0, 0, 0), color_temp_kelvin=None)
        assert entity.is_on is False

    def test_on_when_non_black_color(self):
        entity = _make_main_light_entity(color=(255, 0, 0), color_temp_kelvin=None)
        assert entity.is_on is True

    def test_off_when_device_powered_off(self):
        """powerSwitch off kills everything, so the panel is off too."""
        entity = _make_main_light_entity(power_state=False, color=(255, 0, 0))
        assert entity.is_on is False

    def test_unknown_when_no_state(self):
        """No state yet is "unknown", not "off" — same as GoveeLightEntity."""
        entity = _make_main_light_entity(state_exists=False)
        assert entity.is_on is None


class TestMainLightTurnOff:
    """Turning off writes black to the colour channel — never powerSwitch."""

    @pytest.mark.asyncio
    async def test_turn_off_sends_black_color(self):
        entity = _make_main_light_entity(color_temp_kelvin=4000)

        await entity.async_turn_off()

        cmds = [c[0][1] for c in entity.coordinator.async_control_device.call_args_list]
        assert len(cmds) == 1
        assert isinstance(cmds[0], ColorCommand)
        assert cmds[0].color == RGBColor(r=0, g=0, b=0)

    @pytest.mark.asyncio
    async def test_turn_off_never_uses_power_switch(self):
        """Regression guard: powerSwitch would kill the ring and cause the
        firmware coupling that wakes the panel on any later command."""
        from custom_components.govee.models import PowerCommand

        entity = _make_main_light_entity(color_temp_kelvin=4000)

        await entity.async_turn_off()

        cmds = [c[0][1] for c in entity.coordinator.async_control_device.call_args_list]
        assert not any(isinstance(c, PowerCommand) for c in cmds)

    @pytest.mark.asyncio
    async def test_turn_off_reasserts_segments(self):
        """Black wipes the ring, so it must be restored afterwards."""
        entity = _make_main_light_entity(color_temp_kelvin=4000)

        await entity.async_turn_off()

        # wrote_black lets the coordinator skip the replay when the ring is
        # already all black — safe only because this write was itself black.
        entity.coordinator.async_reassert_segments.assert_awaited_once_with(
            "AA:BB:CC:DD:EE:FF:00:11", wrote_black=True
        )

    @pytest.mark.asyncio
    async def test_turn_off_remembers_color_temp_for_next_on(self):
        entity = _make_main_light_entity(color_temp_kelvin=3200)

        await entity.async_turn_off()

        assert entity._last_on_kelvin == 3200

    @pytest.mark.asyncio
    async def test_turn_off_skipped_when_power_off_pending(self):
        """Area turn_off: the master entity's powerSwitch-off wins.

        Without this, the colour write plus the ring re-assert would land
        after the power-off and — on this fixture, where any light command
        wakes the main panel — turn the light back on (issue #16, #131).
        """
        entity = _make_main_light_entity(color_temp_kelvin=4000, power_off_pending=True)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_not_awaited()
        entity.coordinator.async_reassert_segments.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turn_off_skipped_when_device_already_off(self):
        """Writing colour to a powered-down Govee light powers it back on."""
        entity = _make_main_light_entity(power_state=False, color_temp_kelvin=4000)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_not_awaited()
        entity.coordinator.async_reassert_segments.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turn_off_skips_reassert_when_command_fails(self):
        entity = _make_main_light_entity(color_temp_kelvin=4000)
        entity.coordinator.async_control_device = AsyncMock(return_value=False)

        await entity.async_turn_off()

        entity.coordinator.async_reassert_segments.assert_not_awaited()


class TestMainLightTurnOn:
    """Turning on must always land on a non-black colour."""

    @pytest.mark.asyncio
    async def test_turn_on_from_black_restores_last_color_temp(self):
        entity = _make_main_light_entity(color=(0, 0, 0), color_temp_kelvin=None)
        entity._last_on_kelvin = 3000

        await entity.async_turn_on()

        cmds = [c[0][1] for c in entity.coordinator.async_control_device.call_args_list]
        ct = [c for c in cmds if isinstance(c, ColorTempCommand)]
        assert len(ct) == 1
        assert ct[0].kelvin == 3000

    @pytest.mark.asyncio
    async def test_turn_on_from_black_falls_back_to_default_kelvin(self):
        """With nothing remembered it must still emit a non-black colour,
        otherwise the panel would stay dark and read back as off."""
        from custom_components.govee.light import MAIN_LIGHT_ON_KELVIN

        entity = _make_main_light_entity(color=(0, 0, 0), color_temp_kelvin=None)

        await entity.async_turn_on()

        cmds = [c[0][1] for c in entity.coordinator.async_control_device.call_args_list]
        ct = [c for c in cmds if isinstance(c, ColorTempCommand)]
        assert len(ct) == 1
        assert ct[0].kelvin == MAIN_LIGHT_ON_KELVIN

    @pytest.mark.asyncio
    async def test_turn_on_with_explicit_color_temp(self):
        entity = _make_main_light_entity(color=(0, 0, 0))

        await entity.async_turn_on(color_temp_kelvin=5000)

        cmds = [c[0][1] for c in entity.coordinator.async_control_device.call_args_list]
        ct = [c for c in cmds if isinstance(c, ColorTempCommand)]
        assert ct[0].kelvin == 5000
        assert entity._last_on_kelvin == 5000

    @pytest.mark.asyncio
    async def test_turn_on_with_explicit_rgb(self):
        entity = _make_main_light_entity(color=(0, 0, 0))

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        cmds = [c[0][1] for c in entity.coordinator.async_control_device.call_args_list]
        col = [c for c in cmds if isinstance(c, ColorCommand)]
        assert col[0].color == RGBColor(r=255, g=0, b=0)
        assert entity._last_on_rgb == (255, 0, 0)

    @pytest.mark.asyncio
    async def test_turn_on_with_black_rgb_delegates_to_turn_off(self):
        """Asking for black is asking for off — otherwise the panel would go
        dark while the entity still claimed to be on."""
        entity = _make_main_light_entity(color=(255, 0, 0))

        await entity.async_turn_on(rgb_color=(0, 0, 0))

        cmds = [c[0][1] for c in entity.coordinator.async_control_device.call_args_list]
        assert len(cmds) == 1
        assert isinstance(cmds[0], ColorCommand)
        assert cmds[0].color == RGBColor(r=0, g=0, b=0)

    @pytest.mark.asyncio
    async def test_turn_on_reasserts_segments(self):
        entity = _make_main_light_entity(color=(0, 0, 0))

        await entity.async_turn_on(color_temp_kelvin=4000)

        entity.coordinator.async_reassert_segments.assert_awaited_once_with(
            "AA:BB:CC:DD:EE:FF:00:11"
        )

    @pytest.mark.asyncio
    async def test_turn_on_when_already_on_does_not_force_a_colour(self):
        """Adjusting nothing on an already-lit panel shouldn't override the
        colour the user currently has set."""
        entity = _make_main_light_entity(color_temp_kelvin=4000)

        await entity.async_turn_on()

        cmds = [c[0][1] for c in entity.coordinator.async_control_device.call_args_list]
        assert cmds == []


class TestSegmentReassert:
    """The ring must survive a whole-device write (issue #131).

    A main-panel write clobbers the segment overlay, so the coordinator
    replays the ring's last known colours afterwards. That tracking is
    in-memory, which is what made this fail in practice: after a restart it
    was empty, so the first main on/off had nothing to replay and left the
    ring wiped. The segment entities now seed it as they restore.
    """

    def _coordinator(self, rate_limit_remaining: int = 100):
        from custom_components.govee.coordinator import GoveeCoordinator

        coord = GoveeCoordinator.__new__(GoveeCoordinator)
        coord._segment_colors = {}
        coord._pending_power_off = set()
        coord.async_control_device = AsyncMock(return_value=True)
        coord._api_client = MagicMock(rate_limit_remaining=rate_limit_remaining)
        return coord

    def test_record_seeds_tracking(self):
        coord = self._coordinator()
        coord.record_segment_color("dev", 3, (255, 0, 0))
        assert coord._segment_colors["dev"][3] == (255, 0, 0)

    @pytest.mark.asyncio
    async def test_reassert_is_noop_when_nothing_tracked(self):
        """Must not fire a stray command when there is nothing to restore."""
        coord = self._coordinator()
        await coord.async_reassert_segments("dev")
        coord.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reassert_replays_tracked_colours(self):
        coord = self._coordinator()
        for i in range(12):
            coord.record_segment_color("dev", i, (255, 0, 0))

        await coord.async_reassert_segments("dev")

        # A uniform ring should cost exactly one grouped call, not twelve.
        assert coord.async_control_device.await_count == 1
        cmd = coord.async_control_device.await_args[0][1]
        assert cmd.color == RGBColor(r=255, g=0, b=0)
        assert cmd.segment_indices == tuple(range(12))

    @pytest.mark.asyncio
    async def test_reassert_groups_by_colour(self):
        """Mixed colours are grouped, one call per distinct colour."""
        coord = self._coordinator()
        for i in range(6):
            coord.record_segment_color("dev", i, (255, 0, 0))
        for i in range(6, 12):
            coord.record_segment_color("dev", i, (0, 0, 255))

        await coord.async_reassert_segments("dev")

        assert coord.async_control_device.await_count == 2
        by_colour = {
            c[0][1].color.as_tuple: c[0][1].segment_indices
            for c in coord.async_control_device.await_args_list
        }
        assert by_colour[(255, 0, 0)] == tuple(range(6))
        assert by_colour[(0, 0, 255)] == tuple(range(6, 12))

    @pytest.mark.asyncio
    async def test_reassert_skipped_when_power_off_pending(self):
        """Guarded in the coordinator so every caller benefits, not just one."""
        coord = self._coordinator()
        for i in range(12):
            coord.record_segment_color("dev", i, (255, 0, 0))
        coord._pending_power_off.add("dev")

        await coord.async_reassert_segments("dev")

        coord.async_control_device.assert_not_awaited()


class TestReassertGuards:
    """The re-assert spends REST quota, so it must not fire needlessly.

    Segment commands ride neither the BLE nor the LAN dispatcher, so every
    call here goes to REST against a 100/min, 10,000/day budget.
    """

    def _coordinator(self, rate_limit_remaining: int = 100):
        from custom_components.govee.coordinator import GoveeCoordinator

        coord = GoveeCoordinator.__new__(GoveeCoordinator)
        coord._segment_colors = {}
        coord._pending_power_off = set()
        coord.async_control_device = AsyncMock(return_value=True)
        coord._api_client = MagicMock(rate_limit_remaining=rate_limit_remaining)
        return coord

    @pytest.mark.asyncio
    async def test_black_write_onto_black_ring_is_skipped(self):
        """The panel going dark leaves an already-dark ring dark anyway."""
        coord = self._coordinator()
        for i in range(12):
            coord.record_segment_color("dev", i, (0, 0, 0))

        await coord.async_reassert_segments("dev", wrote_black=True)

        coord.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_black_write_onto_a_lit_ring_still_replays(self):
        """A lit ring must survive the panel being switched off — that is #131."""
        coord = self._coordinator()
        for i in range(12):
            coord.record_segment_color("dev", i, (255, 0, 0))

        await coord.async_reassert_segments("dev", wrote_black=True)

        coord.async_control_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_colour_write_onto_black_ring_still_replays(self):
        """The unsafe skip: colour alone must never suppress the replay.

        A whole-device colour write leaves the ring showing the panel's
        colour, so a ring that should be dark has to be re-blacked. Skipping
        on "all tracked colours are black" without checking what was written
        would light the ring every time the panel came on.
        """
        coord = self._coordinator()
        for i in range(12):
            coord.record_segment_color("dev", i, (0, 0, 0))

        await coord.async_reassert_segments("dev")  # wrote_black defaults False

        coord.async_control_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skipped_when_the_api_budget_is_nearly_spent(self):
        """Better a stale ring than an exhausted quota for the whole account."""
        coord = self._coordinator(rate_limit_remaining=21)
        for i in range(12):
            coord.record_segment_color("dev", i, (i * 20, 0, 0))  # 12 distinct

        await coord.async_reassert_segments("dev")

        coord.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runs_when_the_budget_is_comfortable(self):
        coord = self._coordinator(rate_limit_remaining=100)
        for i in range(12):
            coord.record_segment_color("dev", i, (i * 20, 0, 0))

        await coord.async_reassert_segments("dev")

        assert coord.async_control_device.await_count == 12
