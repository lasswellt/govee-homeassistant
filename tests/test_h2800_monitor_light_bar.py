"""Tests for the H2800 Monitor Light Bar.

Capability shape taken from a real diagnostics download. Two problems:

* Its three light parts (``ambientLightToggle``, ``wallWashingLightToggle``,
  ``deskLightToggle``) got no switches: the named-toggle pattern only allowed
  all-lowercase prefixes (so ``wallWashingLightToggle`` was never detected) and
  the switch platform had no mapping for the other two.
* ``musicMode`` returned HTTP 400 "Missing parameter" because the capability
  declares ``rgb`` as required even when ``autoColor`` is 1.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.govee.const import (
    SUFFIX_AMBIENT_LIGHT,
    SUFFIX_DESK_LIGHT,
    SUFFIX_WALL_WASHING_LIGHT,
)
from custom_components.govee.models import GoveeCapability, GoveeDevice
from custom_components.govee.models.commands import MusicModeCommand
from custom_components.govee.models.device import (
    CAPABILITY_MUSIC_MODE,
    CAPABILITY_ON_OFF,
    CAPABILITY_TOGGLE,
    DEVICE_TYPE_LIGHT,
    INSTANCE_MUSIC_MODE,
    INSTANCE_POWER,
)
from custom_components.govee.switch import NAMED_LIGHT_TOGGLE_SPECS

_GOVEE_ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "govee"


def _cap(cap_type: str, instance: str, params: dict | None = None) -> GoveeCapability:
    return GoveeCapability(type=cap_type, instance=instance, parameters=params or {})


def _music_params(rgb_required: bool = True) -> dict:
    return {
        "dataType": "STRUCT",
        "fields": [
            {
                "fieldName": "musicMode",
                "dataType": "ENUM",
                "options": [{"name": "Rhythm", "value": 0}],
                "required": True,
            },
            {"fieldName": "sensitivity", "dataType": "INTEGER", "range": {"min": 0, "max": 100}, "required": True},
            {"fieldName": "autoColor", "dataType": "INTEGER", "range": {"min": 0, "max": 1}, "required": True},
            {
                "fieldName": "rgb",
                "dataType": "INTEGER",
                "range": {"min": 0, "max": 16777215},
                "required": rgb_required,
            },
        ],
    }


def _h2800(rgb_required: bool = True) -> GoveeDevice:
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:28:00",
        sku="H2800",
        name="Monitor Light Bar",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_TOGGLE, "gradientToggle"),
            _cap(CAPABILITY_MUSIC_MODE, INSTANCE_MUSIC_MODE, _music_params(rgb_required)),
            _cap(CAPABILITY_TOGGLE, "ambientLightToggle"),
            _cap(CAPABILITY_TOGGLE, "wallWashingLightToggle"),
            _cap(CAPABILITY_TOGGLE, "deskLightToggle"),
        ),
    )


class TestH2800LightToggles:
    def test_detects_all_three_parts(self):
        assert _h2800().named_light_toggle_instances == [
            "ambientLightToggle",
            "wallWashingLightToggle",
            "deskLightToggle",
        ]

    def test_mixed_case_prefix_does_not_catch_other_toggles(self):
        device = GoveeDevice(
            device_id="X",
            sku="H0000",
            name="x",
            device_type=DEVICE_TYPE_LIGHT,
            capabilities=(
                _cap(CAPABILITY_TOGGLE, "nightlightToggle"),
                _cap(CAPABILITY_TOGGLE, "light1Toggle"),
                _cap(CAPABILITY_TOGGLE, "gradientToggle"),
            ),
        )
        assert device.named_light_toggle_instances == []

    def test_specs_are_mapped(self):
        assert NAMED_LIGHT_TOGGLE_SPECS["ambientLightToggle"] == ("govee_ambient_light", SUFFIX_AMBIENT_LIGHT)
        assert NAMED_LIGHT_TOGGLE_SPECS["wallWashingLightToggle"] == (
            "govee_wall_washing_light",
            SUFFIX_WALL_WASHING_LIGHT,
        )
        assert NAMED_LIGHT_TOGGLE_SPECS["deskLightToggle"] == ("govee_desk_light", SUFFIX_DESK_LIGHT)

    @pytest.mark.asyncio
    async def test_switch_platform_creates_all_three(self):
        from custom_components.govee import switch as switch_mod

        device = _h2800()
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await switch_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))

        named = {e._toggle_instance: e for e in added if type(e).__name__ == "GoveeNamedLightSwitchEntity"}
        assert sorted(named) == ["ambientLightToggle", "deskLightToggle", "wallWashingLightToggle"]
        assert named["deskLightToggle"]._attr_unique_id == "AA:BB:CC:DD:EE:FF:28:00_desk_light"

    @pytest.mark.parametrize("key", ["govee_ambient_light", "govee_wall_washing_light", "govee_desk_light"])
    def test_strings_and_icons_carry_the_keys(self, key):
        strings = json.loads((_GOVEE_ROOT / "strings.json").read_text())
        assert strings["entity"]["switch"][key]["name"]
        icons = json.loads((_GOVEE_ROOT / "icons.json").read_text())
        assert icons["entity"]["switch"][key]["default"].startswith("mdi:")


class TestH2800MusicMode:
    def test_rgb_required_is_detected(self):
        assert _h2800().music_mode_requires_rgb is True
        assert _h2800(rgb_required=False).music_mode_requires_rgb is False

    def test_required_rgb_is_sent_with_auto_color(self):
        value = MusicModeCommand(music_mode=0, sensitivity=50, auto_color=1, rgb_required=True).get_value()
        assert value == {"musicMode": 0, "sensitivity": 50, "autoColor": 1, "rgb": 0xFFFFFF}

    def test_required_rgb_keeps_a_given_color(self):
        value = MusicModeCommand(
            music_mode=0, sensitivity=50, auto_color=0, rgb=0x00FF00, rgb_required=True
        ).get_value()
        assert value["rgb"] == 0x00FF00

    def test_default_behavior_unchanged(self):
        value = MusicModeCommand(music_mode=1, sensitivity=50, auto_color=1, rgb=0xFF0000).get_value()
        assert value == {"musicMode": 1, "sensitivity": 50, "autoColor": 1}
