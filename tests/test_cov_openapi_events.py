"""Lifecycle and connection-loop tests for the OpenAPI event client.

``test_openapi_events.py`` covers event fan-out; this file drives the
start/stop lifecycle and ``_connection_loop`` against a scripted ``aiomqtt``
stand-in (no sockets, no real sleeps, no executor threads) and pins down the
remaining ``_handle_message`` guards and the diagnostics ring buffer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.api import openapi_events as openapi_mod
from custom_components.govee.api.openapi_events import GoveeOpenApiEventClient

LOGGER_NAME = "custom_components.govee.api.openapi_events"
API_KEY = "test-key"
DEVICE_ID = "AA:BB:CC:DD:EE:FF:71:50"


def _event_cap(instance: str = "waterFullEvent", state=None) -> dict:
    return {
        "type": "devices.capabilities.event",
        "instance": instance,
        "state": [{"name": "waterFull", "value": 1}] if state is None else state,
    }


def _push(**over) -> dict:
    payload = {"sku": "H7150", "device": DEVICE_ID, "deviceName": "Dehumidifier", "capabilities": [_event_cap()]}
    payload.update(over)
    return payload


def _message(payload) -> MagicMock:
    message = MagicMock()
    message.payload = json.dumps(payload).encode() if isinstance(payload, (dict, list)) else payload
    return message


class ScriptedMessages:
    """Async iterator that yields scripted messages, then ends, raises or parks forever."""

    def __init__(self, items=(), *, error: Exception | None = None, block: bool = False) -> None:
        self._items = list(items)
        self._error = error
        self._block = block

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._items:
            return self._items.pop(0)
        if self._block:
            await asyncio.Event().wait()  # parked until the owning task is cancelled
        if self._error is not None:
            raise self._error
        raise StopAsyncIteration


class FakeAiomqtt:
    """Stand-in for the aiomqtt module: one scripted session per ``Client()``."""

    class MqttError(Exception):
        pass

    def __init__(self, sessions) -> None:
        self.sessions = list(sessions)
        self.clients: list[MagicMock] = []
        self.on_subscribe = None  # hook to observe client state at SUBACK time

    def Client(self, **kwargs):  # noqa: N802 - mimics aiomqtt.Client
        spec = self.sessions.pop(0) if self.sessions else {"drop_error": self.MqttError("no more sessions")}
        client = MagicMock()
        client.kwargs = kwargs
        connect_error = spec.get("connect_error")
        granted = spec.get("granted", (1,))

        async def aenter():
            if connect_error is not None:
                raise connect_error
            return client

        async def subscribe(topic, qos=0):
            if self.on_subscribe is not None:
                self.on_subscribe()
            return granted

        client.__aenter__ = AsyncMock(side_effect=aenter)
        client.__aexit__ = AsyncMock(return_value=False)
        client.subscribe = AsyncMock(side_effect=subscribe)
        client.messages = ScriptedMessages(
            spec.get("messages", ()),
            error=spec.get("drop_error"),
            block=spec.get("block", False),
        )
        self.clients.append(client)
        return client


class _InlineLoop:
    """Event-loop wrapper whose ``run_in_executor`` runs the callable inline.

    Keeps the TLS-context step single-threaded so the loop's progress is
    deterministic under ``asyncio.sleep(0)`` polling.
    """

    def __init__(self, loop) -> None:
        self._loop = loop

    def run_in_executor(self, executor, func, *args):
        future = self._loop.create_future()
        future.set_result(func(*args))
        return future

    def __getattr__(self, name):
        return getattr(self._loop, name)


class _ModuleProxy:
    """Forward attribute access to a real module except for explicit overrides."""

    def __init__(self, module, **overrides) -> None:
        self._module = module
        self._overrides = overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._module, name)


class Harness:
    """An event client wired to a FakeAiomqtt with instant sleeps and stop conditions."""

    def __init__(
        self,
        monkeypatch,
        sessions,
        *,
        stop_after_sleeps: int | None = None,
        stop_after_events: int | None = None,
        event_error: Exception | None = None,
    ) -> None:
        self.fake = FakeAiomqtt(sessions)
        self.sleeps: list[float] = []
        self.events: list[tuple] = []
        self.connected_during_event: list[bool] = []
        self.stop_after_sleeps = stop_after_sleeps
        self.stop_after_events = stop_after_events
        self.event_error = event_error
        self.client = GoveeOpenApiEventClient(api_key=API_KEY, on_event=self._on_event)
        self.client._create_ssl_context_sync = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(openapi_mod, "aiomqtt", self.fake)
        monkeypatch.setattr(openapi_mod, "HAS_AIOMQTT", True)
        monkeypatch.setattr(
            openapi_mod,
            "asyncio",
            _ModuleProxy(
                asyncio,
                sleep=self._sleep,
                get_running_loop=lambda: _InlineLoop(asyncio.get_running_loop()),
            ),
        )

    def _on_event(self, *args) -> None:
        self.events.append(args)
        self.connected_during_event.append(self.client.connected)
        if self.stop_after_events is not None and len(self.events) >= self.stop_after_events:
            self.client._running = False
        if self.event_error is not None:
            raise self.event_error

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.stop_after_sleeps is not None and len(self.sleeps) >= self.stop_after_sleeps:
            self.client._running = False

    async def run(self) -> None:
        self.client._running = True
        await self.client._connection_loop()


@pytest.fixture
def harness(monkeypatch):
    return lambda sessions, **kwargs: Harness(monkeypatch, sessions, **kwargs)


async def _until(predicate, *, attempts: int = 500) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")


# ==============================================================================
# Lifecycle
# ==============================================================================


class TestLifecycle:
    def test_fresh_client_is_idle(self):
        client = GoveeOpenApiEventClient(api_key=API_KEY, on_event=MagicMock())
        assert client.available is True  # aiomqtt is installed in the test env
        assert client.connected is False
        assert client.recent_events == []

    @pytest.mark.asyncio
    async def test_start_without_aiomqtt_warns_and_stays_idle(self, monkeypatch, caplog):
        monkeypatch.setattr(openapi_mod, "HAS_AIOMQTT", False)
        client = GoveeOpenApiEventClient(api_key=API_KEY, on_event=MagicMock())
        assert client.available is False

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            await client.async_start()

        assert client._task is None
        assert client._running is False
        assert any("aiomqtt not available" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, harness):
        h = harness([{"block": True}])
        await h.client.async_start()
        first = h.client._task
        assert first is not None
        await h.client.async_start()
        assert h.client._task is first
        await _until(lambda: h.client.connected)
        assert len(h.fake.clients) == 1
        await h.client.async_stop()

    @pytest.mark.asyncio
    async def test_stop_without_a_task_is_safe(self):
        client = GoveeOpenApiEventClient(api_key=API_KEY, on_event=MagicMock())
        client._connected = True
        await client.async_stop()
        assert client.connected is False
        assert client._running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_the_live_subscription(self, harness, caplog):
        h = harness([{"block": True}])
        await h.client.async_start()
        await _until(lambda: h.client.connected)
        broker = h.fake.clients[0]
        broker.subscribe.assert_awaited_once_with(f"GA/{API_KEY}", qos=1)
        assert broker.kwargs["hostname"] == openapi_mod.OPENAPI_MQTT_HOST
        assert broker.kwargs["port"] == openapi_mod.OPENAPI_MQTT_PORT
        assert broker.kwargs["username"] == API_KEY
        assert broker.kwargs["password"] == API_KEY
        assert broker.kwargs["keepalive"] == openapi_mod.OPENAPI_KEEPALIVE
        assert broker.kwargs["timeout"] == openapi_mod.CONNECTION_TIMEOUT

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            await h.client.async_stop()

        assert h.client._task is None
        assert h.client.connected is False
        broker.__aexit__.assert_awaited_once()
        assert any("cancelled" in rec.getMessage() for rec in caplog.records)
        assert h.sleeps == []  # cancellation is not a failure: no backoff, no reconnect
        assert len(h.fake.clients) == 1

    def test_default_tls_context_verifies_the_broker(self):
        client = GoveeOpenApiEventClient(api_key=API_KEY, on_event=MagicMock())
        context = client._create_ssl_context_sync()
        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True


# ==============================================================================
# Connection loop
# ==============================================================================


class TestConnectionLoop:
    @pytest.mark.asyncio
    async def test_connected_only_after_suback_then_events_fan_out(self, harness):
        h = harness([{"messages": [_message(_push()), _message(_push())]}], stop_after_events=1)
        seen_at_subscribe: list[bool] = []
        h.fake.on_subscribe = lambda: seen_at_subscribe.append(h.client.connected)

        await h.run()

        assert seen_at_subscribe == [False]  # not "connected" until the topic is confirmed
        assert h.connected_during_event == [True]
        assert h.events == [(DEVICE_ID, "H7150", "waterFullEvent", [{"name": "waterFull", "value": 1}])]
        assert h.client.connected is False  # stopped: the second push was never dispatched
        assert h.sleeps == []
        assert len(h.fake.clients) == 1

    @pytest.mark.asyncio
    async def test_failure_streak_warns_once_then_logs_reconnect(self, harness, caplog):
        h = harness(
            [
                {"connect_error": OSError("dns")},
                {"connect_error": OSError("dns")},
                {"messages": [_message(_push())], "drop_error": FakeAiomqtt.MqttError("bye")},
            ],
            stop_after_sleeps=3,
        )

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            await h.run()

        # 5 then 10 for the streak; the third session dropped at once (well
        # inside STABLE_SESSION_SECONDS), so it is part of the streak: the
        # backoff keeps doubling and nothing announces a reconnect. The stable
        # case is in test_account_mqtt_robustness.py.
        assert h.sleeps == [5, 10, 20]
        error_levels = [rec.levelno for rec in caplog.records if "connection error" in rec.getMessage()]
        assert error_levels == [logging.WARNING, logging.DEBUG, logging.DEBUG]
        assert not [rec for rec in caplog.records if rec.levelno == logging.INFO and "reconnected" in rec.getMessage()]
        assert len(h.events) == 1
        assert h.client.connected is False
        assert h.client._failure_logged is True

    @pytest.mark.asyncio
    async def test_backoff_doubles_and_caps(self, harness):
        h = harness([{"connect_error": OSError("x")}] * 9, stop_after_sleeps=9)

        await h.run()

        assert h.sleeps == [5, 10, 20, 40, 80, 160, 300, 300, 300]
        assert len(h.fake.clients) == 9
        assert h.client.connected is False

    @pytest.mark.asyncio
    async def test_drop_after_stop_exits_without_sleeping(self, harness):
        h = harness(
            [{"messages": [_message(_push())], "drop_error": FakeAiomqtt.MqttError("bye")}],
            stop_after_events=1,
        )

        await h.run()

        assert len(h.events) == 1
        assert h.sleeps == []
        assert len(h.fake.clients) == 1
        assert h.client.connected is False

    @pytest.mark.asyncio
    async def test_clean_stream_end_reconnects(self, harness):
        h = harness([{"messages": [_message(_push())]}, {"connect_error": OSError("x")}], stop_after_sleeps=1)

        await h.run()

        assert len(h.events) == 1
        assert len(h.fake.clients) == 2
        assert h.sleeps == [5]

    @pytest.mark.asyncio
    async def test_callback_error_inside_the_loop_does_not_drop_the_session(self, harness):
        h = harness(
            [{"messages": [_message(_push()), _message(_push())]}],
            stop_after_events=2,
            event_error=RuntimeError("entity boom"),
        )

        await h.run()

        assert len(h.events) == 2  # both pushes handled despite the raising callback
        assert h.sleeps == []


# ==============================================================================
# _handle_message guards and the ring buffer
# ==============================================================================


class TestHandleMessageGuards:
    def _client(self):
        events: list[tuple] = []
        client = GoveeOpenApiEventClient(api_key=API_KEY, on_event=lambda *args: events.append(args))
        return client, events

    @pytest.mark.parametrize("payload", [b"[1, 2]", b'"text"', b"42", b"null"])
    def test_non_object_payload_is_not_buffered(self, payload):
        client, events = self._client()
        client._handle_message(_message(payload))
        assert events == []
        assert client.recent_events == []

    def test_none_payload_is_a_decode_error(self):
        client, events = self._client()
        message = MagicMock()
        message.payload = None
        client._handle_message(message)
        assert events == []
        assert client.recent_events == []

    def test_str_payload_is_accepted(self):
        client, events = self._client()
        message = MagicMock()
        message.payload = json.dumps(_push())
        client._handle_message(message)
        assert len(events) == 1

    @pytest.mark.parametrize(
        "push",
        [
            {"sku": "H7150", "capabilities": [_event_cap()]},  # no device
            {"sku": "H7150", "device": "", "capabilities": [_event_cap()]},  # empty device
            _push(capabilities={"type": "devices.capabilities.event"}),  # not a list
            _push(capabilities=None),
        ],
    )
    def test_push_without_device_or_capabilities_is_buffered_but_not_fanned_out(self, push):
        client, events = self._client()
        client._handle_message(_message(push))
        assert events == []
        assert len(client.recent_events) == 1
        assert client.recent_events[0]["payload"] == push

    def test_non_dict_capability_entries_are_skipped(self):
        client, events = self._client()
        client._handle_message(_message(_push(capabilities=["junk", 42, None, _event_cap("lackWaterEvent")])))
        assert [event[2] for event in events] == ["lackWaterEvent"]

    def test_non_list_state_is_normalised_to_empty(self):
        client, events = self._client()
        client._handle_message(_message(_push(capabilities=[_event_cap(state={"value": 1})])))
        assert events == [(DEVICE_ID, "H7150", "waterFullEvent", [])]

    def test_missing_sku_and_instance_default_to_empty_strings(self):
        client, events = self._client()
        cap = {"type": "devices.capabilities.event", "state": [{"value": 1}]}
        client._handle_message(_message({"device": DEVICE_ID, "capabilities": [cap]}))
        assert events == [(DEVICE_ID, "", "", [{"value": 1}])]

    def test_callback_error_does_not_stop_later_capabilities(self, caplog):
        callback = MagicMock(side_effect=[RuntimeError("first"), None])
        client = GoveeOpenApiEventClient(api_key=API_KEY, on_event=callback)
        push = _push(capabilities=[_event_cap("waterFullEvent"), _event_cap("lackWaterEvent")])

        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            client._handle_message(_message(push))

        assert callback.call_count == 2
        assert callback.call_args_list[1].args[2] == "lackWaterEvent"
        assert any("callback failed" in rec.getMessage() for rec in caplog.records)

    def test_ring_buffer_keeps_the_newest_pushes(self):
        client, _ = self._client()
        for i in range(openapi_mod.EVENT_BUFFER_SIZE + 6):
            client._handle_message(_message(_push(seq=i)))
        buffered = client.recent_events
        assert len(buffered) == openapi_mod.EVENT_BUFFER_SIZE
        assert buffered[0]["payload"]["seq"] == 6
        assert buffered[-1]["payload"]["seq"] == openapi_mod.EVENT_BUFFER_SIZE + 5
        assert all("received_at" in entry for entry in buffered)
