"""Coordinator control paths: transport tiers, mode commands and scene clearing.

Behavioural coverage for ``GoveeCoordinator.async_control_device`` and the
helpers around it — the BLE > LAN > MQTT > REST tiers, per-device segment
serialisation, the music-mode / DreamView / DIY-scene fallbacks from REST to
the BLE passthrough, optimistic state application, segment re-assertion and
``async_clear_scene``. Every transport is an in-process fake; no socket is
ever opened.
"""

from __future__ import annotations

import base64
import time
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

import custom_components.govee.coordinator as coord_mod
import custom_components.govee.ble_advertisement as ble_mod
from custom_components.govee.api.ble import BLE_COMMAND_SUPPORTED_MODELS, SEGMENTED_MODELS, GoveeBLEDevice
from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.ble_packet import (
    build_packet,
    build_segment_brightness_ptreal,
    build_segment_color_ptreal,
    encode_packet_base64,
)
from custom_components.govee.api.exceptions import GoveeApiError, GoveeAuthError
from custom_components.govee.api.lan_client import LanDevStatus, LanDeviceInfo
from custom_components.govee.const import CONF_ENABLE_H6104_BLE, CONF_ENABLE_MQTT_CONTROL
from custom_components.govee.ble_passthrough import BlePassthroughManager
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    DIYSceneCommand,
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    ModeCommand,
    MusicModeCommand,
    PowerCommand,
    RGBColor,
    SceneCommand,
    SegmentColorCommand,
    ToggleCommand,
)
from custom_components.govee.models.commands import TemperatureSettingCommand
from custom_components.govee.models.device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_MUSIC_MODE,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_COLOR_TEMP,
    INSTANCE_DREAMVIEW,
    INSTANCE_HDMI_SOURCE,
    INSTANCE_MUSIC_MODE,
    INSTANCE_POWER,
    INSTANCE_THERMOSTAT_TOGGLE,
)

DEV = "AA:BB:CC:DD:EE:FF:00:11"
IP = "10.0.0.5"


@pytest.fixture(autouse=True)
def _mock_h6104_status_queries(monkeypatch):
    """These routing tests mock writes; readback has dedicated reply tests."""
    monkeypatch.setattr(GoveeBLEDevice, "async_read_status", AsyncMock(return_value=[]))


CREDS = GoveeIotCredentials(
    token="tok",
    refresh_token="r",
    account_topic="GA/x",
    iot_cert="c",
    iot_key="k",
    iot_ca=None,
    client_id="cid",
    endpoint="ep",
)

_POWER = GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={})
_BRIGHTNESS = GoveeCapability(
    type=CAPABILITY_RANGE, instance=INSTANCE_BRIGHTNESS, parameters={"range": {"min": 0, "max": 100}}
)
_RGB = GoveeCapability(type=CAPABILITY_COLOR_SETTING, instance=INSTANCE_COLOR_RGB, parameters={})
_CT = GoveeCapability(
    type=CAPABILITY_COLOR_SETTING, instance=INSTANCE_COLOR_TEMP, parameters={"range": {"min": 2000, "max": 9000}}
)
_CT_NO_RANGE = GoveeCapability(type=CAPABILITY_COLOR_SETTING, instance=INSTANCE_COLOR_TEMP, parameters={})
_SEGMENTS = GoveeCapability(
    type=CAPABILITY_SEGMENT_COLOR,
    instance="segmentedColorRgb",
    parameters={"fields": [{"fieldName": "segment", "elementRange": {"min": 0, "max": 3}}]},
)
_MUSIC_STRUCT = GoveeCapability(
    type=CAPABILITY_MUSIC_MODE,
    instance=INSTANCE_MUSIC_MODE,
    parameters={
        "dataType": "STRUCT",
        "fields": [
            {"fieldName": "musicMode", "dataType": "ENUM", "options": [{"name": "Rhythm", "value": 3}]},
            {"fieldName": "sensitivity", "dataType": "INTEGER"},
        ],
    },
)


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _coordinator(*, iot: GoveeIotCredentials | None = None, options: dict[str, Any] | None = None) -> GoveeCoordinator:
    """A real coordinator over a mocked hass/entry, with HA notifications stubbed."""
    entry = MagicMock()
    entry.entry_id = "cov_entry"
    entry.title = "Govee"
    entry.options = options if options is not None else {}
    entry.data = {}
    coord = GoveeCoordinator(
        hass=MagicMock(),
        config_entry=entry,
        api_client=MagicMock(),
        iot_credentials=iot,
        poll_interval=60,
    )
    coord._api_client.control_device = AsyncMock(return_value=True)
    coord._api_client.rate_limit_remaining = 100
    coord.async_set_updated_data = MagicMock()
    coord.async_update_listeners = MagicMock()
    return coord


def _device(device_id: str = DEV, *, sku: str = "H6072", caps: tuple[GoveeCapability, ...] = ()) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name="Test light",
        device_type="devices.types.light",
        capabilities=caps or (_POWER, _BRIGHTNESS, _RGB, _CT),
    )


def _add(coord: GoveeCoordinator, device: GoveeDevice, state: GoveeDeviceState | None = None) -> GoveeDeviceState:
    coord._devices[device.device_id] = device
    state = state or GoveeDeviceState.create_empty(device.device_id)
    coord._states[device.device_id] = state
    coord._ensure_transport_health(device.device_id)
    return state


def _lan_ready(coord: GoveeCoordinator, client: Any) -> None:
    """Correlate DEV on the LAN with a healthy read history so the write gate is open."""
    coord._lan_client = client
    coord._lan_devices[DEV] = LanDeviceInfo(
        device_id=DEV, ip=IP, mac=DEV, sku="H6072", firmware="1.0.0", last_correlated_ts=time.monotonic()
    )
    coord._record_transport_success(DEV, "lan")


class _FakeWriteClient:
    """LAN client fake exposing only the write-tier surface."""

    def __init__(self, *, send_result: bool = True, read_reply: LanDevStatus | None = None) -> None:
        self.available = True
        self.send_result = send_result
        self.read_reply = read_reply
        self.send_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.read_calls: list[tuple[str, float]] = []

    async def async_send_command(self, ip: str, cmd: str, data: dict[str, Any]) -> bool:
        self.send_calls.append((ip, cmd, data))
        return self.send_result

    async def async_read_one(self, ip: str, timeout: float = 0.5) -> LanDevStatus | None:
        self.read_calls.append((ip, timeout))
        return self.read_reply


def _mqtt(*, publish_ok: bool = True) -> MagicMock:
    client = MagicMock()
    client.connected = True
    client.async_publish_command = AsyncMock(return_value=publish_ok)
    return client


def _ble_manager(*, available: bool = True, result: bool = True) -> MagicMock:
    manager = MagicMock()
    manager.available = available
    manager.async_send_music_mode = AsyncMock(return_value=result)
    manager.async_send_music_mode_v3 = AsyncMock(return_value=result)
    manager.async_send_dreamview = AsyncMock(return_value=result)
    manager.async_send_diy_scene = AsyncMock(return_value=result)
    manager.async_send_ble_packet = AsyncMock(return_value=result)
    return manager


def _sent(coord: GoveeCoordinator) -> list[Any]:
    """Commands that reached the REST control call, in order."""
    return [call.args[2] for call in coord._api_client.control_device.await_args_list]


# --------------------------------------------------------------------------- #
# async_control_device: tiers, segments, error mapping
# --------------------------------------------------------------------------- #


