"""Coordinator setup, discovery, LAN, BFF, leak, water-detector and probe paths.

Behavioural coverage for the parts of ``GoveeCoordinator`` that run at setup
and on the slow account-API timers: device discovery, the MQTT/OpenAPI client
lifecycle, LAN overrides and rescans, BFF leak-sensor / thermo-hygrometer
discovery and refresh, the standalone water-detector poll and the probe
thermometer polling switch. Every network client is replaced by an in-process
fake; no socket is ever opened.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

import custom_components.govee.coordinator as coord_mod
from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.exceptions import GoveeApiError, GoveeAuthError
from custom_components.govee.api.lan_client import LanDevStatus, LanDeviceInfo
from custom_components.govee.const import (
    CONF_EMAIL,
    CONF_LAN_TARGETS,
    CONF_PASSWORD,
    DOMAIN,
    IOT_RELOGIN_MIN_INTERVAL,
)
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState, RGBColor
from custom_components.govee.models.device import (
    CAPABILITY_EVENT,
    CAPABILITY_ON_OFF,
    CAPABILITY_PROPERTY,
    CAPABILITY_RANGE,
    INSTANCE_BODY_APPEARED_EVENT,
    INSTANCE_BRIGHTNESS,
    INSTANCE_POWER,
    INSTANCE_SENSOR_TEMPERATURE,
    GoveeLeakSensor,
    GoveeLeakSensorState,
)
from custom_components.govee.transport_health import TransportHealthTracker

DEV = "AA:BB:CC:DD:EE:FF:00:11"
DEV2 = "AA:BB:CC:DD:EE:FF:00:22"
GROUP = "11825917"
WD = "DABFC0D6A5FE0008E8"
LEAK = "01:32:7A:C4:06:03:0D:0C"
HUB = "09:C2:60:74:F4:64:AB:FA"
THERMO = "AA:BB:CC:DD:EE:FF:51:10"
PROBE = "AA:BB:CC:DD:EE:FF:51:92"

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


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _coordinator(
    *,
    iot: GoveeIotCredentials | None = None,
    options: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    enable_groups: bool = False,
) -> GoveeCoordinator:
    """A real coordinator over a mocked hass/entry, with HA notifications stubbed."""
    entry = MagicMock()
    entry.entry_id = "cov_entry"
    entry.title = "Govee"
    entry.options = options if options is not None else {}
    entry.data = data if data is not None else {}
    coord = GoveeCoordinator(
        hass=MagicMock(),
        config_entry=entry,
        api_client=MagicMock(),
        iot_credentials=iot,
        poll_interval=60,
        enable_groups=enable_groups,
    )
    coord.async_set_updated_data = MagicMock()
    coord.async_update_listeners = MagicMock()
    return coord


def _add(coord: GoveeCoordinator, device: GoveeDevice, state: GoveeDeviceState | None = None) -> GoveeDeviceState:
    coord._devices[device.device_id] = device
    state = state or GoveeDeviceState.create_empty(device.device_id)
    coord._states[device.device_id] = state
    coord._ensure_transport_health(device.device_id)
    return state


def _light(device_id: str, sku: str = "H6072") -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name=f"Light {device_id[-2:]}",
        device_type="devices.types.light",
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(
                type=CAPABILITY_RANGE,
                instance=INSTANCE_BRIGHTNESS,
                parameters={"range": {"min": 0, "max": 100}},
            ),
        ),
    )


def _group(device_id: str = GROUP) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku="GROUP",
        name="All lights",
        device_type="devices.types.group",
        capabilities=(),
        is_group=True,
    )


def _water_detector(device_id: str = WD) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku="H5054",
        name="Washing machine",
        device_type="devices.types.sensor",
        capabilities=(GoveeCapability(type=CAPABILITY_EVENT, instance=INSTANCE_BODY_APPEARED_EVENT, parameters={}),),
    )


def _thermometer(device_id: str = THERMO, sku: str = "H5109") -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name="Garage",
        device_type="devices.types.thermometer",
        capabilities=(GoveeCapability(type=CAPABILITY_PROPERTY, instance=INSTANCE_SENSOR_TEMPERATURE, parameters={}),),
    )


def _info(device_id: str, ip: str, ts: float | None = None) -> LanDeviceInfo:
    return LanDeviceInfo(
        device_id=device_id,
        ip=ip,
        mac=device_id,
        sku="H6072",
        firmware="1.0.0",
        last_correlated_ts=time.monotonic() if ts is None else ts,
    )


def _status(**kw: Any) -> LanDevStatus:
    defaults: dict[str, Any] = dict(on=True, brightness_0_100=80, color=RGBColor(255, 0, 0), color_temp_kelvin=None)
    defaults.update(kw)
    return LanDevStatus(**defaults)


class _AsyncCM:
    """Minimal async context manager yielding a configured inner mock."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def __aenter__(self) -> Any:
        return self._inner

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _patch_auth(monkeypatch: pytest.MonkeyPatch, inner: Any) -> None:
    """Route ``async with GoveeAuthClient(hass=...)`` in the coordinator to ``inner``."""
    monkeypatch.setattr(coord_mod, "GoveeAuthClient", lambda **kw: _AsyncCM(inner))


class _TaskCapture:
    """Stand-in for ``ConfigEntry.async_create_background_task`` that keeps the coroutines.

    The captured coroutines are either awaited by the test (``run_all``) or
    closed (``close_all``) so none is left un-awaited at teardown.
    """

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

    def close_all(self) -> None:
        while self.pending:
            _name, coro = self.pending.pop(0)
            coro.close()


class _TimerCapture:
    """Stand-in for ``async_call_later`` recording delays and cancellations."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, Any]] = []
        self.cancelled = 0

    def __call__(self, hass: Any, delay: float, action: Any) -> Any:
        self.calls.append((delay, action))

        def _cancel() -> None:
            self.cancelled += 1

        return _cancel


class _FakeLanClient:
    """Socket-free stand-in for ``GoveeLanClient`` used by the setup path."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.start_calls = 0
        self.stop_calls = 0

    async def async_start(self, interface_ips: list[str]) -> None:
        self.start_calls += 1

    async def async_stop(self) -> None:
        self.stop_calls += 1

    async def async_scan(self, **kwargs: Any) -> Any:
        # Rescans run on the client's own socket; delegate to the patched scan.
        return await coord_mod.async_scan_lan_devices(**kwargs)


def _patch_lan(monkeypatch: pytest.MonkeyPatch, *, scan: Any, client: Any, seen: dict[str, Any] | None = None) -> None:
    """Patch the LAN helpers the coordinator imported so no UDP is ever sent."""

    async def _ifaces(hass: Any) -> list[str]:
        return []

    async def _broadcasts(hass: Any) -> list[str]:
        return []

    async def _scan(*, interface_ips: Any, extra_targets: Any, broadcast_targets: Any = None) -> Any:
        if seen is not None:
            seen["extra_targets"] = extra_targets
            seen["scans"] = seen.get("scans", 0) + 1
        if isinstance(scan, Exception):
            raise scan
        return scan

    monkeypatch.setattr(coord_mod, "async_get_lan_interface_ips", _ifaces)
    monkeypatch.setattr(coord_mod, "async_get_lan_broadcast_addresses", _broadcasts)
    monkeypatch.setattr(coord_mod, "async_scan_lan_devices", _scan)
    monkeypatch.setattr(coord_mod, "GoveeLanClient", lambda callback: client)


# --------------------------------------------------------------------------- #
# Read-only accessors used by diagnostics and entities
# --------------------------------------------------------------------------- #


