"""BFF leak fallback vs. MQTT wet/dry reports (COORD-6).

The BFF poll forces a leak sensor wet when Govee's cloud shows a recent leak
MQTT never reported. A leak MQTT already reported wet and then dry must not be
latched wet again, even after an entry reload or a Home Assistant restart has
reset the per-sensor state; a leak MQTT missed (even if its dry frame arrived),
a newer one, or one still wet must still be forced wet.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.core import HomeAssistant

import custom_components.govee.coordinator as coord_mod
from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.coordinator import (
    LEAK_MQTT_SKEW_MARGIN,
    LEAK_MQTT_STORE_KEY,
    LEAK_MQTT_STORE_VERSION,
    GoveeCoordinator,
)
from custom_components.govee.models.device import GoveeLeakSensor, GoveeLeakSensorState

LEAK = "01:32:7A:C4:06:03:0D:0C"
HUB = "09:C2:60:74:F4:64:AB:FA"
SLOT = 3

CREDS = GoveeIotCredentials(
    token="tok",
    refresh_token="r",
    account_topic="GA/x",
    iot_cert="c",
    iot_key="k",
    iot_ca=None,
    client_id="cid",
    endpoint="ep",
)


def _coordinator(hass: Any) -> GoveeCoordinator:
    entry = MagicMock()
    entry.entry_id = "leak_entry"
    entry.title = "Govee"
    entry.options = {}
    entry.data = {}
    coord = GoveeCoordinator(
        hass=hass,
        config_entry=entry,
        api_client=MagicMock(),
        iot_credentials=CREDS,
        poll_interval=60,
        enable_groups=False,
    )
    coord.async_set_updated_data = MagicMock()
    coord.async_update_listeners = MagicMock()
    coord._schedule_bff_leak_poll = MagicMock()
    return coord


def _add_sensor(coord: GoveeCoordinator, sku: str = "H5058") -> GoveeLeakSensorState:
    """A freshly discovered sensor, as after setup or a reload."""
    coord._leak_sensors[LEAK] = GoveeLeakSensor(
        device_id=LEAK, name="Test leak sensor", sku=sku, hub_device_id=HUB, sno=SLOT
    )
    state = GoveeLeakSensorState()
    coord._leak_states[LEAK] = state
    coord._sno_to_sensor_id[(HUB, SLOT)] = LEAK
    return state


def _bff(coord: GoveeCoordinator, last_wet_ms: int) -> None:
    sensor = {
        "device_id": LEAK,
        "name": "Test leak sensor",
        "sku": "H5058",
        "hub_device_id": HUB,
        "sno": SLOT,
        "battery": 80,
        "online": True,
        "gateway_online": True,
        "last_wet_time": last_wet_ms,
        "read": True,
    }
    coord._async_bff_call = AsyncMock(return_value=([sensor], {}, {}))


def _frame(coord: GoveeCoordinator, is_wet: bool, **probes: bool) -> None:
    coord._handle_leak_event({"hub_device_id": HUB, "sensor_slot": SLOT, "is_wet": is_wet, **probes})


@pytest.fixture(autouse=True)
def _no_dispatch(monkeypatch):
    monkeypatch.setattr(coord_mod, "async_dispatcher_send", MagicMock())


class TestFallbackDecision:
    """The decision itself, with the stamps set directly."""

    @pytest.mark.asyncio
    async def test_leak_missed_by_mqtt_is_forced_wet(self):
        coord = _coordinator(MagicMock())
        state = _add_sensor(coord)
        _bff(coord, int(time.time() * 1000) - 60_000)

        await coord._poll_bff_leak_state()

        assert state.is_wet is True

    @pytest.mark.asyncio
    async def test_leak_seen_wet_and_dry_over_mqtt_is_not_forced(self):
        coord = _coordinator(MagicMock())
        state = _add_sensor(coord)
        now = time.time()
        _bff(coord, int((now - 240) * 1000))
        coord._leak_mqtt_at[LEAK] = {"wet": now - 239, "dry": now - 120}

        await coord._poll_bff_leak_state()

        assert state.is_wet is False

    @pytest.mark.asyncio
    async def test_dry_report_alone_does_not_hide_a_leak_mqtt_missed(self):
        """A short leak whose wet frame was lost but whose dry frame arrived."""
        coord = _coordinator(MagicMock())
        state = _add_sensor(coord)
        _bff(coord, int((time.time() - 120) * 1000))
        _frame(coord, False)

        await coord._poll_bff_leak_state()

        assert state.is_wet is True

    @pytest.mark.asyncio
    async def test_older_mqtt_leak_does_not_hide_a_newer_one(self):
        coord = _coordinator(MagicMock())
        state = _add_sensor(coord)
        now = time.time()
        coord._leak_mqtt_at[LEAK] = {"wet": now - 3600, "dry": now - 3000}
        _bff(coord, int((now - 60) * 1000))  # a new leak MQTT missed

        await coord._poll_bff_leak_state()

        assert state.is_wet is True

    @pytest.mark.asyncio
    async def test_newer_bff_leak_after_a_dry_report_is_forced_wet_again(self):
        coord = _coordinator(MagicMock())
        state = _add_sensor(coord)
        now = time.time()
        coord._leak_mqtt_at[LEAK] = {"wet": now - 240, "dry": now - 120}
        _bff(coord, int((now - 240) * 1000))  # the cloud still shows leak #1
        await coord._poll_bff_leak_state()
        assert state.is_wet is False

        _bff(coord, int((now - 30) * 1000))  # a second leak MQTT missed
        await coord._poll_bff_leak_state()

        assert state.is_wet is True

    @pytest.mark.asyncio
    async def test_still_wet_sensor_is_forced_wet_after_a_reload(self):
        """MQTT reported wet and never dry: a reload must not drop the leak."""
        coord = _coordinator(MagicMock())
        state = _add_sensor(coord)
        now = time.time()
        coord._leak_mqtt_at[LEAK] = {"wet": now - 120, "dry": now - 3600}
        _bff(coord, int((now - 120) * 1000))

        await coord._poll_bff_leak_state()

        assert state.is_wet is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("wet_before_cloud", "forced"),
        [(LEAK_MQTT_SKEW_MARGIN + 1, True), (LEAK_MQTT_SKEW_MARGIN - 1, False)],
    )
    async def test_mqtt_wet_must_be_within_the_skew_margin(self, wet_before_cloud: int, forced: bool):
        coord = _coordinator(MagicMock())
        state = _add_sensor(coord)
        now = time.time()
        last_wet_ms = int((now - 200) * 1000)
        coord._leak_mqtt_at[LEAK] = {"wet": last_wet_ms / 1000 - wet_before_cloud, "dry": now - 100}
        _bff(coord, last_wet_ms)

        await coord._poll_bff_leak_state()

        assert state.is_wet is forced


class TestMqttFrames:
    def test_frames_are_stamped_without_a_store(self):
        coord = _coordinator(MagicMock())
        _add_sensor(coord)
        before = time.time()

        _frame(coord, True)
        assert set(coord._leak_mqtt_at[LEAK]) == {"wet"}
        _frame(coord, False)

        assert coord._leak_mqtt_at[LEAK]["dry"] >= coord._leak_mqtt_at[LEAK]["wet"] >= before

    @pytest.mark.asyncio
    async def test_dual_probe_sensor_stays_dry_after_the_poll(self):
        coord = _coordinator(MagicMock())
        state = _add_sensor(coord, sku="H5059")
        _frame(coord, True, upper_probe_wet=True, lower_probe_wet=False)
        coord._leak_mqtt_at[LEAK]["wet"] -= 60  # the leak lasted a minute
        state.last_mqtt_wet_at = 0.0  # as after a reload
        _bff(coord, int((coord._leak_mqtt_at[LEAK]["wet"] - 1) * 1000))
        _frame(coord, False)

        await coord._poll_bff_leak_state()

        assert state.is_wet is False
        assert state.upper_probe_wet is False
        assert state.lower_probe_wet is False


class TestPersistence:
    """The MQTT stamps outlive an entry reload and a Home Assistant restart."""

    @pytest.mark.asyncio
    async def test_dried_sensor_is_not_latched_wet_after_a_reload(self, hass: HomeAssistant):
        first = _coordinator(hass)
        await first._async_load_leak_mqtt_stamps()
        _add_sensor(first)
        _frame(first, True)
        first._leak_mqtt_at[LEAK]["wet"] -= 120  # the leak lasted two minutes
        _frame(first, False)

        # Entry reload: a new coordinator with fresh per-sensor state.
        second = _coordinator(hass)
        await second._async_load_leak_mqtt_stamps()
        state = _add_sensor(second)
        assert state.last_mqtt_wet_at == 0.0
        # The hub stamped the leak a moment before the wet frame arrived.
        _bff(second, int((second._leak_mqtt_at[LEAK]["wet"] - 2) * 1000))

        await second._poll_bff_leak_state()

        assert state.is_wet is False

    @pytest.mark.asyncio
    async def test_stamps_are_saved_and_reloaded_after_a_restart(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ):
        coord = _coordinator(hass)
        await coord._async_load_leak_mqtt_stamps()
        _add_sensor(coord)
        _frame(coord, True)
        _frame(coord, False)
        # Home Assistant shutting down flushes the pending delayed save.
        hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
        await hass.async_block_till_done()

        saved = hass_storage[LEAK_MQTT_STORE_KEY]
        assert saved["version"] == LEAK_MQTT_STORE_VERSION
        assert saved["data"] == {LEAK: coord._leak_mqtt_at[LEAK]}
        assert set(saved["data"][LEAK]) == {"wet", "dry"}

        # Restart: hass.data is empty, the stamps come back from storage.
        hass.data.pop(coord_mod._LEAK_MQTT_DATA_KEY)
        restarted = _coordinator(hass)
        await restarted._async_load_leak_mqtt_stamps()

        assert restarted._leak_mqtt_at == {LEAK: coord._leak_mqtt_at[LEAK]}

    @pytest.mark.asyncio
    async def test_malformed_stored_stamps_are_dropped(self, hass: HomeAssistant, hass_storage: dict[str, Any]):
        hass_storage[LEAK_MQTT_STORE_KEY] = {
            "version": LEAK_MQTT_STORE_VERSION,
            "minor_version": 1,
            "key": LEAK_MQTT_STORE_KEY,
            "data": {
                LEAK: {"wet": 1_700_000_000, "dry": "x", "other": 5},
                "flag": True,
                "bool": {"wet": True, "dry": 1_700_000_100.5},
            },
        }
        coord = _coordinator(hass)

        await coord._async_load_leak_mqtt_stamps()

        assert coord._leak_mqtt_at == {LEAK: {"wet": 1_700_000_000.0}, "bool": {"dry": 1_700_000_100.5}}

    @pytest.mark.asyncio
    async def test_storage_failure_keeps_setup_going(self, hass: HomeAssistant, monkeypatch):
        monkeypatch.setattr(coord_mod, "Store", MagicMock(side_effect=OSError("disk")))
        coord = _coordinator(hass)

        await coord._async_load_leak_mqtt_stamps()

        assert coord._leak_mqtt_store is None
        _add_sensor(coord)
        _frame(coord, False)
        assert LEAK in coord._leak_mqtt_at