class TestControlDeviceTiers:
    @pytest.mark.asyncio
    async def test_unknown_device_is_refused_before_any_transport(self):
        coord = _coordinator()

        assert await coord.async_control_device("nope", PowerCommand(power_on=True)) is False

        coord._api_client.control_device.assert_not_awaited()
        coord.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_segment_command_is_remembered_and_serialised_per_device(self, monkeypatch):
        monkeypatch.setattr(coord_mod, "SEGMENT_COMMAND_PACING_SECONDS", 0)
        coord = _coordinator()
        _add(coord, _device(caps=(_POWER, _BRIGHTNESS, _RGB, _SEGMENTS)))
        command = SegmentColorCommand(segment_indices=(0, 2), color=RGBColor(255, 0, 0))

        assert await coord.async_control_device(DEV, command) is True

        assert coord._segment_colors[DEV] == {0: (255, 0, 0), 2: (255, 0, 0)}
        assert DEV in coord._segment_locks
        assert _sent(coord) == [command]
        health = coord._transport.get(DEV, "cloud_api")
        assert health.last_send_ts is not None and health.last_success_ts is not None
        coord.async_set_updated_data.assert_called()

    @pytest.mark.asyncio
    async def test_segment_rejection_records_a_failure(self, monkeypatch):
        monkeypatch.setattr(coord_mod, "SEGMENT_COMMAND_PACING_SECONDS", 0)
        coord = _coordinator()
        _add(coord, _device(caps=(_POWER, _RGB, _SEGMENTS)))
        coord._api_client.control_device = AsyncMock(return_value=False)
        command = SegmentColorCommand(segment_indices=(1,), color=RGBColor(0, 0, 255))

        assert await coord.async_control_device(DEV, command) is False

        assert coord._transport.get(DEV, "cloud_api").last_failure_reason == "segment_returned_false"

    @pytest.mark.asyncio
    async def test_segment_api_error_is_reported_as_failure(self, monkeypatch):
        monkeypatch.setattr(coord_mod, "SEGMENT_COMMAND_PACING_SECONDS", 0)
        coord = _coordinator()
        _add(coord, _device(caps=(_POWER, _RGB, _SEGMENTS)))
        coord._api_client.control_device = AsyncMock(side_effect=GoveeApiError("segment rejected", code=400))
        command = SegmentColorCommand(segment_indices=(1,), color=RGBColor(0, 0, 255))

        assert await coord.async_control_device(DEV, command) is False

        assert coord._transport.get(DEV, "cloud_api").last_failure_reason == "segment rejected"

    @pytest.mark.asyncio
    async def test_segment_auth_error_starts_reauth(self, monkeypatch):
        monkeypatch.setattr(coord_mod, "SEGMENT_COMMAND_PACING_SECONDS", 0)
        coord = _coordinator()
        _add(coord, _device(caps=(_POWER, _RGB, _SEGMENTS)))
        coord._api_client.control_device = AsyncMock(side_effect=GoveeAuthError("key revoked"))
        command = SegmentColorCommand(segment_indices=(1,), color=RGBColor(0, 0, 255))

        with pytest.raises(ConfigEntryAuthFailed):
            await coord.async_control_device(DEV, command)

        assert coord._transport.get(DEV, "cloud_api").last_failure_reason == "auth_failed"

    @pytest.mark.asyncio
    async def test_mqtt_tier_delivers_and_applies_optimistic_state(self):
        coord = _coordinator(options={CONF_ENABLE_MQTT_CONTROL: True})
        _add(coord, _device())
        coord._mqtt_client = _mqtt()
        coord._device_topics[DEV] = "GD/dev"
        coord._transport.record_success(DEV, "mqtt")

        assert await coord.async_control_device(DEV, PowerCommand(power_on=True)) is True

        coord._mqtt_client.async_publish_command.assert_awaited_once_with("GD/dev", "turn", {"val": 1}, cmd_version=0)
        coord._api_client.control_device.assert_not_awaited()
        state = coord._states[DEV]
        assert state.power_state is True
        assert state.source == "optimistic"
        assert coord._transport.get(DEV, "mqtt").last_send_ts is not None
        coord.async_set_updated_data.assert_called_once_with(coord._states)

    @pytest.mark.asyncio
    async def test_mqtt_colour_write_is_followed_by_the_legacy_frame(self):
        coord = _coordinator(options={CONF_ENABLE_MQTT_CONTROL: True})
        _add(coord, _device())
        coord._mqtt_client = _mqtt()
        coord._device_topics[DEV] = "GD/dev"
        coord._transport.record_success(DEV, "mqtt")

        assert await coord.async_control_device(DEV, ColorCommand(color=RGBColor(1, 2, 3))) is True

        publishes = [call.args for call in coord._mqtt_client.async_publish_command.await_args_list]
        assert publishes[0] == ("GD/dev", "colorwc", {"color": {"r": 1, "g": 2, "b": 3}, "colorTemInKelvin": 0})
        assert publishes[1] == ("GD/dev", "color", {"r": 1, "g": 2, "b": 3})
        assert coord._mqtt_client.async_publish_command.await_args_list[1].kwargs == {"cmd_version": 1}
        assert coord._states[DEV].color == RGBColor(1, 2, 3)

    @pytest.mark.asyncio
    async def test_mqtt_tier_waits_until_the_device_has_answered_on_aws_iot(self):
        # An H6163 never listens on AWS IoT; its unacknowledged publishes vanished (#198).
        coord = _coordinator(options={CONF_ENABLE_MQTT_CONTROL: True})
        _add(coord, _device())
        coord._mqtt_client = _mqtt()
        coord._device_topics[DEV] = "GD/dev"

        assert await coord.async_control_device(DEV, PowerCommand(power_on=True)) is True

        coord._mqtt_client.async_publish_command.assert_not_awaited()
        coord._api_client.control_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mqtt_tier_is_skipped_for_groups(self):
        coord = _coordinator(options={CONF_ENABLE_MQTT_CONTROL: True})
        group = GoveeDevice(
            device_id="11825917", sku="GROUP", name="All", device_type="devices.types.group", is_group=True
        )
        _add(coord, group)
        coord._mqtt_client = _mqtt()

        assert await coord.async_control_device("11825917", PowerCommand(power_on=True)) is True

        coord._mqtt_client.async_publish_command.assert_not_awaited()
        coord._api_client.control_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rest_rejection_records_a_failure(self):
        coord = _coordinator()
        _add(coord, _device())
        coord._api_client.control_device = AsyncMock(return_value=False)

        assert await coord.async_control_device(DEV, PowerCommand(power_on=True)) is False

        assert coord._transport.get(DEV, "cloud_api").last_failure_reason == "control_returned_false"
        assert coord._states[DEV].power_state is False
        coord.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_rest_auth_error_starts_reauth_and_clears_the_power_off_flag(self):
        coord = _coordinator()
        _add(coord, _device())
        coord._api_client.control_device = AsyncMock(side_effect=GoveeAuthError("key revoked"))

        with pytest.raises(ConfigEntryAuthFailed):
            await coord.async_control_device(DEV, PowerCommand(power_on=False))

        assert coord._transport.get(DEV, "cloud_api").last_failure_reason == "auth_failed"
        assert coord.is_power_off_pending(DEV) is False

    @pytest.mark.asyncio
    async def test_rest_api_error_is_reported_as_failure(self):
        coord = _coordinator()
        _add(coord, _device())
        coord._api_client.control_device = AsyncMock(side_effect=GoveeApiError("out of range", code=400))

        assert await coord.async_control_device(DEV, BrightnessCommand(brightness=50)) is False

        assert coord._transport.get(DEV, "cloud_api").last_failure_reason == "out of range"


# --------------------------------------------------------------------------- #
# LAN / MQTT / BLE tier edges
# --------------------------------------------------------------------------- #


class TestLanTierEdges:
    @pytest.mark.asyncio
    async def test_commands_without_a_lan_form_fall_through_untouched(self):
        coord = _coordinator()
        _add(coord, _device())
        client = _FakeWriteClient()
        _lan_ready(coord, client)

        assert (
            await coord._try_lan_command(DEV, coord._devices[DEV], SceneCommand(scene_id=7, scene_name="Sunset"))
            is False
        )

        assert client.send_calls == []

    @pytest.mark.asyncio
    async def test_write_only_override_treats_the_send_as_confirmation(self):
        coord = _coordinator()
        state = _add(coord, _device())
        client = _FakeWriteClient()
        coord._lan_client = client
        coord._lan_devices[DEV] = LanDeviceInfo(
            device_id=DEV, ip=IP, mac=DEV, sku="H6072", firmware="", last_correlated_ts=time.monotonic()
        )
        coord._lan_write_only = {DEV}
        # No LAN read has ever succeeded: the write-health gate would be shut.
        assert coord._transport.get(DEV, "lan").is_available is False

        assert await coord.async_control_device(DEV, PowerCommand(power_on=True)) is True

        assert client.send_calls == [(IP, "turn", {"value": 1})]
        assert client.read_calls == []
        assert state.power_state is True
        health = coord._transport.get(DEV, "lan")
        assert health.is_available is True and health.last_send_ts is not None
        coord._api_client.control_device.assert_not_awaited()
        kwargs = coord._api_client.record_local_command.call_args.kwargs
        assert kwargs["delivered"] is True
        assert "write-only" in kwargs["detail"]
        # A send is not a reading: it must not make the cloud poll skip this device.
        assert health.last_read_ts is None

    def test_readback_without_brightness_cannot_confirm(self):
        coord = _coordinator()
        device = _device()
        reply = LanDevStatus(on=True, brightness_0_100=None, color=None, color_temp_kelvin=None)

        assert coord._lan_write_confirmed(device, BrightnessCommand(brightness=40), reply) is False

    def test_non_lan_commands_are_never_confirmed(self):
        coord = _coordinator()
        reply = LanDevStatus(on=True, brightness_0_100=40, color=None, color_temp_kelvin=None)

        assert (
            coord._lan_write_confirmed(
                _device(), ToggleCommand(toggle_instance="nightlightToggle", enabled=True), reply
            )
            is False
        )


