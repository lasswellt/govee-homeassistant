"""Coverage tests for the Govee REST client (``api/client.py``).

Complements ``test_api_client.py``. The request paths (``get_devices``,
``get_device_state``, both scene fetches, ``validate_api_key``), every
``_handle_response`` error branch, session ownership and the command-history
bookkeeping are driven through fake aiohttp objects — no network, no Home
Assistant instance and no real ``aiohttp.ClientSession``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from aiohttp_retry import RetryClient
import pytest

import custom_components.govee.api.client as client_mod
from custom_components.govee.api.client import (
    ENDPOINT_DEVICES,
    ENDPOINT_DIY_SCENES,
    ENDPOINT_SCENES,
    ENDPOINT_STATE,
    GoveeApiClient,
    validate_api_key,
)
from custom_components.govee.api.exceptions import (
    GoveeApiError,
    GoveeAuthError,
    GoveeConnectionError,
    GoveeDeviceNotFoundError,
    GoveeRateLimitError,
)
from custom_components.govee.models import PowerCommand

LOGGER_NAME = "custom_components.govee.api.client"
DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
SKU = "H6072"


# ==============================================================================
# Fakes
# ==============================================================================


def _response(status: int = 200, body: Any = None, headers: dict[str, str] | None = None) -> MagicMock:
    """A fake aiohttp response with a cached JSON body.

    ``method``/``closed`` are set so the real ``aiohttp_retry.RetryClient`` can
    drive it too (it checks the method against its retry list and closes the
    response on exit).
    """
    response = MagicMock()
    response.status = status
    response.method = "GET"
    response.closed = True
    response.headers = headers if headers is not None else {}
    body = {"code": 200, "message": "Success"} if body is None else body
    response.json = AsyncMock(return_value=body)
    response.text = AsyncMock(return_value=str(body))
    return response


@asynccontextmanager
async def _entering(item: Any):
    """Yield ``item`` — or raise it on entry, the way aiohttp raises ``ClientError``."""
    if isinstance(item, BaseException):
        raise item
    yield item


def _queued(items: tuple[Any, ...]) -> MagicMock:
    """A ``get``/``post`` stand-in handing out ``items`` in order (the last one repeats)."""
    pending = list(items)

    def _call(*_args: Any, **_kwargs: Any):
        item = pending.pop(0) if len(pending) > 1 else pending[0]
        return _entering(item)

    return MagicMock(side_effect=_call)


def _fake_session() -> MagicMock:
    session = MagicMock(spec=aiohttp.ClientSession)
    session.close = AsyncMock()
    return session


def _client(*items: Any) -> GoveeApiClient:
    """A client over a fake session whose retry client answers with ``items``.

    Each item is a response (entered normally) or an exception (raised on
    entry). The retry client is stubbed rather than real because
    ``aiohttp_retry`` sleeps for real seconds before retrying a transport error.
    """
    client = GoveeApiClient("test_key", session=_fake_session())
    retry_client = MagicMock()
    retry_client.get = _queued(items)
    retry_client.post = _queued(items)
    retry_client.close = AsyncMock()
    client._retry_client = retry_client
    return client


def _device_payload(device_id: str = DEVICE_ID, sku: str = SKU) -> dict[str, Any]:
    return {
        "device": device_id,
        "sku": sku,
        "deviceName": "Lamp",
        "type": "devices.types.light",
        "capabilities": [{"type": "devices.capabilities.on_off", "instance": "powerSwitch", "parameters": {}}],
    }


# ==============================================================================
# _handle_response
# ==============================================================================


class TestHandleResponse:
    """Every ``_handle_response`` branch maps to the documented exception."""

    @staticmethod
    async def _handle(status: int, body: Any = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        client = GoveeApiClient("test_key", session=_fake_session())
        return await client._handle_response(_response(status, body, headers))

    async def test_http_401_is_an_auth_error(self):
        with pytest.raises(GoveeAuthError) as exc_info:
            await self._handle(401, {"message": "Unauthorized"})
        assert exc_info.value.code == 401
        assert "Invalid API key" in str(exc_info.value)

    async def test_http_429_carries_retry_after_when_govee_sends_it(self):
        with pytest.raises(GoveeRateLimitError) as exc_info:
            await self._handle(429, {"message": "Too many requests"}, {"Retry-After": "30"})
        assert exc_info.value.code == 429
        assert exc_info.value.retry_after == 30.0

    async def test_http_429_without_retry_after_leaves_it_unset(self):
        with pytest.raises(GoveeRateLimitError) as exc_info:
            await self._handle(429, {"message": "Too many requests"})
        assert exc_info.value.retry_after is None

    async def test_http_400_devices_not_exist_is_device_not_found(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            with pytest.raises(GoveeDeviceNotFoundError) as exc_info:
                await self._handle(400, {"code": 400, "message": "Devices not exist"})
        assert exc_info.value.code == 400
        assert "Devices not exist" in str(exc_info.value)
        assert any("API 400 error response" in r.getMessage() for r in caplog.records)

    async def test_http_400_other_message_is_a_plain_api_error(self):
        with pytest.raises(GoveeApiError) as exc_info:
            await self._handle(400, {"msg": "parameter invalid"})
        assert not isinstance(exc_info.value, GoveeDeviceNotFoundError)
        assert exc_info.value.code == 400
        assert str(exc_info.value) == "parameter invalid"

    async def test_http_400_without_any_message_falls_back_to_bad_request(self):
        with pytest.raises(GoveeApiError) as exc_info:
            await self._handle(400, {"code": 400})
        assert str(exc_info.value) == "Bad request"

    @pytest.mark.parametrize(
        ("status", "body", "expected_message"),
        [
            (403, {"message": "Forbidden"}, "Forbidden"),
            (404, {"msg": "route missing"}, "route missing"),
            (500, {}, "HTTP 500"),
        ],
    )
    async def test_other_http_errors_keep_status_and_message(self, status, body, expected_message):
        with pytest.raises(GoveeApiError) as exc_info:
            await self._handle(status, body)
        assert type(exc_info.value) is GoveeApiError
        assert exc_info.value.code == status
        assert str(exc_info.value) == expected_message

    async def test_in_body_code_401_is_an_auth_error(self):
        with pytest.raises(GoveeAuthError) as exc_info:
            await self._handle(200, {"code": 401, "message": "api key expired"})
        assert str(exc_info.value) == "api key expired"
        assert exc_info.value.code == 401

    async def test_in_body_non_200_code_is_an_api_error_with_that_code(self):
        with pytest.raises(GoveeApiError) as exc_info:
            await self._handle(200, {"code": 500, "msg": "internal"})
        assert type(exc_info.value) is GoveeApiError
        assert exc_info.value.code == 500
        assert str(exc_info.value) == "internal"

    async def test_in_body_code_without_message_names_the_code(self):
        with pytest.raises(GoveeApiError) as exc_info:
            await self._handle(200, {"code": 1007})
        assert str(exc_info.value) == "API error code 1007"

    async def test_success_returns_the_body_and_reads_rate_limit_headers(self):
        client = GoveeApiClient("test_key", session=_fake_session())
        headers = {"X-RateLimit-Remaining": "42", "X-RateLimit-Limit": "100", "X-RateLimit-Reset": "1700000000"}

        data = await client._handle_response(_response(200, {"code": 200, "data": []}, headers))

        assert data == {"code": 200, "data": []}
        assert client.rate_limit_remaining == 42
        assert client.rate_limit_total == 100
        assert client.rate_limit_reset == 1700000000
        assert client.requests_last_24h == 1

    async def test_unparseable_rate_limit_reset_header_is_ignored(self):
        client = GoveeApiClient("test_key", session=_fake_session())

        await client._handle_response(_response(200, {"code": 200}, {"X-RateLimit-Reset": "soon"}))

        assert client.rate_limit_reset == 0

    async def test_non_json_body_is_reported_with_a_text_snippet(self):
        response = _response(502)
        response.json = AsyncMock(side_effect=aiohttp.ContentTypeError(MagicMock(), ()))
        response.text = AsyncMock(return_value="<html>Bad Gateway</html>")
        client = GoveeApiClient("test_key", session=_fake_session())

        with pytest.raises(GoveeApiError) as exc_info:
            await client._handle_response(response)

        assert "Invalid JSON response" in str(exc_info.value)
        assert "Bad Gateway" in str(exc_info.value)
        assert client.requests_last_24h == 1  # still spent against the quota


# ==============================================================================
# Session ownership and lazy retry-client construction
# ==============================================================================


class TestLifecycle:
    """Session ownership follows who created the session."""

    def test_api_key_property_exposes_the_key(self):
        assert GoveeApiClient("abc-123", session=_fake_session()).api_key == "abc-123"

    async def test_ensure_client_without_a_session_explains_the_fix(self):
        client = GoveeApiClient("test_key")
        with pytest.raises(RuntimeError, match="hass=hass"):
            await client._ensure_client()

    async def test_ensure_client_builds_one_retry_client_and_reuses_it(self):
        client = GoveeApiClient("test_key", session=_fake_session())

        first = await client._ensure_client()
        second = await client._ensure_client()

        assert isinstance(first, RetryClient)
        assert second is first
        assert first.retry_options.attempts == client_mod.RETRY_ATTEMPTS
        assert first.retry_options.statuses == client_mod.RETRY_STATUSES

    async def test_context_manager_leaves_a_borrowed_session_open(self):
        session = _fake_session()

        async with GoveeApiClient("test_key", session=session) as client:
            assert client._retry_client is not None

        assert client._retry_client is None
        assert client._session is session
        session.close.assert_not_awaited()

    async def test_close_on_an_unused_client_is_a_no_op(self):
        client = GoveeApiClient("test_key")

        await client.close()
        await client.close()

        assert client._retry_client is None
        assert client._session is None

    async def test_hass_session_is_borrowed_not_owned(self, monkeypatch):
        session = _fake_session()
        hass = MagicMock()
        getter = MagicMock(return_value=session)
        monkeypatch.setattr(client_mod, "async_get_clientsession", getter)

        client = GoveeApiClient("test_key", hass=hass)

        getter.assert_called_once_with(hass)
        assert client._session is session
        assert client._owns_session is False
        await client.close()
        session.close.assert_not_awaited()

    async def test_explicit_session_wins_over_hass(self, monkeypatch):
        getter = MagicMock()
        monkeypatch.setattr(client_mod, "async_get_clientsession", getter)
        session = _fake_session()

        client = GoveeApiClient("test_key", session=session, hass=MagicMock())

        getter.assert_not_called()
        assert client._session is session
        assert client._owns_session is False


# ==============================================================================
# get_devices
# ==============================================================================


class TestGetDevices:
    async def test_parses_devices_and_keeps_the_raw_payload(self):
        raw = [_device_payload(), _device_payload("11:22:33:44:55:66:77:88", "H6159")]
        client = _client(_response(200, {"code": 200, "data": raw}))

        devices = await client.get_devices()

        assert [d.device_id for d in devices] == [DEVICE_ID, "11:22:33:44:55:66:77:88"]
        assert devices[0].sku == SKU
        assert client.last_raw_devices == raw
        call = client._retry_client.get.call_args
        assert call.args[0] == ENDPOINT_DEVICES
        assert call.kwargs["headers"]["Govee-API-Key"] == "test_key"
        assert call.kwargs["timeout"] is client_mod.REQUEST_TIMEOUT

    async def test_unparseable_device_is_skipped_with_a_warning(self, caplog):
        raw = [{"device": "AA:BB:CC:DD:EE:FF:00:22"}, _device_payload()]  # first one has no SKU
        client = _client(_response(200, {"code": 200, "data": raw}))

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            devices = await client.get_devices()

        assert [d.device_id for d in devices] == [DEVICE_ID]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "Failed to parse device AA:BB:CC:DD:EE:FF:00:22" in warnings[0].getMessage()
        # The raw payload is retained untouched so diagnostics show what Govee sent.
        assert client.last_raw_devices == raw

    async def test_missing_data_yields_no_devices(self):
        client = _client(_response(200, {"code": 200}))

        assert await client.get_devices() == []
        assert client.last_raw_devices == []

    async def test_connection_error_is_wrapped(self):
        client = _client(aiohttp.ClientConnectionError("dns"))

        with pytest.raises(GoveeConnectionError) as exc_info:
            await client.get_devices()

        assert "dns" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, aiohttp.ClientError)

    async def test_a_bare_timeout_is_wrapped_as_a_connection_error(self):
        """Issue #207: aiohttp's total timeout raises a bare ``TimeoutError``, not a ``ClientError``."""
        client = _client(TimeoutError())

        with pytest.raises(GoveeConnectionError, match="TimeoutError") as exc_info:
            await client.get_devices()

        assert isinstance(exc_info.value.__cause__, TimeoutError)

    async def test_auth_error_propagates_without_touching_the_raw_payload(self):
        client = _client(_response(401, {"message": "Unauthorized"}))

        with pytest.raises(GoveeAuthError):
            await client.get_devices()

        assert client.last_raw_devices is None


