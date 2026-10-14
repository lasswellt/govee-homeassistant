"""Regression tests for account-login and MQTT robustness fixes.

Covers timers re-arming on a shut-down coordinator, the background retry of a
setup login that failed transiently, the water-detector poll's token
recovery, the OpenAPI events SUBACK/backoff handling and topic redaction in
MQTT debug logs. Every network client is an in-process fake.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

import custom_components.govee.coordinator as coord_mod
from custom_components.govee.api import openapi_events as openapi_mod
from custom_components.govee.api.mqtt import STABLE_SESSION_SECONDS, GoveeAwsIotClient
from custom_components.govee.api.exceptions import (
    Govee2FARequiredError,
    GoveeApiError,
    GoveeAuthError,
    GoveeLoginRejectedError,
)
from custom_components.govee.const import (
    ACCOUNT_LOGIN_RETRY_INTERVAL,
    ACCOUNT_LOGIN_RETRY_MAX_INTERVAL,
    CONF_EMAIL,
    CONF_PASSWORD,
    KEY_IOT_CREDENTIALS,
    KEY_IOT_LOGIN_FAILED,
)
from tests.test_cov_coordinator_setup import (
    CREDS,
    WD,
    _add,
    _coordinator,
    _patch_auth,
    _TimerCapture,
    _water_detector,
)

from tests.test_cov_mqtt import _creds, _msg
from tests.test_cov_openapi_events import LOGGER_NAME as OPENAPI_LOGGER
from tests.test_cov_openapi_events import FakeAiomqtt, Harness, _push
from tests.test_cov_openapi_events import _message as _openapi_message

ACCOUNT = {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "pw"}


async def _shut_down(coord: Any) -> None:
    coord._api_client = MagicMock()
    coord._api_client.close = AsyncMock()
    await coord.async_shutdown()


class TestTimersStayDeadAfterShutdown:
    """A timer callback that resumes after unload must not re-arm (COORD-1)."""

    @pytest.mark.asyncio
    async def test_bff_poll_callback_does_not_rearm_after_shutdown(self, monkeypatch):
        timers = _TimerCapture()
        monkeypatch.setattr(coord_mod, "async_call_later", timers)
        coord = _coordinator(iot=CREDS)

        # The unload lands while the callback awaits its HTTP call.
        async def _poll() -> None:
            await _shut_down(coord)

        coord._poll_bff_leak_state = _poll
        coord._refresh_bff_thermometers = AsyncMock()

        await coord._bff_poll_callback()

        assert timers.calls == []

    @pytest.mark.asyncio
    async def test_water_detector_callback_does_not_rearm_after_shutdown(self, monkeypatch):
        timers = _TimerCapture()
        monkeypatch.setattr(coord_mod, "async_call_later", timers)
        coord = _coordinator(iot=CREDS)

        async def _poll() -> None:
            await _shut_down(coord)

        coord._poll_water_detectors = _poll
        await coord._water_detector_poll_callback()

        assert timers.calls == []

    @pytest.mark.asyncio
    async def test_probe_callback_does_not_rearm_after_shutdown(self, monkeypatch):
        timers = _TimerCapture()
        monkeypatch.setattr(coord_mod, "async_call_later", timers)
        coord = _coordinator(iot=CREDS)
        coord._probe_polling_enabled = {"dev"}

        async def _poll() -> None:
            await _shut_down(coord)
            coord._probe_polling_enabled = {"dev"}

        coord._poll_probe_thermometers = _poll
        await coord._probe_poll_callback()

        assert timers.calls == []

    @pytest.mark.asyncio
    async def test_status_callback_does_not_rearm_after_shutdown(self, monkeypatch):
        timers = _TimerCapture()
        monkeypatch.setattr(coord_mod, "async_call_later", timers)
        coord = _coordinator(iot=CREDS)

        async def _poll() -> None:
            await _shut_down(coord)

        coord._poll_mqtt_status = _poll
        await coord._status_poll_callback()

        assert timers.calls == []

    @pytest.mark.asyncio
    async def test_start_mqtt_is_a_noop_after_shutdown(self, monkeypatch):
        coord = _coordinator(iot=CREDS)
        await _shut_down(coord)
        built = MagicMock()
        monkeypatch.setattr(coord_mod, "GoveeAwsIotClient", built)

        await coord._start_mqtt()

        built.assert_not_called()
        assert coord._mqtt_client is None

    def test_schedulers_still_arm_while_running(self, monkeypatch):
        timers = _TimerCapture()
        monkeypatch.setattr(coord_mod, "async_call_later", timers)
        coord = _coordinator(iot=CREDS)
        coord._schedule_bff_poll()
        coord._schedule_water_detector_poll()
        coord._schedule_probe_poll()
        coord._schedule_status_poll()

        assert len(timers.calls) == 4


def _network_error() -> GoveeApiError:
    """The error login() raises for a DNS/connect failure (chained to the aiohttp error)."""
    err = GoveeApiError("Connection error during login: Cannot connect to host")
    err.__cause__ = aiohttp.ClientConnectionError("dns")
    return err


class TestSetupLoginRetry:
    """A transient setup login failure is retried in the background (SETUP-1)."""

    def _coord(self, monkeypatch, login: Any) -> tuple[Any, _TimerCapture, MagicMock]:
        timers = _TimerCapture()
        monkeypatch.setattr(coord_mod, "async_call_later", timers)
        inner = MagicMock()
        inner.login = AsyncMock(side_effect=login) if isinstance(login, Exception) else AsyncMock(return_value=login)
        _patch_auth(monkeypatch, inner)
        monkeypatch.setattr(coord_mod.ir, "async_create_issue", MagicMock())
        monkeypatch.setattr(coord_mod, "async_create_mqtt_issue", MagicMock())
        monkeypatch.setattr(coord_mod, "async_delete_mqtt_issue", MagicMock())
        coord = _coordinator(data=dict(ACCOUNT))
        return coord, timers, inner

    def _written(self, coord: Any) -> dict[str, Any]:
        return coord.hass.config_entries.async_update_entry.call_args.kwargs["data"]

    def test_schedule_arms_one_timer_with_growing_delay(self, monkeypatch):
        coord, timers, _ = self._coord(monkeypatch, CREDS)

        coord.schedule_account_login_retry()
        coord.schedule_account_login_retry()

        assert [d for d, _ in timers.calls] == [ACCOUNT_LOGIN_RETRY_INTERVAL, ACCOUNT_LOGIN_RETRY_INTERVAL * 2]
        assert timers.cancelled == 1

    @pytest.mark.asyncio
    async def test_consecutive_network_failures_back_off_to_the_cap(self, monkeypatch):
        """Every attempt that reaches Govee is a password login: never a fixed 5-minute loop."""
        coord, timers, inner = self._coord(monkeypatch, _network_error())
        coord.schedule_account_login_retry()

        for _ in range(9):
            await coord._account_login_retry_callback()

        delays = [d for d, _ in timers.calls]
        assert delays[:4] == [300, 600, 1200, 2400]
        assert delays == sorted(delays)
        assert delays[-1] == ACCOUNT_LOGIN_RETRY_MAX_INTERVAL
        assert inner.login.await_count == 9

    @pytest.mark.asyncio
    async def test_success_stores_credentials_and_reloads(self, monkeypatch):
        coord, timers, inner = self._coord(monkeypatch, CREDS)

        await coord._account_login_retry_callback()

        inner.login.assert_awaited_once()
        assert self._written(coord)[KEY_IOT_CREDENTIALS]["token"] == "tok"
        coord.hass.config_entries.async_schedule_reload.assert_called_once_with("cov_entry")
        assert timers.calls == []

    @pytest.mark.asyncio
    async def test_transient_failure_rearms(self, monkeypatch):
        coord, timers, _ = self._coord(monkeypatch, _network_error())

        await coord._async_retry_account_login()

        assert [d for d, _ in timers.calls] == [ACCOUNT_LOGIN_RETRY_INTERVAL]
        coord.hass.config_entries.async_update_entry.assert_not_called()
        coord.hass.config_entries.async_schedule_reload.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            GoveeAuthError("wrong password"),
            GoveeLoginRejectedError("refused"),
            GoveeApiError("No token in login response"),
            RuntimeError("boom"),
        ],
    )
    async def test_non_network_failure_stores_marker_and_stops(self, monkeypatch, error):
        coord, timers, _ = self._coord(monkeypatch, error)

        await coord._async_retry_account_login()

        assert self._written(coord)[KEY_IOT_LOGIN_FAILED] == str(error)
        coord_mod.async_create_mqtt_issue.assert_called_once()
        assert timers.calls == []

    @pytest.mark.asyncio
    async def test_two_factor_stores_marker_and_raises_issue(self, monkeypatch):
        coord, timers, _ = self._coord(monkeypatch, Govee2FARequiredError())

        await coord._async_retry_account_login()

        assert self._written(coord)[KEY_IOT_LOGIN_FAILED] == "2FA verification required"
        assert coord_mod.ir.async_create_issue.call_args.args[2] == "mqtt_2fa_required_cov_entry"
        # The setup-time "sign-in failed, retry" repair would only hit 2FA again.
        coord_mod.async_delete_mqtt_issue.assert_called_once()
        assert timers.calls == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "outcome",
        [CREDS, Govee2FARequiredError(), GoveeAuthError("wrong password"), "network"],
    )
    async def test_result_ignored_when_shut_down_during_login(self, monkeypatch, outcome):
        """A login that lands after unload must not touch the (possibly new) entry."""
        coord, timers, inner = self._coord(monkeypatch, CREDS)

        async def _login(*_a: Any, **_k: Any) -> Any:
            await _shut_down(coord)
            if outcome == "network":
                raise _network_error()
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        inner.login = AsyncMock(side_effect=_login)
        await coord._async_retry_account_login()

        coord.hass.config_entries.async_update_entry.assert_not_called()
        coord.hass.config_entries.async_schedule_reload.assert_not_called()
        coord_mod.async_create_mqtt_issue.assert_not_called()
        coord_mod.ir.async_create_issue.assert_not_called()
        assert timers.calls == []

    @pytest.mark.asyncio
    async def test_noop_when_credentials_present_or_no_account(self, monkeypatch):
        coord, _, inner = self._coord(monkeypatch, CREDS)
        coord._iot_credentials = CREDS
        await coord._async_retry_account_login()
        coord._iot_credentials = None
        coord._config_entry.data = {}
        await coord._async_retry_account_login()

        inner.login.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_retry_or_reload_after_shutdown(self, monkeypatch):
        coord, timers, inner = self._coord(monkeypatch, CREDS)

        async def _login(*_a: Any, **_k: Any) -> Any:
            await _shut_down(coord)
            return CREDS

        inner.login = AsyncMock(side_effect=_login)
        await coord._async_retry_account_login()
        coord.hass.config_entries.async_schedule_reload.assert_not_called()

        # A timer armed before shutdown is cancelled; none is armed after.
        coord._shut_down = False
        coord.schedule_account_login_retry()
        await _shut_down(coord)
        assert timers.cancelled == 1
        coord.schedule_account_login_retry()
        await coord._async_retry_account_login()
        assert len(timers.calls) == 1


class TestWaterDetectorTokenRecovery:
    """An expired token on the H5054 poll triggers the #132 re-login (COORD-5)."""

    @pytest.mark.asyncio
    async def test_rejected_token_relogs_in_and_retries(self, monkeypatch):
        coord = _coordinator(iot=CREDS, data=dict(ACCOUNT))
        state = _add(coord, _water_detector())
        fresh = dataclasses.replace(CREDS, token="fresh")
        seen_tokens: list[str] = []

        async def _states(token: str, _ids: Any) -> dict[str, Any]:
            seen_tokens.append(token)
            if token == "tok":
                raise GoveeAuthError("token expired", code=401)
            return {WD: {"online": True, "gateway_online": True, "last_time": 0, "battery": 77}}

        inner = MagicMock()
        inner.fetch_water_detector_states = AsyncMock(side_effect=_states)
        inner.login = AsyncMock(return_value=fresh)
        _patch_auth(monkeypatch, inner)
        coord._start_mqtt = AsyncMock()

        await coord._poll_water_detectors()

        inner.login.assert_awaited_once()
        assert seen_tokens == ["tok", "fresh"]
        assert state.battery == 77