class TestH6104BleTier:
    """Route commands after a real H6104 enrollment; only the GATT write is mocked."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cloud_online", [False, True])
    @pytest.mark.parametrize("reason", ["stale_advertisement", "connection failed"])
    async def test_idle_reconnect_and_unsupported_cloud_toggle_guard(self, monkeypatch, cloud_online, reason):
        coord = _coordinator()
        state = _add(coord, _device(sku="H6104"))
        state.online = cloud_online
        ble = GoveeBLEDevice(MagicMock(), legacy_uart=True)
        ble._idle_disconnected = True
        coord._ble_devices[DEV] = ble
        coord._transport.record_failure(DEV, "ble", reason)
        monkeypatch.setattr(coord_mod, "HAS_BLUETOOTH", True)
        write = AsyncMock()
        monkeypatch.setattr(ble, "_write", write)
        if reason == "stale_advertisement":
            assert coord._ble_write_eligible(DEV) is True
            assert coord.device_reachable(DEV) is True
            assert await coord.async_send_dreamview(DEV, True) is True
            write.assert_awaited_once()
            assert state.dreamview_enabled is True
        else:
            assert coord._ble_write_eligible(DEV) is False
            with pytest.raises(HomeAssistantError, match="connection failed"):
                await coord.async_send_dreamview(DEV, True)
            write.assert_not_awaited()
        coord._api_client.control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unenrolled_tv_never_sends_unsupported_cloud_toggle(self):
        coord = _coordinator()
        _add(coord, _device(sku="H6104"))
        with pytest.raises(HomeAssistantError, match="Bluetooth"):
            await coord.async_send_dreamview(DEV, True)
        coord._api_client.control_device.assert_not_awaited()

    def test_reconnected_tv_remains_actionable_before_next_poll(self):
        coord = _coordinator()
        state = _add(coord, _device(sku="H6104"))
        state.online = False
        coord._ble_devices[DEV] = MagicMock(is_connected=True, idle_disconnected=False)
        coord._transport.record_failure(DEV, "ble", "stale_advertisement")
        assert coord.device_reachable(DEV) is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("api_mode,effect", [(5, 0), (6, 1), (4, 2), (3, 3)])
    async def test_music_cloud_ids_are_translated_to_legacy_ble_codes(self, monkeypatch, api_mode, effect):
        coord = _coordinator()
        state = _add(coord, _device(sku="H6104"))
        state.color = RGBColor(10, 20, 30)
        ble = GoveeBLEDevice(MagicMock(), legacy_uart=True)
        write = AsyncMock()
        monkeypatch.setattr(ble, "_write", write)
        coord._ble_devices[DEV] = ble
        assert await coord._try_ble_command(DEV, MusicModeCommand(music_mode=api_mode, sensitivity=80)) is True
        frame = write.call_args.args[0]
        assert frame[:5] == bytes([0x33, 5, 1, effect, 80])
        assert frame[5:8] == (bytes([10, 20, 30]) if effect in (1, 2) else bytes(3))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", [1, 3])
    async def test_music_on_and_off_use_ble_and_update_mode_switches(self, monkeypatch, mode):
        coord = _coordinator()
        state = _add(coord, _device(sku="H6104", caps=(_POWER, _RGB, _MUSIC_STRUCT)))
        state.dreamview_enabled = True
        state.last_color = RGBColor(10, 20, 30)
        ble = GoveeBLEDevice(MagicMock(), legacy_uart=True)
        write = AsyncMock()
        monkeypatch.setattr(ble, "_write", write)
        monkeypatch.setattr(coord_mod, "HAS_BLUETOOTH", True)
        coord._ble_devices[DEV] = ble
        coord._transport.record_success(DEV, "ble")
        assert await coord.async_send_music_mode(DEV, True, sensitivity=70, music_mode=mode) is True
        assert write.call_args.args[0][:5] == bytes.fromhex("3305010346")
        assert state.music_mode_enabled is True
        assert state.music_mode_name == "Rhythm"
        assert state.music_sensitivity == 70
        assert state.dreamview_enabled is False
        assert await coord.async_send_music_mode(DEV, False) is True
        assert write.call_args.args[0][:6] == bytes.fromhex("3305020a141e")
        assert state.music_mode_enabled is False
        coord._api_client.control_device.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "command,head",
        [
            (PowerCommand(power_on=True), bytes.fromhex("330101")),
            (PowerCommand(power_on=False), bytes.fromhex("330100")),
            (BrightnessCommand(brightness=50), bytes.fromhex("33047f")),
            (ColorCommand(color=RGBColor(r=255, g=0, b=64)), bytes.fromhex("330502ff0040")),
            (ColorTempCommand(kelvin=3000), None),
            (MusicModeCommand(music_mode=5, sensitivity=80), bytes.fromhex("3305010050")),
            (MusicModeCommand(music_mode=5, sensitivity=80, auto_color=0, rgb=0xFF0000), None),
            (ToggleCommand(toggle_instance=INSTANCE_DREAMVIEW, enabled=True), bytes.fromhex("330500000132")),
            (ToggleCommand(toggle_instance=INSTANCE_DREAMVIEW, enabled=False), bytes.fromhex("330502ffffff")),
        ],
    )
    @pytest.mark.parametrize("write_fails", [False, True])
    async def test_enroll_and_control(self, monkeypatch, command, head, write_fails):
        coord = _coordinator(options={CONF_ENABLE_H6104_BLE: True})
        _add(coord, _device(sku="H6104"))
        monkeypatch.setattr(ble_mod, "GoveeBLEDevice", GoveeBLEDevice, raising=False)
        monkeypatch.setattr(ble_mod, "SEGMENTED_MODELS", SEGMENTED_MODELS, raising=False)
        monkeypatch.setattr(ble_mod, "BLE_COMMAND_SUPPORTED_MODELS", BLE_COMMAND_SUPPORTED_MODELS, raising=False)
        bt = MagicMock()
        bt.async_scanner_count.return_value = 1
        monkeypatch.setattr(ble_mod, "bt_component", bt, raising=False)
        monkeypatch.setattr(coord_mod, "HAS_BLUETOOTH", True)
        info = MagicMock()
        info.name = "ihoment_H6104_ABCD"
        info.address = "AA:BB:CC:DD:EE:FF"
        ble_mod.BleAdvertisementHandler(coord).handle_advertisement(info)
        write = AsyncMock(side_effect=TimeoutError("gatt") if write_fails else None)
        monkeypatch.setattr(coord._ble_devices[DEV], "_write", write)

        if isinstance(command, ToggleCommand):
            if write_fails:
                with pytest.raises(HomeAssistantError, match="requires Bluetooth"):
                    await coord.async_send_dreamview(DEV, command.enabled)
                coord._api_client.control_device.assert_not_awaited()
                return
            assert await coord.async_send_dreamview(DEV, command.enabled) is True
        else:
            assert await coord.async_control_device(DEV, command) is True

        if head is not None:
            write.assert_awaited_once()
            assert write.call_args.args[0].startswith(head)
        else:
            write.assert_not_awaited()
        if head is None or write_fails:
            coord._api_client.control_device.assert_awaited_once()
        else:
            coord._api_client.control_device.assert_not_awaited()


class TestBleTier:
    @pytest.mark.asyncio
    async def test_successful_ble_write_restores_a_cloud_offline_device(self):
        coord = _coordinator()
        state = _add(coord, _device())
        state.online = False
        ble = MagicMock()
        ble.turn_on = AsyncMock()
        coord._ble_devices[DEV] = ble
        coord._transport.record_success(DEV, "ble")

        assert await coord.async_control_device(DEV, PowerCommand(power_on=True)) is True

        ble.turn_on.assert_awaited_once()
        assert state.online is True
        assert state.power_state is True
        # A BLE command is not a reading of the device's state.
        assert coord._transport.get(DEV, "ble").last_read_ts is None
        assert coord._transport.get(DEV, "ble").is_available is True
        coord._api_client.control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ble_failure_falls_through_to_rest(self):
        coord = _coordinator()
        _add(coord, _device())
        ble = MagicMock()
        ble.set_brightness = AsyncMock(side_effect=TimeoutError("gatt"))
        coord._ble_devices[DEV] = ble
        coord._transport.record_success(DEV, "ble")

        assert await coord.async_control_device(DEV, BrightnessCommand(brightness=30)) is True

        assert coord._transport.get(DEV, "ble").last_failure_reason == "gatt"
        coord._api_client.control_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stale_ble_is_skipped_for_the_next_tier(self):
        coord = _coordinator()
        _add(coord, _device())
        ble = MagicMock()
        ble.turn_on = AsyncMock()
        coord._ble_devices[DEV] = ble
        coord._transport.record_success(DEV, "ble")
        health = coord._transport.get(DEV, "ble")
        health.is_available = False
        health.last_failure_reason = "stale_advertisement"

        assert await coord.async_control_device(DEV, PowerCommand(power_on=True)) is True

        ble.turn_on.assert_not_awaited()
        coord._api_client.control_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ble_with_no_health_stamp_is_skipped(self):
        coord = _coordinator()
        _add(coord, _device())
        ble = MagicMock()
        ble.turn_on = AsyncMock()
        coord._ble_devices[DEV] = ble

        assert await coord.async_control_device(DEV, PowerCommand(power_on=True)) is True

        ble.turn_on.assert_not_awaited()
        coord._api_client.control_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ble_write_is_recorded_in_recent_commands(self):
        coord = _coordinator()
        _add(coord, _device())
        ble = MagicMock()
        ble.turn_on = AsyncMock()
        coord._ble_devices[DEV] = ble
        coord._transport.record_success(DEV, "ble")
        command = PowerCommand(power_on=True)

        assert await coord.async_control_device(DEV, command) is True

        coord._api_client.record_local_command.assert_called_with(
            DEV, "H6072", "ble", command.to_api_payload(), delivered=True, detail=None
        )

    @pytest.mark.asyncio
    async def test_ble_write_stamps_send_not_receive(self):
        coord = _coordinator()
        _add(coord, _device())
        ble = MagicMock()
        ble.turn_on = AsyncMock()
        coord._ble_devices[DEV] = ble
        coord._transport.record_success(DEV, "ble")
        health = coord._transport.get(DEV, "ble")
        original_success_ts = health.last_success_ts

        assert await coord.async_control_device(DEV, PowerCommand(power_on=True)) is True

        assert health.last_send_ts is not None
        assert health.last_success_ts == original_success_ts

    @pytest.mark.asyncio
    async def test_ble_failure_is_recorded_in_recent_commands(self):
        coord = _coordinator()
        _add(coord, _device())
        ble = MagicMock()
        ble.set_brightness = AsyncMock(side_effect=TimeoutError("gatt"))
        coord._ble_devices[DEV] = ble
        coord._transport.record_success(DEV, "ble")
        command = BrightnessCommand(brightness=30)

        assert await coord.async_control_device(DEV, command) is True

        kwargs = coord._api_client.record_local_command.call_args_list[0].kwargs
        assert coord._api_client.record_local_command.call_args_list[0].args == (
            DEV,
            "H6072",
            "ble",
            command.to_api_payload(),
        )
        assert kwargs["delivered"] is False
        assert kwargs["detail"] == "gatt"


class TestEnsureDeviceTopic:
    @pytest.mark.asyncio
    async def test_cached_topic_needs_no_refresh(self):
        coord = _coordinator(iot=CREDS)
        coord._device_topics[DEV] = "GD/dev"
        coord._fetch_device_topics = AsyncMock()

        assert await coord._ensure_device_topic(DEV) == "GD/dev"
        coord._fetch_device_topics.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_topic_is_refreshed_once_from_the_account_api(self):
        coord = _coordinator(iot=CREDS)

        async def _refresh() -> None:
            coord._device_topics[DEV] = "GD/fresh"

        coord._fetch_device_topics = AsyncMock(side_effect=_refresh)

        assert await coord._ensure_device_topic(DEV) == "GD/fresh"
        coord._fetch_device_topics.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_that_yields_nothing_returns_none(self):
        coord = _coordinator(iot=CREDS)
        coord._fetch_device_topics = AsyncMock()

        assert await coord._ensure_device_topic(DEV) is None

    @pytest.mark.asyncio
    async def test_missing_topic_refetch_is_throttled_per_device(self):
        """A device absent from the topic list does not cost two account calls per send."""
        coord = _coordinator(iot=CREDS)
        coord._fetch_device_topics = AsyncMock()

        for _ in range(5):
            assert await coord._ensure_device_topic(DEV) is None
        coord._fetch_device_topics.assert_awaited_once()

        # Another device is not held back by the first one's throttle.
        assert await coord._ensure_device_topic("AA:BB:CC:DD:EE:FF:00:99") is None
        assert coord._fetch_device_topics.await_count == 2

        # Once the window has passed, the missing device is asked about again.
        from custom_components.govee.coordinator import TOPIC_REFRESH_MIN_INTERVAL

        coord._topic_refresh_at[DEV] -= TOPIC_REFRESH_MIN_INTERVAL + 1
        assert await coord._ensure_device_topic(DEV) is None
        assert coord._fetch_device_topics.await_count == 3

    @pytest.mark.asyncio
    async def test_no_credentials_means_no_refresh(self):
        coord = _coordinator(iot=None)
        coord._fetch_device_topics = AsyncMock()

        assert await coord._ensure_device_topic(DEV) is None
        coord._fetch_device_topics.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Music mode
# --------------------------------------------------------------------------- #


class TestMusicMode:
    def _struct_device(self) -> GoveeDevice:
        return _device(sku="H6072", caps=(_POWER, _MUSIC_STRUCT))

    @pytest.mark.asyncio
    async def test_unknown_device_is_refused(self):
        coord = _coordinator()
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_music_mode("nope", True) is False
        coord._ble_manager.async_send_music_mode.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_struct_device_enables_over_rest_with_the_mode_name(self):
        coord = _coordinator()
        state = _add(coord, self._struct_device())
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_music_mode(DEV, True, sensitivity=40, music_mode=3) is True

        (command,) = _sent(coord)
        assert command == MusicModeCommand(music_mode=3, sensitivity=40, auto_color=1)
        assert state.music_mode_enabled is True
        assert state.music_mode_value == 3
        assert state.music_mode_name == "Rhythm"
        assert state.music_sensitivity == 40
        coord._ble_manager.async_send_music_mode.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unadvertised_mode_has_no_display_name(self):
        coord = _coordinator()
        state = _add(coord, self._struct_device())

        assert await coord.async_send_music_mode(DEV, True, music_mode=9) is True

        assert state.music_mode_value == 9
        assert state.music_mode_name is None

    @pytest.mark.asyncio
    async def test_rest_auth_failure_propagates(self):
        coord = _coordinator()
        _add(coord, self._struct_device())
        coord._api_client.control_device = AsyncMock(side_effect=GoveeAuthError("key revoked"))
        coord._ble_manager = _ble_manager()

        with pytest.raises(ConfigEntryAuthFailed):
            await coord.async_send_music_mode(DEV, True)

        coord._ble_manager.async_send_music_mode.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rest_error_falls_back_to_the_passthrough(self):
        coord = _coordinator()
        state = _add(coord, self._struct_device())
        coord._api_client.control_device = AsyncMock(side_effect=RuntimeError("cloud hiccup"))
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_music_mode(DEV, True, sensitivity=70) is True

        coord._ble_manager.async_send_music_mode.assert_awaited_once_with(DEV, "H6072", True, 70)
        assert state.music_mode_enabled is True

    @pytest.mark.asyncio
    async def test_rest_rejection_without_mqtt_is_a_clean_failure(self):
        coord = _coordinator()
        state = _add(coord, self._struct_device())
        coord._api_client.control_device = AsyncMock(return_value=False)
        coord._ble_manager = _ble_manager(available=False)

        assert await coord.async_send_music_mode(DEV, True) is False

        coord._ble_manager.async_send_music_mode.assert_not_awaited()
        assert state.music_mode_enabled is None

    @pytest.mark.asyncio
    async def test_struct_device_disables_over_rest_first(self):
        coord = _coordinator()
        _add(coord, self._struct_device())
        coord._rest_disable_music_mode = AsyncMock(return_value=True)
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_music_mode(DEV, False, last_scene_id="7", last_scene_name="Sunset") is True

        coord._rest_disable_music_mode.assert_awaited_once_with(DEV, "7", "Sunset")
        coord._ble_manager.async_send_music_mode.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_rest_disable_falls_back_to_the_passthrough(self):
        coord = _coordinator()
        state = _add(coord, self._struct_device())
        state.music_mode_enabled = True
        coord._rest_disable_music_mode = AsyncMock(return_value=False)
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_music_mode(DEV, False) is True

        coord._ble_manager.async_send_music_mode.assert_awaited_once_with(DEV, "H6072", False, 50)
        assert state.music_mode_enabled is False

    @pytest.mark.asyncio
    async def test_legacy_device_uses_the_passthrough_directly(self):
        coord = _coordinator()
        state = _add(coord, _device())
        state.active_scene = "7"
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_music_mode(DEV, True) is True

        coord._api_client.control_device.assert_not_awaited()
        coord._ble_manager.async_send_music_mode.assert_awaited_once_with(DEV, "H6072", True, 50)
        assert state.music_mode_enabled is True
        assert state.active_scene is None

    @pytest.mark.asyncio
    async def test_passthrough_failure_leaves_state_alone(self):
        coord = _coordinator()
        state = _add(coord, _device())
        coord._ble_manager = _ble_manager(result=False)

        assert await coord.async_send_music_mode(DEV, True) is False

        assert state.music_mode_enabled is None


_MUSIC_H612F = GoveeCapability(
    type=CAPABILITY_MUSIC_MODE,
    instance=INSTANCE_MUSIC_MODE,
    parameters={
        "dataType": "STRUCT",
        "fields": [
            {
                "fieldName": "musicMode",
                "dataType": "ENUM",
                "options": [
                    {"name": "Rhythm", "value": 0},
                    {"name": "Sprouting", "value": 1},
                    {"name": "Shiny", "value": 2},
                ],
            },
            {"fieldName": "sensitivity", "dataType": "INTEGER"},
        ],
    },
)


class TestMusicModePtReal:
    """#215/#186: affected SKUs get the app's 33 05 13 frame instead of the empty REST relay."""

    def _setup(self, *, sku: str = "H612F", available: bool = True, result: bool = True):
        coord = _coordinator()
        state = _add(coord, _device(sku=sku, caps=(_POWER, _MUSIC_H612F)))
        coord._ble_manager = _ble_manager(available=available, result=result)
        return coord, state

    @pytest.mark.asyncio
    async def test_affected_sku_sends_the_app_frame_not_rest(self):
        coord, state = self._setup()

        assert await coord.async_control_device(DEV, MusicModeCommand(music_mode=2, sensitivity=40)) is True

        coord._ble_manager.async_send_music_mode_v3.assert_awaited_once_with(DEV, "H612F", 0x31, 40)
        assert _sent(coord) == []
        assert state.music_mode_enabled is True
        assert state.music_mode_value == 2
        assert coord._transport.get(DEV, "mqtt").last_send_ts is not None
        _, kwargs = coord._api_client.record_local_command.call_args
        assert kwargs["delivered"] is True
        assert "33 05 13 31" in kwargs["detail"]
        coord.async_set_updated_data.assert_called()

    @pytest.mark.asyncio
    async def test_sku_match_ignores_case(self):
        coord, _ = self._setup(sku="h612f")

        assert await coord.async_control_device(DEV, MusicModeCommand(music_mode=0, sensitivity=50)) is True

        coord._ble_manager.async_send_music_mode_v3.assert_awaited_once_with(DEV, "h612f", 0x03, 50)

    @pytest.mark.asyncio
    async def test_unaffected_sku_stays_on_rest(self):
        coord, _ = self._setup(sku="H6072")

        assert await coord.async_control_device(DEV, MusicModeCommand(music_mode=2, sensitivity=40)) is True

        coord._ble_manager.async_send_music_mode_v3.assert_not_awaited()
        assert _sent(coord) == [MusicModeCommand(music_mode=2, sensitivity=40)]

    @pytest.mark.asyncio
    async def test_no_aws_iot_falls_back_to_rest(self):
        coord, _ = self._setup(available=False)

        assert await coord.async_control_device(DEV, MusicModeCommand(music_mode=2, sensitivity=40)) is True

        coord._ble_manager.async_send_music_mode_v3.assert_not_awaited()
        assert len(_sent(coord)) == 1

    @pytest.mark.asyncio
    async def test_effect_without_an_app_code_falls_back_to_rest(self):
        coord, _ = self._setup()

        # Sprouting has no confirmed app code.
        assert await coord.async_control_device(DEV, MusicModeCommand(music_mode=1, sensitivity=40)) is True

        coord._ble_manager.async_send_music_mode_v3.assert_not_awaited()
        assert len(_sent(coord)) == 1

    @pytest.mark.asyncio
    async def test_failed_publish_falls_back_to_rest(self):
        coord, _ = self._setup(result=False)

        assert await coord.async_control_device(DEV, MusicModeCommand(music_mode=2, sensitivity=40)) is True

        coord._ble_manager.async_send_music_mode_v3.assert_awaited_once()
        _, kwargs = coord._api_client.record_local_command.call_args
        assert kwargs["delivered"] is False
        assert len(_sent(coord)) == 1


