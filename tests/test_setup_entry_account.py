"""Entry setup with account login, setup failures, and service calls through Home Assistant.

Complements ``test_setup_entry.py``: these drive ``async_setup_entry`` with
email/password configured (cached credentials, failure marker, fresh login,
2FA, rejected login), the not-ready and auth-failed outcomes, and the
service actions resolved through the device registry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee.api import (
    Govee2FARequiredError,
    GoveeApiError,
    GoveeAuthError,
    GoveeConnectionError,
    GoveeIotCredentials,
    GoveeLoginRejectedError,
)
from custom_components.govee.registry import get_registry_device
from custom_components.govee.const import (
    CONF_API_KEY,
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
    KEY_IOT_CREDENTIALS,
    KEY_IOT_LOGIN_FAILED,
)

API_KEY = "12345678-1234-1234-1234-123456789abc"
EMAIL = "user@example.com"
PASSWORD = "app-password"

CREDS = GoveeIotCredentials(
    token="tok",
    refresh_token="ref",
    account_topic="GA/abc",
    iot_cert="cert",
    iot_key="key",
    iot_ca=None,
    client_id="AP/1/abc",
    endpoint="example.iot.amazonaws.com",
)


@pytest.fixture(autouse=True)
def _custom_integrations(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration without starting Bluetooth."""
    hass.config.components.add("bluetooth_adapters")
    hass.config.components.add("network")


def _network_error() -> GoveeApiError:
    """What login() raises when DNS/connect fails: chained to the aiohttp error."""
    err = GoveeApiError("Connection error during login: Cannot connect to host")
    err.__cause__ = aiohttp.ClientConnectionError("dns")
    return err


def _api_client(device, state, *, devices_error: Exception | None = None) -> MagicMock:
    client = MagicMock(name="GoveeApiClient")
    if devices_error is not None:
        client.get_devices = AsyncMock(side_effect=devices_error)
    else:
        client.get_devices = AsyncMock(return_value=[device])
    client.get_device_state = AsyncMock(return_value=state)
    client.get_dynamic_scenes = AsyncMock(return_value=[])
    client.get_diy_scenes = AsyncMock(return_value=[])
    client.control_device = AsyncMock(return_value=True)
    client.record_local_command = MagicMock()
    client.peek_last_command_record = MagicMock(return_value=None)
    client.close = AsyncMock()
    client.api_key = API_KEY
    client.rate_limit_remaining = 100
    client.rate_limit_total = 100
    client.rate_limit_reset = 0
    client.requests_last_24h = 0
    client.requests_today = 0
    client.requests_per_hour = 0.0
    client.last_raw_state = {}
    client.last_raw_devices = []
    client.recent_commands = []
    # Persisted at unload; the store needs real data, not a mock.
    client.export_request_counts = MagicMock(return_value={})
    return client


def _auth_client(login=None) -> MagicMock:
    """A GoveeAuthClient stand-in for both the entry setup and the coordinator."""
    client = MagicMock(name="GoveeAuthClient")
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.login = AsyncMock(side_effect=login) if isinstance(login, Exception) else AsyncMock(return_value=login)
    client.fetch_device_topics = AsyncMock(return_value={})
    client.gateway_routes = MagicMock(return_value={})
    client.fetch_bff_leak_sensors = AsyncMock(return_value=([], {}, {}))
    client.fetch_bff_thermo_hygrometers = AsyncMock(return_value=[])
    client.bff_device_census = MagicMock(return_value=[])
    client.bff_response_skeleton = MagicMock(return_value=None)
    client.bff_device_values = MagicMock(return_value=[])
    return client


def _mqtt_client() -> MagicMock:
    mqtt = MagicMock(name="GoveeAwsIotClient")
    mqtt.available = True
    mqtt.connected = False
    mqtt.async_start = AsyncMock()
    mqtt.async_stop = AsyncMock()
    mqtt.async_restart = AsyncMock()
    mqtt.last_messages = {}
    mqtt.last_message_ts = None
    mqtt.last_message_ts_for = MagicMock(return_value=None)
    mqtt.recent_multisync = []
    mqtt.recent_probe_frames = []
    mqtt.fan_swing_tail = MagicMock(return_value=None)
    return mqtt