class TestAccessors:
    def test_api_client_and_scene_cache_counts(self):
        coord = _coordinator()

        assert coord.api_client is coord._api_client
        assert coord.scene_cache_count == 0
        assert coord.diy_scene_cache_count == 0

        coord._scene_cache._scene_cache[DEV] = (time.time(), [])
        coord._scene_cache._diy_scene_cache[DEV] = (time.time(), [])
        coord._scene_cache._diy_scene_cache[DEV2] = (time.time(), [])

        assert coord.scene_cache_count == 1
        assert coord.diy_scene_cache_count == 2

    def test_rate_limit_accessors_read_the_api_client(self):
        """The rate-limit sensor is disabled by default, so read the accessors directly."""
        coord = _coordinator()
        client = coord._api_client
        client.rate_limit_remaining = 93
        client.rate_limit_total = 100
        client.rate_limit_reset = 42
        client.requests_last_24h = 1200
        client.requests_today = 340
        client.requests_per_hour = 50.5

        assert coord.api_rate_limit_remaining == 93
        assert coord.api_rate_limit_total == 100
        assert coord.api_rate_limit_reset == 42
        assert coord.api_requests_last_24h == 1200
        assert coord.api_requests_today == 340
        assert coord.api_requests_per_hour == 50.5

    def test_mqtt_last_message_ts_follows_the_client(self):
        coord = _coordinator()
        assert coord.mqtt_last_message_ts is None

        stamp = datetime.now(timezone.utc)
        coord._mqtt_client = MagicMock(last_message_ts=stamp)

        assert coord.mqtt_last_message_ts is stamp

    def test_ble_availability_reflects_enrolled_devices(self):
        coord = _coordinator()
        assert coord.is_ble_available(DEV) is False

        coord._ble_devices[DEV] = MagicMock()

        assert coord.is_ble_available(DEV) is True

    def test_state_and_bff_diagnostic_views(self):
        coord = _coordinator()
        coord._bff_device_census = [{"sku": "H5058"}]
        coord._bff_response_skeleton = {"devices": "list"}
        coord._bff_device_values = [{"battery": 90}]

        assert coord.states is coord._states
        assert coord.leak_states is coord._leak_states
        assert coord.bff_device_census == [{"sku": "H5058"}]
        assert coord.bff_response_skeleton == {"devices": "list"}
        assert coord.bff_device_values == [{"battery": 90}]

    def test_lan_counts(self):
        coord = _coordinator()
        coord._lan_devices = {DEV: _info(DEV, "10.0.0.5")}
        coord._lan_unmatched = [{"device": "x"}, {"device": "y"}]

        assert coord.lan_active_count == 1
        assert coord.lan_unmatched_count == 2

    def test_iot_topic_and_gateway_views(self):
        coord = _coordinator()
        assert coord.has_iot_credentials is False
        assert coord.openapi_events_client is None
        assert coord.get_device(DEV) is None

        coord._iot_credentials = CREDS
        coord._device_topics = {DEV: "GD/dev"}
        coord._gateway_routes = {DEV2: {"device": HUB, "sku": "H5044", "topic": "GD/hub"}}
        _add(coord, _light(DEV))

        assert coord.has_iot_credentials is True
        assert coord.device_topic_count == 1
        assert coord.gateway_route_count == 1
        assert coord.gateway_route(DEV2) == {"device": HUB, "sku": "H5044", "topic": "GD/hub"}
        assert coord.get_device(DEV).sku == "H6072"

    def test_consume_button_press_counts_down_to_removal(self):
        coord = _coordinator()
        assert coord.consume_button_press(LEAK) is False

        coord._pending_button_presses[LEAK] = 2

        assert coord.consume_button_press(LEAK) is True
        assert coord._pending_button_presses[LEAK] == 1
        assert coord.consume_button_press(LEAK) is True
        assert LEAK not in coord._pending_button_presses
        assert coord.consume_button_press(LEAK) is False

    def test_setup_ble_subscriptions_delegates_to_the_handler(self):
        coord = _coordinator()
        coord._ble_handler = MagicMock()
        coord._ble_handler.setup_subscriptions.return_value = ["unsub"]

        assert coord.setup_ble_subscriptions() == ["unsub"]

    def test_optimistic_grace_needs_a_timestamp(self):
        coord = _coordinator()
        state = GoveeDeviceState.create_empty(DEV)
        state.source = "optimistic"
        state.last_optimistic_update = None

        assert coord._in_optimistic_grace(state) is False

        state.last_optimistic_update = time.monotonic()
        assert coord._in_optimistic_grace(state) is True


# --------------------------------------------------------------------------- #
# Device discovery
# --------------------------------------------------------------------------- #


class TestDiscoverDevices:
    @pytest.mark.asyncio
    async def test_group_devices_are_skipped_unless_enabled(self):
        coord = _coordinator(enable_groups=False)
        coord._api_client.get_devices = AsyncMock(return_value=[_light(DEV), _group()])

        await coord._discover_devices()

        assert set(coord._devices) == {DEV}
        assert set(coord._states) == {DEV}
        assert coord._transport.get(DEV, "cloud_api") is not None

    @pytest.mark.asyncio
    async def test_group_devices_are_kept_when_enabled(self):
        coord = _coordinator(enable_groups=True)
        coord._api_client.get_devices = AsyncMock(return_value=[_light(DEV), _group()])

        await coord._discover_devices()

        assert set(coord._devices) == {DEV, GROUP}
        assert coord._states[GROUP].device_id == GROUP

    @pytest.mark.asyncio
    async def test_stale_scene_cache_entries_are_dropped(self):
        coord = _coordinator()
        coord._scene_cache._scene_cache["gone"] = (time.time(), [])
        coord._api_client.get_devices = AsyncMock(return_value=[_light(DEV)])

        await coord._discover_devices()

        assert coord.scene_cache_count == 0

    @pytest.mark.asyncio
    async def test_invalid_api_key_starts_reauth(self):
        coord = _coordinator()
        coord._api_client.get_devices = AsyncMock(side_effect=GoveeAuthError("bad key"))

        with pytest.raises(ConfigEntryAuthFailed):
            await coord._discover_devices()

    @pytest.mark.asyncio
    async def test_api_error_becomes_update_failed(self):
        coord = _coordinator()
        coord._api_client.get_devices = AsyncMock(side_effect=GoveeApiError("boom", code=500))

        with pytest.raises(UpdateFailed, match="Failed to discover devices"):
            await coord._discover_devices()


class TestAsyncSetupWaterDetectors:
    def _stubbed(self, coord: GoveeCoordinator) -> GoveeCoordinator:
        for name in (
            "_discover_devices",
            "_start_mqtt",
            "_fetch_device_topics",
            "_start_openapi_events",
            "_discover_leak_sensors",
            "_discover_bff_thermometers",
            "_async_setup_lan",
            "_poll_water_detectors",
        ):
            setattr(coord, name, AsyncMock())
        coord._schedule_status_poll = MagicMock()
        coord._schedule_water_detector_poll = MagicMock()
        return coord

    @pytest.mark.asyncio
    async def test_detectors_polled_and_timer_armed_with_account_login(self):
        coord = self._stubbed(_coordinator(iot=CREDS))
        _add(coord, _water_detector())

        await coord._async_setup()

        coord._poll_water_detectors.assert_awaited_once()
        coord._schedule_water_detector_poll.assert_called_once()
        coord._schedule_status_poll.assert_called_once()

    @pytest.mark.asyncio
    async def test_detector_poll_skipped_without_account_login(self):
        coord = self._stubbed(_coordinator(iot=None))
        _add(coord, _water_detector())

        await coord._async_setup()

        coord._poll_water_detectors.assert_not_awaited()
        coord._schedule_water_detector_poll.assert_not_called()
        coord._start_mqtt.assert_not_awaited()