class TestRestDisableMusicMode:
    @pytest.mark.asyncio
    async def test_last_scene_is_restored_and_music_cleared(self):
        coord = _coordinator()
        state = _add(coord, _device())
        state.music_mode_enabled = True

        assert await coord._rest_disable_music_mode(DEV, "7", "Sunset") is True

        assert _sent(coord) == [SceneCommand(scene_id=7, scene_name="Sunset")]
        assert state.music_mode_enabled is False
        assert state.active_scene == "7"

    @pytest.mark.asyncio
    async def test_failed_scene_restore_falls_back_to_brightness(self):
        coord = _coordinator()
        state = _add(coord, _device())
        state.brightness = 42
        state.music_mode_enabled = True
        coord._api_client.control_device = AsyncMock(side_effect=[False, True])

        assert await coord._rest_disable_music_mode(DEV, "7", "Sunset") is True

        assert _sent(coord) == [SceneCommand(scene_id=7, scene_name="Sunset"), BrightnessCommand(brightness=42)]
        assert state.music_mode_enabled is False

    @pytest.mark.asyncio
    async def test_without_a_scene_a_full_brightness_write_exits_music_mode(self):
        coord = _coordinator()
        state = _add(coord, _device())
        state.brightness = 0
        state.music_mode_enabled = True

        assert await coord._rest_disable_music_mode(DEV) is True

        assert _sent(coord) == [BrightnessCommand(brightness=100)]
        assert state.music_mode_enabled is False

    @pytest.mark.asyncio
    async def test_rejected_brightness_keeps_music_mode_flagged(self):
        coord = _coordinator()
        state = _add(coord, _device())
        state.music_mode_enabled = True
        coord._api_client.control_device = AsyncMock(return_value=False)

        assert await coord._rest_disable_music_mode(DEV) is False

        assert state.music_mode_enabled is True


