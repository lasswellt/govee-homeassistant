"""Optional display labels for the H7175's custom slots.

The Developer API calls the slots only Custom 1-4. Labels can be set in the
options ("Kettle slot labels"); they are display text only, the mode state
stays ``custom_N``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee.const import CONF_API_KEY, CONF_KETTLE_SLOT_LABELS, CONF_POLL_INTERVAL, DOMAIN
from custom_components.govee.kettle.entities import GoveeKettleBrewModeSelect
from custom_components.govee.kettle.labels import (
    ERROR_DUPLICATE_SLOT_LABEL,
    ERROR_RESERVED_SLOT_LABEL,
    ERROR_SLOT_LABEL_TOO_LONG,
    slot_labels,
    validate_slot_labels,
)
from custom_components.govee.models import GoveeDevice, GoveeDeviceState
from custom_components.govee.water_heater import GoveeKettleWaterHeater

from .kettle_samples import DEVICE_ID, H7175_DEVICE, H7175_STATE

LABELS = {"custom_1": "Herbal tea", "custom_2": "Proofing", "custom_3": "Rooibos", "custom_4": "Hot cocoa"}
FIELDS = {f"slot_{n}": label for n, label in enumerate(LABELS.values(), start=1)}
SECOND = "AA:BB:CC:DD:71:75:00:02"


def _h7175(device_id: str = DEVICE_ID, name: str = "Kettle") -> GoveeDevice:
    return GoveeDevice.from_api_response({**H7175_DEVICE, "device": device_id, "deviceName": name})


class TestLabels:
    def test_valid(self):
        assert validate_slot_labels(_h7175(), LABELS) == {}
        assert validate_slot_labels(_h7175(), {"custom_1": "  ", "custom_2": ""}) == {}

    @pytest.mark.parametrize("label", ["Green Tea", "green tea ", "OFF", "manual", "Black Tea/Boil"])
    def test_reserved(self, label):
        assert validate_slot_labels(_h7175(), {"custom_1": label}) == {"custom_1": ERROR_RESERVED_SLOT_LABEL}

    def test_duplicates_and_length(self):
        errors = validate_slot_labels(_h7175(), {"custom_1": "Sencha", "custom_2": "sencha", "custom_3": "x" * 41})
        assert errors == {
            "custom_1": ERROR_DUPLICATE_SLOT_LABEL,
            "custom_2": ERROR_DUPLICATE_SLOT_LABEL,
            "custom_3": ERROR_SLOT_LABEL_TOO_LONG,
        }

    @pytest.mark.parametrize(
        ("options", "labels"),
        [
            ({CONF_KETTLE_SLOT_LABELS: {DEVICE_ID: LABELS}}, LABELS),
            (
                {CONF_KETTLE_SLOT_LABELS: {DEVICE_ID: {"custom_1": "Coffee", "custom_2": 7, "custom_3": " Rooibos "}}},
                {"custom_3": "Rooibos"},
            ),
            ({CONF_KETTLE_SLOT_LABELS: "bad"}, {}),
            ({CONF_KETTLE_SLOT_LABELS: {DEVICE_ID: ["bad"]}}, {}),
            ({}, {}),
        ],
    )
    def test_saved_labels_hand_edited_options_do_not_break(self, options, labels):
        assert slot_labels(options, _h7175()) == labels


class TestEntities:
    def _coordinator(self, options):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.update_from_api(H7175_STATE)
        coordinator = MagicMock()
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.config_entry = SimpleNamespace(options=options)
        return coordinator

    def test_labels_attribute_and_stable_states(self):
        coordinator = self._coordinator({CONF_KETTLE_SLOT_LABELS: {DEVICE_ID: LABELS}})
        select = GoveeKettleBrewModeSelect(coordinator, _h7175())
        heater = GoveeKettleWaterHeater(coordinator, _h7175())
        assert select.options[:4] == ["custom_1", "custom_2", "custom_3", "custom_4"]
        assert select.extra_state_attributes["labels"] == LABELS
        assert heater.extra_state_attributes["labels"] == LABELS

    def test_no_labels_no_attribute(self):
        select = GoveeKettleBrewModeSelect(self._coordinator({}), _h7175())
        assert "labels" not in select.extra_state_attributes
        coordinator = self._coordinator({})
        coordinator.config_entry = None
        assert "labels" not in GoveeKettleBrewModeSelect(coordinator, _h7175()).extra_state_attributes


# --------------------------------------------------------------------------- #
# Options flow
# --------------------------------------------------------------------------- #


def _entry(hass: HomeAssistant, *devices: GoveeDevice, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "k"}, options=options or {}, version=2)
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    for device in devices:
        registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, device.device_id)})
    if devices:
        entry.runtime_data = SimpleNamespace(devices={d.device_id: d for d in devices})
    return entry


async def _open_slots(hass: HomeAssistant, entry: MockConfigEntry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["general", "kettle_slots"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "kettle_slots"})
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "kettle_slots")
    return result


def _suggested(result) -> dict:
    return {str(key): key.description["suggested_value"] for key in result["data_schema"].schema}


@pytest.fixture
def _custom_integrations(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    """Load the integration without starting Bluetooth (as in test_setup_entry)."""
    hass.config.components.add("bluetooth_adapters")
    hass.config.components.add("network")


@pytest.mark.usefixtures("_custom_integrations")
class TestOptionsFlow:
    async def test_labels_saved_and_other_options_kept(self, hass: HomeAssistant):
        entry = _entry(hass, _h7175(), options={CONF_POLL_INTERVAL: 90})
        result = await _open_slots(hass, entry)
        assert result["description_placeholders"] == {"device_name": "Kettle"}
        assert list(_suggested(result)) == ["slot_1", "slot_2", "slot_3", "slot_4"]
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {**FIELDS, "slot_2": " Proofing "}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_KETTLE_SLOT_LABELS] == {DEVICE_ID: LABELS}
        assert entry.options[CONF_POLL_INTERVAL] == 90

    async def test_invalid_labels_on_their_fields(self, hass: HomeAssistant):
        entry = _entry(hass, _h7175())
        result = await _open_slots(hass, entry)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"slot_1": "Coffee", "slot_2": "Sencha", "slot_3": "sencha"}
        )
        assert result["errors"] == {
            "slot_1": ERROR_RESERVED_SLOT_LABEL,
            "slot_2": ERROR_DUPLICATE_SLOT_LABEL,
            "slot_3": ERROR_DUPLICATE_SLOT_LABEL,
        }
        assert _suggested(result)["slot_2"] == "Sencha"
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"slot_4": "Sencha"})
        assert entry.options[CONF_KETTLE_SLOT_LABELS] == {DEVICE_ID: {"custom_4": "Sencha"}}

    async def test_prefilled_and_clearing_removes_them(self, hass: HomeAssistant):
        entry = _entry(hass, _h7175(), options={CONF_KETTLE_SLOT_LABELS: {DEVICE_ID: LABELS}})
        result = await _open_slots(hass, entry)
        assert _suggested(result) == FIELDS
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert CONF_KETTLE_SLOT_LABELS not in entry.options

    async def test_one_form_per_kettle(self, hass: HomeAssistant):
        entry = _entry(hass, _h7175(), _h7175(SECOND, "Office kettle"))
        result = await _open_slots(hass, entry)
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"slot_1": "Herbal tea"})
        assert result["description_placeholders"]["device_name"] == "Office kettle"
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"slot_1": "Genmaicha"})
        assert entry.options[CONF_KETTLE_SLOT_LABELS] == {
            DEVICE_ID: {"custom_1": "Herbal tea"},
            SECOND: {"custom_1": "Genmaicha"},
        }

    async def test_a_kettle_vanishing_mid_flow_aborts(self, hass: HomeAssistant):
        entry = _entry(hass, _h7175(), _h7175(SECOND, "Office kettle"))
        result = await _open_slots(hass, entry)
        del entry.runtime_data.devices[SECOND]
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"slot_1": "Herbal tea"})
        assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "kettle_unavailable")

    async def test_general_settings_keep_labels_and_prune_only_removed_devices(self, hass: HomeAssistant):
        """A kettle missing from the device list (a transient gap) keeps its labels."""
        entry = _entry(
            hass, _h7175(), options={CONF_KETTLE_SLOT_LABELS: {DEVICE_ID: LABELS, SECOND: LABELS, "gone": LABELS}}
        )
        dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, SECOND)})
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "general"})
        assert result["step_id"] == "general"
        result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_POLL_INTERVAL: 120})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_POLL_INTERVAL] == 120
        assert entry.options[CONF_KETTLE_SLOT_LABELS] == {DEVICE_ID: LABELS, SECOND: LABELS}

    async def test_not_loaded_or_no_slots_opens_the_general_form(self, hass: HomeAssistant):
        entry = _entry(hass, options={CONF_KETTLE_SLOT_LABELS: "bad"})
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "general")
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        assert CONF_KETTLE_SLOT_LABELS not in entry.options
        plain = GoveeDevice.from_api_response(
            {**H7175_DEVICE, "capabilities": [c for c in H7175_DEVICE["capabilities"] if c["instance"] != "workMode"]}
        )
        result = await hass.config_entries.options.async_init(_entry(hass, plain).entry_id)
        assert result["step_id"] == "general"
