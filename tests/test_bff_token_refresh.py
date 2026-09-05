"""Recovery from an expired Govee account token (issue #132).

The token obtained at setup was cached in ``entry.data`` and reused
indefinitely — nothing refreshed it, and the stored ``refresh_token`` was never
used at all. Once it expired, every BFF call failed while MQTT kept working (it
authenticates with long-lived certificates rather than this token), so battery
levels and gateway-bridged sensor readings stopped with the integration still
reporting itself healthy.

Two accounts on #132 sat in exactly that state, both showing a
``{"status": "int", "message": "str"}`` BFF response skeleton with no
``devices`` in it at all.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.exceptions import (
    Govee2FARequiredError,
    GoveeAuthError,
)
from custom_components.govee.const import IOT_RELOGIN_MIN_INTERVAL
from custom_components.govee.coordinator import GoveeCoordinator

STALE = GoveeIotCredentials(
    token="stale",
    refresh_token="r",
    account_topic="GA/x",
    iot_cert="c",
    iot_key="k",
    iot_ca=None,
    client_id="cid",
    endpoint="ep",
)
# What a re-login hands back: same account, rotated certificate.
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


def _coordinator(*, email="a@b.c", password="pw", credentials=STALE):
    """A coordinator with just the attributes the token paths touch."""
    coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
    data = {}
    if email:
        data["email"] = email
    if password:
        data["password"] = password
    coordinator._config_entry = SimpleNamespace(
        data=data, options={}, entry_id="e1", title="Govee"
    )
    coordinator.hass = MagicMock()
    coordinator._iot_credentials = credentials
    coordinator._last_iot_relogin = -IOT_RELOGIN_MIN_INTERVAL * 10
    coordinator._persist_refreshed_credentials = MagicMock()
    # The refresh hands new material to the MQTT transport (or starts it).
    coordinator._mqtt_client = None
    coordinator._start_mqtt = AsyncMock()
    return coordinator


def _patched_client(auth_client):
    """Patch the coordinator's GoveeAuthClient to yield ``auth_client``."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=auth_client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch(
        "custom_components.govee.coordinator.GoveeAuthClient", return_value=ctx
    )


class TestBffCallRetry:
    """_async_bff_call refreshes the token once and retries."""

    @pytest.mark.asyncio
    async def test_expired_token_is_refreshed_and_the_call_retried(self):
        coordinator = _coordinator()
        fresh = dataclasses.replace(STALE, token="fresh")
        seen: list[str] = []

        async def _op(_client, token):
            seen.append(token)
            if token == "stale":
                raise GoveeAuthError("token is invalid", code=401)
            return ["payload"]

        auth_client = MagicMock()
        auth_client.login = AsyncMock(return_value=fresh)

        with _patched_client(auth_client), patch(
            "custom_components.govee.coordinator.ir"
        ):
            result = await coordinator._async_bff_call(_op, "test call")

        # First attempt on the stale token, second on the refreshed one.
        assert seen == ["stale", "fresh"]
        assert result == ["payload"]
        assert coordinator._iot_credentials.token == "fresh"
        coordinator._persist_refreshed_credentials.assert_called_once()

    @pytest.mark.asyncio
    async def test_successful_call_does_not_relogin(self):
        coordinator = _coordinator()

        async def _op(_client, token):
            return {"token": token}

        auth_client = MagicMock()
        auth_client.login = AsyncMock()

        with _patched_client(auth_client):
            result = await coordinator._async_bff_call(_op, "test call")

        assert result == {"token": "stale"}
        auth_client.login.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_credentials_short_circuits(self):
        coordinator = _coordinator(credentials=None)
        called = False

        async def _op(_client, _token):
            nonlocal called
            called = True

        assert await coordinator._async_bff_call(_op, "test call") is None
        assert called is False

    @pytest.mark.asyncio
    async def test_retry_failure_propagates_to_the_caller(self):
        """A second rejection is the caller's to handle, not swallowed here."""
        coordinator = _coordinator()
        fresh = dataclasses.replace(STALE, token="fresh")

        async def _op(_client, _token):
            raise GoveeAuthError("still invalid", code=401)

        auth_client = MagicMock()
        auth_client.login = AsyncMock(return_value=fresh)

        with _patched_client(auth_client), patch(
            "custom_components.govee.coordinator.ir"
        ), pytest.raises(GoveeAuthError):
            await coordinator._async_bff_call(_op, "test call")