# --------------------------------------------------------------------------- #
# DreamView and DIY scenes
# --------------------------------------------------------------------------- #


class TestDreamview:
    @pytest.mark.asyncio
    async def test_unknown_device_is_refused(self):
        coord = _coordinator()
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_dreamview("nope", True) is False
        coord._ble_manager.async_send_dreamview.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rest_success_applies_the_toggle_optimistically(self):
        coord = _coordinator()
        state = _add(coord, _device())
        state.active_scene = "7"
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_dreamview(DEV, True) is True

        assert _sent(coord) == [ToggleCommand(toggle_instance=INSTANCE_DREAMVIEW, enabled=True)]
        assert state.dreamview_enabled is True
        assert state.active_scene is None
        coord._ble_manager.async_send_dreamview.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rest_auth_failure_propagates(self):
        coord = _coordinator()
        _add(coord, _device())
        coord._api_client.control_device = AsyncMock(side_effect=GoveeAuthError("key revoked"))
        coord._ble_manager = _ble_manager()

        with pytest.raises(ConfigEntryAuthFailed):
            await coord.async_send_dreamview(DEV, True)

    @pytest.mark.asyncio
    async def test_rest_error_without_mqtt_is_a_clean_failure(self):
        coord = _coordinator()
        state = _add(coord, _device())
        coord._api_client.control_device = AsyncMock(side_effect=RuntimeError("cloud hiccup"))
        coord._ble_manager = _ble_manager(available=False)

        assert await coord.async_send_dreamview(DEV, True) is False

        assert state.dreamview_enabled is None
        coord._ble_manager.async_send_dreamview.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_h66a0_skips_the_inert_rest_toggle_and_sends_the_video_frame(self):
        # Govee accepts dreamViewToggle for the H66A0 and the light ignores it (#213).
        coord = _coordinator()
        state = _add(coord, _device(sku="H66A0"))
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_dreamview(DEV, True) is True

        assert _sent(coord) == []
        coord._ble_manager.async_send_dreamview.assert_awaited_once_with(DEV, "H66A0")
        assert state.dreamview_enabled is True

    @pytest.mark.asyncio
    async def test_h66a0_off_restores_the_last_colour_instead_of_the_toggle(self):
        coord = _coordinator()
        state = _add(coord, _device(sku="H66A0"))
        state.dreamview_enabled = True
        state.last_color = RGBColor(10, 20, 30)
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_dreamview(DEV, False) is True

        assert _sent(coord) == [ColorCommand(color=RGBColor(10, 20, 30))]
        assert state.dreamview_enabled is False
        coord._ble_manager.async_send_dreamview.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_h605b_on_sends_the_rest_toggle(self):
        # The H605B does enter video mode on ON, only OFF is inert (#220).
        coord = _coordinator()
        state = _add(coord, _device(sku="H605B"))
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_dreamview(DEV, True) is True

        assert _sent(coord) == [ToggleCommand(toggle_instance=INSTANCE_DREAMVIEW, enabled=True)]
        assert state.dreamview_enabled is True
        coord._ble_manager.async_send_dreamview.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_h605b_off_restores_the_last_colour_instead_of_the_inert_toggle(self):
        # Govee accepts dreamViewToggle 0 for the H605B but the light stays in
        # video mode (#220), so OFF skips REST and restores the last colour.
        coord = _coordinator()
        state = _add(coord, _device(sku="H605B"))
        state.dreamview_enabled = True
        state.last_color = RGBColor(10, 20, 30)
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_dreamview(DEV, False) is True

        assert _sent(coord) == [ColorCommand(color=RGBColor(10, 20, 30))]
        assert state.dreamview_enabled is False
        coord._ble_manager.async_send_dreamview.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rest_error_falls_back_to_the_passthrough(self):
        coord = _coordinator()
        state = _add(coord, _device())
        coord._api_client.control_device = AsyncMock(side_effect=RuntimeError("cloud hiccup"))
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_dreamview(DEV, True) is True

        coord._ble_manager.async_send_dreamview.assert_awaited_once_with(DEV, "H6072")
        assert state.dreamview_enabled is True


class TestDiyScene:
    @pytest.mark.asyncio
    async def test_unknown_device_is_refused(self):
        coord = _coordinator()
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_diy_scene("nope", 5, "Waves") is False
        coord._ble_manager.async_send_diy_scene.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rest_success_activates_the_scene_optimistically(self):
        coord = _coordinator()
        state = _add(coord, _device())
        state.color = RGBColor(10, 20, 30)
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_diy_scene(DEV, 5, "Waves") is True

        assert _sent(coord) == [DIYSceneCommand(scene_id=5, scene_name="Waves")]
        assert state.active_diy_scene == "5"
        assert state.last_color == RGBColor(10, 20, 30)
        coord._ble_manager.async_send_diy_scene.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rest_rejection_falls_back_to_the_passthrough(self):
        coord = _coordinator()
        state = _add(coord, _device())
        coord._api_client.control_device = AsyncMock(return_value=False)
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_diy_scene(DEV, 5, "Waves") is True

        coord._ble_manager.async_send_diy_scene.assert_awaited_once_with(DEV, "H6072", 5)
        assert state.active_diy_scene == "5"

    @pytest.mark.asyncio
    async def test_rest_auth_failure_propagates(self):
        coord = _coordinator()
        _add(coord, _device())
        coord._api_client.control_device = AsyncMock(side_effect=GoveeAuthError("key revoked"))
        coord._ble_manager = _ble_manager()

        with pytest.raises(ConfigEntryAuthFailed):
            await coord.async_send_diy_scene(DEV, 5)

    @pytest.mark.asyncio
    async def test_rest_error_without_mqtt_is_a_clean_failure(self):
        coord = _coordinator()
        state = _add(coord, _device())
        coord._api_client.control_device = AsyncMock(side_effect=RuntimeError("cloud hiccup"))
        coord._ble_manager = _ble_manager(available=False)

        assert await coord.async_send_diy_scene(DEV, 5) is False

        assert state.active_diy_scene is None

    @pytest.mark.asyncio
    async def test_passthrough_failure_leaves_state_alone(self):
        coord = _coordinator()
        state = _add(coord, _device())
        coord._api_client.control_device = AsyncMock(return_value=False)
        coord._ble_manager = _ble_manager(result=False)

        assert await coord.async_send_diy_scene(DEV, 5) is False

        assert state.active_diy_scene is None


