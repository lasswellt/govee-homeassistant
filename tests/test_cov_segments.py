"""Coverage tests for the RGBIC segment platforms.

Exercises the parts of ``platforms/segment.py`` and
``platforms/grouped_segment.py`` the other suites leave alone: the
optimistic-state properties, ``async_turn_on``, the failure path (a rejected
command must raise), and ``async_added_to_hass`` — restoring the previous
state, seeding the coordinator's segment tracking (issue #131), and wiring the
grouped-entity dispatcher signal (SEGMENT_MODE_BOTH).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.govee.entity import GoveeEntity
from custom_components.govee.models import (
    GoveeDevice,
    GoveeDeviceState,
    RGBColor,
    SegmentBrightnessCommand,
    SegmentColorCommand,
)
from custom_components.govee.platforms.grouped_segment import (
    GoveeGroupedSegmentEntity,
    segments_optimistic_signal,
)
from custom_components.govee.platforms.segment import GoveeSegmentEntity
from .coordinator_mocks import follow_online_flag

DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:22"


def _coordinator(*, power_state: bool = True) -> MagicMock:
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.is_power_off_pending = MagicMock(return_value=False)
    coordinator.record_segment_color = MagicMock()
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    state.power_state = power_state
    coordinator.get_state = MagicMock(return_value=state)
    return follow_online_flag(coordinator)


def _hass() -> MagicMock:
    """A hass stand-in that the real dispatcher helpers can work against."""
    hass = MagicMock()
    hass.data = {}
    return hass


def _segment(mock_rgbic_device, index: int = 2, coordinator: MagicMock | None = None) -> GoveeSegmentEntity:
    entity = GoveeSegmentEntity(coordinator or _coordinator(), mock_rgbic_device, index)
    entity.hass = _hass()
    entity.async_write_ha_state = MagicMock()
    return entity


def _grouped(mock_rgbic_device, coordinator: MagicMock | None = None) -> GoveeGroupedSegmentEntity:
    entity = GoveeGroupedSegmentEntity(coordinator or _coordinator(), mock_rgbic_device)
    entity.hass = _hass()
    entity.async_write_ha_state = MagicMock()
    return entity


def _sent(entity) -> list:
    return [call.args[1] for call in entity.coordinator.async_control_device.await_args_list]


def _without_segment_brightness(device: GoveeDevice) -> GoveeDevice:
    caps = tuple(c for c in device.capabilities if c.instance != "segmentedBrightness")
    return GoveeDevice(
        device_id=device.device_id, sku=device.sku, name=device.name, device_type=device.device_type, capabilities=caps
    )


def _last_state(state: str, **attributes) -> State:
    return State("light.bedroom_led_strip_segment_3", state, attributes)


class TestSegmentEntityInit:
    """Construction through the real __init__ (the other suite bypasses it)."""

    def test_unique_id_and_placeholder_use_zero_and_one_based_indices(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device, index=2)
        assert entity.unique_id == f"{DEVICE_ID}_segment_2"
        # Users count segments from 1 on the strip.
        assert entity._attr_translation_placeholders == {"segment_index": "3"}

    def test_optimistic_defaults(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device)
        assert entity.is_on is True
        assert entity.brightness == 255
        assert entity.rgb_color == (255, 255, 255)

    def test_available_follows_coordinator_health_and_device_state(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device)
        assert entity.available is True
        # Govee reporting the device offline makes the segment unavailable too.
        entity.coordinator.get_state.return_value.online = False
        assert entity.available is False
        entity.coordinator.get_state.return_value.online = True
        entity.coordinator.last_update_success = False
        assert entity.available is False


class TestSegmentTurnOn:
    async def test_turn_on_sends_current_colour_for_this_segment(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device, index=4)

        await entity.async_turn_on()

        entity.coordinator.async_control_device.assert_awaited_once()
        device_id, cmd = entity.coordinator.async_control_device.await_args[0]
        assert device_id == DEVICE_ID
        assert isinstance(cmd, SegmentColorCommand)
        assert cmd.segment_indices == (4,)
        assert cmd.color == RGBColor(r=255, g=255, b=255)
        assert entity.is_on is True
        entity.async_write_ha_state.assert_called_once()

    async def test_turn_on_with_colour_and_brightness_updates_local_state(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device)

        await entity.async_turn_on(brightness=100, rgb_color=(10, 20, 30))

        colour, brightness = _sent(entity)
        assert colour == SegmentColorCommand(segment_indices=(2,), color=RGBColor(r=10, g=20, b=30))
        # HA 100 of 255 on the device's 0-100 segmentedBrightness scale.
        assert brightness == SegmentBrightnessCommand(segment_indices=(2,), brightness=39)
        assert entity.brightness == 100
        assert entity.rgb_color == (10, 20, 30)

    async def test_brightness_only_on_a_lit_segment_sends_only_brightness(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device, index=3)

        await entity.async_turn_on(brightness=51)

        assert _sent(entity) == [SegmentBrightnessCommand(segment_indices=(3,), brightness=20)]
        assert entity.brightness == 51

    async def test_brightness_on_an_off_segment_relights_it_first(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device, index=3)
        entity._is_on = False

        await entity.async_turn_on(brightness=255)

        assert _sent(entity) == [
            SegmentColorCommand(segment_indices=(3,), color=RGBColor(r=255, g=255, b=255)),
            SegmentBrightnessCommand(segment_indices=(3,), brightness=100),
        ]
        assert entity.is_on is True

    async def test_without_segment_brightness_capability_brightness_is_not_claimed(self, mock_rgbic_device):
        device = _without_segment_brightness(mock_rgbic_device)
        entity = _segment(device, index=1)

        await entity.async_turn_on(brightness=10)

        # Nothing could send it, so nothing is reported or stored.
        assert entity.brightness is None
        assert entity._brightness == 255
        assert _sent(entity) == [SegmentColorCommand(segment_indices=(1,), color=RGBColor(r=255, g=255, b=255))]

    async def test_turn_on_from_off_relights_with_remembered_colour(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device)
        entity._rgb_color = (0, 255, 0)
        entity._is_on = False

        await entity.async_turn_on()

        cmd = entity.coordinator.async_control_device.await_args[0][1]
        assert cmd.color == RGBColor(r=0, g=255, b=0)
        assert entity.is_on is True

    async def test_turn_on_raises_and_keeps_state_when_rejected(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device)
        entity._is_on = False
        entity.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on(rgb_color=(1, 2, 3))

        assert entity.is_on is False
        entity.async_write_ha_state.assert_not_called()

    async def test_rejected_turn_on_keeps_the_previous_colour_and_brightness(self, mock_rgbic_device):
        # The unsent values must not reach HA on the next coordinator update,
        # nor be persisted by RestoreEntity.
        entity = _segment(mock_rgbic_device)
        entity.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on(brightness=10, rgb_color=(1, 2, 3))

        assert entity.rgb_color == (255, 255, 255)
        assert entity.brightness == 255

    async def test_turn_off_raises_when_rejected(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device)
        entity.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_off()

        # The optimistic flip happens only after the write is accepted.
        assert entity.is_on is True
        entity.coordinator.record_segment_color.assert_not_called()


class TestSegmentAddedToHass:
    """Restore → seed coordinator tracking → subscribe to the group signal."""

    async def _add(self, entity: GoveeSegmentEntity, last_state: State | None) -> None:
        with (
            patch.object(GoveeEntity, "async_added_to_hass", new_callable=AsyncMock),
            patch.object(entity, "async_get_last_state", new_callable=AsyncMock, return_value=last_state),
        ):
            await entity.async_added_to_hass()

    async def test_restores_on_state_with_attributes(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device, index=2)

        await self._add(entity, _last_state("on", brightness=77, rgb_color=[12, 34, 56]))

        assert entity.is_on is True
        assert entity.brightness == 77
        assert entity.rgb_color == (12, 34, 56)
        # A lit segment seeds its real colour so a later whole-device write
        # can replay it (issue #131).
        entity.coordinator.record_segment_color.assert_called_once_with(DEVICE_ID, 2, (12, 34, 56))

    async def test_restores_off_state_and_seeds_black(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device, index=5)

        await self._add(entity, _last_state("off", rgb_color=[200, 0, 0]))

        assert entity.is_on is False
        # The colour is remembered for the next turn_on...
        assert entity.rgb_color == (200, 0, 0)
        # ...but an off segment is black as far as the ring replay goes.
        entity.coordinator.record_segment_color.assert_called_once_with(DEVICE_ID, 5, (0, 0, 0))

    async def test_missing_attributes_keep_defaults(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device)

        await self._add(entity, _last_state("on"))

        assert entity.brightness == 255
        assert entity.rgb_color == (255, 255, 255)

    async def test_no_previous_state_seeds_defaults(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device, index=0)

        await self._add(entity, None)

        assert entity.is_on is True
        entity.coordinator.record_segment_color.assert_called_once_with(DEVICE_ID, 0, (255, 255, 255))

    async def test_subscribes_to_group_signal_and_mirrors_it(self, mock_rgbic_device):
        """SEGMENT_MODE_BOTH: the grouped entity's write reaches this entity."""
        entity = _segment(mock_rgbic_device)

        await self._add(entity, None)

        async_dispatcher_send(entity.hass, segments_optimistic_signal(DEVICE_ID), False, 40, (1, 2, 3))

        assert entity.is_on is False
        assert entity.brightness == 40
        assert entity.rgb_color == (1, 2, 3)
        entity.async_write_ha_state.assert_called_once()

    async def test_signal_is_per_device(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device)

        await self._add(entity, None)

        async_dispatcher_send(entity.hass, segments_optimistic_signal("SOME:OTHER:DEVICE"), False, 1, (0, 0, 0))

        assert entity.is_on is True
        entity.async_write_ha_state.assert_not_called()

    async def test_subscription_is_removed_with_the_entity(self, mock_rgbic_device):
        entity = _segment(mock_rgbic_device)

        await self._add(entity, None)
        for remove in entity._on_remove:
            remove()
        async_dispatcher_send(entity.hass, segments_optimistic_signal(DEVICE_ID), False, 1, (0, 0, 0))

        assert entity.is_on is True