class TestInboundTopicIsTruncatedInLogs:
    """Debug logs get pasted into public issues; the account topic must not (CLOUD-7)."""

    @pytest.mark.asyncio
    async def test_full_account_topic_never_logged(self, caplog):
        account_topic = "GA/0123456789abcdef0123456789abcdef-secret-tail"
        client = GoveeAwsIotClient(_creds(account_topic=account_topic), on_state_update=MagicMock())

        with caplog.at_level(logging.DEBUG, logger="custom_components.govee.api.mqtt"):
            await client._handle_message(_msg({"sku": "H6072", "state": {"onOff": 1}}, topic=account_topic))

        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "inbound topic=" in logged
        assert "secret-tail" not in logged


class _Clock:
    """A ``time`` stand-in whose monotonic clock advances ``step`` per read."""

    def __init__(self, step: float) -> None:
        self.now = 1000.0
        self.step = step

    def monotonic(self) -> float:
        self.now += self.step
        return self.now


@pytest.fixture(name="harness")
def _harness_fixture(monkeypatch: pytest.MonkeyPatch) -> Any:
    return lambda sessions, **kwargs: Harness(monkeypatch, sessions, **kwargs)


class TestOpenApiEventsSubackAndBackoff:
    """The event channel honours SUBACK and only resets backoff on a stable session (CLOUD-6)."""

    @pytest.mark.asyncio
    async def test_refused_subscription_is_a_failure_not_connected(self, harness, monkeypatch, caplog):
        monkeypatch.setattr(openapi_mod, "time", _Clock(0.0), raising=False)
        h = harness([{"granted": (0x80,), "block": True}, {"connect_error": OSError("x")}], stop_after_sleeps=2)

        with caplog.at_level(logging.DEBUG, logger=OPENAPI_LOGGER):
            await asyncio.wait_for(h.run(), timeout=5)

        assert any("refused" in r.getMessage() for r in caplog.records)
        assert h.sleeps == [5, 10]
        assert h.client.connected is False

    @pytest.mark.asyncio
    async def test_flapping_session_keeps_backing_off(self, harness, monkeypatch):
        monkeypatch.setattr(openapi_mod, "time", _Clock(0.0), raising=False)
        drop = {"drop_error": FakeAiomqtt.MqttError("bye")}
        h = harness([dict(drop), dict(drop), dict(drop)], stop_after_sleeps=3)

        await h.run()

        assert h.sleeps == [5, 10, 20]

    @pytest.mark.asyncio
    async def test_stable_session_resets_backoff_and_announces_reconnect(self, harness, monkeypatch, caplog):
        monkeypatch.setattr(openapi_mod, "time", _Clock(STABLE_SESSION_SECONDS + 1), raising=False)
        h = harness(
            [
                {"connect_error": OSError("dns")},
                {"messages": [_openapi_message(_push())], "drop_error": FakeAiomqtt.MqttError("bye")},
            ],
            stop_after_sleeps=2,
        )

        with caplog.at_level(logging.DEBUG, logger=OPENAPI_LOGGER):
            await h.run()

        assert h.sleeps == [5, 5]
        assert any(r.levelno == logging.INFO and "reconnected" in r.getMessage() for r in caplog.records)
        drop_levels = [r.levelno for r in caplog.records if "connection error" in r.getMessage()]
        assert drop_levels == [logging.WARNING, logging.WARNING]