class TestRelogin:
    """_async_refresh_iot_credentials and its guards."""

    @pytest.mark.asyncio
    async def test_api_key_only_setup_never_logs_in(self):
        """No stored account credentials means nothing to recover."""
        coordinator = _coordinator(email=None, password=None)
        auth_client = MagicMock()
        auth_client.login = AsyncMock()

        with _patched_client(auth_client):
            assert await coordinator._async_refresh_iot_credentials() is False
        auth_client.login.assert_not_called()

    @pytest.mark.asyncio
    async def test_relogin_is_throttled(self):
        """A persistently failing account must not hammer the login endpoint.

        Repeated logins are what trips Govee's own 2FA hardening, which would
        turn a recoverable expiry into one that needs the user.
        """
        coordinator = _coordinator()
        fresh = dataclasses.replace(STALE, token="fresh")
        auth_client = MagicMock()
        auth_client.login = AsyncMock(return_value=fresh)

        with _patched_client(auth_client), patch(
            "custom_components.govee.coordinator.ir"
        ):
            assert await coordinator._async_refresh_iot_credentials() is True
            assert await coordinator._async_refresh_iot_credentials() is False

        assert auth_client.login.call_count == 1

    @pytest.mark.asyncio
    async def test_2fa_raises_a_repair_instead_of_retrying_forever(self):
        """A verification code needs the user — say so once, loudly."""
        coordinator = _coordinator()
        auth_client = MagicMock()
        auth_client.login = AsyncMock(side_effect=Govee2FARequiredError())

        with _patched_client(auth_client), patch(
            "custom_components.govee.coordinator.ir"
        ) as ir_mod:
            assert await coordinator._async_refresh_iot_credentials() is False

        ir_mod.async_create_issue.assert_called_once()
        assert (
            ir_mod.async_create_issue.call_args.kwargs["translation_key"]
            == "mqtt_2fa_required"
        )

    @pytest.mark.asyncio
    async def test_rejected_password_raises_its_own_repair(self):
        coordinator = _coordinator()
        auth_client = MagicMock()
        auth_client.login = AsyncMock(side_effect=GoveeAuthError("bad password"))

        with _patched_client(auth_client), patch(
            "custom_components.govee.coordinator.ir"
        ) as ir_mod:
            assert await coordinator._async_refresh_iot_credentials() is False

        assert (
            ir_mod.async_create_issue.call_args.kwargs["translation_key"]
            == "mqtt_token_expired"
        )

    @pytest.mark.asyncio
    async def test_success_clears_the_expiry_repair(self):
        coordinator = _coordinator()
        fresh = dataclasses.replace(STALE, token="fresh")
        auth_client = MagicMock()
        auth_client.login = AsyncMock(return_value=fresh)

        with _patched_client(auth_client), patch(
            "custom_components.govee.coordinator.ir"
        ) as ir_mod:
            assert await coordinator._async_refresh_iot_credentials() is True

        ir_mod.async_delete_issue.assert_called_once()

    @pytest.mark.asyncio
    async def test_unexpected_error_is_not_fatal(self):
        """A network hiccup must not take the poll loop down with it."""
        coordinator = _coordinator()
        auth_client = MagicMock()
        auth_client.login = AsyncMock(side_effect=OSError("connection reset"))

        with _patched_client(auth_client), patch(
            "custom_components.govee.coordinator.ir"
        ):
            assert await coordinator._async_refresh_iot_credentials() is False


class TestRefreshReachesMqtt:
    """A refreshed credential set must reach the transport that authenticates with it."""

    @pytest.mark.asyncio
    async def test_running_client_is_restarted_with_new_credentials(self):
        coordinator = _coordinator()
        coordinator._mqtt_client = MagicMock()
        coordinator._mqtt_client.async_restart = AsyncMock(return_value=True)
        auth_client = MagicMock()
        auth_client.login = AsyncMock(return_value=FRESH)

        with _patched_client(auth_client):
            assert await coordinator._async_refresh_iot_credentials() is True

        coordinator._mqtt_client.async_restart.assert_awaited_once_with(FRESH)
        coordinator._start_mqtt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_client_that_never_started_is_started(self):
        """Login failed at boot -> no client; a later successful refresh arms MQTT."""
        coordinator = _coordinator()
        auth_client = MagicMock()
        auth_client.login = AsyncMock(return_value=FRESH)

        with _patched_client(auth_client):
            assert await coordinator._async_refresh_iot_credentials() is True

        coordinator._start_mqtt.assert_awaited_once()
