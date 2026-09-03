"""Tests for GoveeCoordinator.async_send_fan_oscillation (Tower Fan 2, PR #176).

The coordinator is the glue between the fan entity and the BLE passthrough
manager: it resolves the SKU and the harvested swing tail, applies the
optimistic state on success, notifies listeners, and mirrors the send into
the diagnostics command history.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeDeviceState
from custom_components.govee.models.commands import OscillationCommand
from custom_components.govee.transport_health import TransportHealthTracker

FAN_ID = "AA:BB:CC:DD:EE:FF:00:44"
TAIL = [0x01, 0x06, 0x03, 0x50]


def _make_coordinator(*, send_ok: bool = True, tail: list[int] | None = TAIL, mqtt: bool = True):
    coord = object.__new__(GoveeCoordinator)
    device = MagicMock()
    device.sku = "H7107"
    coord._devices = {FAN_ID: device}
    state = GoveeDeviceState.create_empty(FAN_ID)
    state.oscillating = not send_ok  # something the optimistic write would flip
    coord._states = {FAN_ID: state}
    coord._transport = TransportHealthTracker()
    coord._api_client = MagicMock()
    coord._ble_manager = MagicMock()
    coord._ble_manager.async_send_fan_oscillation = AsyncMock(return_value=send_ok)
    if mqtt:
        coord._mqtt_client = MagicMock()
        coord._mqtt_client.fan_swing_tail = MagicMock(return_value=tail)
    else:
        coord._mqtt_client = None
    coord.async_set_updated_data = MagicMock()
    return coord


class TestAsyncSendFanOscillation:
    @pytest.mark.asyncio
    async def test_unknown_device_returns_false_without_sending(self):
        coord = _make_coordinator()

        assert await coord.async_send_fan_oscillation("nope", True) is False
        coord._ble_manager.async_send_fan_oscillation.assert_not_awaited()
        coord.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_forwards_sku_and_tail_and_applies_optimistic_state(self):
        coord = _make_coordinator()

        assert await coord.async_send_fan_oscillation(FAN_ID, True) is True

        coord._ble_manager.async_send_fan_oscillation.assert_awaited_once_with(
            FAN_ID, "H7107", True, TAIL
        )
        state = coord._states[FAN_ID]
        assert state.oscillating is True
        assert state.source == "optimistic"
        coord.async_set_updated_data.assert_called_once_with(coord._states)
        health = coord._transport.get(FAN_ID, "mqtt")
        assert health is not None and health.last_send_ts is not None
        coord._api_client.record_local_command.assert_called_once()
        args, kwargs = coord._api_client.record_local_command.call_args
        assert args[:3] == (FAN_ID, "H7107", "mqtt")
        assert args[3] == OscillationCommand(oscillating=True).to_api_payload()
        assert kwargs["delivered"] is True
        assert "tail=[1, 6, 3, 80]" in kwargs["detail"]

    @pytest.mark.asyncio
    async def test_no_mqtt_client_sends_bare_frame(self):
        coord = _make_coordinator(mqtt=False)

        assert await coord.async_send_fan_oscillation(FAN_ID, False) is True
        coord._ble_manager.async_send_fan_oscillation.assert_awaited_once_with(
            FAN_ID, "H7107", False, None
        )

    @pytest.mark.asyncio
    async def test_declined_send_leaves_state_alone(self):
        coord = _make_coordinator(send_ok=False)

        assert await coord.async_send_fan_oscillation(FAN_ID, True) is False

        state = coord._states[FAN_ID]
        assert state.oscillating is True  # untouched (seeded as `not send_ok`)
        assert state.source == "api"
        coord.async_set_updated_data.assert_not_called()
        health = coord._transport.get(FAN_ID, "mqtt")
        assert health is None or health.last_send_ts is None
        kwargs = coord._api_client.record_local_command.call_args[1]
        assert kwargs["delivered"] is False