def _events_client() -> MagicMock:
    events = MagicMock(name="GoveeOpenApiEventClient")
    events.async_start = AsyncMock()
    events.async_stop = AsyncMock()
    events.available = True
    events.connected = False
    events.recent_events = []
    return events


class _Stubs:
    """Context manager patching every network boundary of the entry setup."""

    def __init__(self, api_client: MagicMock, auth_client: MagicMock) -> None:
        self._patches = [
            patch("custom_components.govee.GoveeApiClient", return_value=api_client),
            patch("custom_components.govee.GoveeAuthClient", return_value=auth_client),
            patch("custom_components.govee.coordinator.GoveeAuthClient", return_value=auth_client),
            patch("custom_components.govee.coordinator.GoveeAwsIotClient", return_value=_mqtt_client()),
            patch("custom_components.govee.coordinator.GoveeOpenApiEventClient", return_value=_events_client()),
            patch("custom_components.govee.coordinator.GoveeCoordinator._async_setup_lan", AsyncMock()),
            patch("custom_components.govee.coordinator.GoveeCoordinator.setup_ble_subscriptions", return_value=[]),
        ]

    def __enter__(self) -> "_Stubs":
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc) -> None:
        for p in self._patches:
            p.stop()


def _entry(hass: HomeAssistant, **data) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: API_KEY, CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD, **data},
        options={},
        version=2,
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, api_client, auth_client) -> bool:
    with _Stubs(api_client, auth_client):
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return ok


async def test_fresh_login_persists_credentials(hass, mock_light_device, mock_device_state) -> None:
    entry = _entry(hass)
    auth = _auth_client(login=CREDS)

    assert await _setup(hass, entry, _api_client(mock_light_device, mock_device_state), auth)

    auth.login.assert_awaited_once()
    assert entry.data[KEY_IOT_CREDENTIALS]["token"] == "tok"
    assert entry.runtime_data.has_iot_credentials is True
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_cached_credentials_skip_login(hass, mock_light_device, mock_device_state) -> None:
    from dataclasses import asdict

    entry = _entry(hass, **{KEY_IOT_CREDENTIALS: asdict(CREDS)})
    auth = _auth_client(login=CREDS)

    assert await _setup(hass, entry, _api_client(mock_light_device, mock_device_state), auth)

    auth.login.assert_not_awaited()
    assert entry.runtime_data.has_iot_credentials is True
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_login_failure_marker_skips_login(hass, mock_light_device, mock_device_state) -> None:
    entry = _entry(hass, **{KEY_IOT_LOGIN_FAILED: "bad password"})
    auth = _auth_client(login=CREDS)

    assert await _setup(hass, entry, _api_client(mock_light_device, mock_device_state), auth)

    auth.login.assert_not_awaited()
    assert entry.runtime_data.has_iot_credentials is False
    # Repairs do not survive a restart; the skip must raise it again so the
    # user can see why real-time updates are off and retry with one click.
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"mqtt_disconnected_{entry.entry_id}")
    assert issue is not None
    assert issue.is_fixable is True


async def test_two_factor_marker_skips_login_and_reraises_issue(hass, mock_light_device, mock_device_state) -> None:
    entry = _entry(hass, **{KEY_IOT_LOGIN_FAILED: "2FA verification required"})
    auth = _auth_client(login=CREDS)

    assert await _setup(hass, entry, _api_client(mock_light_device, mock_device_state), auth)

    auth.login.assert_not_awaited()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"mqtt_2fa_required_{entry.entry_id}") is not None


async def test_two_factor_required_creates_issue_and_marker(hass, mock_light_device, mock_device_state) -> None:
    entry = _entry(hass)

    assert await _setup(
        hass, entry, _api_client(mock_light_device, mock_device_state), _auth_client(login=Govee2FARequiredError())
    )

    assert entry.data[KEY_IOT_LOGIN_FAILED] == "2FA verification required"
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"mqtt_2fa_required_{entry.entry_id}") is not None
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    "error",
    [
        GoveeAuthError("wrong password"),
        GoveeLoginRejectedError("Login rejected (status 400): no"),
        # Got past the network: would fail the same way on every retry.
        GoveeApiError("No token in login response"),
        RuntimeError("boom"),
    ],
)
async def test_rejected_login_creates_mqtt_issue(hass, mock_light_device, mock_device_state, error) -> None:
    entry = _entry(hass)

    assert await _setup(hass, entry, _api_client(mock_light_device, mock_device_state), _auth_client(login=error))

    assert entry.data[KEY_IOT_LOGIN_FAILED] == str(error)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"mqtt_disconnected_{entry.entry_id}")
    assert issue is not None
    assert issue.is_fixable is True
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data._login_retry_unsub is None


