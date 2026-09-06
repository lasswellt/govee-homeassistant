"""Connection lifecycle of the AWS IoT client, validated against HA core's MQTT client.

Covers the reconnect policy (never give up; flap-aware backoff; repair fired
once), the SUBACK check, QoS-1 publishes with an ack timeout, the cached TLS
context, and credential rotation via async_restart.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.api import mqtt as mqtt_mod
from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.mqtt import GoveeAwsIotClient, _subscription_refused


def _creds(**over) -> GoveeIotCredentials:
    base = dict(
        token="t", refresh_token="r", account_topic="GA/account", iot_cert="cert",
        iot_key="key", iot_ca=None, client_id="cid", endpoint="endpoint",
    )
    base.update(over)
    return GoveeIotCredentials(**base)


class FakeMessages:
    """Async iterator that raises the given error after yielding nothing."""

    def __init__(self, error: Exception | None):
        self._error = error

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._error is not None:
            raise self._error
        raise StopAsyncIteration


class FakeAiomqtt:
    """Stand-in for the aiomqtt module: scripted sessions."""

    class MqttError(Exception):
        pass

    def __init__(self, sessions):
        # sessions: list of dicts {connect_error, granted, drop_error}
        self.sessions = list(sessions)
        self.clients = []

    def Client(self, **kwargs):  # noqa: N802 - mimics aiomqtt.Client
        spec = self.sessions.pop(0) if self.sessions else {"drop_error": self.MqttError("no more sessions")}
        client = MagicMock()
        client.kwargs = kwargs
        connect_error = spec.get("connect_error")

        async def aenter():
            if connect_error is not None:
                raise connect_error
            return client

        client.__aenter__ = AsyncMock(side_effect=aenter)
        client.__aexit__ = AsyncMock(return_value=False)
        client.subscribe = AsyncMock(return_value=spec.get("granted", (1,)))
        client.messages = FakeMessages(spec.get("drop_error", self.MqttError("dropped")))
        client.publish = AsyncMock()
        self.clients.append(client)
        return client


@pytest.fixture
def run_loop(monkeypatch):
    """Run the connection loop against scripted sessions with instant sleeps."""

    async def _run(sessions, *, stop_after_sleeps: int, clock=None, **client_kwargs):
        fake = FakeAiomqtt(sessions)
        monkeypatch.setattr(mqtt_mod, "aiomqtt", fake)
        monkeypatch.setattr(mqtt_mod, "AIOMQTT_AVAILABLE", True)
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock(), **client_kwargs)
        client._create_ssl_context_sync = MagicMock(return_value=MagicMock())
        sleeps: list[float] = []
        if clock is not None:
            monkeypatch.setattr(mqtt_mod.time, "monotonic", clock)

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= stop_after_sleeps:
                client._running = False

        monkeypatch.setattr(mqtt_mod.asyncio, "sleep", fake_sleep)
        client._running = True
        await client._connection_loop()
        return client, fake, sleeps

    return _run


class TestReconnectPolicy:
    @pytest.mark.asyncio
    async def test_never_gives_up_but_reports_once(self, run_loop, monkeypatch):
        """After MAX_RECONNECT_ATTEMPTS failures the repair fires once; retries continue."""
        monkeypatch.setattr(mqtt_mod, "MAX_RECONNECT_ATTEMPTS", 3)
        give_up = MagicMock()
        sessions = [{"connect_error": OSError("unreachable")}] * 6
        client, fake, sleeps = await run_loop(sessions, stop_after_sleeps=6, on_give_up=give_up)

        assert len(fake.clients) == 6  # kept trying past the threshold
        give_up.assert_called_once()
        assert give_up.call_args[0][0] == 3
        assert client.consecutive_failures == 6
        assert sleeps == [5, 10, 20, 40, 80, 160]  # exponential backoff, capped later

    @pytest.mark.asyncio
    async def test_backoff_caps_at_reconnect_max(self, run_loop):
        sessions = [{"connect_error": OSError("x")}] * 9
        _, _, sleeps = await run_loop(sessions, stop_after_sleeps=9)
        assert sleeps[-1] == mqtt_mod.RECONNECT_MAX
        assert max(sleeps) == mqtt_mod.RECONNECT_MAX

    @pytest.mark.asyncio
    async def test_flap_counts_as_failure_and_backs_off(self, run_loop):
        """Connect-then-immediate-drop (client-id takeover) must not reset backoff."""
        now = [1000.0]
        sessions = [{"granted": (1,), "drop_error": FakeAiomqtt.MqttError("kicked")}] * 4
        client, _, sleeps = await run_loop(sessions, stop_after_sleeps=4, clock=lambda: now[0])
        assert sleeps == [5, 10, 20, 40]
        assert client.consecutive_failures == 4

    @pytest.mark.asyncio
    async def test_stable_session_drop_resets_backoff(self, run_loop):
        now = [1000.0]

        def clock():
            now[0] += mqtt_mod.STABLE_SESSION_SECONDS  # every read advances a full minute
            return now[0]

        sessions = [
            {"connect_error": OSError("x")},
            {"connect_error": OSError("x")},
            {"granted": (1,), "drop_error": FakeAiomqtt.MqttError("keepalive")},
            {"connect_error": OSError("x")},
        ]
        client, _, sleeps = await run_loop(sessions, stop_after_sleeps=4, clock=clock)
        # 5, 10 for the two failures; the long session resets to 5; then 10.
        assert sleeps == [5, 10, 5, 10]
        assert client.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_connected_callback_fires_after_suback(self, run_loop):
        connected = MagicMock()
        sessions = [{"granted": (1,), "drop_error": FakeAiomqtt.MqttError("bye")}]
        client, fake, _ = await run_loop(sessions, stop_after_sleeps=1, on_connected=connected)
        connected.assert_called_once()
        fake.clients[0].subscribe.assert_awaited_once_with("GA/account", qos=1)
        assert client.last_error is not None and "bye" in client.last_error

    @pytest.mark.asyncio
    async def test_disconnected_callback_fires_only_after_a_live_session(self, run_loop):
        disconnected = MagicMock()
        sessions = [
            {"connect_error": OSError("x")},  # never connected: no callback
            {"granted": (1,), "drop_error": FakeAiomqtt.MqttError("bye")},
        ]
        await run_loop(sessions, stop_after_sleeps=2, on_disconnected=disconnected)
        disconnected.assert_called_once()

    @pytest.mark.asyncio
    async def test_refused_subscription_is_a_failure(self, run_loop):
        """SUBACK 0x80 must not leave the client 'connected' and deaf."""
        connected = MagicMock()
        sessions = [{"granted": (128,)}, {"granted": (0x80,)}]
        client, _, sleeps = await run_loop(sessions, stop_after_sleeps=2, on_connected=connected)
        connected.assert_not_called()
        assert client.connected is False
        assert client.consecutive_failures == 2
        assert "refused" in (client.last_error or "")

    @pytest.mark.asyncio
    async def test_ssl_context_built_once_across_attempts(self, run_loop):
        sessions = [{"connect_error": OSError("x")}] * 3
        client, _, _ = await run_loop(sessions, stop_after_sleeps=3)
        client._create_ssl_context_sync.assert_called_once()


class TestSubackHelper:
    def test_int_codes(self):
        assert _subscription_refused((128,)) is True
        assert _subscription_refused((1,)) is False
        assert _subscription_refused(()) is False
        assert _subscription_refused(None) is False

    def test_reason_codes(self):
        ok = MagicMock(is_failure=False)
        bad = MagicMock(is_failure=True)
        assert _subscription_refused([ok]) is False
        assert _subscription_refused([ok, bad]) is True


class TestPublish:
    @pytest.mark.asyncio
    async def test_publish_uses_qos1_with_ack_timeout(self):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        client._connected = True
        client._client = MagicMock()
        client._client.publish = AsyncMock()

        assert await client.async_publish_command("GD/topic", "turn", {"val": 1}) is True

        args, kwargs = client._client.publish.call_args
        assert args[0] == "GD/topic"
        assert json.loads(args[1])["msg"]["cmd"] == "turn"
        assert kwargs == {"qos": 1, "timeout": mqtt_mod.ACK_TIMEOUT}

    @pytest.mark.asyncio
    async def test_publish_ack_timeout_returns_false(self):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        client._connected = True
        client._client = MagicMock()
        client._client.publish = AsyncMock(side_effect=asyncio.TimeoutError())

        assert await client.async_publish_command("GD/topic", "turn", {"val": 1}) is False


class TestRestart:
    @pytest.mark.asyncio
    async def test_same_material_is_a_no_op(self):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        client.async_stop = AsyncMock()
        client.async_start = AsyncMock()

        assert await client.async_restart(_creds(token="new-token")) is False

        client.async_stop.assert_not_awaited()
        assert client._credentials.token == "new-token"

    @pytest.mark.asyncio
    async def test_rotated_certificate_restarts_and_drops_cached_context(self):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        client._running = True
        client._ssl_context = MagicMock()
        client._consecutive_failures = 7
        client.async_stop = AsyncMock()
        client.async_start = AsyncMock()

        assert await client.async_restart(_creds(iot_cert="cert2")) is True

        client.async_stop.assert_awaited_once()
        client.async_start.assert_awaited_once()
        assert client._ssl_context is None
        assert client._credentials.iot_cert == "cert2"
        assert client.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_stopped_client_is_not_started_by_restart(self):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        client.async_stop = AsyncMock()
        client.async_start = AsyncMock()

        await client.async_restart(_creds(account_topic="GA/other"))

        client.async_start.assert_not_awaited()


class TestMsgWrappedPushes:
    @pytest.mark.asyncio
    async def test_legacy_msg_wrapped_status_is_unwrapped(self):
        callback = MagicMock()
        client = GoveeAwsIotClient(_creds(), on_state_update=callback)
        inner = {"device": "AA:BB", "sku": "H6104", "state": {"onOff": 1}}
        message = MagicMock()
        message.topic = "GA/account"
        message.payload = json.dumps({"msg": json.dumps(inner)}).encode()

        await client._handle_message(message)

        callback.assert_called_once_with("AA:BB", {"onOff": 1})

    @pytest.mark.asyncio
    async def test_own_command_echo_is_still_ignored(self):
        callback = MagicMock()
        client = GoveeAwsIotClient(_creds(), on_state_update=callback)
        message = MagicMock()
        message.topic = "GA/account"
        message.payload = json.dumps({"msg": {"cmd": "turn", "data": {"val": 1}}}).encode()

        await client._handle_message(message)

        callback.assert_not_called()
