"""Bluetooth discovery of the config flow, driven through the flow manager.

The manifest's Bluetooth matchers make Home Assistant start a flow with
source ``bluetooth`` for any Govee advertisement. The flow offers the cloud
set-up once and otherwise aborts, so a house full of Govee lights produces a
single discovery card.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_IGNORE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.bluetooth import BluetoothServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee.const import CONF_API_KEY, DOMAIN

API_KEY = "12345678-1234-1234-1234-123456789abc"


def _advertisement(name: str = "Govee_H6072_1234", address: str = "AA:BB:CC:DD:EE:FF") -> BluetoothServiceInfo:
    return BluetoothServiceInfo(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source="local",
    )


@pytest.fixture(autouse=True)
def _custom_integrations(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration without starting Bluetooth."""
    hass.config.components.add("bluetooth_adapters")
    hass.config.components.add("network")


@pytest.fixture(autouse=True)
def _no_setup():
    """Creating an entry must not start the real integration."""
    with patch("custom_components.govee.async_setup_entry", AsyncMock(return_value=True)):
        yield


@pytest.fixture(autouse=True)
def _valid_key():
    with patch("custom_components.govee.config_flow.validate_api_key", AsyncMock(return_value=True)):
        yield


async def _discover(hass: HomeAssistant, info: BluetoothServiceInfo | None = None):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info or _advertisement()
    )


async def test_discovery_offers_the_cloud_setup(hass: HomeAssistant) -> None:
    """A nearby Govee device leads, after confirmation, to the normal API key set-up."""
    result = await _discover(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {"name": "Govee_H6072_1234"}
    progress = hass.config_entries.flow.async_get(result["flow_id"])
    assert progress["context"]["title_placeholders"] == {"name": "Govee_H6072_1234"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: API_KEY})
    assert result["step_id"] == "account"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_API_KEY: API_KEY}
    assert result["result"].unique_id == DOMAIN


async def test_discovery_without_a_name_uses_the_address(hass: HomeAssistant) -> None:
    result = await _discover(hass, _advertisement(name="", address="11:22:33:44:55:66"))

    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"] == {"name": "11:22:33:44:55:66"}


async def test_discovery_aborts_when_an_entry_exists(hass: HomeAssistant) -> None:
    """One cloud account serves every device, so an existing entry ends the prompt."""
    MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, version=2).add_to_hass(hass)

    result = await _discover(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_discovery_aborts_when_ignored(hass: HomeAssistant) -> None:
    """Ignoring the discovery card silences later advertisements."""
    MockConfigEntry(domain=DOMAIN, source=SOURCE_IGNORE, unique_id=DOMAIN, data={}).add_to_hass(hass)

    result = await _discover(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_second_discovery_aborts_while_the_first_is_open(hass: HomeAssistant) -> None:
    """Advertisements from other Govee devices do not stack up discovery cards."""
    first = await _discover(hass)
    assert first["type"] is FlowResultType.FORM

    second = await _discover(hass, _advertisement(name="Govee_H6199_9999", address="AA:BB:CC:DD:EE:00"))

    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_in_progress"
    assert len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 1


async def test_setting_up_by_hand_closes_the_discovery_card(hass: HomeAssistant) -> None:
    """An entry made from the integrations page, not the card, ends the prompt too."""
    card = await _discover(hass)
    assert card["type"] is FlowResultType.FORM
    other = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: API_KEY})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [flow["flow_id"] for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)] == [
        other["flow_id"]
    ]
