"""Ceiling Light Pro (H1252) main panel / ring zones over ptReal — issue #223.

The Developer API accepts ``mainLightToggle`` / ``backgroundLightToggle`` on
the H1252 with HTTP 200 and then ignores them; the app switches each zone with
``33 30 <zone> <on>`` and the device reports both as ``aa 30 <main> <ring>``.
Frames below are from a real H1252 (WiFi FW 1.01.31), toggled in the app and
via ``govee.send_raw_ptreal``, by holger-dunst. Ported by hand under the
security review's checklist: the ring mask is fixed to bits 0-15, Kelvin is
clamped in the frame builder, and only checksummed 20-byte ``aa 30`` frames
count.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.api.ble_packet import (
    LIGHT_ZONE_MAIN,
    LIGHT_ZONE_RING,
    build_light_zone_ptreal,
    build_packet,
    build_segment_color_temp_ptreal,
    encode_packet_base64,
)
from custom_components.govee.const import (
    PTREAL_LIGHT_ZONE_SKUS,
    PTREAL_MAIN_PANEL_BIT,
    PTREAL_RING_MASK,
    PTREAL_SEGMENT_SKUS,
    PTREAL_ZONE_MAX_KELVIN,
    PTREAL_ZONE_MIN_KELVIN,
)
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    RGBColor,
    ToggleCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_SEGMENT_COLOR,
    CAPABILITY_TOGGLE,
    INSTANCE_BACKGROUND_LIGHT_TOGGLE,
    INSTANCE_MAIN_LIGHT_TOGGLE,
    INSTANCE_POWER,
)
from custom_components.govee.transport_health import TransportHealthTracker

DEV = "AA:BB:CC:DD:EE:FF:12:52"

# Real H1252 status frames (20 bytes, XOR checksum last).
ZONES_BOTH = bytes.fromhex("aa3001010000000000000000000000000000009a")
ZONES_MAIN_OFF = bytes.fromhex("aa3000010000000000000000000000000000009b")
ZONES_RING_OFF = bytes.fromhex("aa3001000000000000000000000000000000009b")
SEGMENT_COLOURS = bytes.fromhex("aaa50164ffd5a164ffd5a164ffd5a164ffd5a10e")


def _device(sku: str = "H1252") -> GoveeDevice:
    return GoveeDevice(
        device_id=DEV,
        sku=sku,
        name="Ceiling",
        device_type="devices.types.light",
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(type=CAPABILITY_TOGGLE, instance=INSTANCE_MAIN_LIGHT_TOGGLE, parameters={}),
            GoveeCapability(type=CAPABILITY_TOGGLE, instance=INSTANCE_BACKGROUND_LIGHT_TOGGLE, parameters={}),
            # As the real H1252 advertises it: 16 ring segments, 0-15.
            GoveeCapability(
                type=CAPABILITY_SEGMENT_COLOR,
                instance="segmentedColorRgb",
                parameters={
                    "fields": [
                        {"fieldName": "segment", "size": {"min": 1, "max": 16}, "elementRange": {"min": 0, "max": 15}}
                    ]
                },
            ),
        ),
        is_group=False,
    )


class TestFrames:
    def test_zone_frames_match_the_verified_captures(self):
        assert build_light_zone_ptreal(LIGHT_ZONE_MAIN, False) == [0x33, 0x30, 0x00, 0x00]
        assert build_light_zone_ptreal(LIGHT_ZONE_MAIN, True) == [0x33, 0x30, 0x00, 0x01]
        assert build_light_zone_ptreal(LIGHT_ZONE_RING, True) == [0x33, 0x30, 0x01, 0x01]
        assert build_light_zone_ptreal(LIGHT_ZONE_RING, False) == [0x33, 0x30, 0x01, 0x00]

    def test_h1252_gets_main_panel_and_segment_ptreal(self):
        assert PTREAL_MAIN_PANEL_BIT["H1252"] == 16
        assert "H1252" in PTREAL_SEGMENT_SKUS
        # Only the H1252's REST zone toggles are inert; the H1232's work.
        assert PTREAL_LIGHT_ZONE_SKUS == frozenset({"H1252"})

    def test_unknown_zone_is_refused(self):
        with pytest.raises(ValueError):
            build_light_zone_ptreal(2, True)

    @pytest.mark.parametrize(
        ("kelvin", "expected"),
        [(1000, PTREAL_ZONE_MIN_KELVIN), (10000, PTREAL_ZONE_MAX_KELVIN), (4000, 4000)],
    )
    def test_kelvin_is_clamped_in_the_frame(self, kelvin, expected):
        frame = build_segment_color_temp_ptreal(kelvin, RGBColor(255, 255, 255), 1 << 16)
        assert (frame[7] << 8) | frame[8] == expected

    def test_ring_colour_temperature_matches_the_verified_capture(self):
        frame = build_segment_color_temp_ptreal(6500, RGBColor(0xFF, 0xFF, 0xFF), PTREAL_RING_MASK)
        assert bytes(frame).hex(" ") == "33 05 15 01 ff ff ff 19 64 ff ff ff ff ff 00 00"


class TestStateFromFrames:
    @pytest.mark.parametrize(
        ("frame", "main", "ring"),
        [(ZONES_BOTH, True, True), (ZONES_MAIN_OFF, False, True), (ZONES_RING_OFF, True, False)],
    )
    def test_zone_frame_sets_both_toggles(self, frame, main, ring):
        state = GoveeDeviceState.create_empty(DEV)

        assert state.update_light_zones_from_frames([SEGMENT_COLOURS, frame]) is True

        assert state.toggles[INSTANCE_MAIN_LIGHT_TOGGLE] is main
        assert state.toggles[INSTANCE_BACKGROUND_LIGHT_TOGGLE] is ring

    def test_unrelated_or_short_frames_are_ignored(self):
        state = GoveeDeviceState.create_empty(DEV)

        assert state.update_light_zones_from_frames([SEGMENT_COLOURS, bytes([0xAA, 0x30, 0x01]), b""]) is False
        assert state.toggles == {}

    def test_a_frame_with_a_bad_checksum_or_length_is_ignored(self):
        state = GoveeDeviceState.create_empty(DEV)
        bad_checksum = ZONES_BOTH[:19] + bytes([ZONES_BOTH[19] ^ 0xFF])

        assert state.update_light_zones_from_frames([bad_checksum, ZONES_BOTH[:19], ZONES_BOTH + b"\x00"]) is False
        assert state.toggles == {}


def _push_coordinator(sku: str) -> GoveeCoordinator:
    coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
    coordinator._devices = {DEV: _device(sku)}
    coordinator._states = {DEV: GoveeDeviceState.create_empty(DEV)}
    coordinator._transport = TransportHealthTracker()
    coordinator.async_set_updated_data = MagicMock()
    return coordinator


class TestCoordinatorPush:
    def test_h1252_push_decodes_zone_frame(self):
        coordinator = _push_coordinator("H1252")

        coordinator._on_mqtt_state_update(DEV, {"onOff": 1, "_op_frames": [ZONES_MAIN_OFF.hex()]})

        state = coordinator._states[DEV]
        assert state.power_state is True
        assert state.toggles[INSTANCE_MAIN_LIGHT_TOGGLE] is False
        assert state.toggles[INSTANCE_BACKGROUND_LIGHT_TOGGLE] is True

    def test_other_sku_ignores_zone_frame(self):
        coordinator = _push_coordinator("H1232")

        coordinator._on_mqtt_state_update(DEV, {"onOff": 1, "_op_frames": [ZONES_MAIN_OFF.hex()]})

        assert INSTANCE_MAIN_LIGHT_TOGGLE not in coordinator._states[DEV].toggles


def _control_coordinator(sku: str, *, available: bool = True, sent: bool = True) -> GoveeCoordinator:
    entry = MagicMock()
    entry.entry_id = "zones"
    entry.title = "Govee"
    entry.options = {}
    entry.data = {}
    coordinator = GoveeCoordinator(
        hass=MagicMock(),
        config_entry=entry,
        api_client=MagicMock(),
        iot_credentials=None,
        poll_interval=60,
    )
    coordinator._api_client.control_device = AsyncMock(return_value=True)
    coordinator._api_client.rate_limit_remaining = 100
    coordinator.async_set_updated_data = MagicMock()
    coordinator.async_update_listeners = MagicMock()
    coordinator._devices[DEV] = _device(sku)
    coordinator._states[DEV] = GoveeDeviceState.create_empty(DEV)
    ble = MagicMock()
    ble.available = available
    ble.async_send_ble_packet = AsyncMock(return_value=sent)
    coordinator._ble_manager = ble
    return coordinator


class TestControl:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("instance", "enabled", "zone"),
        [
            (INSTANCE_MAIN_LIGHT_TOGGLE, False, LIGHT_ZONE_MAIN),
            (INSTANCE_MAIN_LIGHT_TOGGLE, True, LIGHT_ZONE_MAIN),
            (INSTANCE_BACKGROUND_LIGHT_TOGGLE, True, LIGHT_ZONE_RING),
            (INSTANCE_BACKGROUND_LIGHT_TOGGLE, False, LIGHT_ZONE_RING),
        ],
    )
    async def test_h1252_toggle_goes_out_as_zone_frame(self, instance, enabled, zone):
        coordinator = _control_coordinator("H1252")
        command = ToggleCommand(toggle_instance=instance, enabled=enabled)

        assert await coordinator.async_control_device(DEV, command) is True

        expected = encode_packet_base64(build_packet(build_light_zone_ptreal(zone, enabled)))
        coordinator._ble_manager.async_send_ble_packet.assert_awaited_once_with(DEV, "H1252", expected)
        coordinator._api_client.control_device.assert_not_awaited()
        assert coordinator._states[DEV].toggles[instance] is enabled

    @pytest.mark.asyncio
    async def test_passthrough_down_falls_back_to_rest(self):
        coordinator = _control_coordinator("H1252", available=False)
        command = ToggleCommand(toggle_instance=INSTANCE_MAIN_LIGHT_TOGGLE, enabled=False)

        assert await coordinator.async_control_device(DEV, command) is True

        coordinator._ble_manager.async_send_ble_packet.assert_not_awaited()
        coordinator._api_client.control_device.assert_awaited_once_with(DEV, "H1252", command)

    @pytest.mark.asyncio
    async def test_send_failure_falls_back_to_rest(self):
        coordinator = _control_coordinator("H1252", sent=False)
        command = ToggleCommand(toggle_instance=INSTANCE_BACKGROUND_LIGHT_TOGGLE, enabled=True)

        assert await coordinator.async_control_device(DEV, command) is True

        coordinator._api_client.control_device.assert_awaited_once_with(DEV, "H1252", command)

    @pytest.mark.asyncio
    async def test_other_sku_keeps_rest_toggle(self):
        coordinator = _control_coordinator("H1232")
        command = ToggleCommand(toggle_instance=INSTANCE_MAIN_LIGHT_TOGGLE, enabled=False)

        assert await coordinator.async_control_device(DEV, command) is True

        coordinator._ble_manager.async_send_ble_packet.assert_not_awaited()
        coordinator._api_client.control_device.assert_awaited_once_with(DEV, "H1232", command)


class TestZoneColourTemperature:
    def test_colour_temperature_frame_matches_the_verified_capture(self):
        # Sent to a real H1252: main panel 2700 K, colour left at full white.
        frame = build_segment_color_temp_ptreal(2700, RGBColor(0xFF, 0xA6, 0x57), 1 << 16)
        assert bytes(frame).hex(" ") == "33 05 15 01 ff ff ff 0a 8c ff a6 57 00 00 01 00"

    @pytest.mark.asyncio
    async def test_main_panel_kelvin_sends_one_masked_frame(self):
        coordinator = _control_coordinator("H1252")

        assert await coordinator.async_set_main_panel(DEV, kelvin=6500) is True

        sent = coordinator._ble_manager.async_send_ble_packet.await_args_list
        assert len(sent) == 1
        packet = base64.b64decode(sent[0].args[2])
        assert packet[:9].hex() == "33051501ffffff1964"
        assert packet[12:15] == bytes([0x00, 0x00, 0x01])

    @pytest.mark.asyncio
    async def test_ring_covers_all_sixteen_segments(self):
        coordinator = _control_coordinator("H1252")

        assert await coordinator.async_set_ring(DEV, rgb=RGBColor(0, 0, 255), brightness=40) is True

        sent = [
            base64.b64decode(call.args[2]) for call in coordinator._ble_manager.async_send_ble_packet.await_args_list
        ]
        assert sent[0][:15].hex() == "3305150100 00ff0000000000ffff00".replace(" ", "")
        assert sent[1][:8].hex() == "3305150228ffff00"

    @pytest.mark.asyncio
    async def test_ring_write_never_sets_panel_bit(self):
        """Even if the API ever advertised 17 ring segments, the mask stays bits 0-15."""
        coordinator = _control_coordinator("H1252")

        assert await coordinator.async_set_ring(DEV, rgb=RGBColor(1, 2, 3), brightness=50, kelvin=3000) is True

        for call in coordinator._ble_manager.async_send_ble_packet.await_args_list:
            packet = base64.b64decode(call.args[2])
            mask_at = 12 if packet[3] == 0x01 else 5
            mask = int.from_bytes(packet[mask_at : mask_at + 3], "little")
            assert mask == PTREAL_RING_MASK
            assert not mask & (1 << 16)

    @pytest.mark.asyncio
    async def test_panel_write_never_sets_ring_bits(self):
        coordinator = _control_coordinator("H1252")

        assert await coordinator.async_set_main_panel(DEV, rgb=RGBColor(1, 2, 3), brightness=50, kelvin=3000) is True

        for call in coordinator._ble_manager.async_send_ble_packet.await_args_list:
            packet = base64.b64decode(call.args[2])
            mask_at = 12 if packet[3] == 0x01 else 5
            assert int.from_bytes(packet[mask_at : mask_at + 3], "little") == 1 << 16

    @pytest.mark.asyncio
    async def test_panel_kelvin_outside_the_range_is_clamped(self):
        coordinator = _control_coordinator("H1252")

        assert await coordinator.async_set_main_panel(DEV, kelvin=20000) is True

        packet = base64.b64decode(coordinator._ble_manager.async_send_ble_packet.await_args.args[2])
        assert (packet[7] << 8) | packet[8] == PTREAL_ZONE_MAX_KELVIN

    @pytest.mark.asyncio
    async def test_main_panel_rejected_for_an_unknown_sku(self):
        assert await _control_coordinator("H6008").async_set_main_panel(DEV, brightness=10) is False

    @pytest.mark.asyncio
    async def test_ring_rejected_for_other_skus_and_without_passthrough(self):
        assert await _control_coordinator("H1232").async_set_ring(DEV, brightness=10) is False
        assert await _control_coordinator("H1252", available=False).async_set_ring(DEV, brightness=10) is False

    @pytest.mark.asyncio
    async def test_send_failure_stops_and_reports(self):
        coordinator = _control_coordinator("H1252", sent=False)

        assert await coordinator.async_set_ring(DEV, rgb=RGBColor(1, 2, 3), brightness=10) is False
        coordinator._ble_manager.async_send_ble_packet.assert_awaited_once()