class TestRawPtreal:
    @pytest.mark.asyncio
    async def test_short_frame_gets_a_checksum(self):
        coord = _coordinator()
        _add(coord, _device())
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_raw_ptreal(DEV, bytes([0x33, 0x05, 0x01])) is True

        expected = encode_packet_base64(build_packet([0x33, 0x05, 0x01]))
        coord._ble_manager.async_send_ble_packet.assert_awaited_once_with(DEV, "H6072", expected)

    @pytest.mark.asyncio
    async def test_valid_20_byte_frame_is_sent_unchanged(self):
        coord = _coordinator()
        _add(coord, _device())
        coord._ble_manager = _ble_manager()

        frame = build_packet([0x33, 0x05, 0x01])
        assert len(frame) == 20

        assert await coord.async_send_raw_ptreal(DEV, frame) is True

        coord._ble_manager.async_send_ble_packet.assert_awaited_once_with(DEV, "H6072", encode_packet_base64(frame))

    @pytest.mark.asyncio
    async def test_bad_checksum_20_byte_frame_is_refused(self):
        coord = _coordinator()
        _add(coord, _device())
        coord._ble_manager = _ble_manager()

        frame = bytearray(build_packet([0x33, 0x05, 0x01]))
        frame[19] ^= 0xFF

        assert await coord.async_send_raw_ptreal(DEV, bytes(frame)) is False
        coord._ble_manager.async_send_ble_packet.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_too_long_frame_is_refused(self):
        coord = _coordinator()
        _add(coord, _device())
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_raw_ptreal(DEV, bytes(21)) is False
        coord._ble_manager.async_send_ble_packet.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_frame_is_refused(self):
        coord = _coordinator()
        _add(coord, _device())
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_raw_ptreal(DEV, b"") is False
        coord._ble_manager.async_send_ble_packet.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_device_is_refused(self):
        coord = _coordinator()
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_raw_ptreal("nope", bytes([0x33])) is False
        coord._ble_manager.async_send_ble_packet.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_device_is_refused(self):
        coord = _coordinator()
        _add(coord, _device())
        coord._devices[DEV] = replace(coord._devices[DEV], is_group=True)
        coord._ble_manager = _ble_manager()

        assert await coord.async_send_raw_ptreal(DEV, bytes([0x33])) is False
        coord._ble_manager.async_send_ble_packet.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_passthrough_unavailable_is_refused(self):
        coord = _coordinator()
        _add(coord, _device())
        coord._ble_manager = _ble_manager(available=False)

        assert await coord.async_send_raw_ptreal(DEV, bytes([0x33])) is False
        coord._ble_manager.async_send_ble_packet.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_failure_propagates(self):
        coord = _coordinator()
        _add(coord, _device())
        coord._ble_manager = _ble_manager(result=False)

        assert await coord.async_send_raw_ptreal(DEV, bytes([0x33])) is False


# --------------------------------------------------------------------------- #
# Optimistic state application
# --------------------------------------------------------------------------- #


class TestApplyOptimisticUpdate:
    def test_unknown_device_state_is_ignored(self):
        coord = _coordinator()

        coord._apply_optimistic_update("nope", PowerCommand(power_on=True))

        assert coord._states == {}

    def test_scene_and_diy_scene_commands(self):
        coord = _coordinator()
        state = _add(coord, _device())
        state.color = RGBColor(255, 0, 0)

        coord._apply_optimistic_update(DEV, SceneCommand(scene_id=7, scene_name="Sunset"))
        assert state.active_scene == "7"
        assert state.active_scene_name == "Sunset"
        assert state.last_color == RGBColor(255, 0, 0)

        coord._apply_optimistic_update(DEV, DIYSceneCommand(scene_id=9, scene_name="Waves"))
        assert state.active_diy_scene == "9"
        assert state.active_scene is None

    def test_hdmi_source_mode_command(self):
        coord = _coordinator()
        state = _add(coord, _device())

        coord._apply_optimistic_update(DEV, ModeCommand(mode_instance=INSTANCE_HDMI_SOURCE, value=3))

        assert state.hdmi_source == 3
        assert state.source == "optimistic"

    def test_temperature_setting_command(self):
        coord = _coordinator()
        state = _add(coord, _device())

        coord._apply_optimistic_update(DEV, TemperatureSettingCommand(temperature=22, auto_stop=1))

        assert state.heater_temperature == 22
        assert state.heater_auto_stop == 1

    def test_dreamview_and_thermostat_toggles(self):
        coord = _coordinator()
        state = _add(coord, _device())
        state.music_mode_enabled = True

        coord._apply_optimistic_update(DEV, ToggleCommand(toggle_instance=INSTANCE_DREAMVIEW, enabled=True))
        assert state.dreamview_enabled is True
        assert state.music_mode_enabled is False

        coord._apply_optimistic_update(DEV, ToggleCommand(toggle_instance=INSTANCE_THERMOSTAT_TOGGLE, enabled=True))
        assert state.heater_auto_stop == 1
        coord._apply_optimistic_update(DEV, ToggleCommand(toggle_instance=INSTANCE_THERMOSTAT_TOGGLE, enabled=False))
        assert state.heater_auto_stop == 0

    def test_generic_toggle_lands_in_the_toggle_map(self):
        coord = _coordinator()
        state = _add(coord, _device())

        coord._apply_optimistic_update(DEV, ToggleCommand(toggle_instance="socketToggle2", enabled=True))

        assert state.toggles == {"socketToggle2": True}


# --------------------------------------------------------------------------- #
# Segment re-assert (issue #131)
# --------------------------------------------------------------------------- #


class TestReassertSegments:
    def _ready(self, remaining: int = 100) -> GoveeCoordinator:
        coord = _coordinator()
        _add(coord, _device(caps=(_POWER, _RGB, _SEGMENTS)))
        coord._api_client.rate_limit_remaining = remaining
        coord.async_control_device = AsyncMock(return_value=True)
        return coord

    @pytest.mark.asyncio
    async def test_nothing_tracked_sends_nothing(self):
        coord = self._ready()

        await coord.async_reassert_segments(DEV)

        coord.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_power_off_in_flight_suppresses_the_replay(self):
        coord = self._ready()
        coord.record_segment_color(DEV, 0, (255, 0, 0))
        coord._pending_power_off.add(DEV)

        await coord.async_reassert_segments(DEV)

        coord.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_black_ring_is_left_alone_only_after_a_black_write(self):
        coord = self._ready()
        coord.record_segment_color(DEV, 0, (0, 0, 0))
        coord.record_segment_color(DEV, 1, (0, 0, 0))

        await coord.async_reassert_segments(DEV, wrote_black=True)
        coord.async_control_device.assert_not_awaited()

        await coord.async_reassert_segments(DEV, wrote_black=False)
        coord.async_control_device.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_segments_are_grouped_by_colour_with_sorted_indices(self):
        coord = self._ready()
        coord.record_segment_color(DEV, 3, (255, 0, 0))
        coord.record_segment_color(DEV, 1, (0, 0, 255))
        coord.record_segment_color(DEV, 0, (255, 0, 0))

        await coord.async_reassert_segments(DEV)

        commands = [call.args[1] for call in coord.async_control_device.await_args_list]
        assert commands == [
            SegmentColorCommand(segment_indices=(0, 3), color=RGBColor(255, 0, 0)),
            SegmentColorCommand(segment_indices=(1,), color=RGBColor(0, 0, 255)),
        ]

    @pytest.mark.asyncio
    async def test_replay_is_skipped_when_it_would_eat_the_api_reserve(self):
        coord = self._ready(remaining=coord_mod._REASSERT_RATE_LIMIT_RESERVE + 1)
        coord.record_segment_color(DEV, 0, (255, 0, 0))
        coord.record_segment_color(DEV, 1, (0, 0, 255))

        await coord.async_reassert_segments(DEV)
        coord.async_control_device.assert_not_awaited()

        coord._api_client.rate_limit_remaining = coord_mod._REASSERT_RATE_LIMIT_RESERVE + 2
        await coord.async_reassert_segments(DEV)
        assert coord.async_control_device.await_count == 2


def _h8057() -> GoveeDevice:
    """Reported capability order: brightness has four indices, RGB has three."""

    def capability(instance: str, max_index: int) -> GoveeCapability:
        return GoveeCapability(
            type=CAPABILITY_SEGMENT_COLOR,
            instance=instance,
            parameters={
                "fields": [
                    {
                        "fieldName": "segment",
                        "size": {"min": 1, "max": 4},
                        "elementRange": {"min": 0, "max": max_index},
                    }
                ]
            },
        )

    return _device(
        sku="H8057",
        caps=(
            _POWER,
            _RGB,
            capability("segmentedBrightness", 3),
            capability("segmentedColorRgb", 2),
        ),
    )