# --------------------------------------------------------------------------- #
# MQTT / OpenAPI client lifecycle
# --------------------------------------------------------------------------- #


class _FakeIotClient:
    def __init__(self, *, available: bool = True, start_error: Exception | None = None) -> None:
        self.available = available
        self.start_error = start_error
        self.started = 0
        self.connected = False

    async def async_start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started += 1


class TestStartMqtt:
    def _patch(self, monkeypatch: pytest.MonkeyPatch, client: _FakeIotClient) -> dict[str, Any]:
        seen: dict[str, Any] = {}

        def _factory(**kwargs: Any) -> _FakeIotClient:
            seen.update(kwargs)
            return client

        monkeypatch.setattr(coord_mod, "GoveeAwsIotClient", _factory)
        return seen

    @pytest.mark.asyncio
    async def test_no_credentials_creates_no_client(self, monkeypatch):
        coord = _coordinator(iot=None)
        seen = self._patch(monkeypatch, _FakeIotClient())

        await coord._start_mqtt()

        assert coord._mqtt_client is None
        assert seen == {}

    @pytest.mark.asyncio
    async def test_client_is_wired_to_the_coordinator_callbacks_and_started(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        client = _FakeIotClient()
        seen = self._patch(monkeypatch, client)

        await coord._start_mqtt()

        assert coord._mqtt_client is client
        assert client.started == 1
        assert seen["credentials"] is CREDS
        assert seen["on_state_update"] == coord._on_mqtt_state_update
        assert seen["on_give_up"] == coord._on_mqtt_give_up
        assert seen["on_connected"] == coord._on_mqtt_connected
        assert seen["on_disconnected"] == coord._on_mqtt_disconnected

    @pytest.mark.asyncio
    async def test_start_failure_raises_a_repair_instead_of_failing_setup(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        self._patch(monkeypatch, _FakeIotClient(start_error=RuntimeError("cert rejected")))
        issue = MagicMock()
        monkeypatch.setattr(coord_mod, "async_create_mqtt_issue", issue)

        await coord._start_mqtt()

        issue.assert_called_once_with(coord.hass, coord._config_entry, "cert rejected")

    @pytest.mark.asyncio
    async def test_missing_mqtt_library_is_logged_not_fatal(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        client = _FakeIotClient(available=False)
        self._patch(monkeypatch, client)
        issue = MagicMock()
        monkeypatch.setattr(coord_mod, "async_create_mqtt_issue", issue)

        await coord._start_mqtt()

        assert client.started == 0
        issue.assert_not_called()


class TestStartOpenApiEvents:
    @pytest.mark.asyncio
    async def test_client_is_created_with_the_api_key_and_started(self, monkeypatch):
        coord = _coordinator()
        coord._api_client.api_key = "key-123"
        client = MagicMock()
        client.async_start = AsyncMock()
        seen: dict[str, Any] = {}

        def _factory(**kwargs: Any) -> Any:
            seen.update(kwargs)
            return client

        monkeypatch.setattr(coord_mod, "GoveeOpenApiEventClient", _factory)

        await coord._start_openapi_events()

        assert coord.openapi_events_client is client
        assert seen["api_key"] == "key-123"
        assert seen["on_event"] == coord._on_openapi_event
        client.async_start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_broker_failure_leaves_the_channel_disabled(self, monkeypatch):
        coord = _coordinator()
        client = MagicMock()
        client.async_start = AsyncMock(side_effect=OSError("broker down"))
        monkeypatch.setattr(coord_mod, "GoveeOpenApiEventClient", lambda **kw: client)

        await coord._start_openapi_events()

        assert coord._openapi_events_client is None


# --------------------------------------------------------------------------- #
# LAN overrides, rescans and correlation merging
# --------------------------------------------------------------------------- #


class TestLanOverrides:
    def test_overrides_bind_known_devices_and_mark_write_only(self):
        coord = _coordinator(options={CONF_LAN_TARGETS: f"{DEV}=10.0.0.9! {DEV2}=10.0.0.10 ZZ:ZZ=10.0.0.11"})
        _add(coord, _light(DEV))
        _add(coord, _light(DEV2, sku="H6159"))

        resolved = coord._resolve_lan_overrides()

        assert set(resolved) == {DEV, DEV2}
        assert resolved[DEV].ip == "10.0.0.9"
        assert resolved[DEV].sku == "H6072"
        assert resolved[DEV2].sku == "H6159"
        assert coord._lan_write_only == {DEV}

    def test_removing_the_bang_unmarks_write_only_on_the_next_resolve(self):
        coord = _coordinator(options={CONF_LAN_TARGETS: f"{DEV}=10.0.0.9!"})
        _add(coord, _light(DEV))
        coord._resolve_lan_overrides()
        assert coord._lan_write_only == {DEV}

        coord._config_entry.options = {CONF_LAN_TARGETS: f"{DEV}=10.0.0.9"}
        coord._resolve_lan_overrides()

        assert coord._lan_write_only == set()

    @pytest.mark.asyncio
    async def test_setup_binds_the_override_when_the_scan_finds_nothing(self, monkeypatch):
        coord = _coordinator(options={CONF_LAN_TARGETS: f"{DEV}=10.0.0.9!"})
        _add(coord, _light(DEV))
        client = _FakeLanClient()
        _patch_lan(monkeypatch, scan=[], client=client)

        await coord._async_setup_lan()

        assert coord._lan_client is client
        assert coord._lan_devices[DEV].ip == "10.0.0.9"
        assert coord._lan_write_only == {DEV}
        assert client.stop_calls == 0

    @pytest.mark.asyncio
    async def test_a_fresh_scan_match_beats_a_manual_override(self, monkeypatch):
        coord = _coordinator(options={CONF_LAN_TARGETS: f"{DEV}=10.0.0.9"})
        _add(coord, _light(DEV))
        scan = [{"device": DEV, "ip": "10.0.0.5", "sku": "H6072", "wifiVersionSoft": "1.0.0"}]
        _patch_lan(monkeypatch, scan=scan, client=_FakeLanClient())

        await coord._async_setup_lan()

        assert coord._lan_devices[DEV].ip == "10.0.0.5"

    @pytest.mark.asyncio
    async def test_read_poll_skips_write_only_devices(self):
        coord = _coordinator()
        _add(coord, _light(DEV))
        coord._lan_devices = {DEV: _info(DEV, "10.0.0.9")}
        coord._lan_write_only = {DEV}
        client = MagicMock()
        client.async_read_batch = AsyncMock(return_value={})
        coord._lan_client = client

        await coord._refresh_lan_reads()

        client.async_read_batch.assert_not_awaited()
        assert coord._lan_read_misses == {}
        assert DEV in coord._lan_devices


class TestLanRescan:
    def _armed(self, options: dict[str, Any] | None = None) -> GoveeCoordinator:
        coord = _coordinator(options=options)
        _add(coord, _light(DEV))
        coord._lan_client = _FakeLanClient()
        coord._request_lan_rescan()
        return coord

    @pytest.mark.asyncio
    async def test_invalid_targets_option_falls_back_to_no_extras(self, monkeypatch):
        coord = self._armed({CONF_LAN_TARGETS: "not-an-ip"})
        seen: dict[str, Any] = {}
        scan = [{"device": DEV, "ip": "10.0.0.5", "sku": "H6072"}]
        _patch_lan(monkeypatch, scan=scan, client=_FakeLanClient(), seen=seen)

        await coord._async_maybe_rescan_lan()

        assert seen["extra_targets"] == []
        assert coord._lan_devices[DEV].ip == "10.0.0.5"

    @pytest.mark.asyncio
    async def test_scan_bind_failure_keeps_the_existing_map(self, monkeypatch):
        coord = self._armed()
        coord._lan_devices = {DEV: _info(DEV, "10.0.0.5")}
        _patch_lan(monkeypatch, scan=OSError("port busy"), client=_FakeLanClient())

        await coord._async_maybe_rescan_lan()

        assert coord._lan_devices[DEV].ip == "10.0.0.5"
        # The throttle was stamped so the failure is not retried on every poll.
        assert coord._last_lan_rescan != float("-inf")

    @pytest.mark.asyncio
    async def test_rescan_reapplies_manual_overrides(self, monkeypatch):
        coord = self._armed({CONF_LAN_TARGETS: f"{DEV}=10.0.0.9!"})
        _patch_lan(monkeypatch, scan=[], client=_FakeLanClient())

        await coord._async_maybe_rescan_lan()

        assert coord._lan_devices[DEV].ip == "10.0.0.9"
        assert coord._lan_write_only == {DEV}
        assert coord._lan_read_misses[DEV] == 0

    def test_merge_replaces_a_device_the_fresh_scan_found_again(self):
        coord = _coordinator()
        _add(coord, _light(DEV))
        coord._lan_devices = {DEV: _info(DEV, "10.0.0.5")}
        coord._lan_read_misses[DEV] = 2

        coord._merge_lan_correlation({DEV: _info(DEV, "10.0.0.6")}, [{"device": "??"}])

        assert coord._lan_devices[DEV].ip == "10.0.0.6"
        assert coord._lan_read_misses[DEV] == 0
        assert coord.lan_unmatched_count == 1


class TestRescanUsesTheClientSocket:
    @pytest.mark.asyncio
    async def test_rescan_never_binds_a_second_4002_socket(self, monkeypatch):
        """A second :4002 socket would split unicast replies with the client's on Linux."""
        coord = _coordinator()
        _add(coord, _light(DEV))
        client = MagicMock()
        client.async_scan = AsyncMock(return_value=[{"device": DEV, "ip": "10.0.0.7", "sku": "H6072"}])
        coord._lan_client = client
        coord._request_lan_rescan()

        async def _no_second_socket(**_kwargs: Any) -> Any:
            raise AssertionError("rescan opened its own :4002 socket")

        _patch_lan(monkeypatch, scan=[], client=client)
        monkeypatch.setattr(coord_mod, "async_scan_lan_devices", _no_second_socket)

        await coord._async_maybe_rescan_lan()

        client.async_scan.assert_awaited_once()
        assert coord._lan_devices[DEV].ip == "10.0.0.7"


class TestLanSetupRetry:
    """LAN that found nothing at setup must come back without a reload."""

    @pytest.mark.asyncio
    async def test_empty_first_scan_is_retried_on_a_later_poll(self, monkeypatch):
        coord = _coordinator()
        _add(coord, _light(DEV))
        _patch_lan(monkeypatch, scan=[], client=_FakeLanClient())
        await coord._async_setup_lan()
        assert coord._lan_client is None

        # The lights' Wi-Fi comes up after Home Assistant did.
        client = _FakeLanClient()
        _patch_lan(monkeypatch, scan=[{"device": DEV, "ip": "10.0.0.5", "sku": "H6072"}], client=client)
        coord._last_lan_rescan = float("-inf")  # throttle elapsed
        await coord._async_maybe_rescan_lan()

        assert coord._lan_client is client
        assert coord._lan_devices[DEV].ip == "10.0.0.5"

    @pytest.mark.asyncio
    async def test_retry_is_throttled(self, monkeypatch):
        coord = _coordinator()
        _add(coord, _light(DEV))
        seen: dict[str, Any] = {}
        _patch_lan(monkeypatch, scan=[], client=_FakeLanClient(), seen=seen)
        await coord._async_setup_lan()
        assert seen["scans"] == 1

        # The failed setup counts as the latest attempt: polls right after it
        # must not rescan.
        await coord._async_maybe_rescan_lan()
        await coord._async_maybe_rescan_lan()
        assert seen["scans"] == 1

        coord._last_lan_rescan = float("-inf")
        await coord._async_maybe_rescan_lan()
        await coord._async_maybe_rescan_lan()
        assert seen["scans"] == 2

    @pytest.mark.asyncio
    async def test_empty_retries_back_off_to_an_hour_and_success_resets(self, monkeypatch):
        """Cloud-only installs must not scan every five minutes forever."""
        coord = _coordinator()
        _add(coord, _light(DEV))
        seen: dict[str, Any] = {}
        _patch_lan(monkeypatch, scan=[], client=_FakeLanClient(), seen=seen)
        clock = {"now": 10_000.0}
        monkeypatch.setattr(coord_mod.time, "monotonic", lambda: clock["now"])

        await coord._async_setup_lan()  # empty first attempt at t=10000
        gaps = []
        last = clock["now"]
        for _ in range(7):
            # Advance minute by minute until the next retry actually scans.
            before = seen["scans"]
            while seen["scans"] == before:
                clock["now"] += 60
                await coord._async_maybe_rescan_lan()
            gaps.append(clock["now"] - last)
            last = clock["now"]

        assert gaps == [300, 600, 1200, 2400, 3600, 3600, 3600]

        # Lights finally answer: the client starts and the interval resets.
        client = _FakeLanClient()
        _patch_lan(monkeypatch, scan=[{"device": DEV, "ip": "10.0.0.5", "sku": "H6072"}], client=client, seen=seen)
        clock["now"] += 3600
        await coord._async_maybe_rescan_lan()
        assert coord._lan_client is client
        assert coord._lan_retry_interval == 300

    @pytest.mark.asyncio
    async def test_off_never_retries(self, monkeypatch):
        coord = _coordinator(options={CONF_LAN_TARGETS: "off"})
        _add(coord, _light(DEV))
        seen: dict[str, Any] = {}
        _patch_lan(monkeypatch, scan=[], client=_FakeLanClient(), seen=seen)
        coord._last_lan_rescan = float("-inf")

        await coord._async_maybe_rescan_lan()

        assert "scans" not in seen
        assert coord._lan_client is None

    @pytest.mark.asyncio
    async def test_retry_failure_never_raises_into_the_poll(self, monkeypatch):
        coord = _coordinator()
        _add(coord, _light(DEV))
        _patch_lan(monkeypatch, scan=[], client=_FakeLanClient())

        async def _boom(hass: Any) -> list[str]:
            raise RuntimeError("adapter lookup failed")

        monkeypatch.setattr(coord_mod, "async_get_lan_interface_ips", _boom)
        coord._last_lan_rescan = float("-inf")

        await coord._async_maybe_rescan_lan()

        assert coord._lan_client is None

    @pytest.mark.asyncio
    async def test_retry_after_unload_opens_no_socket(self, monkeypatch):
        coord = _coordinator()
        _add(coord, _light(DEV))
        client = _FakeLanClient()
        _patch_lan(monkeypatch, scan=[{"device": DEV, "ip": "10.0.0.5", "sku": "H6072"}], client=client)
        coord._lan_closed = True

        await coord._async_setup_lan(retry=True)

        assert coord._lan_client is None
        assert client.start_calls == 0

    @pytest.mark.asyncio
    async def test_unload_during_bind_releases_the_socket(self, monkeypatch):
        coord = _coordinator()
        _add(coord, _light(DEV))

        class _UnloadWhileStarting(_FakeLanClient):
            async def async_start(self, interface_ips: list[str]) -> None:
                await super().async_start(interface_ips)
                coord._lan_closed = True

        client = _UnloadWhileStarting()
        _patch_lan(monkeypatch, scan=[{"device": DEV, "ip": "10.0.0.5", "sku": "H6072"}], client=client)

        await coord._async_setup_lan(retry=True)

        assert coord._lan_client is None
        assert client.stop_calls == 1


# --------------------------------------------------------------------------- #
# Device topics and the BFF token retry
# --------------------------------------------------------------------------- #


class TestFetchDeviceTopics:
    @pytest.mark.asyncio
    async def test_no_credentials_never_opens_a_client(self, monkeypatch):
        coord = _coordinator(iot=None)
        factory = MagicMock()
        monkeypatch.setattr(coord_mod, "GoveeAuthClient", factory)

        await coord._fetch_device_topics()

        factory.assert_not_called()
        assert coord._device_topics == {}

    @pytest.mark.asyncio
    async def test_topics_and_gateway_routes_are_stored(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        inner = MagicMock()
        inner.fetch_device_topics = AsyncMock(return_value={DEV: "GD/dev"})
        inner.gateway_routes = MagicMock(return_value={DEV2: {"device": HUB, "sku": "H5044", "topic": "GD/hub"}})
        _patch_auth(monkeypatch, inner)

        await coord._fetch_device_topics()

        inner.fetch_device_topics.assert_awaited_once_with("tok")
        assert coord._device_topics == {DEV: "GD/dev"}
        assert coord.gateway_route(DEV2)["topic"] == "GD/hub"

    @pytest.mark.asyncio
    async def test_api_error_is_swallowed(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        inner = MagicMock()
        inner.fetch_device_topics = AsyncMock(side_effect=GoveeApiError("nope", code=500))
        _patch_auth(monkeypatch, inner)

        await coord._fetch_device_topics()

        assert coord._device_topics == {}

    @pytest.mark.asyncio
    async def test_unexpected_error_is_swallowed(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        inner = MagicMock()
        inner.fetch_device_topics = AsyncMock(side_effect=RuntimeError("weird"))
        _patch_auth(monkeypatch, inner)

        await coord._fetch_device_topics()

        assert coord._device_topics == {}

    @pytest.mark.asyncio
    async def test_bff_call_gives_up_when_the_relogin_fails(self, monkeypatch):
        coord = _coordinator(iot=CREDS, data={CONF_EMAIL: "a@b.c", CONF_PASSWORD: "pw"})
        coord._last_iot_relogin = -IOT_RELOGIN_MIN_INTERVAL * 10
        inner = MagicMock()
        inner.login = AsyncMock(side_effect=OSError("login endpoint down"))
        _patch_auth(monkeypatch, inner)
        op = AsyncMock(side_effect=GoveeAuthError("token expired"))

        assert await coord._async_bff_call(op, "topics") is None

        assert op.await_count == 1
        inner.login.assert_awaited_once()
        assert coord._iot_credentials is CREDS


# --------------------------------------------------------------------------- #
# BFF leak-sensor discovery and the 5-minute poll
# --------------------------------------------------------------------------- #


def _leak_payload(*, battery: int = 77, last_wet_time: int | None = None, extra: list[dict[str, Any]] | None = None):
    sensor_data = [
        {
            "device_id": LEAK,
            "name": "Kitchen sink",
            "sku": "H5058",
            "hub_device_id": HUB,
            "sno": 3,
            "hw_version": "1.0",
            "sw_version": "2.0",
            "battery": battery,
            "online": True,
            "gateway_online": False,
            "last_wet_time": last_wet_time,
            "read": False,
        }
    ] + list(extra or [])
    hub_data = {HUB: {"sku": "H5043", "name": "Hub"}}
    return sensor_data, hub_data, {}


class TestDiscoverLeakSensors:
    @pytest.mark.asyncio
    async def test_sensors_states_and_slot_map_are_built_from_the_bff_list(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        coord._schedule_bff_poll = MagicMock()
        inner = MagicMock()
        inner.fetch_bff_leak_sensors = AsyncMock(return_value=_leak_payload(last_wet_time=1_700_000_000_000))
        inner.bff_device_census = MagicMock(return_value=[{"sku": "H5058"}])
        inner.bff_response_skeleton = MagicMock(return_value={"devices": "list"})
        inner.bff_device_values = MagicMock(return_value=[{"battery": 77}])
        _patch_auth(monkeypatch, inner)

        await coord._discover_leak_sensors()

        sensor = coord.leak_sensors[LEAK]
        assert sensor == GoveeLeakSensor(
            device_id=LEAK,
            name="Kitchen sink",
            sku="H5058",
            hub_device_id=HUB,
            sno=3,
            hw_version="1.0",
            sw_version="2.0",
        )
        state = coord.leak_states[LEAK]
        assert state.battery == 77
        assert state.gateway_online is False
        assert state.last_wet_time == 1_700_000_000_000
        assert state.read is False
        assert coord._sno_to_sensor_id[(HUB, 3)] == LEAK
        assert coord._leak_hubs == {HUB: {"sku": "H5043", "name": "Hub"}}
        assert coord.hub_device_ids == {HUB}
        assert coord.bff_device_census == [{"sku": "H5058"}]
        assert coord.bff_response_skeleton == {"devices": "list"}
        assert coord.bff_device_values == [{"battery": 77}]
        coord._schedule_bff_poll.assert_called_once()

    @pytest.mark.asyncio
    async def test_nothing_fetched_leaves_discovery_empty(self):
        coord = _coordinator(iot=CREDS)
        coord._schedule_bff_poll = MagicMock()
        coord._async_bff_call = AsyncMock(return_value=None)

        await coord._discover_leak_sensors()

        assert coord.leak_sensors == {}
        coord._schedule_bff_poll.assert_not_called()

    @pytest.mark.asyncio
    async def test_bff_failure_is_non_fatal(self):
        coord = _coordinator(iot=CREDS)
        coord._async_bff_call = AsyncMock(side_effect=RuntimeError("BFF exploded"))

        await coord._discover_leak_sensors()

        assert coord.leak_sensors == {}


class TestApplyBffThermoBattery:
    def test_device_without_state_is_skipped(self):
        coord = _coordinator()
        coord._devices[THERMO] = _thermometer()

        coord._apply_bff_thermo_battery({THERMO: {"battery": 55}})

        assert THERMO not in coord._states

    def test_unparseable_battery_leaves_state_untouched(self):
        coord = _coordinator()
        _add(coord, _thermometer())

        coord._apply_bff_thermo_battery({THERMO: {"battery": "n/a"}})

        assert coord._states[THERMO].battery is None

    def test_mains_powered_sku_never_gets_a_battery(self):
        coord = _coordinator()
        _add(coord, _thermometer(sku="H5106"))

        coord._apply_bff_thermo_battery({THERMO: {"battery": 100}})

        assert coord._states[THERMO].battery is None


class TestBffPollScheduling:
    def test_rescheduling_cancels_the_pending_timer(self, monkeypatch):
        timers = _TimerCapture()
        monkeypatch.setattr(coord_mod, "async_call_later", timers)
        coord = _coordinator()

        coord._schedule_bff_poll()
        coord._schedule_bff_poll()

        assert timers.cancelled == 1
        assert [delay for delay, _ in timers.calls] == [coord_mod.BFF_POLL_INTERVAL] * 2

    @pytest.mark.asyncio
    async def test_poll_callback_runs_both_polls_then_rearms(self):
        coord = _coordinator()
        coord._poll_bff_leak_state = AsyncMock()
        coord._refresh_bff_thermometers = AsyncMock()
        coord._schedule_bff_poll = MagicMock()

        await coord._bff_poll_callback()

        coord._poll_bff_leak_state.assert_awaited_once()
        coord._refresh_bff_thermometers.assert_awaited_once()
        coord._schedule_bff_poll.assert_called_once()


class TestPollBffLeakState:
    def _with_sensor(self, coord: GoveeCoordinator, *, is_wet: bool = False) -> GoveeLeakSensorState:
        coord._leak_sensors[LEAK] = GoveeLeakSensor(
            device_id=LEAK, name="Kitchen sink", sku="H5058", hub_device_id=HUB, sno=3
        )
        state = GoveeLeakSensorState(is_wet=is_wet, battery=90, last_wet_time=1_000)
        coord._leak_states[LEAK] = state
        return state

    @pytest.mark.asyncio
    async def test_no_credentials_is_a_noop(self):
        coord = _coordinator(iot=None)
        self._with_sensor(coord)
        coord._async_bff_call = AsyncMock()

        await coord._poll_bff_leak_state()

        coord._async_bff_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_nothing_to_poll_is_a_noop(self):
        coord = _coordinator(iot=CREDS)
        coord._async_bff_call = AsyncMock()

        await coord._poll_bff_leak_state()

        coord._async_bff_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bff_failure_keeps_the_last_state(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        state = self._with_sensor(coord)
        coord._async_bff_call = AsyncMock(side_effect=RuntimeError("BFF down"))
        dispatch = MagicMock()
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", dispatch)

        await coord._poll_bff_leak_state()

        assert state.battery == 90
        dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_fetch_is_ignored(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        self._with_sensor(coord)
        coord._async_bff_call = AsyncMock(return_value=None)
        dispatch = MagicMock()
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", dispatch)

        await coord._poll_bff_leak_state()

        dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_hub_sensor_schedules_one_reload_and_skips_the_update(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        state = self._with_sensor(coord)
        new_sensor = {
            "device_id": "01:32:7A:C4:06:03:0D:0D",
            "name": "New",
            "sku": "H5058",
            "hub_device_id": HUB,
            "sno": 4,
        }
        coord._async_bff_call = AsyncMock(return_value=_leak_payload(battery=10, extra=[new_sensor]))
        dispatch = MagicMock()
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", dispatch)

        await coord._poll_bff_leak_state()
        await coord._poll_bff_leak_state()

        coord.hass.config_entries.async_schedule_reload.assert_called_once_with("cov_entry")
        assert coord._bff_reload_scheduled is True
        # The first poll returned before touching state; the second one (reload
        # already pending) applied the BFF data instead of queueing another.
        assert state.battery == 10
        dispatch.assert_called_once_with(coord.hass, f"{DOMAIN}_leak_update")

    @pytest.mark.asyncio
    async def test_known_sensor_state_is_refreshed_and_listeners_told(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        state = self._with_sensor(coord)
        unknown = {
            "device_id": "01:32:7A:C4:06:03:0D:0E",
            "name": "Ghost",
            "sku": "H5058",
            "hub_device_id": HUB,
            "sno": 5,
        }
        coord._bff_reload_scheduled = True  # a reload is already queued for the ghost
        coord._async_bff_call = AsyncMock(return_value=_leak_payload(battery=42, last_wet_time=2_000, extra=[unknown]))
        dispatch = MagicMock()
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", dispatch)

        await coord._poll_bff_leak_state()

        assert state.battery == 42
        assert state.gateway_online is False
        assert state.last_wet_time == 2_000
        assert state.read is False
        assert state.is_wet is False  # the wet event is far outside the poll window
        dispatch.assert_called_once_with(coord.hass, f"{DOMAIN}_leak_update")

    @pytest.mark.asyncio
    async def test_recent_unreported_leak_is_forced_wet(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        state = self._with_sensor(coord)
        state.last_mqtt_wet_at = 0.0  # MQTT never reported this one
        recent_ms = int(time.time() * 1000) - 60_000
        coord._async_bff_call = AsyncMock(return_value=_leak_payload(last_wet_time=recent_ms))
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", MagicMock())

        await coord._poll_bff_leak_state()

        assert state.is_wet is True
        assert state.last_wet_time == recent_ms

    @pytest.mark.asyncio
    async def test_older_bff_wet_time_never_rewinds_the_mqtt_one(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        state = self._with_sensor(coord)
        state.last_wet_time = 5_000
        coord._async_bff_call = AsyncMock(return_value=_leak_payload(last_wet_time=2_000))
        monkeypatch.setattr(coord_mod, "async_dispatcher_send", MagicMock())

        await coord._poll_bff_leak_state()

        assert state.last_wet_time == 5_000


# --------------------------------------------------------------------------- #
# BFF thermo-hygrometer discovery and refresh
# --------------------------------------------------------------------------- #


def _bff_sensor(device_id: str, **fields: Any) -> dict[str, Any]:
    sensor: dict[str, Any] = {"device_id": device_id, "name": "Sensor", "sku": "H5301", "online": True}
    sensor.update(fields)
    return sensor


class TestDiscoverBffThermometers:
    @pytest.mark.asyncio
    async def test_nothing_fetched_discovers_nothing(self):
        coord = _coordinator(iot=CREDS)
        coord._schedule_bff_poll = MagicMock()
        coord._async_bff_call = AsyncMock(return_value=None)

        await coord._discover_bff_thermometers()

        assert coord._bff_thermometer_ids == set()
        coord._schedule_bff_poll.assert_not_called()

    @pytest.mark.asyncio
    async def test_entries_without_a_device_id_are_skipped(self):
        coord = _coordinator(iot=CREDS)
        coord._schedule_bff_poll = MagicMock()
        coord._async_bff_call = AsyncMock(return_value=[_bff_sensor("", temperature=20.0)])

        await coord._discover_bff_thermometers()

        assert coord._devices == {}
        coord._schedule_bff_poll.assert_not_called()

    @pytest.mark.asyncio
    async def test_second_probe_reading_is_taken_over_for_a_developer_device(self):
        coord = _coordinator(iot=CREDS)
        coord._schedule_bff_poll = MagicMock()
        _add(coord, _thermometer(sku="H5112"))
        coord._async_bff_call = AsyncMock(
            return_value=[
                _bff_sensor(THERMO, sku="H5112", temperature_2=-18.5, hub_device_id=HUB, sno=2, fah_open=False)
            ]
        )

        await coord._discover_bff_thermometers()

        assert THERMO in coord._bff_thermometer_ids
        assert coord._states[THERMO].sensor_temperature_2 == -18.5
        assert coord._states[THERMO].sensor_temperature is None
        assert coord._sno_to_thermo_id[(HUB, 2)] == THERMO
        assert coord.account_temperature_unit(THERMO) == "celsius"
        coord._schedule_bff_poll.assert_called_once()

    @pytest.mark.asyncio
    async def test_probe_thermometer_is_synthesised_without_a_reading(self):
        coord = _coordinator(iot=CREDS)
        coord._schedule_bff_poll = MagicMock()
        coord._async_bff_call = AsyncMock(return_value=[_bff_sensor(PROBE, sku="H5192", name="Grill", online=False)])

        await coord._discover_bff_thermometers()

        device = coord._devices[PROBE]
        assert device.is_probe_thermometer
        assert device.name == "Grill"
        assert PROBE in coord._bff_thermometer_ids
        assert coord._states[PROBE].online is False
        assert coord._states[PROBE].sensor_temperature is None
        assert coord._transport.get(PROBE, "mqtt") is not None
        coord._schedule_bff_poll.assert_called_once()

    @pytest.mark.asyncio
    async def test_bff_failure_is_non_fatal(self):
        coord = _coordinator(iot=CREDS)
        coord._async_bff_call = AsyncMock(side_effect=RuntimeError("BFF exploded"))

        await coord._discover_bff_thermometers()

        assert coord._bff_thermometer_ids == set()


class TestRefreshBffThermometers:
    def _owned(self, coord: GoveeCoordinator, **state_fields: Any) -> GoveeDeviceState:
        state = _add(coord, GoveeDevice.synthetic_thermometer(THERMO, "H5301", "Office"))
        for key, value in state_fields.items():
            setattr(state, key, value)
        coord._bff_thermometer_ids.add(THERMO)
        return state

    @pytest.mark.asyncio
    async def test_no_credentials_is_a_noop(self):
        coord = _coordinator(iot=None)
        self._owned(coord)
        coord._async_bff_call = AsyncMock()

        await coord._refresh_bff_thermometers()

        coord._async_bff_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_nothing_owned_is_a_noop(self):
        coord = _coordinator(iot=CREDS)
        coord._async_bff_call = AsyncMock()

        await coord._refresh_bff_thermometers()

        coord._async_bff_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bff_failure_keeps_the_last_reading(self):
        coord = _coordinator(iot=CREDS)
        state = self._owned(coord, sensor_temperature=21.0)
        coord._async_bff_call = AsyncMock(side_effect=RuntimeError("BFF down"))

        await coord._refresh_bff_thermometers()

        assert coord._states[THERMO] is state
        assert state.sensor_temperature == 21.0

    @pytest.mark.asyncio
    async def test_empty_fetch_changes_nothing(self):
        coord = _coordinator(iot=CREDS)
        self._owned(coord, sensor_temperature=21.0)
        coord._async_bff_call = AsyncMock(return_value=None)

        await coord._refresh_bff_thermometers()

        assert coord._states[THERMO].sensor_temperature == 21.0
        coord.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_reading_for_a_device_without_state_is_ignored(self):
        coord = _coordinator(iot=CREDS)
        coord._bff_thermometer_ids.add(THERMO)
        coord._async_bff_call = AsyncMock(return_value=[_bff_sensor(THERMO, temperature=20.0)])

        await coord._refresh_bff_thermometers()

        assert THERMO not in coord._states
        coord.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_cloud_copy_older_than_the_applied_frame_is_ignored(self):
        coord = _coordinator(iot=CREDS)
        self._owned(coord, sensor_temperature=24.9, sensor_temperature_2=1.5, sensor_humidity=40.0, battery=80)
        coord._thermo_frame_ts[THERMO] = 1_700_000_000
        stale = _bff_sensor(THERMO, temperature=23.0, humidity=55.0, battery=79, last_time=1_699_999_000_000)
        coord._async_bff_call = AsyncMock(return_value=[stale])

        await coord._refresh_bff_thermometers()

        state = coord._states[THERMO]
        assert state.sensor_temperature == 24.9
        assert state.sensor_temperature_2 == 1.5
        assert state.sensor_humidity == 40.0
        assert state.battery == 79  # battery is not frame-timestamped, so the BFF value still lands
        coord.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_second_probe_appearing_later_schedules_one_reload(self):
        coord = _coordinator(iot=CREDS)
        self._owned(coord, sensor_temperature=20.0)
        coord._async_bff_call = AsyncMock(return_value=[_bff_sensor(THERMO, temperature=20.5, temperature_2=4.5)])

        await coord._refresh_bff_thermometers()
        await coord._refresh_bff_thermometers()

        assert coord._states[THERMO].sensor_temperature_2 == 4.5
        coord.hass.config_entries.async_schedule_reload.assert_called_once_with("cov_entry")
        assert coord._probe2_reload_scheduled is True
        assert coord._transport.get(THERMO, "cloud_api").is_available is True


# --------------------------------------------------------------------------- #
# Hub registration
# --------------------------------------------------------------------------- #


class TestRegisterHubs:
    def _with_registry(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        device_reg = MagicMock()
        monkeypatch.setattr(coord_mod.dr, "async_get", lambda _hass: device_reg)
        return device_reg

    def test_leak_hubs_prefer_the_bff_sku_and_name(self, monkeypatch):
        coord = _coordinator()
        device_reg = self._with_registry(monkeypatch)
        coord._leak_sensors[LEAK] = GoveeLeakSensor(device_id=LEAK, name="Sink", sku="H5059", hub_device_id=HUB, sno=1)
        coord._leak_hubs = {HUB: {"sku": "H5044", "name": "Garage hub"}}

        coord.register_leak_hubs()

        kwargs = device_reg.async_get_or_create.call_args.kwargs
        assert kwargs["identifiers"] == {(DOMAIN, HUB)}
        assert kwargs["model"] == "H5044"
        assert kwargs["name"] == "Garage hub"
        assert kwargs["config_entry_id"] == "cov_entry"

    def test_h5058_children_imply_an_h5043_hub(self, monkeypatch):
        coord = _coordinator()
        device_reg = self._with_registry(monkeypatch)
        coord._leak_sensors[LEAK] = GoveeLeakSensor(device_id=LEAK, name="Sink", sku="H5058", hub_device_id=HUB, sno=1)

        coord.register_leak_hubs()

        kwargs = device_reg.async_get_or_create.call_args.kwargs
        assert kwargs["model"] == "H5043"
        assert kwargs["name"] == "Govee Leak Sensor Hub"

    def test_unknown_children_leave_the_model_blank(self, monkeypatch):
        coord = _coordinator()
        device_reg = self._with_registry(monkeypatch)
        coord._leak_sensors[LEAK] = GoveeLeakSensor(device_id=LEAK, name="Sink", sku="H5059", hub_device_id=HUB, sno=1)
        coord._leak_sensors[DEV2] = GoveeLeakSensor(
            device_id=DEV2, name="Orphan", sku="H5059", hub_device_id="", sno=2
        )

        coord.register_leak_hubs()

        assert device_reg.async_get_or_create.call_count == 1
        assert device_reg.async_get_or_create.call_args.kwargs["model"] is None

    def test_thermo_hub_with_an_empty_id_is_skipped(self, monkeypatch):
        coord = _coordinator()
        device_reg = self._with_registry(monkeypatch)
        coord._bff_thermo_hubs = {"": {"sku": "H5044"}, HUB: {"sku": ""}}

        coord.register_thermo_hubs()

        assert device_reg.async_get_or_create.call_count == 1
        kwargs = device_reg.async_get_or_create.call_args.kwargs
        assert kwargs["identifiers"] == {(DOMAIN, HUB)}
        assert kwargs["model"] is None
        assert kwargs["name"] == "Govee Gateway"


# --------------------------------------------------------------------------- #
# Standalone water detectors (H5054)
# --------------------------------------------------------------------------- #


class TestWaterDetectorPolling:
    def test_rescheduling_cancels_the_pending_timer(self, monkeypatch):
        timers = _TimerCapture()
        monkeypatch.setattr(coord_mod, "async_call_later", timers)
        coord = _coordinator()

        coord._schedule_water_detector_poll()
        coord._schedule_water_detector_poll()

        assert timers.cancelled == 1
        assert timers.calls[-1][0] == coord._water_detector_poll_interval

    @pytest.mark.asyncio
    async def test_poll_callback_polls_then_rearms(self):
        coord = _coordinator()
        coord._poll_water_detectors = AsyncMock()
        coord._schedule_water_detector_poll = MagicMock()

        await coord._water_detector_poll_callback()

        coord._poll_water_detectors.assert_awaited_once()
        coord._schedule_water_detector_poll.assert_called_once()

    def _inner(self, states: dict[str, Any], *, warning: Any = False) -> MagicMock:
        inner = MagicMock()
        inner.fetch_water_detector_states = AsyncMock(return_value=states)
        if isinstance(warning, Exception):
            inner.fetch_leak_warning = AsyncMock(side_effect=warning)
        else:
            inner.fetch_leak_warning = AsyncMock(return_value=warning)
        return inner

    @pytest.mark.asyncio
    async def test_detector_without_state_is_skipped(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        coord._devices[WD] = _water_detector()
        inner = self._inner({WD: {"online": True, "last_time": 5}})
        _patch_auth(monkeypatch, inner)

        await coord._poll_water_detectors()

        inner.fetch_leak_warning.assert_not_awaited()
        coord.async_update_listeners.assert_not_called()

    @pytest.mark.asyncio
    async def test_warn_message_failure_keeps_the_last_leak_state(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        state = _add(coord, _water_detector())
        state.water_leak = True
        inner = self._inner({WD: {"online": True, "gateway_online": True, "last_time": 5}}, warning=OSError("timeout"))
        _patch_auth(monkeypatch, inner)

        await coord._poll_water_detectors()

        assert state.water_leak is True
        assert coord._water_leak_last_time[WD] == 5
        coord.async_update_listeners.assert_not_called()

    @pytest.mark.asyncio
    async def test_online_and_battery_changes_notify_listeners(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        state = _add(coord, _water_detector())
        inner = self._inner(
            {WD: {"online": False, "gateway_online": True, "battery": 64, "last_time": 9}}, warning=True
        )
        _patch_auth(monkeypatch, inner)

        await coord._poll_water_detectors()

        assert state.online is False
        assert state.battery == 64
        assert state.water_leak is True
        assert state.source == "api"
        inner.fetch_leak_warning.assert_awaited_once_with("tok", WD, "H5054")
        coord.async_update_listeners.assert_called_once()

    @pytest.mark.asyncio
    async def test_device_list_failure_is_swallowed(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        state = _add(coord, _water_detector())
        inner = MagicMock()
        inner.fetch_water_detector_states = AsyncMock(side_effect=RuntimeError("BFF down"))
        _patch_auth(monkeypatch, inner)

        await coord._poll_water_detectors()

        assert state.water_leak is None
        coord.async_update_listeners.assert_not_called()


# --------------------------------------------------------------------------- #
# Probe thermometers (H5192): polling switch and limits write
# --------------------------------------------------------------------------- #


class TestProbePolling:
    def _with_probe(self, coord: GoveeCoordinator) -> GoveeCoordinator:
        _add(coord, GoveeDevice.synthetic_probe_thermometer(device_id=PROBE, sku="H5192", name="Grill"))
        return coord

    @pytest.mark.asyncio
    async def test_arming_fires_an_immediate_read_and_starts_the_timer(self, monkeypatch):
        timers = _TimerCapture()
        monkeypatch.setattr(coord_mod, "async_call_later", timers)
        coord = self._with_probe(_coordinator())
        tasks = _TaskCapture()
        coord._config_entry.async_create_background_task = tasks
        coord._poll_probe_thermometers = AsyncMock()

        coord.set_probe_polling(PROBE, True)

        assert coord.is_probe_polling(PROBE)
        assert tasks.names == ["govee_probe_poll_now"]
        await tasks.run_all()
        coord._poll_probe_thermometers.assert_awaited_once()
        assert len(timers.calls) == 1

    def test_rescheduling_cancels_the_pending_timer(self, monkeypatch):
        timers = _TimerCapture()
        monkeypatch.setattr(coord_mod, "async_call_later", timers)
        coord = _coordinator()

        coord._schedule_probe_poll()
        coord._schedule_probe_poll()

        assert timers.cancelled == 1

    @pytest.mark.asyncio
    async def test_poll_callback_rearms_only_while_a_device_is_armed(self):
        coord = _coordinator()
        coord._poll_probe_thermometers = AsyncMock()
        coord._schedule_probe_poll = MagicMock()

        await coord._probe_poll_callback()
        coord._schedule_probe_poll.assert_not_called()

        coord._probe_polling_enabled.add(PROBE)
        await coord._probe_poll_callback()
        coord._schedule_probe_poll.assert_called_once()
        assert coord._poll_probe_thermometers.await_count == 2

    @pytest.mark.asyncio
    async def test_poll_sends_a_reading_and_a_limits_read_per_probe(self):
        coord = self._with_probe(_coordinator())
        coord._probe_polling_enabled = {PROBE, "gone"}
        coord._mqtt_client = MagicMock(connected=True)
        coord._ble_manager = MagicMock()
        coord._ble_manager.async_send_ble_packet = AsyncMock(return_value=True)

        await coord._poll_probe_thermometers()

        calls = coord._ble_manager.async_send_ble_packet.await_args_list
        assert len(calls) == 4
        assert {call.args[:2] for call in calls} == {(PROBE, "H5192")}

    @pytest.mark.asyncio
    async def test_h5194_polls_all_four_probes_h5192_still_only_two(self):
        """H5194 is the 4-probe sibling of the H5192 (issue #197): each SKU
        must be queried for only the probes it actually has, not the union.
        """
        coord = self._with_probe(_coordinator())
        _add(coord, GoveeDevice.synthetic_probe_thermometer(device_id="grill4", sku="H5194", name="Big Grill"))
        coord._probe_polling_enabled = {PROBE, "grill4"}
        coord._mqtt_client = MagicMock(connected=True)
        coord._ble_manager = MagicMock()
        coord._ble_manager.async_send_ble_packet = AsyncMock(return_value=True)

        await coord._poll_probe_thermometers()

        calls = coord._ble_manager.async_send_ble_packet.await_args_list
        assert sum(1 for call in calls if call.args[:2] == (PROBE, "H5192")) == 4
        assert sum(1 for call in calls if call.args[:2] == ("grill4", "H5194")) == 8

    @pytest.mark.asyncio
    async def test_poll_is_a_noop_when_nothing_is_armed(self):
        coord = self._with_probe(_coordinator())
        coord._mqtt_client = MagicMock(connected=True)
        coord._ble_manager = MagicMock()
        coord._ble_manager.async_send_ble_packet = AsyncMock(return_value=True)

        await coord._poll_probe_thermometers()

        coord._ble_manager.async_send_ble_packet.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_limits_write_refuses_an_unknown_device(self):
        coord = _coordinator()
        coord._ble_manager = MagicMock()
        coord._ble_manager.async_send_ble_packet = AsyncMock(return_value=True)

        assert await coord.async_set_probe_limits("nope", 1, core_max=70.0) is False
        coord._ble_manager.async_send_ble_packet.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_write_skips_the_readback(self):
        coord = self._with_probe(_coordinator())
        coord._ble_manager = MagicMock()
        coord._ble_manager.async_send_ble_packet = AsyncMock(return_value=False)

        assert await coord.async_set_probe_limits(PROBE, 1, core_max=70.0) is False
        assert coord._ble_manager.async_send_ble_packet.await_count == 1


# --------------------------------------------------------------------------- #
# Transport-health bookkeeping helpers
# --------------------------------------------------------------------------- #


class TestTransportHelpers:
    def test_tracker_is_the_single_source_for_health(self):
        coord = _coordinator()
        assert isinstance(coord._transport, TransportHealthTracker)
        coord._ensure_transport_health(DEV)

        coord._record_transport_success(DEV, "lan")
        coord._record_transport_send(DEV, "mqtt")
        coord._record_transport_failure(DEV, "ble", "gatt_error")

        assert coord.get_transport_health(DEV, "lan").is_available is True
        assert coord.get_transport_health(DEV, "mqtt").last_send_ts is not None
        assert coord.get_transport_health(DEV, "ble").last_failure_reason == "gatt_error"
        assert coord.device_data_last_updated(DEV) is not None
        assert coord.device_last_command_sent(DEV) is not None

    def test_unsolicited_push_from_a_fresh_correlation_is_applied(self):
        coord = _coordinator()
        state = _add(coord, _light(DEV))
        state.power_state = False
        coord._lan_devices = {DEV: _info(DEV, "10.0.0.5")}

        coord._on_lan_dev_status("10.0.0.5", _status(on=True, brightness_0_100=30))

        assert state.power_state is True
        assert state.brightness == 30
        assert state.source == "lan"
        coord.async_set_updated_data.assert_called_once_with(coord._states)