# ==============================================================================
# get_device_state
# ==============================================================================


class TestGetDeviceState:
    ONLINE = {"type": "devices.capabilities.online", "instance": "online", "state": {"value": True}}
    POWER = {"type": "devices.capabilities.on_off", "instance": "powerSwitch", "state": {"value": 1}}
    EVENT = {"type": "devices.capabilities.event", "instance": "bodyAppearedEvent", "state": {"value": ""}}

    @staticmethod
    def _payload(*caps: dict[str, Any]) -> dict[str, Any]:
        return {"sku": SKU, "device": DEVICE_ID, "capabilities": list(caps)}

    async def test_returns_parsed_state_and_retains_the_raw_payload(self):
        payload = self._payload(self.ONLINE, self.POWER)
        client = _client(_response(200, {"code": 200, "payload": payload}))

        state = await client.get_device_state(DEVICE_ID, SKU)

        assert state.device_id == DEVICE_ID
        assert state.online is True
        assert state.power_state is True
        assert client.last_raw_state == {DEVICE_ID: payload}
        call = client._retry_client.post.call_args
        assert call.args[0] == ENDPOINT_STATE
        assert call.kwargs["json"]["payload"] == {"sku": SKU, "device": DEVICE_ID}
        assert call.kwargs["json"]["requestId"]

    async def test_event_sensor_poll_is_logged_raw_at_debug(self, caplog):
        payload = self._payload(self.ONLINE, self.EVENT)
        client = _client(_response(200, {"code": 200, "payload": payload}))

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            await client.get_device_state(DEVICE_ID, "H5054")

        lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Event-sensor poll")]
        assert len(lines) == 1
        assert DEVICE_ID in lines[0]
        assert "H5054" in lines[0]
        assert "bodyAppearedEvent" in lines[0]

    async def test_non_event_devices_are_not_logged_raw(self, caplog):
        client = _client(_response(200, {"code": 200, "payload": self._payload(self.ONLINE, self.POWER)}))

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            await client.get_device_state(DEVICE_ID, SKU)

        assert not any(r.getMessage().startswith("Event-sensor poll") for r in caplog.records)

    async def test_missing_payload_yields_an_empty_state(self):
        client = _client(_response(200, {"code": 200}))

        state = await client.get_device_state(DEVICE_ID, SKU)

        assert state.device_id == DEVICE_ID
        assert client.last_raw_state[DEVICE_ID] == {}

    async def test_group_device_not_found_propagates(self):
        client = _client(_response(400, {"code": 400, "message": "devices not exist"}))

        with pytest.raises(GoveeDeviceNotFoundError):
            await client.get_device_state("11825917", SKU)

        assert "11825917" not in client.last_raw_state

    async def test_connection_error_is_wrapped(self):
        client = _client(aiohttp.ServerDisconnectedError())

        with pytest.raises(GoveeConnectionError):
            await client.get_device_state(DEVICE_ID, SKU)