class TestH8057SegmentColor:
    """Only H8057's fourth RGB segment bypasses the public API."""

    @pytest.mark.parametrize("rgb", [(0, 0, 255), (12, 34, 56), (0, 0, 0)])
    @pytest.mark.asyncio
    async def test_fourth_segment_packet_and_real_passthrough_publish(self, rgb):
        coord = _coordinator()
        _add(coord, _h8057())
        client = _mqtt()
        client.async_publish_ptreal = AsyncMock(return_value=True)
        coord._ble_manager = BlePassthroughManager(lambda: client, {}, AsyncMock(return_value="GD/test"))
        command = SegmentColorCommand(segment_indices=(3,), color=RGBColor(*rgb))

        assert await coord.async_control_device(DEV, command) is True

        args = client.async_publish_ptreal.await_args.args
        assert args[:2] == (DEV, "H8057")
        assert args[3] == "GD/test"
        packet = base64.b64decode(args[2])
        assert packet[:15] == bytes([0x33, 0x05, 0x15, 0x01, *rgb, 0, 0, 0, 0, 0, 8, 0, 0])
        assert len(packet) == 20
        checksum = 0
        for byte in packet[:19]:
            checksum ^= byte
        assert packet[19] == checksum
        coord._api_client.control_device.assert_not_awaited()
        assert coord._segment_colors[DEV][3] == rgb
        assert coord._transport.get(DEV, "mqtt").last_send_ts is not None
        coord.async_set_updated_data.assert_called_once()

    @pytest.mark.parametrize("indices", [(0,), (1,), (2,), (0, 1, 2)])
    @pytest.mark.parametrize("available", [False, True])
    @pytest.mark.asyncio
    async def test_lower_segments_keep_rest(self, indices, available, monkeypatch):
        monkeypatch.setattr(coord_mod, "SEGMENT_COMMAND_PACING_SECONDS", 0)
        coord = _coordinator()
        _add(coord, _h8057())
        coord._ble_manager = _ble_manager(available=available)
        command = SegmentColorCommand(segment_indices=indices, color=RGBColor(1, 2, 3))

        assert await coord.async_control_device(DEV, command) is True
        coord._api_client.control_device.assert_awaited_once_with(DEV, "H8057", command)
        coord._ble_manager.async_send_ble_packet.assert_not_awaited()

    @pytest.mark.parametrize("available", [False, True])
    @pytest.mark.asyncio
    async def test_missing_transport_or_failed_publish_never_falls_back(self, available):
        coord = _coordinator()
        _add(coord, _h8057())
        coord._ble_manager = _ble_manager(available=available, result=False)

        with pytest.raises(HomeAssistantError) as err:
            await coord.async_control_device(DEV, SegmentColorCommand(segment_indices=(3,), color=RGBColor(1, 2, 3)))

        assert err.value.translation_key == "segment_passthrough_failed"

        coord._api_client.control_device.assert_not_awaited()
        assert coord._ble_manager.async_send_ble_packet.await_count == int(available)
        coord.async_set_updated_data.assert_not_called()
        assert coord._transport.get(DEV, "mqtt").last_failure_reason == "h8057_segment_4_passthrough_failed"

    @pytest.mark.parametrize("rest_ok,private_ok", [(True, True), (False, True), (True, False), (False, False)])
    @pytest.mark.asyncio
    async def test_mixed_request_splits_and_reports_partial_failure(self, rest_ok, private_ok, monkeypatch):
        monkeypatch.setattr(coord_mod, "SEGMENT_COMMAND_PACING_SECONDS", 0)
        coord = _coordinator()
        _add(coord, _h8057())
        coord._api_client.control_device.return_value = rest_ok
        coord._ble_manager = _ble_manager(result=private_ok)
        events = []

        async def rest(*args):
            events.append("rest")
            return rest_ok

        async def private(*args):
            events.append("private")
            return private_ok

        coord._api_client.control_device.side_effect = rest
        coord._ble_manager.async_send_ble_packet.side_effect = private
        command = SegmentColorCommand(segment_indices=(0, 1, 2, 3), color=RGBColor(12, 34, 56))
        if private_ok:
            assert await coord.async_control_device(DEV, command) is rest_ok
        else:
            with pytest.raises(HomeAssistantError) as err:
                await coord.async_control_device(DEV, command)
            assert err.value.translation_key == "segment_passthrough_failed"

        assert events == ["rest", "private"]
        assert _sent(coord) == [SegmentColorCommand(segment_indices=(0, 1, 2), color=command.color)]
        expected = encode_packet_base64(build_packet(build_segment_color_ptreal(command.color, 8)))
        coord._ble_manager.async_send_ble_packet.assert_awaited_once_with(DEV, "H8057", expected)
        assert coord.async_set_updated_data.call_count == int(rest_ok) + int(private_ok)

    @pytest.mark.asyncio
    async def test_mixed_request_still_sends_lower_segments_without_mqtt(self, monkeypatch):
        monkeypatch.setattr(coord_mod, "SEGMENT_COMMAND_PACING_SECONDS", 0)
        coord = _coordinator()
        _add(coord, _h8057())
        coord._ble_manager = _ble_manager(available=False)
        command = SegmentColorCommand(segment_indices=(3, 1), color=RGBColor(1, 2, 3))
        with pytest.raises(HomeAssistantError) as err:
            await coord.async_control_device(DEV, command)
        assert err.value.translation_key == "segment_passthrough_failed"
        assert _sent(coord) == [SegmentColorCommand(segment_indices=(1,), color=command.color)]
        coord._ble_manager.async_send_ble_packet.assert_not_awaited()

    def test_four_existing_entity_identities_and_segment_count(self):
        from custom_components.govee.platforms.segment import GoveeSegmentEntity

        device = _h8057()
        coord = _coordinator()
        _add(coord, device)
        assert device.segment_count == 4
        assert device.segment_count_resolution["override"] is None
        entities = [GoveeSegmentEntity(coord, device, i) for i in range(device.segment_count)]
        assert [entity.unique_id for entity in entities] == [f"{DEV}_segment_{i}" for i in range(4)]

    @pytest.mark.asyncio
    async def test_entity_off_and_on_use_masked_ptreal(self):
        from custom_components.govee.platforms.segment import GoveeSegmentEntity

        coord = _coordinator()
        device = _h8057()
        state = _add(coord, device)
        state.power_state = True
        coord._ble_manager = _ble_manager()
        entity = GoveeSegmentEntity(coord, device, 3)
        entity.async_write_ha_state = MagicMock()

        await entity.async_turn_on(rgb_color=(12, 34, 56))
        await entity.async_turn_off()
        await entity.async_turn_on()

        packets = [base64.b64decode(call.args[2]) for call in coord._ble_manager.async_send_ble_packet.await_args_list]
        assert [packet[4:7] for packet in packets] == [bytes([12, 34, 56]), bytes(3), bytes([12, 34, 56])]
        assert all(packet[12:15] == bytes([8, 0, 0]) for packet in packets)
        coord._api_client.control_device.assert_not_awaited()
        assert entity.is_on is True

    @pytest.mark.asyncio
    async def test_other_sku_fourth_segment_stays_on_rest(self, monkeypatch):
        monkeypatch.setattr(coord_mod, "SEGMENT_COMMAND_PACING_SECONDS", 0)
        coord = _coordinator()
        _add(coord, _device(caps=(_POWER, _RGB, _SEGMENTS)))
        coord._ble_manager = _ble_manager()
        command = SegmentColorCommand(segment_indices=(3,), color=RGBColor(1, 2, 3))
        assert await coord.async_control_device(DEV, command) is True
        coord._api_client.control_device.assert_awaited_once_with(DEV, "H6072", command)
        coord._ble_manager.async_send_ble_packet.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_grouped_entity_uses_split_routing(self, monkeypatch):
        from custom_components.govee.platforms.grouped_segment import GoveeGroupedSegmentEntity
        import custom_components.govee.platforms.grouped_segment as grouped_mod

        monkeypatch.setattr(coord_mod, "SEGMENT_COMMAND_PACING_SECONDS", 0)
        monkeypatch.setattr(grouped_mod, "async_dispatcher_send", MagicMock())
        coord = _coordinator()
        device = _h8057()
        _add(coord, device)
        coord._ble_manager = _ble_manager()
        entity = GoveeGroupedSegmentEntity(coord, device)
        entity.async_write_ha_state = MagicMock()

        await entity.async_turn_on(rgb_color=(12, 34, 56))

        assert _sent(coord) == [SegmentColorCommand(segment_indices=(0, 1, 2), color=RGBColor(12, 34, 56))]
        coord._ble_manager.async_send_ble_packet.assert_awaited_once()
        assert entity.is_on is True

    @pytest.mark.asyncio
    async def test_segment_service_uses_private_routing(self, monkeypatch):
        import custom_components.govee.services as services_mod

        coord = _coordinator()
        _add(coord, _h8057())
        coord._ble_manager = _ble_manager()
        monkeypatch.setattr(services_mod, "_get_coordinator_for_device", lambda *args: (coord, DEV))
        call = MagicMock(data={"device_id": DEV, "segments": [3], "rgb_color": [12, 34, 56]})

        await services_mod.async_set_segment_color_handler(MagicMock(), call)

        coord._ble_manager.async_send_ble_packet.assert_awaited_once()
        coord._api_client.control_device.assert_not_awaited()


class TestPtrealSegmentTier:
    """H1232 ring writes fold into one masked ptReal frame (issue #223)."""

    @pytest.mark.asyncio
    async def test_passthrough_sends_one_masked_frame_and_skips_rest(self):
        coord = _coordinator()
        _add(coord, _device(sku="H1232", caps=(_POWER, _RGB, _SEGMENTS)))
        coord._ble_manager = _ble_manager()
        command = SegmentColorCommand(segment_indices=(13, 14, 15), color=RGBColor(10, 20, 30))

        assert await coord.async_control_device(DEV, command) is True

        mask = (1 << 13) | (1 << 14) | (1 << 15)
        expected = encode_packet_base64(build_packet(build_segment_color_ptreal(RGBColor(10, 20, 30), mask)))
        coord._ble_manager.async_send_ble_packet.assert_awaited_once_with(DEV, "H1232", expected)
        coord._api_client.control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_passthrough_send_failure_falls_back_to_rest_in_range(self):
        coord = _coordinator()
        _add(coord, _device(sku="H1232", caps=(_POWER, _RGB, _SEGMENTS)))
        coord._ble_manager = _ble_manager(result=False)
        command = SegmentColorCommand(segment_indices=(0,), color=RGBColor(1, 2, 3))

        assert await coord.async_control_device(DEV, command) is True

        coord._ble_manager.async_send_ble_packet.assert_awaited_once()
        coord._api_client.control_device.assert_awaited_once_with(DEV, "H1232", command)

    @pytest.mark.asyncio
    async def test_passthrough_send_failure_out_of_range_fails(self):
        coord = _coordinator()
        _add(coord, _device(sku="H1232", caps=(_POWER, _RGB, _SEGMENTS)))
        coord._ble_manager = _ble_manager(result=False)
        command = SegmentColorCommand(segment_indices=(15,), color=RGBColor(1, 2, 3))

        assert await coord.async_control_device(DEV, command) is False

        coord._api_client.control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_passthrough_in_range_index_falls_through_to_rest(self):
        coord = _coordinator()
        _add(coord, _device(sku="H1232", caps=(_POWER, _RGB, _SEGMENTS)))
        coord._ble_manager = _ble_manager(available=False)
        command = SegmentColorCommand(segment_indices=(3,), color=RGBColor(1, 2, 3))

        assert await coord.async_control_device(DEV, command) is True

        coord._api_client.control_device.assert_awaited_once_with(DEV, "H1232", command)

    @pytest.mark.asyncio
    async def test_no_passthrough_out_of_range_index_fails(self):
        coord = _coordinator()
        _add(coord, _device(sku="H1232", caps=(_POWER, _RGB, _SEGMENTS)))
        coord._ble_manager = _ble_manager(available=False)
        command = SegmentColorCommand(segment_indices=(14,), color=RGBColor(1, 2, 3))

        assert await coord.async_control_device(DEV, command) is False

        coord._api_client.control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_ptreal_sku_is_unaffected(self):
        coord = _coordinator()
        _add(coord, _device(caps=(_POWER, _RGB, _SEGMENTS)))
        coord._ble_manager = _ble_manager()
        command = SegmentColorCommand(segment_indices=(0,), color=RGBColor(1, 2, 3))

        assert await coord.async_control_device(DEV, command) is True

        coord._api_client.control_device.assert_awaited_once_with(DEV, "H6072", command)
        coord._ble_manager.async_send_ble_packet.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Scene clearing and the small state helpers
