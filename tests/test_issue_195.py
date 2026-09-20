"""Tests for issue #195 — the MQTT status sweep must not take push state down.

AWS IoT answers a publish it refuses by closing the whole session. The
connect-time sweep added in 2026.9.5 queried every device in a burst, so one
device whose topic AWS rejects put the account into a connect/drop loop, and
nothing could say which device it was. The sweep now goes one device per
``MQTT_STATUS_QUERY_SPACING``, the device whose query is in flight is blamed
for a drop inside that window, and after
``MQTT_STATUS_QUERY_QUARANTINE_STRIKES`` the device is left out of the sweep.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, call

import pytest

import custom_components.govee.coordinator as coord_mod
from custom_components.govee import const
from custom_components.govee.models import GoveeDevice

_LOGGER_NAME = "custom_components.govee.coordinator"


def _device(device_id: str, sku: str = "H6001") -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name=f"Lamp {device_id}",
        device_type="devices.types.light",
        capabilities=(),
    )


def _coord(devices: tuple[str, ...] = ("A", "B", "C"), skus: dict[str, str] | None = None):
    """A coordinator with a connected MQTT client and a topic per device."""
    skus = skus or {}
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = {}
    coord = coord_mod.GoveeCoordinator(
        hass=MagicMock(),
        config_entry=config_entry,
        api_client=MagicMock(),
        iot_credentials=MagicMock(token="tok"),
        poll_interval=60,
    )
    coord._devices = {device_id: _device(device_id, skus.get(device_id, "H6001")) for device_id in devices}
    coord._device_topics = {device_id: f"GD/{device_id.lower()}" for device_id in devices}
    client = MagicMock()
    client.connected = True
    client.async_publish_status_query = AsyncMock(return_value=True)
    coord._mqtt_client = client
    return coord, client


@pytest.fixture
def sleeps(monkeypatch):
    """Record the sweep's pacing sleeps instead of waiting them out."""
    seen: list[float] = []

    async def _sleep(delay: float) -> None:
        seen.append(delay)

    monkeypatch.setattr(coord_mod.asyncio, "sleep", _sleep)
    return seen


class TestPacedSweep:
    @pytest.mark.asyncio
    async def test_queries_go_out_one_spacing_apart(self, sleeps):
        coord, client = _coord()

        await coord._poll_mqtt_status()

        assert client.async_publish_status_query.await_args_list == [call("GD/a"), call("GD/b"), call("GD/c")]
        assert sleeps == [const.MQTT_STATUS_QUERY_SPACING] * 3

    @pytest.mark.asyncio
    async def test_queried_device_stays_in_flight_through_its_window(self, monkeypatch):
        coord, _ = _coord(devices=("A",))
        seen: list[str | None] = []

        async def _sleep(_delay: float) -> None:
            seen.append(coord._status_query_in_flight)

        monkeypatch.setattr(coord_mod.asyncio, "sleep", _sleep)

        await coord._poll_mqtt_status()

        assert seen == ["A"]
        assert coord._status_query_in_flight is None
        assert coord._status_sweep_running is False

    @pytest.mark.asyncio
    async def test_sweep_stops_once_the_session_is_gone(self, sleeps):
        coord, client = _coord()

        async def _publish(_topic: str) -> bool:
            client.connected = False  # AWS closed the session on this publish
            return True

        client.async_publish_status_query = AsyncMock(side_effect=_publish)

        await coord._poll_mqtt_status()

        assert client.async_publish_status_query.await_args_list == [call("GD/a")]

    @pytest.mark.asyncio
    async def test_a_second_sweep_does_not_interleave(self, sleeps):
        coord, client = _coord()
        coord._status_sweep_running = True

        await coord._poll_mqtt_status()

        client.async_publish_status_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancellation_clears_the_sweep_state(self, monkeypatch):
        """Entry unload cancels the background sweep mid-window."""
        coord, _ = _coord(devices=("A", "B"))
        inside_window = asyncio.Event()

        async def _sleep(_delay: float) -> None:
            inside_window.set()
            await asyncio.Event().wait()  # held until cancelled

        monkeypatch.setattr(coord_mod.asyncio, "sleep", _sleep)
        task = asyncio.ensure_future(coord._poll_mqtt_status())
        await inside_window.wait()
        assert coord._status_sweep_running is True
        assert coord._status_query_in_flight == "A"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert coord._status_sweep_running is False
        assert coord._status_query_in_flight is None


