"""Account, 2FA, reconfigure, and options steps driven through the flow manager.

Complements ``test_config_flow_manager.py`` (user and reauth steps) so every
step of the config flow is exercised through ``hass.config_entries`` rather
than by calling the flow class with a mocked ``hass``.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee.api import (
    Govee2FACodeInvalidError,
    Govee2FARequiredError,
    GoveeApiError,
    GoveeAuthError,
    GoveeIotCredentials,
    GoveeLoginRejectedError,
)
from custom_components.govee.config_flow import GoveeConfigFlow
from custom_components.govee.const import (
    CONF_API_KEY,
    CONF_API_TEMPERATURE_UNIT,
    CONF_EMAIL,
    CONF_ENABLE_GROUPS,
    CONF_LAN_TARGETS,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_SEGMENT_MODE_BY_DEVICE,
    DOMAIN,
    KEY_IOT_CREDENTIALS,
    KEY_IOT_LOGIN_FAILED,
    SEGMENT_MODE_GROUPED,
    SEGMENT_MODE_INDIVIDUAL,
)

API_KEY = "12345678-1234-1234-1234-123456789abc"
NEW_KEY = "abcdefab-abcd-abcd-abcd-abcdefabcdef"
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


@pytest.fixture(autouse=True)
def _no_setup():
    """Creating or reloading an entry must not start the real integration."""
    with patch("custom_components.govee.async_setup_entry", AsyncMock(return_value=True)):
        yield


@pytest.fixture(autouse=True)
def _valid_key():
    with patch("custom_components.govee.config_flow.validate_api_key", AsyncMock(return_value=True)):
        yield


@pytest.fixture
def auth_client():
    """A GoveeAuthClient stand-in usable as an async context manager."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.request_verification_code = AsyncMock()
    with patch("custom_components.govee.config_flow.GoveeAuthClient", return_value=client):
        yield client


async def _to_account_step(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: API_KEY})
    assert result["step_id"] == "account"
    return result["flow_id"]


async def test_account_step_stores_credentials_and_iot_material(hass: HomeAssistant) -> None:
    """A successful login stores the account and the IoT credentials it obtained."""
    flow_id = await _to_account_step(hass)
    with patch(
        "custom_components.govee.config_flow.validate_govee_credentials",
        AsyncMock(return_value=CREDS),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_EMAIL] == EMAIL
    assert result["data"][CONF_PASSWORD] == PASSWORD
    assert result["data"][KEY_IOT_CREDENTIALS]["token"] == "tok"


@pytest.mark.parametrize(
    ("user_input", "error"),
    [
        ({CONF_EMAIL: "not-an-email", CONF_PASSWORD: PASSWORD}, "invalid_email_format"),
        ({CONF_EMAIL: EMAIL}, "email_without_password"),
        ({CONF_PASSWORD: PASSWORD}, "password_without_email"),
    ],
)
async def test_account_step_input_validation(hass: HomeAssistant, user_input, error) -> None:
    flow_id = await _to_account_step(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (GoveeAuthError("bad password"), "invalid_account"),
        (GoveeLoginRejectedError("rejected"), "login_rejected"),
        (GoveeApiError("boom"), "cannot_connect"),
        (RuntimeError("unexpected"), "unknown"),
    ],
)
async def test_account_step_login_errors(hass: HomeAssistant, exc, error) -> None:
    flow_id = await _to_account_step(hass)
    with patch(
        "custom_components.govee.config_flow.validate_govee_credentials",
        AsyncMock(side_effect=exc),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "account"
    assert result["errors"] == {"base": error}


async def test_two_factor_round_trip(hass: HomeAssistant, auth_client) -> None:
    """2FA required -> code requested -> wrong code -> right code -> entry."""
    flow_id = await _to_account_step(hass)
    with patch(
        "custom_components.govee.config_flow.validate_govee_credentials",
        AsyncMock(side_effect=[Govee2FARequiredError(), Govee2FACodeInvalidError(), CREDS]),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD})
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "verification_code"
        assert result["description_placeholders"] == {"email": EMAIL}
        auth_client.request_verification_code.assert_awaited_once()

        result = await hass.config_entries.flow.async_configure(flow_id, {"verification_code": "0000"})
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_verification_code"}

        result = await hass.config_entries.flow.async_configure(flow_id, {"verification_code": "1234"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_EMAIL] == EMAIL
    assert result["data"][KEY_IOT_CREDENTIALS]["token"] == "tok"


async def test_two_factor_code_request_failure(hass: HomeAssistant, auth_client) -> None:
    """If Govee refuses to send the code, the account form shows cannot_connect."""
    auth_client.request_verification_code = AsyncMock(side_effect=GoveeApiError("no mail"))
    flow_id = await _to_account_step(hass)
    with patch(
        "custom_components.govee.config_flow.validate_govee_credentials",
        AsyncMock(side_effect=Govee2FARequiredError()),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "account"
    assert result["errors"] == {"base": "cannot_connect"}


def _entry(hass: HomeAssistant, **data) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY, **data}, version=2)
    entry.add_to_hass(hass)
    return entry