@pytest.mark.parametrize("error", [_network_error(), TimeoutError()])
async def test_transient_login_failure_stores_no_marker_and_retries(
    hass, mock_light_device, mock_device_state, error
) -> None:
    """A network/DNS error at boot must not switch real-time updates off for good."""
    entry = _entry(hass)
    auth = _auth_client(login=error)

    assert await _setup(hass, entry, _api_client(mock_light_device, mock_device_state), auth)

    assert KEY_IOT_LOGIN_FAILED not in entry.data
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"mqtt_disconnected_{entry.entry_id}") is not None
    assert entry.state is ConfigEntryState.LOADED
    # The coordinator keeps retrying in the background.
    assert entry.runtime_data._login_retry_unsub is not None

    # And the next setup (restart / reload) tries the login again.
    assert await hass.config_entries.async_unload(entry.entry_id)
    auth.login.reset_mock()
    auth.login.side_effect = None
    auth.login.return_value = CREDS
    assert await _setup(hass, entry, _api_client(mock_light_device, mock_device_state), auth)
    auth.login.assert_awaited_once()
    assert entry.data[KEY_IOT_CREDENTIALS]["token"] == "tok"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_network_down_at_boot_retries_login_with_setup(hass, mock_light_device, mock_device_state) -> None:
    """The not-ready retry in the same boot logs in again instead of skipping it."""
    entry = _entry(hass)
    auth = _auth_client(login=_network_error())
    api = _api_client(mock_light_device, mock_device_state, devices_error=GoveeConnectionError("dns"))

    assert not await _setup(hass, entry, api, auth)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert KEY_IOT_LOGIN_FAILED not in entry.data

    auth.login.reset_mock()
    auth.login.side_effect = None
    auth.login.return_value = CREDS
    with _Stubs(_api_client(mock_light_device, mock_device_state), auth):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    auth.login.assert_awaited_once()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.has_iot_credentials is True
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_cloud_unreachable_retries_setup(hass, mock_light_device, mock_device_state) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options={}, version=2)
    entry.add_to_hass(hass)
    api = _api_client(mock_light_device, mock_device_state, devices_error=GoveeConnectionError("dns"))

    assert not await _setup(hass, entry, api, _auth_client())

    assert entry.state is ConfigEntryState.SETUP_RETRY
    api.close.assert_awaited()


async def test_invalid_key_starts_reauth(hass, mock_light_device, mock_device_state) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options={}, version=2)
    entry.add_to_hass(hass)
    api = _api_client(mock_light_device, mock_device_state, devices_error=GoveeAuthError("bad key"))

    assert not await _setup(hass, entry, api, _auth_client())

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["handler"] == DOMAIN and flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_services_resolve_registry_and_govee_ids(hass, mock_rgbic_device, mock_device_state) -> None:
    """Both ID forms reach the coordinator; unknown IDs raise a validation error."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options={}, version=2)
    entry.add_to_hass(hass)
    api = _api_client(mock_rgbic_device, mock_device_state)
    with _Stubs(api, _auth_client()), patch("custom_components.govee.coordinator.asyncio.sleep", AsyncMock()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        ha_device = get_registry_device(dr.async_get(hass), (DOMAIN, mock_rgbic_device.device_id), entry.entry_id)
        assert ha_device is not None

        await hass.services.async_call(
            DOMAIN,
            "set_segment_color",
            {"device_id": ha_device.id, "segments": [0], "rgb_color": [1, 2, 3]},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            "refresh_scenes",
            {"device_id": mock_rgbic_device.device_id},
            blocking=True,
        )
        await hass.services.async_call(DOMAIN, "refresh_scenes", {}, blocking=True)

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, "refresh_scenes", {"device_id": "00:00:00:00:00:00:00:00"}, blocking=True
            )

    api.control_device.assert_awaited_once()
    sent_id, _sku, command = api.control_device.await_args.args
    assert sent_id == mock_rgbic_device.device_id
    assert command.segment_indices == (0,)
    assert api.get_dynamic_scenes.await_count >= 2
