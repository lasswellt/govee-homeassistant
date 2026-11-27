"""Config flow driven through Home Assistant's flow manager.

The unit tests in ``test_config_flow.py`` call the flow class directly with a
mocked ``hass``; these run the user and reauth steps through
``hass.config_entries.flow`` so step IDs, abort reasons, ``strings.json`` keys,
and entry creation are exercised for real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee.const import CONF_API_KEY, DOMAIN

API_KEY = "12345678-1234-1234-1234-123456789abc"
OTHER_KEY = "abcdefab-abcd-abcd-abcd-abcdefabcdef"


@pytest.fixture(autouse=True)
def _custom_integrations(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    """Let Home Assistant load ``custom_components.govee``.

    The integration depends on ``bluetooth_adapters`` and ``network``; mark
    them set up so Home Assistant does not start the Bluetooth stack here.
    """
    hass.config.components.add("bluetooth_adapters")
    hass.config.components.add("network")


@pytest.fixture(autouse=True)
def _no_setup():
    """Creating or reloading an entry must not start the real integration."""
    with patch("custom_components.govee.async_setup_entry", AsyncMock(return_value=True)):
        yield


@pytest.fixture
def _valid_key():
    with patch("custom_components.govee.config_flow.validate_api_key", AsyncMock(return_value=True)) as mock:
        yield mock


async def test_user_flow_creates_entry(hass: HomeAssistant, _valid_key) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: API_KEY})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "account"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Govee"
    assert result["data"] == {CONF_API_KEY: API_KEY}
    _valid_key.assert_awaited_once()


async def test_user_flow_rejects_malformed_key_without_calling_govee(hass: HomeAssistant, _valid_key) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "short"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_api_key_format"}
    _valid_key.assert_not_awaited()


async def test_user_flow_aborts_on_duplicate_key(hass: HomeAssistant, _valid_key) -> None:
    """The same API key cannot be configured twice (rule unique-config-entry)."""
    MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: API_KEY})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    _valid_key.assert_not_awaited()


async def test_reauth_replaces_key(hass: HomeAssistant, _valid_key, caplog: pytest.LogCaptureFixture) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, version=2)
    entry.add_to_hass(hass)
    entry.add_update_listener(AsyncMock())

    entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    flow = next(f for f in hass.config_entries.flow.async_progress() if f["handler"] == DOMAIN)
    assert flow["step_id"] == "reauth_confirm"

    with patch.object(hass.config_entries, "async_schedule_reload") as schedule:
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_API_KEY: OTHER_KEY})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == OTHER_KEY
    schedule.assert_called_once_with(entry.entry_id)
    assert "has an update listener" not in caplog.text


async def test_reauth_refuses_key_of_another_entry(hass: HomeAssistant, _valid_key) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, version=2)
    entry.add_to_hass(hass)
    MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: OTHER_KEY}, version=2).add_to_hass(hass)

    entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    flow = next(f for f in hass.config_entries.flow.async_progress() if f["handler"] == DOMAIN)

    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_API_KEY: OTHER_KEY})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_API_KEY] == API_KEY