class TestBlameAndQuarantine:
    def test_a_drop_with_no_query_in_flight_is_not_a_strike(self):
        coord, _ = _coord()

        coord._on_mqtt_disconnected()

        assert coord._status_query_strikes == {}
        assert coord.mqtt_status_query_strikes == []

    def test_first_strike_is_only_a_debug_line(self, caplog):
        coord, _ = _coord()
        coord._status_query_in_flight = "B"

        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            coord._on_mqtt_disconnected()

        assert coord._status_query_strikes == {"B": 1}
        assert coord._status_query_in_flight is None
        assert coord._status_query_quarantine == set()
        assert coord._mqtt_status_poll_targets == ["A", "B", "C"]
        assert coord.mqtt_status_query_strikes == [
            {"device_id": "B", "sku": "H6001", "strikes": 1, "quarantined": False}
        ]
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
        assert any("strike 1 of 2" in r.getMessage() for r in caplog.records)

    def test_second_strike_quarantines_the_device(self, caplog):
        coord, _ = _coord()

        for _ in range(const.MQTT_STATUS_QUERY_QUARANTINE_STRIKES):
            coord._status_query_in_flight = "B"
            with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
                coord._on_mqtt_disconnected()

        assert coord._status_query_quarantine == {"B"}
        assert coord._mqtt_status_poll_targets == ["A", "C"]
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "Lamp B (H6001)" in warnings[0]
        assert "issues/195" in warnings[0]
        assert coord.mqtt_status_query_strikes == [
            {"device_id": "B", "sku": "H6001", "strikes": 2, "quarantined": True}
        ]

    def test_a_device_no_longer_in_the_list_is_labelled_by_id(self, caplog):
        coord, _ = _coord()

        for _ in range(const.MQTT_STATUS_QUERY_QUARANTINE_STRIKES):
            coord._status_query_in_flight = "ZZ"
            with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
                coord._on_mqtt_disconnected()

        assert coord.mqtt_status_query_strikes == [{"device_id": "ZZ", "sku": None, "strikes": 2, "quarantined": True}]
        assert "to ZZ 2 times" in caplog.records[-1].getMessage()

    @pytest.mark.asyncio
    async def test_quarantined_device_is_left_out_of_the_sweep(self, sleeps):
        coord, client = _coord()
        coord._status_query_quarantine.add("B")

        await coord._poll_mqtt_status()

        assert client.async_publish_status_query.await_args_list == [call("GD/a"), call("GD/c")]

    @pytest.mark.asyncio
    async def test_a_drop_inside_the_window_is_blamed_on_that_device(self, monkeypatch):
        """The reported sequence: AWS closes the session on B's query, the
        client's disconnect callback fires while the sweep sits in B's
        window, the sweep then stops instead of querying C on a dead session.
        """
        coord, client = _coord()

        async def _sleep(_delay: float) -> None:
            if coord._status_query_in_flight == "B":
                client.connected = False
                coord._on_mqtt_disconnected()

        monkeypatch.setattr(coord_mod.asyncio, "sleep", _sleep)

        await coord._poll_mqtt_status()

        assert client.async_publish_status_query.await_args_list == [call("GD/a"), call("GD/b")]
        assert coord._status_query_strikes == {"B": 1}
        assert coord._status_query_in_flight is None


class TestPermanentSkuExclusion:
    """H5110, H5220, H5111, H5075 (issue #195 and #197 follow-ups): BLE/LoRa
    gateway-bridged sensors that AWS IoT always refuses a direct status
    query to. Left out of the sweep outright rather than burning through the
    quarantine on every unit — confirmed independently for each SKU via
    reporter diagnostics showing the identical quarantine signature.
    """

    @pytest.mark.parametrize("excluded_sku", sorted(const.MQTT_STATUS_QUERY_EXCLUDED_SKUS))
    def test_excluded_sku_is_not_a_sweep_target(self, excluded_sku):
        coord, _ = _coord(devices=("A", "B", "C"), skus={"B": excluded_sku})

        assert coord._mqtt_status_poll_targets == ["A", "C"]

    @pytest.mark.parametrize("excluded_sku", sorted(const.MQTT_STATUS_QUERY_EXCLUDED_SKUS))
    @pytest.mark.asyncio
    async def test_excluded_sku_is_never_queried(self, sleeps, excluded_sku):
        coord, client = _coord(devices=("A", "B", "C"), skus={"B": excluded_sku})

        await coord._poll_mqtt_status()

        assert client.async_publish_status_query.await_args_list == [call("GD/a"), call("GD/c")]

    @pytest.mark.asyncio
    async def test_several_units_of_the_excluded_sku_cost_no_strikes(self, sleeps):
        """The reported case: three H5110s on one account. None should ever
        be queried, so none can ever drop the session or reach quarantine.
        """
        coord, client = _coord(devices=("A", "B", "C", "D"), skus={"B": "H5110", "C": "H5110", "D": "H5110"})

        await coord._poll_mqtt_status()

        assert client.async_publish_status_query.await_args_list == [call("GD/a")]
        assert coord._status_query_strikes == {}
        assert coord.mqtt_status_query_strikes == []

    @pytest.mark.asyncio
    async def test_six_h5075_units_cost_no_strikes(self, sleeps):
        """The H5075 report: six units on one account, measured on v2026.9.11.

        Each took the session down on its own reconnect cycle, two strikes
        apiece, so MQTT stayed unusable for about half an hour after every
        restart. Nothing on the account could be read over MQTT in that
        window — including the devices whose only transport is ptReal over
        that same session, such as an H5192 probe thermometer mid-cook.
        """
        units = ("B", "C", "D", "E", "F", "G")
        coord, client = _coord(
            devices=("A",) + units,
            skus={device_id: "H5075" for device_id in units},
        )

        await coord._poll_mqtt_status()

        assert client.async_publish_status_query.await_args_list == [call("GD/a")]
        assert coord._status_query_strikes == {}
        assert coord.mqtt_status_query_strikes == []