# ==============================================================================
# Command-history bookkeeping
# ==============================================================================


class TestCommandRecords:
    def test_peek_is_none_before_any_command(self):
        client = GoveeApiClient("test_key", session=_fake_session())

        assert client.peek_last_command_record() is None
        assert client.recent_commands == []

    def test_peek_returns_the_live_record_so_follow_ups_reach_diagnostics(self):
        client = GoveeApiClient("test_key", session=_fake_session())
        client.record_local_command(DEVICE_ID, SKU, "lan", {"instance": "colorRgb"}, delivered=True)
        client.record_local_command(DEVICE_ID, SKU, "mqtt", {"instance": "brightness"}, delivered=True)

        record = client.peek_last_command_record()
        assert record is not None
        assert record["transport"] == "mqtt"
        record["verify"] = {"matched": False}

        assert client.recent_commands[-1]["verify"] == {"matched": False}
        assert client.recent_commands[-1] is client._recent_commands[-1]

    def test_recent_commands_is_a_snapshot(self):
        client = GoveeApiClient("test_key", session=_fake_session())
        client.record_local_command(DEVICE_ID, SKU, "ble", {"instance": "powerSwitch"}, delivered=True)

        snapshot = client.recent_commands
        snapshot.clear()

        assert len(client.recent_commands) == 1

    def test_undelivered_local_command_without_detail_reads_not_confirmed(self):
        client = GoveeApiClient("test_key", session=_fake_session())

        client.record_local_command(DEVICE_ID, SKU, "lan", {"instance": "colorRgb"}, delivered=False)

        record = client.recent_commands[-1]
        assert record["error"] == "not confirmed"
        assert record["http_status"] is None
        assert record["response"] == {"delivered": False, "detail": None}
        assert record["transport"] == "lan"

    async def test_in_body_rejection_is_recorded_and_logged_at_warning(self, caplog):
        body = {"code": 500, "message": "device offline"}  # HTTP 200, rejected in the body
        client = _client(_response(200, body))

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            with pytest.raises(GoveeApiError) as exc_info:
                await client.control_device(DEVICE_ID, SKU, PowerCommand(power_on=False))

        assert exc_info.value.code == 500
        record = client.peek_last_command_record()
        assert record["http_status"] == 200
        assert record["response"] == body
        assert record["error"] == "device offline"
        assert record["capability"]["value"] == 0
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "HTTP 200" in message
        assert "value=0" in message
        assert "device offline" in message

    async def test_auth_rejection_is_recorded_before_the_error_propagates(self):
        client = _client(_response(401, {"message": "Unauthorized"}))

        with pytest.raises(GoveeAuthError):
            await client.control_device(DEVICE_ID, SKU, PowerCommand(power_on=True))

        record = client.peek_last_command_record()
        assert record["http_status"] == 401
        assert record["error"] == "Invalid API key"