class TestGroupedSegmentEntityInit:
    def test_unique_id_and_indices(self, mock_rgbic_device):
        entity = _grouped(mock_rgbic_device)
        assert entity.unique_id == f"{DEVICE_ID}_grouped_segments"
        assert entity._segment_indices == tuple(range(mock_rgbic_device.segment_count))
        assert entity.available is True

    async def test_turn_on_raises_when_rejected(self, mock_rgbic_device):
        entity = _grouped(mock_rgbic_device)
        entity._is_on = False
        entity.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on()

        assert entity.is_on is False
        entity.async_write_ha_state.assert_not_called()

    async def test_brightness_goes_to_every_segment(self, mock_rgbic_device):
        entity = _grouped(mock_rgbic_device)

        await entity.async_turn_on(brightness=128)

        assert _sent(entity) == [
            SegmentBrightnessCommand(segment_indices=tuple(range(mock_rgbic_device.segment_count)), brightness=50)
        ]
        assert entity.brightness == 128

    async def test_without_segment_brightness_capability_brightness_is_not_claimed(self, mock_rgbic_device):
        entity = _grouped(_without_segment_brightness(mock_rgbic_device))

        await entity.async_turn_on(brightness=10)

        assert entity.brightness is None
        assert [type(c) for c in _sent(entity)] == [SegmentColorCommand]

    async def test_rejected_turn_on_keeps_the_previous_colour_and_brightness(self, mock_rgbic_device):
        entity = _grouped(mock_rgbic_device)
        entity.coordinator.async_control_device = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on(brightness=10, rgb_color=(1, 2, 3))

        assert entity.rgb_color == (255, 255, 255)
        assert entity.brightness == 255