async def _start_reconfigure(hass: HomeAssistant, entry: MockConfigEntry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    return result["flow_id"]


async def test_reconfigure_replaces_key_and_removes_account(hass: HomeAssistant) -> None:
    entry = _entry(
        hass,
        **{CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD, KEY_IOT_LOGIN_FAILED: "2FA verification required"},
    )
    flow_id = await _start_reconfigure(hass, entry)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_API_KEY: NEW_KEY, CONF_EMAIL: "", CONF_PASSWORD: ""}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_API_KEY] == NEW_KEY
    assert CONF_EMAIL not in entry.data
    assert KEY_IOT_LOGIN_FAILED not in entry.data


async def test_reconfigure_keeps_password_for_unchanged_email(hass: HomeAssistant) -> None:
    entry = _entry(hass, **{CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD})
    flow_id = await _start_reconfigure(hass, entry)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_API_KEY: API_KEY, CONF_EMAIL: EMAIL, CONF_PASSWORD: ""}
    )

    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_PASSWORD] == PASSWORD


async def test_reconfigure_new_email_without_password_is_rejected(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    flow_id = await _start_reconfigure(hass, entry)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_API_KEY: API_KEY, CONF_EMAIL: "other@example.com", CONF_PASSWORD: ""}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "email_without_password"}


async def test_reconfigure_with_new_account_and_two_factor(hass: HomeAssistant, auth_client) -> None:
    """Reconfigure with credentials that need 2FA routes through the code step."""
    entry = _entry(hass)
    flow_id = await _start_reconfigure(hass, entry)
    with patch(
        "custom_components.govee.config_flow.validate_govee_credentials",
        AsyncMock(side_effect=[Govee2FARequiredError(), CREDS]),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_API_KEY: NEW_KEY, CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
        assert result["step_id"] == "verification_code"
        result = await hass.config_entries.flow.async_configure(flow_id, {"verification_code": "1234"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_API_KEY] == NEW_KEY
    assert entry.data[CONF_EMAIL] == EMAIL
    assert entry.data[KEY_IOT_CREDENTIALS]["token"] == "tok"


async def test_reconfigure_account_login_errors(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    flow_id = await _start_reconfigure(hass, entry)
    with patch(
        "custom_components.govee.config_flow.validate_govee_credentials",
        AsyncMock(side_effect=GoveeAuthError("nope")),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_API_KEY: API_KEY, CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_account"}


async def test_reconfigure_refuses_key_of_another_entry(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: NEW_KEY}, version=2).add_to_hass(hass)
    flow_id = await _start_reconfigure(hass, entry)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_API_KEY: NEW_KEY, CONF_EMAIL: "", CONF_PASSWORD: ""}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_API_KEY] == API_KEY


async def test_options_flow_without_rgbic_devices(hass: HomeAssistant) -> None:
    """With no segment-capable devices the global step saves straight away."""
    entry = _entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "general"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_POLL_INTERVAL: 120, CONF_ENABLE_GROUPS: True, CONF_API_TEMPERATURE_UNIT: "celsius"},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_POLL_INTERVAL] == 120
    assert entry.options[CONF_ENABLE_GROUPS] is True
    assert entry.options[CONF_API_TEMPERATURE_UNIT] == "celsius"


