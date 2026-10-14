"""Coordinator inbound events, the state poll, credential recovery and shutdown.

Behavioural coverage for the push side of ``GoveeCoordinator`` — AWS IoT
state frames and the leak / thermo / probe / button multiSync handlers they
route to, OpenAPI event pushes, the MQTT give-up hook — plus the cloud poll's
per-device preservation rules (``_fetch_device_state``), the whole-poll
bookkeeping in ``_async_update_data``, refreshed-credential persistence and
``async_shutdown``. Every transport is an in-process fake.
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

import custom_components.govee.coordinator as coord_mod
from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.exceptions import (
    GoveeAuthError,
    GoveeDeviceNotFoundError,
    GoveeRateLimitError,
)
from custom_components.govee.const import CONF_EMAIL, DOMAIN, KEY_IOT_CREDENTIALS, KEY_IOT_LOGIN_FAILED
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState, RGBColor
from custom_components.govee.models.device import (
    CAPABILITY_EVENT,
    CAPABILITY_MODE,
    CAPABILITY_ON_OFF,
    CAPABILITY_TOGGLE,
    INSTANCE_BODY_APPEARED_EVENT,
    INSTANCE_FAN_SPEED_MODE,
    INSTANCE_FAN_TOGGLE,
    INSTANCE_POWER,
    GoveeLeakSensor,
    GoveeLeakSensorState,
)

DEV = "AA:BB:CC:DD:EE:FF:00:11"
GROUP = "11825917"
WD = "DABFC0D6A5FE0008E8"
LEAK = "01:32:7A:C4:06:03:0D:0C"
HUB = "09:C2:60:74:F4:64:AB:FA"
PLUG = "AA:BB:CC:DD:EE:FF:51:60"

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
FRESH = GoveeIotCredentials(
    token="fresh",
    refresh_token="r2",
    account_topic="GA/x",
    iot_cert="c2",
    iot_key="k2",
    iot_ca=None,
    client_id="cid",
    endpoint="ep",
)

_POWER = GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={})


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _coordinator(*, iot: GoveeIotCredentials | None = None, data: dict[str, Any] | None = None) -> GoveeCoordinator:
    """A real coordinator over a mocked hass/entry, with HA notifications stubbed."""
    entry = MagicMock()
    entry.entry_id = "cov_entry"
    entry.title = "Govee"
    entry.options = {}
    entry.data = data if data is not None else {}
    coord = GoveeCoordinator(
        hass=MagicMock(),
        config_entry=entry,
        api_client=MagicMock(),
        iot_credentials=iot,
        poll_interval=60,
    )
    coord._api_client.last_raw_state = {}
    coord.async_set_updated_data = MagicMock()
    coord.async_update_listeners = MagicMock()
    return coord


def _add(coord: GoveeCoordinator, device: GoveeDevice, state: GoveeDeviceState | None = None) -> GoveeDeviceState:
    coord._devices[device.device_id] = device
    state = state or GoveeDeviceState.create_empty(device.device_id)
    coord._states[device.device_id] = state
    coord._ensure_transport_health(device.device_id)
    return state


def _light(device_id: str = DEV, *, sku: str = "H6072", caps: tuple[GoveeCapability, ...] = (_POWER,)) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name="Test light",
        device_type="devices.types.light",
        capabilities=caps,
    )


def _group(device_id: str = GROUP) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id, sku="GROUP", name="All", device_type="devices.types.group", capabilities=(), is_group=True
    )


def _water_detector(device_id: str = WD) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku="H5054",
        name="Washing machine",
        device_type="devices.types.sensor",
        capabilities=(GoveeCapability(type=CAPABILITY_EVENT, instance=INSTANCE_BODY_APPEARED_EVENT, parameters={}),),
    )


def _ceiling_fan(device_id: str = DEV) -> GoveeDevice:
    return _light(
        device_id,
        sku="H1310",
        caps=(
            _POWER,
            GoveeCapability(type=CAPABILITY_TOGGLE, instance=INSTANCE_FAN_TOGGLE, parameters={}),
            GoveeCapability(type=CAPABILITY_MODE, instance=INSTANCE_FAN_SPEED_MODE, parameters={"options": []}),
        ),
    )


def _multi_outlet_plug(device_id: str = PLUG) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id, sku="H5160", name="Strip", device_type="devices.types.socket", capabilities=(_POWER,)
    )


def _polling_shell(coord: GoveeCoordinator) -> GoveeCoordinator:
    """Isolate ``_async_update_data`` from rediscovery, BLE enrolment and the registry."""
    coord._async_maybe_rediscover_devices = AsyncMock()
    coord._ble_handler = MagicMock()
    coord._devices_with_all_entities_disabled = MagicMock(return_value=set())
    return coord


class _TaskCapture:
    """Stand-in for ``ConfigEntry.async_create_background_task`` that keeps the coroutines."""

    def __init__(self) -> None:
        self.pending: list[tuple[str | None, Any]] = []

    def __call__(self, hass: Any, coro: Any, name: str | None = None, **kwargs: Any) -> Any:
        self.pending.append((name, coro))
        task = MagicMock()
        task.done.return_value = False
        return task

    @property
    def names(self) -> list[str | None]:
        return [name for name, _ in self.pending]

    async def run_all(self) -> None:
        while self.pending:
            _name, coro = self.pending.pop(0)
            await coro


# --------------------------------------------------------------------------- #
# OpenAPI event pushes
# --------------------------------------------------------------------------- #


class TestOpenApiEventValues:
    def test_unparseable_values_are_skipped_until_a_usable_one(self):
        coord = _coordinator()
        state = _add(coord, _light())

        coord._on_openapi_event(DEV, "H7150", "waterFullEvent", [{"value": "full"}, {"value": "1"}])

        assert state.water_full is True
        assert coord.water_full_changed_at(DEV) is not None
        coord.async_set_updated_data.assert_called_once_with(coord._states)

    def test_push_without_a_usable_value_changes_nothing(self):
        coord = _coordinator()
        state = _add(coord, _light())

        coord._on_openapi_event(DEV, "H7150", "waterFullEvent", [{"value": "full"}])
        coord._on_openapi_event(DEV, "H7150", "waterFullEvent", [{"name": "x"}, "junk"])

        assert state.water_full is None
        coord.async_set_updated_data.assert_not_called()

    def test_clearing_the_alert_creates_state_for_a_device_seen_only_by_push(self):
        coord = _coordinator()
        coord._devices[DEV] = _light()

        coord.clear_water_full(DEV)

        assert coord._states[DEV].water_full is False
        assert coord.water_full_changed_at(DEV) is not None
        coord.async_set_updated_data.assert_called_once()

    def test_restore_keeps_a_live_value_and_backfills_the_timestamp(self):
        coord = _coordinator()
        state = _add(coord, _light())
        stamp = coord_mod.dt_util.utcnow()

        coord.restore_water_full(DEV, True, stamp)
        assert state.water_full is True
        assert coord.water_full_changed_at(DEV) is stamp

        coord.restore_water_full(DEV, False, None)
        assert state.water_full is True  # a live value wins over a restore


# --------------------------------------------------------------------------- #
# multiSync handlers: leak events, button presses, thermo frames
# --------------------------------------------------------------------------- #


def _with_leak_sensor(coord: GoveeCoordinator, *, is_wet: bool = False) -> GoveeLeakSensorState:
    coord._leak_sensors[LEAK] = GoveeLeakSensor(
        device_id=LEAK, name="Kitchen sink", sku="H5058", hub_device_id=HUB, sno=3
    )
    state = GoveeLeakSensorState(is_wet=is_wet)
    coord._leak_states[LEAK] = state
    coord._sno_to_sensor_id[(HUB, 3)] = LEAK
    coord._schedule_bff_leak_poll = MagicMock()
    return state


class TestLeakEvent:
    def test_unknown_slot_is_ignored(self, monkeypatch):
        coord = _coordinator()
        _with_leak_sensor(coord)
        dispatch = MagicMock()
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", dispatch)

        coord._handle_leak_event({"hub_device_id": HUB, "sensor_slot": 9, "is_wet": True})

        dispatch.assert_not_called()
        coord._schedule_bff_leak_poll.assert_not_called()

    def test_known_slot_without_state_is_ignored(self, monkeypatch):
        coord = _coordinator()
        _with_leak_sensor(coord)
        del coord._leak_states[LEAK]
        dispatch = MagicMock()
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", dispatch)

        coord._handle_leak_event({"hub_device_id": HUB, "sensor_slot": 3, "is_wet": True})

        dispatch.assert_not_called()

    def test_wet_event_stamps_the_state_and_wakes_the_entities(self, monkeypatch):
        coord = _coordinator()
        state = _with_leak_sensor(coord)
        dispatch = MagicMock()
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", dispatch)
        before = time.time()

        coord._handle_leak_event({"hub_device_id": HUB, "sensor_slot": 3, "is_wet": True})

        assert state.is_wet is True
        assert state.last_mqtt_wet_at >= before
        assert state.last_wet_time >= int(before * 1000)
        dispatch.assert_called_once_with(coord.hass, f"{DOMAIN}_leak_update")
        coord._schedule_bff_leak_poll.assert_called_once()

    def test_dry_event_after_wet_clears_without_touching_the_wet_stamps(self, monkeypatch):
        coord = _coordinator()
        state = _with_leak_sensor(coord, is_wet=True)
        state.last_wet_time = 1_234
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", MagicMock())

        coord._handle_leak_event({"hub_device_id": HUB, "sensor_slot": 3, "is_wet": False})

        assert state.is_wet is False
        assert state.last_wet_time == 1_234


class TestLeakDualProbeEvent:
    """Upper/lower probe bytes are stored only for H5059 (#224)."""

    def test_h5059_stores_both_probes_on_wet_event(self, monkeypatch):
        coord = _coordinator()
        state = _with_leak_sensor(coord)
        coord._leak_sensors[LEAK] = replace(coord._leak_sensors[LEAK], sku="H5059")
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", MagicMock())

        coord._handle_leak_event(
            {
                "hub_device_id": HUB,
                "sensor_slot": 3,
                "is_wet": True,
                "upper_probe_wet": True,
                "lower_probe_wet": False,
            }
        )

        assert state.upper_probe_wet is True
        assert state.lower_probe_wet is False

    def test_h5058_leaves_probes_none(self, monkeypatch):
        coord = _coordinator()
        state = _with_leak_sensor(coord)
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", MagicMock())

        coord._handle_leak_event(
            {
                "hub_device_id": HUB,
                "sensor_slot": 3,
                "is_wet": True,
                "upper_probe_wet": True,
                "lower_probe_wet": False,
            }
        )

        assert state.upper_probe_wet is None
        assert state.lower_probe_wet is None

    def test_dry_event_clears_both_probes(self, monkeypatch):
        coord = _coordinator()
        state = _with_leak_sensor(coord, is_wet=True)
        coord._leak_sensors[LEAK] = replace(coord._leak_sensors[LEAK], sku="H5059")
        state.upper_probe_wet = True
        state.lower_probe_wet = True
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", MagicMock())

        coord._handle_leak_event({"hub_device_id": HUB, "sensor_slot": 3, "is_wet": False})

        assert state.upper_probe_wet is False
        assert state.lower_probe_wet is False


class TestButtonPress:
    def test_unknown_sensor_is_ignored(self, monkeypatch):
        coord = _coordinator()
        _with_leak_sensor(coord)
        dispatch = MagicMock()
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", dispatch)

        coord._handle_button_press({"device_id": "nope"})

        dispatch.assert_not_called()
        assert coord._pending_button_presses == {}

    def test_presses_queue_up_until_consumed(self, monkeypatch):
        coord = _coordinator()
        _with_leak_sensor(coord)
        dispatch = MagicMock()
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", dispatch)

        coord._handle_button_press({"device_id": LEAK})
        coord._handle_button_press({"device_id": LEAK})

        assert coord._pending_button_presses[LEAK] == 2
        assert dispatch.call_count == 2
        assert coord._schedule_bff_leak_poll.call_count == 2
        assert coord.consume_button_press(LEAK) is True
        assert coord.consume_button_press(LEAK) is True
        assert coord.consume_button_press(LEAK) is False


class TestThermoFrame:
    def test_frame_for_a_slot_whose_device_is_gone_is_dropped(self):
        coord = _coordinator()
        coord._sno_to_thermo_id[(HUB, 0)] = DEV

        coord._handle_thermo_frame({"hub_device_id": HUB, "sensor_slot": 0, "temperature_c": 24.9})

        assert coord._states == {}
        coord.async_set_updated_data.assert_not_called()

    def test_unmapped_slot_is_dropped(self):
        coord = _coordinator()
        _add(coord, _light())

        coord._handle_thermo_frame({"hub_device_id": HUB, "sensor_slot": 4, "temperature_c": 24.9})

        assert coord._states[DEV].sensor_temperature is None


# --------------------------------------------------------------------------- #
# AWS IoT state frames
# --------------------------------------------------------------------------- #


class TestMqttStateDispatch:
    def test_multisync_frames_route_to_their_handlers(self):
        coord = _coordinator()
        coord._handle_leak_event = MagicMock()
        coord._handle_probe_frame = MagicMock()
        coord._handle_thermo_frame = MagicMock()
        coord._handle_button_press = MagicMock()

        leak = {"_leak_event": True, "hub_device_id": HUB, "sensor_slot": 3, "is_wet": True}
        probe = {"_probe_frame": True, "probes": {1: {"core": 40.0}}}
        thermo = {"_thermo_frame": True, "hub_device_id": HUB, "sensor_slot": 0, "temperature_c": 24.9}
        button = {"_button_press": True, "device_id": LEAK}
        coord._on_mqtt_state_update(HUB, leak)
        coord._on_mqtt_state_update(DEV, probe)
        coord._on_mqtt_state_update(HUB, thermo)
        coord._on_mqtt_state_update(LEAK, button)

        coord._handle_leak_event.assert_called_once_with(leak)
        coord._handle_probe_frame.assert_called_once_with(DEV, probe)
        coord._handle_thermo_frame.assert_called_once_with(thermo)
        coord._handle_button_press.assert_called_once_with(button)
        assert coord._states == {}

    def test_unknown_device_is_ignored(self):
        coord = _coordinator()

        coord._on_mqtt_state_update("nope", {"onOff": 1})

        assert coord._states == {}
        coord.async_set_updated_data.assert_not_called()

    def test_first_push_creates_the_state(self):
        coord = _coordinator()
        coord._devices[DEV] = _light()

        coord._on_mqtt_state_update(DEV, {"onOff": 1, "brightness": 40})

        state = coord._states[DEV]
        assert state.power_state is True
        assert state.brightness == 40
        assert state.source == "mqtt"
        assert coord._transport.get(DEV, "mqtt").is_available is True
        coord.async_set_updated_data.assert_called_once_with(coord._states)

    def test_push_restores_a_cloud_offline_device(self):
        coord = _coordinator()
        state = _add(coord, _light())
        state.online = False
        state.power_state = True

        coord._on_mqtt_state_update(DEV, {"onOff": 1})

        assert state.online is True
        coord.async_set_updated_data.assert_called_once()

    def test_unchanged_push_records_health_but_stays_quiet(self):
        coord = _coordinator()
        state = _add(coord, _light())
        state.power_state = True
        state.source = "mqtt"

        coord._on_mqtt_state_update(DEV, {"onOff": 1})

        assert coord._transport.get(DEV, "mqtt").last_success_ts is not None
        coord.async_set_updated_data.assert_not_called()

    def test_ceiling_fan_frames_drive_the_fan_and_skip_garbage_entries(self):
        coord = _coordinator()
        state = _add(coord, _ceiling_fan())

        coord._on_mqtt_state_update(
            DEV,
            {"onOff": 1, "_op_frames": [123, "zz", "aa3101020100000100", "aa3601000000000000"]},
        )

        assert state.ceiling_fan_on is True
        assert state.ceiling_fan_speed == 2
        assert state.ceiling_fan_reverse is True
        assert state.ceiling_fan_swing is True
        # The unit's onOff never speaks for the light; the aa36 frame does.
        assert state.power_state is True
        assert state.toggles == {"mainLightToggle": True, "backgroundLightToggle": False}

    def test_multi_outlet_push_decodes_the_bitmask(self):
        coord = _coordinator()
        state = _add(coord, _multi_outlet_plug())

        coord._on_mqtt_state_update(PLUG, {"onOff": 2})

        assert state.toggles == {"outlet1": False, "outlet2": True, "outlet3": False}
        assert state.power_state is True


class TestMqttGiveUp:
    @pytest.mark.asyncio
    async def test_give_up_surfaces_a_repair_and_tries_a_relogin(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        tasks = _TaskCapture()
        coord._config_entry.async_create_background_task = tasks
        issue = MagicMock()
        monkeypatch.setattr(coord_mod, "async_create_mqtt_issue", issue)
        coord._async_refresh_iot_credentials = AsyncMock(return_value=False)

        coord._on_mqtt_give_up(5, "TLS handshake failed")

        issue.assert_called_once_with(
            coord.hass, coord._config_entry, "5 reconnect attempts failed: TLS handshake failed"
        )
        assert tasks.names == ["govee_mqtt_give_up_issue"]
        await tasks.run_all()
        coord._async_refresh_iot_credentials.assert_awaited_once()


class TestCredentialPersistence:
    def test_refreshed_credentials_replace_the_stored_set_and_clear_the_failure_marker(self):
        coord = _coordinator(
            iot=CREDS,
            data={CONF_EMAIL: "a@b.c", KEY_IOT_LOGIN_FAILED: True, KEY_IOT_CREDENTIALS: {"token": "old"}},
        )

        coord._persist_refreshed_credentials(FRESH)

        coord.hass.config_entries.async_update_entry.assert_called_once()
        args, kwargs = coord.hass.config_entries.async_update_entry.call_args
        assert args == (coord._config_entry,)
        assert kwargs["data"][KEY_IOT_CREDENTIALS]["token"] == "fresh"
        assert KEY_IOT_LOGIN_FAILED not in kwargs["data"]
        assert kwargs["data"][CONF_EMAIL] == "a@b.c"
        # The entry's own data is never mutated in place.
        assert coord._config_entry.data[KEY_IOT_CREDENTIALS] == {"token": "old"}


# --------------------------------------------------------------------------- #
# The whole-poll bookkeeping
# --------------------------------------------------------------------------- #


class TestUpdateData:
    @pytest.mark.asyncio
    async def test_no_devices_returns_early(self):
        coord = _polling_shell(_coordinator())

        assert await coord._async_update_data() is coord._states

        coord._ble_handler.enroll_from_cache.assert_not_called()
        coord._devices_with_all_entities_disabled.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_api_key_during_a_poll_starts_reauth(self):
        coord = _polling_shell(_coordinator())
        _add(coord, _light())
        coord._api_client.get_device_state = AsyncMock(side_effect=GoveeAuthError("key revoked"))

        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()

    @pytest.mark.asyncio
    async def test_successful_poll_lifts_the_rate_limit_backoff(self, monkeypatch):
        coord = _polling_shell(_coordinator())
        _add(coord, _light())
        coord._rate_limited = True
        coord.update_interval = timedelta(seconds=120)
        delete_issue = MagicMock()
        monkeypatch.setattr(coord_mod, "async_delete_rate_limit_issue", delete_issue)
        fresh = GoveeDeviceState.create_empty(DEV)
        fresh.power_state = True
        coord._api_client.get_device_state = AsyncMock(return_value=fresh)

        states = await coord._async_update_data()

        assert states[DEV].power_state is True
        assert coord._rate_limited is False
        assert coord.update_interval == timedelta(seconds=60)
        delete_issue.assert_called_once_with(coord.hass, coord._config_entry)

    @pytest.mark.asyncio
    async def test_lan_refresh_failure_never_fails_the_poll(self):
        coord = _polling_shell(_coordinator())
        _add(coord, _light())
        coord._api_client.get_device_state = AsyncMock(return_value=GoveeDeviceState.create_empty(DEV))
        coord._async_maybe_rescan_lan = AsyncMock(side_effect=RuntimeError("scan blew up"))

        assert await coord._async_update_data() is coord._states

    def test_entity_owner_lookup_survives_an_underscore_in_the_device_id(self):
        coord = _coordinator()
        _add(coord, _light("AB_CD:01"))

        assert coord._owning_device_id("AB_CD:01_light") == "AB_CD:01"
        assert coord._owning_device_id("AB_CD:01") == "AB_CD:01"
        assert coord._owning_device_id("ZZ_light") is None

    @pytest.mark.asyncio
    async def test_bounded_fetch_reports_an_unexpected_error_as_a_failure(self):
        coord = _coordinator()
        device = _light()
        _add(coord, device)
        coord._fetch_device_state = AsyncMock(side_effect=RuntimeError("parser crashed"))

        result = await coord._fetch_device_state_bounded(DEV, device)

        assert isinstance(result, RuntimeError)
        assert coord._transport.get(DEV, "cloud_api").last_failure_reason == "parser crashed"


# --------------------------------------------------------------------------- #
# Per-device fetch: skips, preservation and error mapping
# --------------------------------------------------------------------------- #


class TestFetchDeviceStateSkips:
    @pytest.mark.asyncio
    async def test_group_state_is_refreshed_as_a_new_online_object(self):
        coord = _coordinator()
        existing = _add(coord, _group())
        existing.online = False
        existing.power_state = True
        coord._api_client.get_device_state = AsyncMock()

        result = await coord._fetch_device_state(GROUP, coord._devices[GROUP])

        assert result is not existing
        assert result.online is True
        assert result.power_state is True
        assert coord._states[GROUP] is result
        coord._api_client.get_device_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_without_state_gets_an_empty_one(self):
        coord = _coordinator()
        coord._devices[GROUP] = _group()

        result = await coord._fetch_device_state(GROUP, coord._devices[GROUP])

        assert result.device_id == GROUP

    @pytest.mark.asyncio
    async def test_water_detector_keeps_its_bff_owned_state(self):
        coord = _coordinator()
        existing = _add(coord, _water_detector())
        existing.water_leak = True
        coord._api_client.get_device_state = AsyncMock()

        assert await coord._fetch_device_state(WD, coord._devices[WD]) is existing
        coord._api_client.get_device_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_polled_outlet_bitmask_is_decoded(self):
        coord = _coordinator()
        _add(coord, _multi_outlet_plug())
        fresh = GoveeDeviceState.create_empty(PLUG)
        coord._api_client.get_device_state = AsyncMock(return_value=fresh)
        coord._api_client.last_raw_state = {
            PLUG: {"capabilities": [{"instance": "powerSwitch", "state": {"value": 6}}]}
        }

        result = await coord._fetch_device_state(PLUG, coord._devices[PLUG])

        assert result.toggles == {"outlet1": False, "outlet2": True, "outlet3": True}
        assert result.power_state is True

    def test_raw_power_switch_value_is_none_when_the_capture_lacks_it(self):
        coord = _coordinator()
        coord._api_client.last_raw_state = {
            PLUG: {"capabilities": [{"instance": "brightness", "state": {"value": 5}}]}
        }

        assert coord._raw_power_switch_value(PLUG) is None
        assert coord._raw_power_switch_value("never-polled") is None


class TestFetchDeviceStatePreservation:
    def _poll(self, coord: GoveeCoordinator, fresh: GoveeDeviceState) -> None:
        coord._api_client.get_device_state = AsyncMock(return_value=fresh)

    @pytest.mark.asyncio
    async def test_optimistic_power_survives_the_grace_window(self):
        coord = _coordinator()
        existing = _add(coord, _light())
        existing.power_state = True
        existing.brightness = 55
        existing.source = "optimistic"
        existing.last_optimistic_update = time.monotonic()
        fresh = GoveeDeviceState.create_empty(DEV)
        fresh.power_state = False
        fresh.brightness = 10
        self._poll(coord, fresh)

        result = await coord._fetch_device_state(DEV, coord._devices[DEV])

        assert result.power_state is True
        assert result.brightness == 55
        assert result.source == "optimistic"
        assert result.last_optimistic_update == existing.last_optimistic_update

    @pytest.mark.asyncio
    async def test_scene_memory_and_colour_sentinel_are_carried_across(self):
        coord = _coordinator()
        existing = _add(coord, _light())
        existing.active_scene = "7"
        existing.active_scene_name = "Sunset"
        existing.active_diy_scene = "9"
        existing.color = RGBColor(255, 0, 0)
        existing.last_color = RGBColor(0, 255, 0)
        existing.last_color_temp_kelvin = 3000
        existing.last_scene_id = "7"
        existing.last_scene_name = "Sunset"
        fresh = GoveeDeviceState.create_empty(DEV)
        fresh.power_state = True
        fresh.color = RGBColor(0, 0, 0)
        self._poll(coord, fresh)

        result = await coord._fetch_device_state(DEV, coord._devices[DEV])

        assert result.active_scene == "7"
        assert result.active_scene_name == "Sunset"
        assert result.active_diy_scene == "9"
        assert result.color == RGBColor(255, 0, 0)
        assert result.last_color == RGBColor(0, 255, 0)
        assert result.last_color_temp_kelvin == 3000
        assert result.last_scene_id == "7"
        assert result.last_scene_name == "Sunset"

    @pytest.mark.asyncio
    async def test_heater_sensor_battery_and_event_fields_survive_an_empty_poll(self):
        coord = _coordinator()
        existing = _add(coord, _light())
        existing.heater_temperature = 22
        existing.heater_auto_stop = 1
        existing.device_temperature_unit = "Fahrenheit"
        existing.sensor_temperature = 21.5
        existing.sensor_humidity = 44.0
        existing.battery = 88
        existing.water_full = True
        existing.presence = True
        existing.pump_state = True
        existing.dehumidifier_mode = "pump"
        fresh = GoveeDeviceState.create_empty(DEV)
        fresh.power_state = True
        fresh.brightness = 30
        self._poll(coord, fresh)

        result = await coord._fetch_device_state(DEV, coord._devices[DEV])

        assert result.heater_temperature == 22
        assert result.heater_auto_stop == 1
        assert result.device_temperature_unit == "Fahrenheit"
        assert result.sensor_temperature == 21.5
        assert result.sensor_humidity == 44.0
        assert result.battery == 88
        assert result.water_full is True
        assert result.presence is True
        assert result.pump_state is True
        assert result.dehumidifier_mode == "pump"
        assert result.brightness == 30

    @pytest.mark.asyncio
    async def test_polled_heater_target_and_auto_stop_replace_the_previous_values(self):
        """A target or auto-stop changed in the Govee app shows up on the next poll."""
        coord = _coordinator()
        existing = _add(coord, _light())
        existing.heater_temperature = 22
        existing.heater_auto_stop = 1
        fresh = GoveeDeviceState.create_empty(DEV)
        fresh.power_state = True
        fresh.heater_temperature = 25
        fresh.heater_auto_stop = 0
        self._poll(coord, fresh)

        result = await coord._fetch_device_state(DEV, coord._devices[DEV])

        assert result.heater_temperature == 25
        assert result.heater_auto_stop == 0

    @pytest.mark.asyncio
    async def test_music_mode_and_dreamview_persist_while_the_device_is_on(self):
        coord = _coordinator()
        existing = _add(coord, _light())
        existing.music_mode_enabled = True
        existing.music_mode_value = 3
        existing.music_mode_name = "Rhythm"
        existing.music_sensitivity = 40
        existing.dreamview_enabled = True
        fresh = GoveeDeviceState.create_empty(DEV)
        fresh.power_state = True
        self._poll(coord, fresh)

        result = await coord._fetch_device_state(DEV, coord._devices[DEV])

        assert result.music_mode_enabled is True
        assert result.music_mode_value == 3
        assert result.music_mode_name == "Rhythm"
        assert result.music_sensitivity == 40
        assert result.dreamview_enabled is True

    @pytest.mark.asyncio
    async def test_music_mode_and_dreamview_clear_once_the_device_is_off(self):
        coord = _coordinator()
        existing = _add(coord, _light())
        existing.music_mode_enabled = True
        existing.music_mode_value = 3
        existing.dreamview_enabled = True
        fresh = GoveeDeviceState.create_empty(DEV)
        fresh.power_state = False
        self._poll(coord, fresh)

        result = await coord._fetch_device_state(DEV, coord._devices[DEV])

        assert result.music_mode_enabled is None
        assert result.music_mode_value is None
        assert result.dreamview_enabled is None

    @pytest.mark.asyncio
    async def test_not_found_keeps_the_existing_state_online(self):
        coord = _coordinator()
        existing = _add(coord, _light())
        existing.online = False
        coord._api_client.get_device_state = AsyncMock(side_effect=GoveeDeviceNotFoundError())

        assert await coord._fetch_device_state(DEV, coord._devices[DEV]) is existing
        assert existing.online is True

    @pytest.mark.asyncio
    async def test_not_found_without_state_yields_an_empty_one(self):
        coord = _coordinator()
        coord._devices[DEV] = _light()
        coord._api_client.get_device_state = AsyncMock(side_effect=GoveeDeviceNotFoundError())

        result = await coord._fetch_device_state(DEV, coord._devices[DEV])

        assert result.device_id == DEV

    @pytest.mark.asyncio
    async def test_rate_limit_backs_off_for_the_advertised_window_once(self, monkeypatch):
        coord = _coordinator()
        existing = _add(coord, _light())
        create_issue = MagicMock()
        monkeypatch.setattr(coord_mod, "async_create_rate_limit_issue", create_issue)
        coord._api_client.get_device_state = AsyncMock(side_effect=GoveeRateLimitError(retry_after=45))

        first = await coord._fetch_device_state(DEV, coord._devices[DEV])
        second = await coord._fetch_device_state(DEV, coord._devices[DEV])

        assert first is existing and second is existing
        assert coord._rate_limited is True
        assert coord.update_interval == timedelta(seconds=45)
        create_issue.assert_called_once_with(coord.hass, coord._config_entry, "45 seconds")

    @pytest.mark.asyncio
    async def test_rate_limit_without_a_window_backs_off_two_minutes(self, monkeypatch):
        coord = _coordinator()
        coord._devices[DEV] = _light()
        create_issue = MagicMock()
        monkeypatch.setattr(coord_mod, "async_create_rate_limit_issue", create_issue)
        coord._api_client.get_device_state = AsyncMock(side_effect=GoveeRateLimitError())

        result = await coord._fetch_device_state(DEV, coord._devices[DEV])

        assert result.device_id == DEV
        assert coord.update_interval == timedelta(seconds=120)
        create_issue.assert_called_once_with(coord.hass, coord._config_entry, "unknown")


# --------------------------------------------------------------------------- #
# Shutdown
# --------------------------------------------------------------------------- #


class TestShutdown:
    @pytest.mark.asyncio
    async def test_every_timer_task_and_transport_is_released(self):
        coord = _coordinator()
        coord._api_client.close = AsyncMock()
        bff_unsub = MagicMock()
        coord._bff_poll_unsub = bff_unsub
        bff_task = MagicMock()
        bff_task.done.return_value = False
        coord._bff_poll_task = bff_task
        wd_unsub = MagicMock()
        coord._wd_poll_unsub = wd_unsub
        ble = MagicMock()
        ble.stop = AsyncMock()
        coord._ble_devices = {DEV: ble}
        mqtt = MagicMock()
        mqtt.async_stop = AsyncMock()
        coord._mqtt_client = mqtt
        events = MagicMock()
        events.async_stop = AsyncMock()
        coord._openapi_events_client = events

        await coord.async_shutdown()

        bff_unsub.assert_called_once()
        assert coord._bff_poll_unsub is None
        bff_task.cancel.assert_called_once()
        assert coord._bff_poll_task is None
        wd_unsub.assert_called_once()
        assert coord._wd_poll_unsub is None
        ble.stop.assert_awaited_once()
        assert coord._ble_devices == {}
        mqtt.async_stop.assert_awaited_once()
        assert coord._mqtt_client is None
        events.async_stop.assert_awaited_once()
        assert coord._openapi_events_client is None
        coord._api_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_finished_bff_task_is_left_alone(self):
        coord = _coordinator()
        coord._api_client.close = AsyncMock()
        bff_task = MagicMock()
        bff_task.done.return_value = True
        coord._bff_poll_task = bff_task

        await coord.async_shutdown()

        bff_task.cancel.assert_not_called()
        assert coord._bff_poll_task is bff_task