class TestGroupedSegmentAddedToHass:
    async def _add(self, entity: GoveeGroupedSegmentEntity, last_state: State | None) -> None:
        with (
            patch.object(GoveeEntity, "async_added_to_hass", new_callable=AsyncMock),
            patch.object(entity, "async_get_last_state", new_callable=AsyncMock, return_value=last_state),
        ):
            await entity.async_added_to_hass()

    async def test_restores_on_state_and_seeds_every_segment(self, mock_rgbic_device):
        entity = _grouped(mock_rgbic_device)

        await self._add(entity, _last_state("on", brightness=90, rgb_color=[9, 8, 7]))

        assert entity.is_on is True
        assert entity.brightness == 90
        assert entity.rgb_color == (9, 8, 7)
        calls = entity.coordinator.record_segment_color.call_args_list
        assert [c[0] for c in calls] == [(DEVICE_ID, i, (9, 8, 7)) for i in range(mock_rgbic_device.segment_count)]

    async def test_restores_off_state_and_seeds_black(self, mock_rgbic_device):
        entity = _grouped(mock_rgbic_device)

        await self._add(entity, _last_state("off", brightness=90, rgb_color=[9, 8, 7]))

        assert entity.is_on is False
        assert entity.rgb_color == (9, 8, 7)
        seeded = {c[0][2] for c in entity.coordinator.record_segment_color.call_args_list}
        assert seeded == {(0, 0, 0)}

    async def test_no_previous_state_keeps_defaults(self, mock_rgbic_device):
        entity = _grouped(mock_rgbic_device)

        await self._add(entity, None)

        assert entity.is_on is True
        assert entity.brightness == 255
        assert entity.coordinator.record_segment_color.call_count == mock_rgbic_device.segment_count

    async def test_missing_attributes_keep_defaults(self, mock_rgbic_device):
        entity = _grouped(mock_rgbic_device)

        await self._add(entity, _last_state("on"))

        assert entity.brightness == 255
        assert entity.rgb_color == (255, 255, 255)