async def test_options_flow_rejects_bad_lan_targets(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_LAN_TARGETS: "not-an-ip"})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_LAN_TARGETS: "invalid_lan_targets"}


async def test_options_flow_configures_segment_modes(hass: HomeAssistant, mock_rgbic_device) -> None:
    """RGBIC devices get the per-device segment-mode steps after the global step."""
    entry = _entry(hass)
    entry.runtime_data = SimpleNamespace(devices={mock_rgbic_device.device_id: mock_rgbic_device})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_POLL_INTERVAL: 60})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_segment_devices"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"devices": [mock_rgbic_device.device_id]}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "configure_device_mode"
    assert result["description_placeholders"]["device_name"] == mock_rgbic_device.name

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"segment_mode": SEGMENT_MODE_GROUPED}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SEGMENT_MODE_BY_DEVICE] == {mock_rgbic_device.device_id: SEGMENT_MODE_GROUPED}
    assert entry.options[CONF_POLL_INTERVAL] == 60


# --------------------------------------------------------------------------- #
# Error branches of the user, verification, reauth, and reconfigure steps
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (GoveeAuthError("bad key"), "invalid_auth"),
        (GoveeApiError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_step_validation_errors(hass: HomeAssistant, exc, error) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch("custom_components.govee.config_flow.validate_api_key", AsyncMock(side_effect=exc)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: API_KEY})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}


@pytest.mark.parametrize("bad_key", ["", "too short", "12345678-1234-1234-1234-1234 56789abc"])
async def test_user_step_rejects_malformed_keys(hass: HomeAssistant, bad_key) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: bad_key})
    assert result["errors"] == {"base": "invalid_api_key_format"}


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (GoveeAuthError("bad password"), "invalid_account"),
        (GoveeApiError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_verification_step_errors(hass: HomeAssistant, auth_client, exc, error) -> None:
    flow_id = await _to_account_step(hass)
    with patch(
        "custom_components.govee.config_flow.validate_govee_credentials",
        AsyncMock(side_effect=[Govee2FARequiredError(), exc]),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD})
        assert result["step_id"] == "verification_code"
        result = await hass.config_entries.flow.async_configure(flow_id, {"verification_code": "1234"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def _start_reauth(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    return next(f for f in hass.config_entries.flow.async_progress() if f["handler"] == DOMAIN)["flow_id"]


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (GoveeAuthError("bad key"), "invalid_auth"),
        (GoveeApiError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_reauth_validation_errors(hass: HomeAssistant, exc, error) -> None:
    entry = _entry(hass)
    flow_id = await _start_reauth(hass, entry)
    with patch("custom_components.govee.config_flow.validate_api_key", AsyncMock(side_effect=exc)):
        result = await hass.config_entries.flow.async_configure(flow_id, {CONF_API_KEY: NEW_KEY})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": error}
    assert entry.data[CONF_API_KEY] == API_KEY


async def test_reauth_rejects_malformed_key(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    flow_id = await _start_reauth(hass, entry)
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_API_KEY: "short"})
    assert result["errors"] == {"base": "invalid_api_key_format"}


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (GoveeAuthError("bad key"), "invalid_auth"),
        (GoveeApiError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_reconfigure_key_validation_errors(hass: HomeAssistant, exc, error) -> None:
    entry = _entry(hass)
    flow_id = await _start_reconfigure(hass, entry)
    with patch("custom_components.govee.config_flow.validate_api_key", AsyncMock(side_effect=exc)):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_API_KEY: NEW_KEY, CONF_EMAIL: "", CONF_PASSWORD: ""}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_reconfigure_rejects_malformed_key(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    flow_id = await _start_reconfigure(hass, entry)
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_API_KEY: "short"})
    assert result["errors"] == {"base": "invalid_api_key_format"}


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (GoveeLoginRejectedError("rejected"), "login_rejected"),
        (GoveeApiError("down"), "cannot_connect"),
    ],
)
async def test_reconfigure_account_errors(hass: HomeAssistant, exc, error) -> None:
    entry = _entry(hass)
    flow_id = await _start_reconfigure(hass, entry)
    with patch(
        "custom_components.govee.config_flow.validate_govee_credentials",
        AsyncMock(side_effect=exc),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_API_KEY: API_KEY, CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
    assert result["errors"] == {"base": error}


async def test_reconfigure_password_without_email(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    flow_id = await _start_reconfigure(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_API_KEY: API_KEY, CONF_EMAIL: "", CONF_PASSWORD: PASSWORD}
    )
    assert result["errors"] == {"base": "password_without_email"}


async def test_reconfigure_two_factor_code_request_failure(hass: HomeAssistant, auth_client) -> None:
    auth_client.request_verification_code = AsyncMock(side_effect=GoveeApiError("no mail"))
    entry = _entry(hass)
    flow_id = await _start_reconfigure(hass, entry)
    with patch(
        "custom_components.govee.config_flow.validate_govee_credentials",
        AsyncMock(side_effect=Govee2FARequiredError()),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_API_KEY: API_KEY, CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_options_flow_no_device_selected_saves_global_options(hass: HomeAssistant, mock_rgbic_device) -> None:
    entry = _entry(hass)
    entry.runtime_data = SimpleNamespace(devices={mock_rgbic_device.device_id: mock_rgbic_device})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_POLL_INTERVAL: 90})
    assert result["step_id"] == "select_segment_devices"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"devices": []})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_POLL_INTERVAL] == 90
    assert CONF_SEGMENT_MODE_BY_DEVICE not in entry.options


