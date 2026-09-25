"""Coordinator control paths: transport tiers, mode commands and scene clearing.

Behavioural coverage for ``GoveeCoordinator.async_control_device`` and the
helpers around it — the BLE > LAN > MQTT > REST tiers, per-device segment
serialisation, the music-mode / DreamView / DIY-scene fallbacks from REST to
the BLE passthrough, optimistic state application, segment re-assertion and
``async_clear_scene``. Every transport is an in-process fake; no socket is
ever opened.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

import custom_components.govee.coordinator as coord_mod
from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.exceptions import GoveeApiError, GoveeAuthError
from custom_components.govee.api.lan_client import LanDevStatus, LanDeviceInfo
from custom_components.govee.const import CONF_ENABLE_MQTT_CONTROL
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

        assert await coord.async_control_device(DEV, ColorCommand(color=RGBColor(1, 2, 3))) is True

        publishes = [call.args for call in coord._mqtt_client.async_publish_command.await_args_list]
        assert publishes[0] == ("GD/dev", "colorwc", {"color": {"r": 1, "g": 2, "b": 3}, "colorTemInKelvin": 0})
        assert publishes[1] == ("GD/dev", "color", {"r": 1, "g": 2, "b": 3})
        assert coord._mqtt_client.async_publish_command.await_args_list[1].kwargs == {"cmd_version": 1}
        assert coord._states[DEV].color == RGBColor(1, 2, 3)

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


class TestBleTier:
    @pytest.mark.asyncio
    async def test_successful_ble_write_restores_a_cloud_offline_device(self):
        coord = _coordinator()
        state = _add(coord, _device())
        state.online = False
        ble = MagicMock()
        ble.turn_on = AsyncMock()
        coord._ble_devices[DEV] = ble

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

        assert await coord.async_control_device(DEV, BrightnessCommand(brightness=30)) is True

        assert coord._transport.get(DEV, "ble").last_failure_reason == "gatt"
        coord._api_client.control_device.assert_awaited_once()


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