# ==============================================================================
# Scene fetches
# ==============================================================================


DYNAMIC_SCENES = {
    "type": "devices.capabilities.dynamic_scene",
    "instance": "lightScene",
    "parameters": {
        "options": [
            {"name": "Sunrise", "value": {"id": 1, "paramId": 10}},
            {"name": "Sunset", "value": {"id": 2, "paramId": 11}},
        ]
    },
}
DIY_SCENES = {
    "type": "devices.capabilities.dynamic_scene",
    "instance": "diyScene",
    "parameters": {"options": [{"name": "My DIY", "value": 4711}]},
}
NOT_A_SCENE = {
    "type": "devices.capabilities.on_off",
    "instance": "powerSwitch",
    "parameters": {"options": [{"name": "on", "value": 1}]},
}
SCENE_METHODS = ["get_dynamic_scenes", "get_diy_scenes"]


def _scene_body(*caps: dict[str, Any]) -> dict[str, Any]:
    return {"code": 200, "payload": {"sku": SKU, "device": DEVICE_ID, "capabilities": list(caps)}}


class TestSceneFetches:
    @pytest.mark.parametrize(
        ("method_name", "endpoint"),
        [("get_dynamic_scenes", ENDPOINT_SCENES), ("get_diy_scenes", ENDPOINT_DIY_SCENES)],
    )
    async def test_collects_options_from_dynamic_scene_capabilities_only(self, method_name, endpoint):
        client = _client(_response(200, _scene_body(NOT_A_SCENE, DYNAMIC_SCENES, DIY_SCENES)))

        scenes = await getattr(client, method_name)(DEVICE_ID, SKU)

        assert [s["name"] for s in scenes] == ["Sunrise", "Sunset", "My DIY"]
        call = client._retry_client.post.call_args
        assert call.args[0] == endpoint
        assert call.kwargs["json"]["payload"] == {"sku": SKU, "device": DEVICE_ID}

    @pytest.mark.parametrize("method_name", SCENE_METHODS)
    async def test_no_capabilities_means_no_scenes(self, method_name):
        client = _client(_response(200, {"code": 200, "payload": {}}))

        assert await getattr(client, method_name)(DEVICE_ID, SKU) == []

    @pytest.mark.parametrize("method_name", SCENE_METHODS)
    async def test_device_not_found_is_an_empty_list(self, method_name, caplog):
        client = _client(_response(400, {"code": 400, "message": "devices not exist"}))

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            assert await getattr(client, method_name)("11825917", SKU) == []

        assert any("scenes available for device 11825917" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("method_name", SCENE_METHODS)
    async def test_connection_error_is_wrapped(self, method_name):
        client = _client(aiohttp.ClientPayloadError("truncated"))

        with pytest.raises(GoveeConnectionError):
            await getattr(client, method_name)(DEVICE_ID, SKU)

    @pytest.mark.parametrize("method_name", SCENE_METHODS)
    async def test_other_api_errors_are_not_swallowed(self, method_name):
        client = _client(_response(429, {"message": "slow down"}))

        with pytest.raises(GoveeRateLimitError):
            await getattr(client, method_name)(DEVICE_ID, SKU)


# ==============================================================================
# Request accounting edge cases
# ==============================================================================


class TestRequestAccountingEdges:
    """Reads are time-relative: aged history must not count even before it is trimmed."""

    HOUR = 3600
    DAY = 86400

    def _client(self, monkeypatch, now: float):
        clock = {"now": now}
        monkeypatch.setattr(client_mod.time, "time", lambda: clock["now"])
        return GoveeApiClient("test_key", session=_fake_session()), clock

    def test_empty_history_reads_zero_everywhere(self, monkeypatch):
        client, _ = self._client(monkeypatch, 1_000_000.0)

        assert (client.requests_last_24h, client.requests_today, client.requests_per_hour) == (0, 0, 0.0)

    def test_requests_today_reads_zero_after_midnight_without_a_new_request(self, monkeypatch):
        start = 1_000_000.0
        client, clock = self._client(monkeypatch, start)
        client._note_request()
        client._note_request()

        clock["now"] = (int(start // self.DAY) + 1) * self.DAY + 1

        assert client.requests_today == 0
        # The stored counter only rolls over on the next noted request.
        assert client._requests_today == 2

    def test_stale_buckets_are_ignored_when_read_after_a_quiet_day(self, monkeypatch):
        client, clock = self._client(monkeypatch, 1_000_000.0)
        for _ in range(6):
            client._note_request()

        clock["now"] += 24 * self.HOUR

        # Trimming happens on write, so the bucket is still held — but must not be counted.
        assert len(client._request_buckets) == 1
        assert client.requests_last_24h == 0
        assert client.requests_per_hour == 0.0

    def test_partially_aged_history_averages_only_the_window(self, monkeypatch):
        client, clock = self._client(monkeypatch, 1_000_000.0)
        for _ in range(10):
            client._note_request()  # will age out
        clock["now"] += 2 * self.HOUR
        for _ in range(4):
            client._note_request()

        clock["now"] += 22 * self.HOUR  # the first hour is now outside the 24h window

        assert client.requests_last_24h == 4
        assert client.requests_per_hour == 4.0


# ==============================================================================
# validate_api_key
# ==============================================================================


class TestValidateApiKey:
    """The config-flow probe runs the real retry client over HA's shared session."""

    def _ha_session(self, monkeypatch, response: MagicMock) -> MagicMock:
        session = _fake_session()
        session.request = AsyncMock(return_value=response)
        monkeypatch.setattr(client_mod, "async_get_clientsession", lambda hass: session)
        return session

    async def test_valid_key_returns_true_and_leaves_the_ha_session_open(self, monkeypatch):
        session = self._ha_session(monkeypatch, _response(200, {"code": 200, "data": [_device_payload()]}))

        assert await validate_api_key("good-key", hass=MagicMock()) is True

        session.close.assert_not_awaited()
        call = session.request.await_args
        assert call.args[:2] == ("GET", ENDPOINT_DEVICES)
        assert call.kwargs["headers"]["Govee-API-Key"] == "good-key"

    async def test_invalid_key_raises_auth_error(self, monkeypatch):
        session = self._ha_session(monkeypatch, _response(401, {"message": "Unauthorized"}))

        with pytest.raises(GoveeAuthError):
            await validate_api_key("bad-key", hass=MagicMock())

        session.close.assert_not_awaited()

    async def test_without_hass_there_is_no_session_to_use(self):
        with pytest.raises(RuntimeError, match="hass=hass"):
            await validate_api_key("key")