async def test_verification_step_without_account_state_aborts(hass: HomeAssistant) -> None:
    """The code step cannot run before the account step stored an email and password.

    The flow manager never routes a flow here without them, so the guard is
    exercised on the flow class directly.
    """
    flow = GoveeConfigFlow()
    flow.hass = hass

    result = await flow.async_step_verification_code({"verification_code": "1234"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "missing_credentials"


async def test_cache_iot_credentials_skips_a_vanished_entry(hass: HomeAssistant) -> None:
    """Caching credentials for an entry removed mid-flow is a no-op, not an error."""
    flow = GoveeConfigFlow()
    flow.hass = hass
    flow._iot_credentials = CREDS

    flow._cache_iot_credentials("entry-that-no-longer-exists")


async def test_reconfigure_removing_account_drops_stored_iot_credentials(hass: HomeAssistant) -> None:
    """Clearing the account also discards the IoT material obtained with it."""
    entry = _entry(hass, **{CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD, KEY_IOT_CREDENTIALS: asdict(CREDS)})
    flow_id = await _start_reconfigure(hass, entry)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_API_KEY: API_KEY, CONF_EMAIL: "", CONF_PASSWORD: ""}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert KEY_IOT_CREDENTIALS not in entry.data
    assert CONF_EMAIL not in entry.data
    assert CONF_PASSWORD not in entry.data


async def test_options_flow_walks_every_selected_device(hass: HomeAssistant, mock_rgbic_device) -> None:
    """Selecting two RGBIC devices asks for a segment mode for each in turn."""
    second = replace(mock_rgbic_device, device_id="AA:BB:CC:DD:EE:FF:00:99", name="Second strip")
    entry = _entry(hass)
    entry.runtime_data = SimpleNamespace(
        devices={mock_rgbic_device.device_id: mock_rgbic_device, second.device_id: second}
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_POLL_INTERVAL: 60})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"devices": [mock_rgbic_device.device_id, second.device_id]}
    )
    assert result["step_id"] == "configure_device_mode"
    assert result["description_placeholders"]["device_name"] == mock_rgbic_device.name

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"segment_mode": SEGMENT_MODE_GROUPED}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "configure_device_mode"
    assert result["description_placeholders"]["device_name"] == "Second strip"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"segment_mode": SEGMENT_MODE_INDIVIDUAL}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SEGMENT_MODE_BY_DEVICE] == {
        mock_rgbic_device.device_id: SEGMENT_MODE_GROUPED,
        second.device_id: SEGMENT_MODE_INDIVIDUAL,
    }
