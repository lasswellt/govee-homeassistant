"""H6104 reply decoding and received-state precedence, without BLE hardware."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.api.ble import GoveeBLEDevice, LEGACY_UART_CHARACTERISTIC_UUID
from custom_components.govee.api.ble_packet import build_packet
from custom_components.govee.api.ble_status import H6104Report, decode_h6104_status
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState, RGBColor
from custom_components.govee.models import BrightnessCommand
from custom_components.govee.number import GoveeMusicSensitivityNumber
from custom_components.govee.select import GoveeMusicModeSelectEntity
from homeassistant.util import dt as dt_util


@pytest.mark.parametrize(
    "data,report",
    [
        ([0xAA, 1, 1], H6104Report(power=True)),
        ([0xAA, 4, 127], H6104Report(brightness=127)),
        ([0xEE, 0x20, 254], H6104Report(brightness=254)),
        ([0xAA, 5, 0, 0, 1, 50], H6104Report(mode="tv")),
        ([0xAA, 5, 1, 3, 80], H6104Report(mode="music", music_effect=3, sensitivity=80)),
        ([0xAA, 5, 1, 99, 255], H6104Report(mode="music")),
        ([0xAA, 5, 2, 10, 20, 30], H6104Report(mode="rgb", rgb=(10, 20, 30))),
        ([0xAA, 5, 2, 255, 255, 255, 1, 250, 200, 100], H6104Report(mode="color_temp")),
        ([0xAA, 5, 4, 1], H6104Report(mode="scene")),
        ([0xAA, 5, 0x0A, 1], H6104Report(mode="diy")),
    ],
)
def test_known_reply_layouts(data, report):
    assert decode_h6104_status(build_packet(data)) == report


@pytest.mark.parametrize("data", [[0x33, 1, 0], [0x33, 5, 0], [0xAA, 7, 1], [0xEE, 0x11, 1], [0xAA, 5, 0x7F]])
def test_command_acks_and_unrelated_or_unknown_replies_are_not_state(data):
    assert decode_h6104_status(build_packet(data)) is None


def test_echo_ambiguous_off_requires_a_previously_validated_power_read():
    query = build_packet([0xAA, 1])
    assert decode_h6104_status(query, query=query) is None
    assert decode_h6104_status(query, query=query, power_read_validated=True) == H6104Report(power=False)
    for data in ([0xAA, 4], [0xAA, 5, 1]):
        query = build_packet(data)
        assert decode_h6104_status(query, query=query) is None


def test_bad_length_or_checksum_is_rejected():
    frame = build_packet([0xAA, 1, 1])
    assert decode_h6104_status(frame[:-1]) is None
    assert decode_h6104_status(frame + frame) is None
    assert decode_h6104_status(frame[:-1] + bytes([frame[-1] ^ 1])) is None


@pytest.mark.asyncio
async def test_queries_subscribe_receive_and_emit_real_reports():
    device = GoveeBLEDevice(MagicMock(), legacy_uart=True)
    client = MagicMock(is_connected=True)
    client.disconnect = AsyncMock()
    client.start_notify = AsyncMock()
    reports = []
    device.register_report_callback(reports.append)

    async def reply(uuid, query, *, response):
        assert uuid == LEGACY_UART_CHARACTERISTIC_UUID and response is False
        raw = {1: [0xAA, 1, 1], 4: [0xAA, 4, 127], 5: [0xAA, 5, 1, 3, 80]}[query[1]]
        callback = client.start_notify.call_args.args[1]
        callback(MagicMock(), bytearray(build_packet(raw)))

    client.write_gatt_char = AsyncMock(side_effect=reply)
    with (
        patch("custom_components.govee.api.ble.close_stale_connections_by_address", AsyncMock()),
        patch("custom_components.govee.api.ble.establish_connection", AsyncMock(return_value=client)),
    ):
        result = await device.async_read_status()
    assert (
        result
        == reports
        == [
            H6104Report(power=True),
            H6104Report(brightness=127),
            H6104Report(mode="music", music_effect=3, sensitivity=80),
        ]
    )
    assert client.write_gatt_char.call_args.args[1] == build_packet([0xAA, 5, 1])
    assert device.readback_diagnostics["power_read_validated"] is True
    assert device.readback_diagnostics["last_queries"] == {"aa01": "reply", "aa04": "reply", "aa05": "reply"}
    await device.stop()


@pytest.mark.asyncio
async def test_timeouts_and_echoes_leave_state_unknown_without_emitting():
    device = GoveeBLEDevice(MagicMock(), legacy_uart=True)
    client = MagicMock(is_connected=True)
    client.disconnect = AsyncMock()
    device._client = client
    callback = MagicMock()
    device.register_report_callback(callback)

    async def echo(_uuid, query, **_kwargs):
        device._on_uart_notification(client, bytearray(query))

    client.write_gatt_char = AsyncMock(side_effect=echo)
    with patch("custom_components.govee.api.ble._STATUS_REPLY_TIMEOUT", 0.001):
        assert await device.async_read_status() == []
    assert device.state.power is None
    assert device.readback_diagnostics["recent_status_frames"] == []
    assert len(device.readback_diagnostics["recent_status_candidates"]) == 3
    callback.assert_not_called()
    await device.stop()


def test_push_updates_and_late_reply_filtering():
    device = GoveeBLEDevice(MagicMock(), legacy_uart=True)
    client = MagicMock()
    device._client = client
    callback = MagicMock()
    device.register_report_callback(callback)
    device._on_uart_notification(client, bytearray(build_packet([0xAA, 1, 1])))  # no pending read
    device._on_uart_notification(MagicMock(), bytearray(build_packet([0xEE, 0x20, 100])))  # old link
    callback.assert_not_called()
    device._on_uart_notification(client, bytearray(build_packet([0xEE, 0x20, 100])))
    callback.assert_called_once_with(H6104Report(brightness=100))
    assert len(device.readback_diagnostics["recent_status_frames"]) == 1


@pytest.fixture
def tv_coord():
    entry = MagicMock(options={}, data={})
    api = MagicMock()
    coord = GoveeCoordinator(
        hass=MagicMock(), config_entry=entry, api_client=api, iot_credentials=None, poll_interval=60
    )
    brightness = GoveeCapability(
        type="devices.capabilities.range", instance="brightness", parameters={"range": {"min": 1, "max": 100}}
    )
    music = GoveeCapability(
        type="devices.capabilities.music_setting",
        instance="musicMode",
        parameters={
            "dataType": "STRUCT",
            "fields": [
                {
                    "fieldName": "musicMode",
                    "options": [
                        {"name": "Rhythm", "value": 3},
                        {"name": "Energic", "value": 5},
                        {"name": "Spectrum", "value": 6},
                        {"name": "Rolling", "value": 4},
                    ],
                }
            ],
        },
    )
    device = GoveeDevice(
        device_id="tv", sku="H6104", name="TV", device_type="devices.types.light", capabilities=(brightness, music)
    )
    coord._devices = {"tv": device}
    coord._states = {"tv": GoveeDeviceState.create_empty("tv")}
    coord.data = coord._states
    coord.async_update_listeners = MagicMock()
    coord._transport.record_success("tv", "ble")
    return coord, device, api


def test_device_report_sets_actual_mode_and_only_reported_fields(tv_coord):
    coord, _device, _api = tv_coord
    coord._states["tv"].brightness = 80
    coord._states["tv"].dreamview_enabled = True
    coord._apply_h6104_ble_report("tv", H6104Report(mode="music", music_effect=0, sensitivity=90))
    state = coord.get_state("tv")
    assert state.music_mode_enabled is True and state.dreamview_enabled is False
    assert state.music_mode_value == 5 and state.music_sensitivity == 90
    assert state.brightness == 80
    coord._apply_h6104_ble_report("tv", H6104Report(mode="rgb", rgb=(10, 20, 30)))
    assert coord.get_state("tv").music_mode_enabled is False
    assert coord.get_state("tv").color == RGBColor(10, 20, 30)
    coord.get_state("tv").color_temp_kelvin = 4000
    coord._apply_h6104_ble_report("tv", H6104Report(mode="color_temp"))
    assert coord.get_state("tv").color_temp_kelvin is None
    assert coord._transport.get("tv", "ble").last_read_ts is not None


@pytest.mark.asyncio
async def test_fresh_received_fields_override_stale_cloud_and_cloud_errors(tv_coord):
    coord, device, api = tv_coord
    client = MagicMock(supports_readback=True)

    async def read():
        coord._apply_h6104_ble_report("tv", H6104Report(power=True, brightness=127, mode="tv"))

    client.async_read_status = AsyncMock(side_effect=read)
    coord._ble_devices["tv"] = client
    cloud = GoveeDeviceState.create_empty("tv")
    cloud.power_state = False
    cloud.online = False
    cloud.brightness = 10
    api.get_device_state = AsyncMock(return_value=cloud)
    state = await coord._fetch_device_state("tv", device)
    assert state.power_state is True and state.online is True
    assert state.brightness == 50 and state.dreamview_enabled is True
    assert state.source == "ble"
    api.get_device_state.side_effect = RuntimeError("cloud offline")
    state = await coord._fetch_device_state("tv", device)
    assert isinstance(state, GoveeDeviceState) and state.power_state is True


@pytest.mark.parametrize("superseded", [False, True])
def test_expired_or_pre_command_reports_do_not_override_new_state(tv_coord, superseded):
    coord, _device, _api = tv_coord
    now = dt_util.utcnow()
    received = now - timedelta(seconds=1 if superseded else 121)
    coord._h6104_ble_fields["tv"] = {"power_state": (True, received)}
    if superseded:
        coord._h6104_last_write["tv"] = now
    state = GoveeDeviceState.create_empty("tv")
    state.power_state = False
    assert coord._merge_h6104_ble_fields("tv", state) is False
    assert state.power_state is False


def test_music_entities_follow_received_mode_and_sensitivity(tv_coord):
    coord, device, _api = tv_coord
    dropdown = GoveeMusicModeSelectEntity(coord, device, device.get_music_mode_options())
    number = GoveeMusicSensitivityNumber(coord, device)
    coord._apply_h6104_ble_report("tv", H6104Report(mode="music", music_effect=3, sensitivity=85))
    assert dropdown.current_option == "Rhythm"
    assert number.native_value == 85
    coord._apply_h6104_ble_report("tv", H6104Report(mode="tv"))
    assert dropdown.current_option is None


@pytest.mark.asyncio
async def test_missing_status_does_not_fabricate_reads_and_poll_queries_are_throttled(tv_coord):
    coord, _device, _api = tv_coord
    client = MagicMock(supports_readback=True)
    client.async_read_status = AsyncMock(return_value=[])
    coord._ble_devices["tv"] = client
    before = coord._transport.get("tv", "ble").last_success_ts
    await coord._async_refresh_h6104_ble_status("tv")
    await coord._async_refresh_h6104_ble_status("tv")
    assert client.async_read_status.await_count == 1
    await coord._async_refresh_h6104_ble_status("tv", force=True)
    assert client.async_read_status.await_count == 2
    assert coord._transport.get("tv", "ble").last_success_ts == before
    assert coord._transport.get("tv", "ble").last_read_ts is None


@pytest.mark.asyncio
async def test_command_readback_replaces_expected_brightness_with_received_value(tv_coord, monkeypatch):
    import custom_components.govee.coordinator as coord_module

    coord, _device, api = tv_coord
    coord.async_set_updated_data = MagicMock()
    api.control_device = AsyncMock()
    client = GoveeBLEDevice(MagicMock(), legacy_uart=True)
    write = AsyncMock()
    monkeypatch.setattr(client, "_write", write)

    async def actual_reply():
        coord._apply_h6104_ble_report("tv", H6104Report(brightness=100))

    monkeypatch.setattr(client, "async_read_status", actual_reply)
    monkeypatch.setattr(coord_module, "HAS_BLUETOOTH", True)
    coord._ble_devices["tv"] = client
    assert await coord.async_control_device("tv", BrightnessCommand(brightness=50)) is True
    assert write.call_args.args[0][2] == 127
    assert coord.get_state("tv").brightness == 39
    api.control_device.assert_not_awaited()


def test_newer_mqtt_report_does_not_get_overwritten_by_old_ble_cache(tv_coord):
    coord, _device, _api = tv_coord
    coord._apply_h6104_ble_report("tv", H6104Report(power=True, mode="tv"))
    coord._on_mqtt_state_update("tv", {"onOff": 0})
    assert "power_state" not in coord._h6104_ble_fields["tv"]
    assert "dreamview_enabled" in coord._h6104_ble_fields["tv"]
