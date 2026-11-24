"""H1A42 segment colours go over AWS IoT ptReal (issue #261)."""

from __future__ import annotations

import pytest

from custom_components.govee.api.ble_packet import build_packet, build_segment_color_ptreal, encode_packet_base64
from custom_components.govee.const import PTREAL_MAIN_PANEL_BIT, PTREAL_SEGMENT_SKUS, SKU_SEGMENT_OVERRIDES
from custom_components.govee.models import GoveeCapability, RGBColor, SegmentColorCommand
from custom_components.govee.models.device import CAPABILITY_SEGMENT_COLOR
from tests.test_cov_coordinator_control import DEV, _add, _ble_manager, _coordinator, _device, _POWER, _RGB

_SEGMENTS_15 = GoveeCapability(
    type=CAPABILITY_SEGMENT_COLOR,
    instance="segmentedColorRgb",
    parameters={"fields": [{"fieldName": "segment", "elementRange": {"min": 0, "max": 14}}]},
)
_RED = RGBColor(255, 0, 0)


def test_h1a42_is_ptreal_only_for_segments() -> None:
    assert "H1A42" in PTREAL_SEGMENT_SKUS
    assert "H1A42" not in PTREAL_MAIN_PANEL_BIT
    assert SKU_SEGMENT_OVERRIDES["H1A42"] == 5


def test_reporter_frames_match_builder() -> None:
    first = [0x33, 0x05, 0x15, 0x01, 0x00, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00]
    all_15 = first[:12] + [0xFF, 0x7F, 0x00]
    all_5 = first[:12] + [0x1F, 0x00, 0x00]
    assert build_segment_color_ptreal(RGBColor(0, 255, 0), 0x000001) == first
    assert build_segment_color_ptreal(RGBColor(0, 255, 0), 0x7FFF) == all_15
    assert build_segment_color_ptreal(RGBColor(0, 255, 0), 0x1F) == all_5


def test_segment_count_is_five() -> None:
    assert _device(sku="H1A42", caps=(_POWER, _RGB, _SEGMENTS_15)).segment_count == 5


@pytest.mark.asyncio
async def test_routes_over_ptreal_with_passthrough() -> None:
    coord = _coordinator()
    _add(coord, _device(sku="H1A42", caps=(_POWER, _RGB, _SEGMENTS_15)))
    coord._ble_manager = _ble_manager()
    command = SegmentColorCommand(segment_indices=tuple(range(5)), color=_RED)

    assert await coord.async_control_device(DEV, command) is True

    expected = encode_packet_base64(build_packet(build_segment_color_ptreal(_RED, 0x1F)))
    coord._ble_manager.async_send_ble_packet.assert_awaited_once_with(DEV, "H1A42", expected)
    coord._api_client.control_device.assert_not_awaited()


@pytest.mark.asyncio
async def test_falls_back_to_rest_without_passthrough() -> None:
    coord = _coordinator()
    _add(coord, _device(sku="H1A42", caps=(_POWER, _RGB, _SEGMENTS_15)))
    coord._ble_manager = _ble_manager(available=False)
    command = SegmentColorCommand(segment_indices=(0,), color=_RED)

    assert await coord.async_control_device(DEV, command) is True

    coord._ble_manager.async_send_ble_packet.assert_not_awaited()
    coord._api_client.control_device.assert_awaited_once_with(DEV, "H1A42", command)
