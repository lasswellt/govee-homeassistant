"""H60B0 ripple flow speed: ``33 05 2c 01 03 <0-100>`` over ptReal.

The Platform API has no capability for the ripple lens speed, so a "Ripple
flow" number sends the app's ptReal frame. It was verified on an H60B0 over
LAN (2026-11): 10 slowed the ripple and 0 stopped it. Govee never reports
the speed, so the number restores the last value set.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.number import NumberExtraStoredData
from homeassistant.exceptions import HomeAssistantError

from custom_components.govee import number as number_mod
from custom_components.govee.api.ble_packet import build_packet, build_ripple_flow_ptreal
from custom_components.govee.models import GoveeCapability, GoveeDevice
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_TOGGLE,
    DEVICE_TYPE_LIGHT,
    INSTANCE_POWER,
)
from tests.test_cov_coordinator_control import _add, _coordinator

ROOT = Path(__file__).resolve().parent.parent / "custom_components/govee"
DEV = "AA:BB:CC:DD:EE:FF:60:B0"
H60B0_TOGGLES = ("bottomLightToggle", "rippleLightToggle", "sideLightToggle")


def _device(sku: str = "H60B0", toggles: tuple[str, ...] = H60B0_TOGGLES, device_id: str = DEV) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name="Floor lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            *(GoveeCapability(type=CAPABILITY_TOGGLE, instance=t, parameters={}) for t in toggles),
        ),
    )


class TestFrame:
    @pytest.mark.parametrize(("speed", "byte"), [(0, 0x00), (10, 0x0A), (100, 0x64), (-5, 0x00), (250, 0x64)])
    def test_frame_bytes(self, speed, byte):
        assert build_ripple_flow_ptreal(speed) == [0x33, 0x05, 0x2C, 0x01, 0x03, byte]

    def test_checksums_match_the_lamp_test(self):
        # The exact packets sent to the lamp on 2026-11-27.
        assert build_packet(build_ripple_flow_ptreal(10))[-1] == 0x12
        assert build_packet(build_ripple_flow_ptreal(0))[-1] == 0x18


class TestSupport:
    def test_h60b0_supports_it(self):
        assert _device().supports_ripple_flow is True

    def test_other_skus_do_not(self):
        assert (
            _device("H60B3", ("nebulaLightToggle", "sideLightToggle", "bottomLightToggle")).supports_ripple_flow
            is False
        )

    def test_needs_the_ripple_toggle(self):
        assert _device(toggles=("bottomLightToggle", "sideLightToggle")).supports_ripple_flow is False

    def test_groups_do_not(self):
        from dataclasses import replace

        assert replace(_device(), is_group=True).supports_ripple_flow is False


class TestCoordinator:
    @pytest.mark.asyncio
    async def test_sends_the_frame_as_raw_ptreal(self):
        coord = _coordinator()
        _add(coord, _device())
        coord.async_send_raw_ptreal = AsyncMock(return_value=True)

        assert await coord.async_set_ripple_flow(DEV, 10) is True
        coord.async_send_raw_ptreal.assert_awaited_once_with(DEV, bytes([0x33, 0x05, 0x2C, 0x01, 0x03, 0x0A]))

    @pytest.mark.asyncio
    async def test_refuses_unsupported_or_unknown_devices(self):
        coord = _coordinator()
        _add(coord, _device("H60B3"))
        coord.async_send_raw_ptreal = AsyncMock(return_value=True)

        assert await coord.async_set_ripple_flow(DEV, 10) is False
        assert await coord.async_set_ripple_flow("nope", 10) is False
        coord.async_send_raw_ptreal.assert_not_awaited()


def _number(sent: bool = True) -> number_mod.GoveeRippleFlowNumber:
    coordinator = MagicMock()
    coordinator.async_set_ripple_flow = AsyncMock(return_value=sent)
    coordinator.get_state = MagicMock(return_value=None)
    entity = number_mod.GoveeRippleFlowNumber(coordinator, _device())
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity


class TestNumber:
    @pytest.mark.asyncio
    async def test_created_for_h60b0_only(self):
        lamp, other = _device(), _device("H60B3", device_id="AA:BB:CC:DD:EE:FF:60:B3")
        coordinator = MagicMock()
        coordinator.devices = {lamp.device_id: lamp, other.device_id: other}
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {}
        added: list = []
        await number_mod.async_setup_entry(MagicMock(), entry, added.extend)

        flows = [e for e in added if isinstance(e, number_mod.GoveeRippleFlowNumber)]
        assert [e._device_id for e in flows] == [lamp.device_id]
        assert flows[0].unique_id == f"{DEV}_ripple_flow"
        assert (flows[0].native_min_value, flows[0].native_max_value, flows[0].native_step) == (0, 100, 1)

    @pytest.mark.asyncio
    async def test_set_sends_and_keeps_the_value(self):
        entity = _number()
        await entity.async_set_native_value(42.4)

        entity.coordinator.async_set_ripple_flow.assert_awaited_once_with(DEV, 42)
        assert entity.native_value == 42
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_send_raises_and_keeps_the_old_value(self):
        entity = _number(sent=False)
        with pytest.raises(HomeAssistantError):
            await entity.async_set_native_value(50)
        assert entity.native_value is None
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("stored", "expected"), [(30.0, 30.0), (150.0, 100.0), (None, None)])
    async def test_restores_the_last_value(self, stored, expected):
        entity = _number()
        data = (
            None
            if stored is None
            else NumberExtraStoredData(
                native_max_value=100,
                native_min_value=0,
                native_step=1,
                native_unit_of_measurement=None,
                native_value=stored,
            )
        )
        entity.async_get_last_number_data = AsyncMock(return_value=data)
        await entity.async_added_to_hass()
        assert entity.native_value == expected


def test_name_and_icon_exist_for_every_language():
    icons = json.loads((ROOT / "icons.json").read_text())
    assert icons["entity"]["number"]["govee_ripple_flow"]["default"].startswith("mdi:")
    for path in [ROOT / "strings.json", *sorted((ROOT / "translations").glob("*.json"))]:
        strings = json.loads(path.read_text())
        assert strings["entity"]["number"]["govee_ripple_flow"]["name"], path.name
