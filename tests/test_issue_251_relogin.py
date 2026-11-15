"""Re-login latch and HTTP-error classification from the #251 security review."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from yarl import URL

from custom_components.govee.api.auth import GoveeIotCredentials, is_connection_error
from custom_components.govee.api.exceptions import (
    Govee2FACodeInvalidError,
    Govee2FARequiredError,
    GoveeApiError,
    GoveeAuthError,
    GoveeLoginRejectedError,
)
from custom_components.govee.const import IOT_RELOGIN_MIN_INTERVAL, LOGIN_FAILED_2FA
from custom_components.govee.coordinator import IOT_RELOGIN_MAX_INTERVAL, GoveeCoordinator

_REQ = aiohttp.RequestInfo(URL("https://example.invalid"), "POST", {}, URL("https://example.invalid"))  # type: ignore[arg-type]
_CREDS = GoveeIotCredentials(
    token="t",
    refresh_token="r",
    account_topic="GA/x",
    iot_cert="c",
    iot_key="k",
    iot_ca=None,
    client_id="cid",
    endpoint="ep",
)


def _coordinator() -> GoveeCoordinator:
    coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
    coordinator._config_entry = SimpleNamespace(data={"email": "a@b.c", "password": "pw"}, options={}, entry_id="e1")
    coordinator.hass = MagicMock()
    coordinator._iot_credentials = _CREDS
    coordinator._last_iot_relogin = -IOT_RELOGIN_MIN_INTERVAL * 10
    coordinator._persist_login_failed = MagicMock()
    coordinator._async_create_iot_reauth_issue = MagicMock()
    coordinator._mqtt_client = None
    coordinator._start_mqtt = AsyncMock()
    return coordinator


def _client(login: AsyncMock) -> MagicMock:
    ctx = MagicMock()
    auth_client = MagicMock()
    auth_client.login = login
    ctx.__aenter__ = AsyncMock(return_value=auth_client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (Govee2FARequiredError("2fa"), LOGIN_FAILED_2FA),
        (GoveeAuthError("bad password", code=401), "bad password"),
        (Govee2FACodeInvalidError("code"), "code"),
    ],
)
async def test_relogin_stops_after_definite_refusal(error: Exception, reason: str) -> None:
    coordinator = _coordinator()
    login = AsyncMock(side_effect=error)
    with patch("custom_components.govee.coordinator.GoveeAuthClient", _client(login)):
        assert await coordinator._async_refresh_iot_credentials() is False
        # Past the throttle window: only the latch can stop a second login.
        coordinator._last_iot_relogin = -IOT_RELOGIN_MIN_INTERVAL * 10
        assert await coordinator._async_refresh_iot_credentials() is False

    login.assert_awaited_once()
    coordinator._persist_login_failed.assert_called_once_with(reason)
    assert coordinator._relogin_disabled is True


@pytest.mark.asyncio
async def test_relogin_retries_after_network_error() -> None:
    coordinator = _coordinator()
    login = AsyncMock(side_effect=GoveeApiError("net"))
    with patch("custom_components.govee.coordinator.GoveeAuthClient", _client(login)):
        await coordinator._async_refresh_iot_credentials()
        coordinator._last_iot_relogin = -IOT_RELOGIN_MIN_INTERVAL * 10
        await coordinator._async_refresh_iot_credentials()

    assert login.await_count == 2
    coordinator._persist_login_failed.assert_not_called()


def test_http_error_is_not_connection_error() -> None:
    assert is_connection_error(aiohttp.ClientResponseError(_REQ, (), status=403)) is False
    assert is_connection_error(aiohttp.ContentTypeError(_REQ, ())) is False
    assert is_connection_error(TimeoutError()) is True
    assert is_connection_error(aiohttp.ClientConnectorError(MagicMock(), OSError("refused"))) is True

    wrapped = GoveeApiError("html page")
    wrapped.__cause__ = aiohttp.ClientResponseError(_REQ, (), status=429)
    assert is_connection_error(wrapped) is False
    wrapped_net = GoveeApiError("dns")
    wrapped_net.__cause__ = TimeoutError()
    assert is_connection_error(wrapped_net) is True


@pytest.mark.asyncio
async def test_rejected_login_backs_off_without_latch() -> None:
    coordinator = _coordinator()
    login = AsyncMock(side_effect=GoveeLoginRejectedError("HTTP 503"))
    base = IOT_RELOGIN_MIN_INTERVAL
    clock = {"now": 1_000_000.0}
    with (
        patch("custom_components.govee.coordinator.GoveeAuthClient", _client(login)),
        patch("custom_components.govee.coordinator.time.monotonic", lambda: clock["now"]),
    ):
        await coordinator._async_refresh_iot_credentials()
        assert login.await_count == 1
        # After one rejection the minimum interval doubles to 30 minutes.
        clock["now"] += base * 2 - 1
        await coordinator._async_refresh_iot_credentials()
        assert login.await_count == 1
        clock["now"] += 1
        await coordinator._async_refresh_iot_credentials()
        assert login.await_count == 2
        # ... then 60 minutes.
        clock["now"] += base * 4 - 1
        await coordinator._async_refresh_iot_credentials()
        assert login.await_count == 2
        clock["now"] += 1
        await coordinator._async_refresh_iot_credentials()
        assert login.await_count == 3
        # Capped at 6 hours however many rejections pile up.
        coordinator._relogin_rejections = 20
        clock["now"] += IOT_RELOGIN_MAX_INTERVAL
        await coordinator._async_refresh_iot_credentials()
        assert login.await_count == 4

    assert coordinator._relogin_disabled is False
    coordinator._persist_login_failed.assert_not_called()


@pytest.mark.asyncio
async def test_backoff_resets_after_success() -> None:
    coordinator = _coordinator()
    coordinator._relogin_rejections = 3
    coordinator._persist_refreshed_credentials = MagicMock()
    login = AsyncMock(return_value=_CREDS)
    with (
        patch("custom_components.govee.coordinator.GoveeAuthClient", _client(login)),
        patch("custom_components.govee.coordinator.ir"),
    ):
        assert await coordinator._async_refresh_iot_credentials()
    assert coordinator._relogin_rejections == 0
