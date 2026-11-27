"""H60A6 Ceiling Light: main / background zone state from the AWS IoT push.

The Developer API returns ``""`` for ``mainLightToggle`` and
``backgroundLightToggle`` on every poll, so the two zone switches only ever
showed what Home Assistant last sent. The fixture does report both zones, in
the same ``aa 30 <main> <ring>`` frame as the H1252 (#223). Unlike the H1252
its REST toggles work, so only the read side changes: writes stay on REST.

The frames below are real H60A6 pushes (WiFi FW 1.00.49, HW 1.04.03),
captured from the integration's diagnostics while toggling the fixture from
Home Assistant: power on, main off, main on + background off, power off. The
power-on and power-off payloads are verbatim; for the two zone steps only
the frame list was captured, so their other keys repeat the power-on push.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.govee.const import (
    CONF_API_KEY,
    DOMAIN,
    LIGHT_ZONE_FRAME_SKUS,
    PTREAL_LIGHT_ZONE_SKUS,
    SUFFIX_BACKGROUND_LIGHT,
    SUFFIX_MAIN_LIGHT,
)
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState, RGBColor, ToggleCommand
from custom_components.govee.models.device import (
    INSTANCE_BACKGROUND_LIGHT_TOGGLE,
    INSTANCE_MAIN_LIGHT_TOGGLE,
)

from tests.test_setup_entry import API_KEY, _api_client, _stub_transports

DEV = "AA:BB:CC:DD:EE:FF:60:A6"

# The frames every H60A6 push carries ahead of the zone frame, verbatim.
_OTHER_FRAMES = [
    "aa051501166c00000000000000000000000000c1",
    "aa11001e0f0f00000000000000000000000000a5",
    "aa1200640000800f000000000000000000000053",
    "aa23ff0000008000000080000000800000008076",
    "aaf100000000000000000000000000000000005b",
    "aaa501640000006400000064000000640000000e",
    "aaa502640000006400000064000000640000000d",
    "aaa503640000006400000064000000640000000c",
    "aaa504640000000000000000000000000000006f",
    "aa410200000000000000000000000000000000e9",
]


def _push(on_off: int, zone_frame: str, stc: str = "0_0_53_58521_0_0_0_400004_33") -> dict[str, Any]:
    return {
        "onOff": on_off,
        "mode": 21,
        "stc": stc,
        "brightness": 7,
        "color": {"r": 0, "g": 0, "b": 0},
        "colorTemInKelvin": 5740,
        "result": 1,
        "_op_frames": [*_OTHER_FRAMES, zone_frame],
    }


PUSH_POWER_ON = _push(1, "aa3001010000000000000000000000000000009a", "0_0_53_58521_0_0_0_400004_33")
PUSH_MAIN_OFF = _push(1, "aa3000010000000000000000000000000000009b")
PUSH_BACKGROUND_OFF = _push(1, "aa3001000000000000000000000000000000009b")
PUSH_POWER_OFF = _push(0, "aa3000000000000000000000000000000000009a", "0_0_53_58585_0_0_0_400004_39")

_ON_OFF = {"dataType": "ENUM", "options": [{"name": "on", "value": 1}, {"name": "off", "value": 0}]}


def _h60a6() -> GoveeDevice:
    """The H60A6 exactly as the Developer API lists it."""
    return GoveeDevice(
        device_id=DEV,
        sku="H60A6",
        name="Ceiling Light",
        device_type="devices.types.light",
        capabilities=(
            GoveeCapability("devices.capabilities.on_off", "powerSwitch", _ON_OFF),
            GoveeCapability(
                "devices.capabilities.range",
                "brightness",
                {"unit": "unit.percent", "dataType": "INTEGER", "range": {"min": 1, "max": 100, "precision": 1}},
            ),
            GoveeCapability(
                "devices.capabilities.color_setting",
                "colorRgb",
                {"dataType": "INTEGER", "range": {"min": 0, "max": 16777215, "precision": 1}},
            ),
            GoveeCapability(
                "devices.capabilities.color_setting",
                "colorTemperatureK",
                {"dataType": "INTEGER", "range": {"min": 2700, "max": 6500, "precision": 1}},
            ),
            GoveeCapability("devices.capabilities.dynamic_scene", "lightScene", {"dataType": "ENUM", "options": []}),
            GoveeCapability("devices.capabilities.dynamic_scene", "diyScene", {"dataType": "ENUM", "options": []}),
            GoveeCapability("devices.capabilities.dynamic_scene", "snapshot", {"dataType": "ENUM", "options": []}),
            GoveeCapability("devices.capabilities.toggle", INSTANCE_MAIN_LIGHT_TOGGLE, _ON_OFF),
            GoveeCapability("devices.capabilities.toggle", INSTANCE_BACKGROUND_LIGHT_TOGGLE, _ON_OFF),
        ),
        is_group=False,
    )


def _polled_state() -> GoveeDeviceState:
    """What the Developer API poll yields: power and colour, no zone values."""
    return GoveeDeviceState(
        device_id=DEV,
        online=True,
        power_state=False,
        brightness=7,
        color=RGBColor(0, 0, 0),
        color_temp_kelvin=5740,
        active_scene=None,
        source="api",
    )


@pytest.fixture(autouse=True)
def _custom_integrations(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    hass.config.components.add("bluetooth_adapters")
    hass.config.components.add("network")


def test_h60a6_reads_zone_frames_but_keeps_rest_writes() -> None:
    assert "H60A6" in LIGHT_ZONE_FRAME_SKUS
    assert "H1252" in LIGHT_ZONE_FRAME_SKUS
    # Writing zones over ptReal stays H1252-only: the H60A6's REST toggles work.
    assert "H60A6" not in PTREAL_LIGHT_ZONE_SKUS


async def _setup(hass: HomeAssistant) -> tuple[MockConfigEntry, MagicMock, dict[str, str]]:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options={}, version=2)
    entry.add_to_hass(hass)
    client = _api_client(_h60a6(), _polled_state())
    client.control_device = AsyncMock(return_value=True)
    patches = _stub_transports(hass, client)
    for p in patches:
        p.start()
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        for p in patches:
            p.stop()
    registry = er.async_get(hass)
    ids = {
        "main": registry.async_get_entity_id("switch", DOMAIN, f"{DEV}{SUFFIX_MAIN_LIGHT}"),
        "background": registry.async_get_entity_id("switch", DOMAIN, f"{DEV}{SUFFIX_BACKGROUND_LIGHT}"),
        "light": registry.async_get_entity_id("light", DOMAIN, DEV),
    }
    assert all(ids.values()), ids
    return entry, client, ids


def _states(hass: HomeAssistant, ids: dict[str, str]) -> tuple[str, str, str]:
    return tuple(hass.states.get(ids[key]).state for key in ("light", "main", "background"))  # type: ignore[return-value]


async def test_zone_switches_follow_the_real_pushes(hass: HomeAssistant) -> None:
    """Replay the captured sequence; both switches show what the fixture reported."""
    entry, _client, ids = await _setup(hass)
    coordinator = entry.runtime_data

    for payload, expected in (
        (PUSH_POWER_ON, ("on", "on", "on")),
        (PUSH_MAIN_OFF, ("on", "off", "on")),
        (PUSH_BACKGROUND_OFF, ("on", "on", "off")),
        (PUSH_POWER_OFF, ("off", "off", "off")),
    ):
        coordinator._on_mqtt_state_update(DEV, dict(payload))
        await hass.async_block_till_done()
        assert _states(hass, ids) == expected, payload["_op_frames"][-1]

    await hass.config_entries.async_unload(entry.entry_id)


async def test_a_push_replaces_a_stale_restored_state(hass: HomeAssistant) -> None:
    """The reported state wins over what was restored from before a restart.

    This is the case seen live: the background switch restored "on" while the
    whole fixture was off.
    """
    mock_restore_cache(
        hass,
        [
            State("switch.ceiling_light_background_light", "on"),
            State("switch.ceiling_light_main_light", "on"),
        ],
    )
    entry, _client, ids = await _setup(hass)
    coordinator = entry.runtime_data
    assert _states(hass, ids)[1:] == ("on", "on")

    coordinator._on_mqtt_state_update(DEV, dict(PUSH_POWER_OFF))
    await hass.async_block_till_done()

    assert _states(hass, ids) == ("off", "off", "off")
    await hass.config_entries.async_unload(entry.entry_id)


async def test_a_poll_does_not_wipe_the_reported_zones(hass: HomeAssistant) -> None:
    """The poll returns "" for both toggles; the last report must survive it."""
    entry, _client, ids = await _setup(hass)
    coordinator = entry.runtime_data
    coordinator._on_mqtt_state_update(DEV, dict(PUSH_MAIN_OFF))
    await hass.async_block_till_done()

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _states(hass, ids)[1:] == ("off", "on")
    await hass.config_entries.async_unload(entry.entry_id)


async def test_switching_a_zone_still_goes_over_rest(hass: HomeAssistant) -> None:
    """The write path is unchanged for the H60A6, and the next push confirms it."""
    entry, client, ids = await _setup(hass)
    coordinator = entry.runtime_data
    coordinator._on_mqtt_state_update(DEV, dict(PUSH_POWER_ON))
    await hass.async_block_till_done()
    coordinator._ble_manager = MagicMock(available=True, async_send_ble_packet=AsyncMock(return_value=True))

    await hass.services.async_call("switch", "turn_off", {"entity_id": ids["background"]}, blocking=True)

    client.control_device.assert_awaited_with(
        DEV, "H60A6", ToggleCommand(toggle_instance=INSTANCE_BACKGROUND_LIGHT_TOGGLE, enabled=False)
    )
    coordinator._ble_manager.async_send_ble_packet.assert_not_awaited()
    assert hass.states.get(ids["background"]).state == "off"

    coordinator._on_mqtt_state_update(DEV, dict(PUSH_BACKGROUND_OFF))
    await hass.async_block_till_done()
    assert _states(hass, ids) == ("on", "on", "off")
    await hass.config_entries.async_unload(entry.entry_id)
