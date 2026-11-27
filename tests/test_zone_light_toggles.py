"""Named light toggles seen in the 2026-11 diagnostics, and two °F thermometers.

The H605A/H605B light bars advertise ``leftLightToggle`` / ``rightLightToggle`` /
``backLightToggle`` and the H607C floor lamp ``pillarLightToggle`` /
``baseLightToggle`` (#241, #306). Govee returns ``""`` for all of them on poll,
so each becomes an optimistic named light switch like the H60B3's (#126).

The H5102 (#209), H5107 (#91) and H5074 (#317) return °F-scale ``sensorTemperature`` with no
unit field, so they join FAHRENHEIT_REPORTING_SKUS for the API-key-only case.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.govee.const import resolve_fahrenheit_conversion
from custom_components.govee.models import GoveeCapability, GoveeDevice
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_TOGGLE,
    DEVICE_TYPE_LIGHT,
    INSTANCE_POWER,
)

ROOT = Path(__file__).resolve().parent.parent / "custom_components/govee"


def _cap(cap_type: str, instance: str) -> GoveeCapability:
    return GoveeCapability(type=cap_type, instance=instance, parameters={})


def _device(sku: str, toggles: tuple[str, ...]) -> GoveeDevice:
    return GoveeDevice(
        device_id=f"AA:BB:CC:DD:EE:FF:{sku[1:3]}:{sku[3:5]}",
        sku=sku,
        name=sku,
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            *(_cap(CAPABILITY_TOGGLE, t) for t in toggles),
            _cap(CAPABILITY_TOGGLE, "gradientToggle"),
            _cap(CAPABILITY_TOGGLE, "dreamViewToggle"),
        ),
    )


H605B_TOGGLES = ("leftLightToggle", "rightLightToggle", "backLightToggle")
H607C_TOGGLES = ("pillarLightToggle", "baseLightToggle")


async def _named_switches(device: GoveeDevice) -> dict:
    from custom_components.govee import switch as switch_mod

    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device}
    entry = MagicMock()
    entry.runtime_data = coordinator
    added: list = []
    await switch_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
    return {e._toggle_instance: e for e in added if type(e).__name__ == "GoveeNamedLightSwitchEntity"}


class TestDetection:
    def test_h605b_light_bar_toggles(self):
        assert _device("H605B", H605B_TOGGLES).named_light_toggle_instances == list(H605B_TOGGLES)

    def test_h607c_floor_lamp_toggles(self):
        assert _device("H607C", H607C_TOGGLES).named_light_toggle_instances == list(H607C_TOGGLES)


class TestSwitches:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("sku", "toggles", "suffixes"),
        [
            ("H605B", H605B_TOGGLES, ("left_light", "right_light", "back_light")),
            ("H605A", H605B_TOGGLES, ("left_light", "right_light", "back_light")),
            ("H607C", H607C_TOGGLES, ("pillar_light", "base_light")),
        ],
    )
    async def test_each_toggle_becomes_a_named_switch(self, sku, toggles, suffixes):
        device = _device(sku, toggles)
        named = await _named_switches(device)

        assert sorted(named) == sorted(toggles)
        for toggle, suffix in zip(toggles, suffixes):
            assert named[toggle]._attr_unique_id == f"{device.device_id}_{suffix}"
            assert named[toggle]._attr_translation_key == f"govee_{suffix}"

    @pytest.mark.parametrize("key", ["left", "right", "back", "pillar", "base"])
    def test_names_and_icons_exist_for_every_language(self, key):
        translation_key = f"govee_{key}_light"
        icons = json.loads((ROOT / "icons.json").read_text())
        assert icons["entity"]["switch"][translation_key]["default"].startswith("mdi:")
        for path in [ROOT / "strings.json", *sorted((ROOT / "translations").glob("*.json"))]:
            strings = json.loads(path.read_text())
            assert strings["entity"]["switch"][translation_key]["name"], path.name


class TestFahrenheitAllowlist:
    @pytest.mark.parametrize("sku", ["H5102", "H5107", "H5074"])
    def test_converts_in_auto_mode(self, sku):
        assert resolve_fahrenheit_conversion(sku, "auto") is True

    @pytest.mark.parametrize("sku", ["H5102", "H5107", "H5074"])
    def test_explicit_celsius_still_wins(self, sku):
        assert resolve_fahrenheit_conversion(sku, "celsius") is False