# --------------------------------------------------------------------------- #


class TestClearScene:
    def _ready(self, device: GoveeDevice | None = None) -> tuple[GoveeCoordinator, GoveeDeviceState]:
        coord = _coordinator()
        state = _add(coord, device or _device())
        state.active_scene = "7"
        state.active_scene_name = "Sunset"
        state.active_diy_scene = "9"
        coord.async_control_device = AsyncMock(return_value=True)
        return coord, state

    def _commands(self, coord: GoveeCoordinator) -> list[Any]:
        return [call.args[1] for call in coord.async_control_device.await_args_list]

    @pytest.mark.asyncio
    async def test_unknown_device_is_ignored(self):
        coord = _coordinator()
        coord.async_control_device = AsyncMock(return_value=True)

        await coord.async_clear_scene("nope")

        coord.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_active_scene_only_resets_the_flags(self):
        coord, state = self._ready()
        state.active_scene = None
        state.active_diy_scene = None
        state.active_scene_name = "stale"
        state.source = "api"

        await coord.async_clear_scene(DEV)

        coord.async_control_device.assert_not_awaited()
        assert state.active_scene_name is None
        assert state.source == "optimistic"

    @pytest.mark.asyncio
    async def test_current_colour_is_restored(self):
        coord, state = self._ready()
        state.color = RGBColor(10, 20, 30)

        await coord.async_clear_scene(DEV)

        assert self._commands(coord) == [ColorCommand(color=RGBColor(10, 20, 30))]
        assert state.active_scene is None
        assert state.active_scene_name is None
        assert state.active_diy_scene is None

    @pytest.mark.asyncio
    async def test_black_sentinel_falls_back_to_the_last_colour(self):
        coord, state = self._ready()
        state.color = RGBColor(0, 0, 0)
        state.last_color = RGBColor(0, 255, 0)

        await coord.async_clear_scene(DEV)

        assert self._commands(coord) == [ColorCommand(color=RGBColor(0, 255, 0))]

    @pytest.mark.asyncio
    async def test_black_last_colour_falls_back_to_colour_temperature(self):
        coord, state = self._ready()
        state.color = RGBColor(0, 0, 0)
        state.last_color = RGBColor(0, 0, 0)
        state.last_color_temp_kelvin = 3500

        await coord.async_clear_scene(DEV)

        assert self._commands(coord) == [ColorTempCommand(kelvin=3500)]

    @pytest.mark.asyncio
    async def test_no_memory_defaults_to_rgb_white(self):
        coord, _state = self._ready()

        await coord.async_clear_scene(DEV)

        assert self._commands(coord) == [ColorCommand(color=RGBColor(255, 255, 255))]

    @pytest.mark.asyncio
    async def test_colour_temp_only_device_uses_the_range_midpoint(self):
        coord, _state = self._ready(_device(caps=(_POWER, _BRIGHTNESS, _CT)))

        await coord.async_clear_scene(DEV)

        assert self._commands(coord) == [ColorTempCommand(kelvin=5500)]

    @pytest.mark.asyncio
    async def test_colour_temp_only_device_without_a_range_uses_4000k(self):
        coord, _state = self._ready(_device(caps=(_POWER, _BRIGHTNESS, _CT_NO_RANGE)))

        await coord.async_clear_scene(DEV)

        assert self._commands(coord) == [ColorTempCommand(kelvin=4000)]

    @pytest.mark.asyncio
    async def test_device_without_colour_capabilities_sends_nothing(self):
        coord, state = self._ready(_device(caps=(_POWER, _BRIGHTNESS)))

        await coord.async_clear_scene(DEV)

        coord.async_control_device.assert_not_awaited()
        assert state.active_scene == "7"

    @pytest.mark.asyncio
    async def test_rejected_write_keeps_the_scene(self):
        coord, state = self._ready()
        coord.async_control_device = AsyncMock(return_value=False)

        await coord.async_clear_scene(DEV)

        assert state.active_scene == "7"
        assert state.active_diy_scene == "9"


class TestStateHelpers:
    @pytest.mark.asyncio
    async def test_diy_scene_lookup_goes_through_the_cache(self):
        coord = _coordinator()
        device = _add(coord, _device()) and coord._devices[DEV]
        coord._scene_cache.async_get_diy_scenes = AsyncMock(return_value=[{"name": "Waves", "value": 5}])

        assert await coord.async_get_diy_scenes(DEV, refresh=True) == [{"name": "Waves", "value": 5}]

        coord._scene_cache.async_get_diy_scenes.assert_awaited_once_with(DEV, device, True)

    def test_clear_music_mode(self):
        coord = _coordinator()
        state = _add(coord, _device())
        state.music_mode_enabled = True
        state.source = "api"

        coord.clear_music_mode(DEV)
        coord.clear_music_mode("nope")

        assert state.music_mode_enabled is False
        assert state.source == "optimistic"

    def test_restore_group_state(self):
        coord = _coordinator()
        state = _add(coord, _device())

        coord.restore_group_state(DEV, True)
        assert state.power_state is True
        assert state.brightness == 100

        coord.restore_group_state(DEV, False, brightness=33)
        assert state.power_state is False
        assert state.brightness == 33
        assert state.source == "optimistic"

        coord.restore_group_state("nope", True)


class TestAsyncSetMainPanel:
    """H1232 main-panel colour/brightness over masked ptReal (issue #223)."""

    @pytest.mark.asyncio
    async def test_unsupported_sku_returns_false(self):
        coord = _coordinator()
        _add(coord, _device(sku="H6072", caps=(_POWER, _RGB)))
        coord._ble_manager = _ble_manager()

        assert await coord.async_set_main_panel(DEV, rgb=RGBColor(1, 2, 3)) is False
        coord._ble_manager.async_send_ble_packet.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_device_returns_false(self):
        coord = _coordinator()
        coord._ble_manager = _ble_manager()

        assert await coord.async_set_main_panel(DEV, rgb=RGBColor(1, 2, 3)) is False

    @pytest.mark.asyncio
    async def test_no_passthrough_returns_false(self):
        coord = _coordinator()
        _add(coord, _device(sku="H1232", caps=(_POWER, _RGB, _SEGMENTS)))
        coord._ble_manager = _ble_manager(available=False)

        assert await coord.async_set_main_panel(DEV, rgb=RGBColor(1, 2, 3)) is False
        coord._ble_manager.async_send_ble_packet.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_colour_sends_one_masked_frame(self):
        coord = _coordinator()
        _add(coord, _device(sku="H1232", caps=(_POWER, _RGB, _SEGMENTS)))
        coord._ble_manager = _ble_manager()

        assert await coord.async_set_main_panel(DEV, rgb=RGBColor(10, 20, 30)) is True

        mask = 1 << 16
        expected = encode_packet_base64(build_packet(build_segment_color_ptreal(RGBColor(10, 20, 30), mask)))
        coord._ble_manager.async_send_ble_packet.assert_awaited_once_with(DEV, "H1232", expected)

    @pytest.mark.asyncio
    async def test_brightness_sends_one_masked_frame(self):
        coord = _coordinator()
        _add(coord, _device(sku="H1232", caps=(_POWER, _RGB, _SEGMENTS)))
        coord._ble_manager = _ble_manager()

        assert await coord.async_set_main_panel(DEV, brightness=50) is True

        mask = 1 << 16
        expected = encode_packet_base64(build_packet(build_segment_brightness_ptreal(50, mask)))
        coord._ble_manager.async_send_ble_packet.assert_awaited_once_with(DEV, "H1232", expected)

    @pytest.mark.asyncio
    async def test_colour_and_brightness_send_both_frames(self):
        coord = _coordinator()
        _add(coord, _device(sku="H1232", caps=(_POWER, _RGB, _SEGMENTS)))
        coord._ble_manager = _ble_manager()

        assert await coord.async_set_main_panel(DEV, rgb=RGBColor(1, 2, 3), brightness=80) is True

        assert coord._ble_manager.async_send_ble_packet.await_count == 2

    @pytest.mark.asyncio
    async def test_colour_send_failure_returns_false(self):
        coord = _coordinator()
        _add(coord, _device(sku="H1232", caps=(_POWER, _RGB, _SEGMENTS)))
        coord._ble_manager = _ble_manager(result=False)

        assert await coord.async_set_main_panel(DEV, rgb=RGBColor(1, 2, 3)) is False

    @pytest.mark.asyncio
    async def test_brightness_send_failure_returns_false(self):
        coord = _coordinator()
        _add(coord, _device(sku="H1232", caps=(_POWER, _RGB, _SEGMENTS)))
        coord._ble_manager = _ble_manager(result=False)

        assert await coord.async_set_main_panel(DEV, brightness=50) is False
