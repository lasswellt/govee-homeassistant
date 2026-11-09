"""Issue #222: a connected AWS IoT session that receives nothing.

The reporter's session stayed connected for over an hour with
``inbound_messages: 0`` while status queries went out. A silent session is
reconnected once, then a ``mqtt_silent`` repair asks for a fresh account
login. It only counts as silent when status queries were actually sent: a
quiet account with the re-query off, or one AWS IoT refuses status queries
for (#259), never raises it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.govee.coordinator as coord_mod
from custom_components.govee.const import (
    DOMAIN,
    MQTT_SILENT_CHECK_DELAY,
    MQTT_SILENT_MIN_MISSED_PUSHES,
    MQTT_SILENT_MIN_QUERIES,
)
from custom_components.govee.repairs import (
    ISSUE_MQTT_SILENT,
    async_create_mqtt_silent_issue,
    async_delete_mqtt_silent_issue,
)

from .test_issue_195 import _coord


@pytest.fixture
def sleeps(monkeypatch):
    async def _sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(coord_mod.asyncio, "sleep", _sleep)


@pytest.fixture
def issues(monkeypatch):
    created = MagicMock()
    deleted = MagicMock()
    monkeypatch.setattr(coord_mod, "async_create_mqtt_silent_issue", created)
    monkeypatch.setattr(coord_mod, "async_delete_mqtt_silent_issue", deleted)
    return created, deleted


def _silent_coord():
    coord, client = _coord()
    client.inbound_total = 0
    client.async_stop = AsyncMock()
    client.async_start = AsyncMock()
    # The connect hook's own status sweep is not under test here.
    coord._config_entry.async_create_background_task = MagicMock(side_effect=lambda _hass, coro, name: coro.close())
    with patch.object(coord_mod, "async_call_later") as call_later, patch.object(coord_mod, "async_delete_mqtt_issue"):
        coord._on_mqtt_connected()
    return coord, client, call_later


class TestArming:
    def test_connect_arms_the_check_and_resets_the_counters(self):
        coord, client, call_later = _silent_coord()
        assert call_later.call_args.args[1] == MQTT_SILENT_CHECK_DELAY
        assert coord._mqtt_queries_since_connect == 0
        assert coord._mqtt_inbound_at_connect == 0

    def test_a_reconnect_replaces_the_pending_check(self):
        coord, client, call_later = _silent_coord()
        first_unsub = call_later.return_value
        with patch.object(coord_mod, "async_call_later"), patch.object(coord_mod, "async_delete_mqtt_issue"):
            coord._on_mqtt_connected()
        first_unsub.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_the_check(self):
        coord, _client, call_later = _silent_coord()
        unsub = call_later.return_value
        coord._api_client.close = AsyncMock()
        await coord.async_shutdown()
        unsub.assert_called_once()
        assert coord._mqtt_silence_unsub is None


class TestSilence:
    @pytest.mark.asyncio
    async def test_reconnects_once_then_raises_the_repair(self, sleeps, issues):
        created, _deleted = issues
        coord, client, _ = _silent_coord()
        await coord._poll_mqtt_status()
        assert coord._mqtt_queries_since_connect >= MQTT_SILENT_MIN_QUERIES

        await coord._async_check_mqtt_silence()
        client.async_stop.assert_awaited_once()
        client.async_start.assert_awaited_once()
        created.assert_not_called()

        # The new session is just as silent.
        await coord._poll_mqtt_status()
        await coord._async_check_mqtt_silence()
        created.assert_called_once_with(coord.hass, coord._config_entry, MQTT_SILENT_CHECK_DELAY // 60)
        assert client.async_stop.await_count == 1

        await coord._async_check_mqtt_silence()
        created.assert_called_once()  # reported once

    @pytest.mark.asyncio
    async def test_a_message_clears_the_repair(self, sleeps, issues):
        created, deleted = issues
        coord, client, _ = _silent_coord()
        coord._mqtt_silent_reconnected = True
        await coord._poll_mqtt_status()
        await coord._async_check_mqtt_silence()
        created.assert_called_once()

        coord._on_mqtt_state_update("unknown-device", {})

        deleted.assert_called_once_with(coord.hass, coord._config_entry)
        assert coord._mqtt_silent_reported is False
        assert coord._mqtt_silent_reconnected is False

    @pytest.mark.asyncio
    async def test_inbound_since_connect_is_not_silent(self, sleeps, issues):
        created, _deleted = issues
        coord, client, _ = _silent_coord()
        await coord._poll_mqtt_status()
        client.inbound_total = 4

        await coord._async_check_mqtt_silence()

        client.async_stop.assert_not_awaited()
        created.assert_not_called()

    @pytest.mark.asyncio
    async def test_sweep_refused_no_repair(self, sleeps, issues):
        """#259 stopped the sweep: no queries go out, so the quiet is expected."""
        created, _deleted = issues
        coord, client, _ = _silent_coord()
        coord._status_queries_refused = True
        await coord._poll_mqtt_status()
        assert await coord.async_request_status("A", user=True) is False

        await coord._async_check_mqtt_silence()

        client.async_publish_status_query.assert_not_awaited()
        client.async_stop.assert_not_awaited()
        created.assert_not_called()

    @pytest.mark.asyncio
    async def test_requery_off_no_repair(self, sleeps, issues):
        created, _deleted = issues
        coord, client, _ = _silent_coord()
        coord._config_entry.options = {coord_mod.CONF_MQTT_STATUS_INTERVAL: 0}
        await coord._poll_mqtt_status()

        await coord._async_check_mqtt_silence()

        client.async_stop.assert_not_awaited()
        created.assert_not_called()

    @pytest.mark.asyncio
    async def test_disconnected_session_is_left_to_the_reconnect(self, sleeps, issues):
        created, _deleted = issues
        coord, client, _ = _silent_coord()
        await coord._poll_mqtt_status()
        client.connected = False

        await coord._async_check_mqtt_silence()

        client.async_stop.assert_not_awaited()
        created.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_client(self, issues):
        created, _deleted = issues
        coord, _client, _ = _silent_coord()
        coord._mqtt_client = None
        await coord._async_check_mqtt_silence()
        created.assert_not_called()


_SESSION_START = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
_START_MS = int(_SESSION_START.timestamp() * 1000)


def _reading(last_time: int | None, hub: str = "HUB", sno: int | None = 1) -> dict:
    return {
        "device_id": "T",
        "name": "Thermo",
        "sku": "H5310",
        "hub_device_id": hub,
        "sno": sno,
        "last_time": last_time,
        "temperature": 20.0,
    }


def _gateway_coord(age: float = MQTT_SILENT_CHECK_DELAY + 60):
    """A silent session that came up ``age`` seconds ago, with one gateway thermometer."""
    coord, client, call_later = _silent_coord()
    client.connected_since = datetime.now(timezone.utc) - timedelta(seconds=age)
    coord._iot_credentials = MagicMock()
    coord._bff_thermometer_ids = {"T"}
    return coord, client


async def _feed(coord, last_time: int | None, **kwargs) -> None:
    coord._async_bff_call = AsyncMock(return_value=[_reading(last_time, **kwargs)])
    await coord._refresh_bff_thermometers()


def _now_ms(offset_s: int = 0) -> int:
    return int((datetime.now(timezone.utc) + timedelta(seconds=offset_s)).timestamp() * 1000)


class TestGatewayPushEvidence:
    @pytest.mark.asyncio
    async def test_missed_gateway_pushes_reconnect_then_repair(self, sleeps, issues):
        created, _deleted = issues
        coord, client = _gateway_coord()
        base = _now_ms(-60)
        await _feed(coord, base)  # first sighting only seeds
        assert coord._mqtt_missed_pushes_since_connect == 0
        await _feed(coord, base + 1000)
        assert coord._mqtt_missed_pushes_since_connect == 1
        client.async_stop.assert_not_awaited()
        await _feed(coord, base + 2000)
        client.async_stop.assert_awaited_once()
        client.async_start.assert_awaited_once()
        created.assert_not_called()

        # The reconnect re-arms the session; two more missed pushes on it raise the repair.
        with patch.object(coord_mod, "async_call_later"), patch.object(coord_mod, "async_delete_mqtt_issue"):
            coord._on_mqtt_connected()
        assert coord._mqtt_missed_pushes_since_connect == 0
        client.connected_since = datetime.now(timezone.utc) - timedelta(seconds=MQTT_SILENT_CHECK_DELAY + 60)
        await _feed(coord, base + 3000)
        await _feed(coord, base + 4000)
        created.assert_called_once_with(coord.hass, coord._config_entry, MQTT_SILENT_CHECK_DELAY // 60)
        assert client.async_stop.await_count == 1

    @pytest.mark.asyncio
    async def test_session_younger_than_the_delay_waits_for_the_timer(self, sleeps, issues):
        coord, client = _gateway_coord(age=60)
        base = _now_ms(-30)
        for step in range(3):
            await _feed(coord, base + step * 1000)
        assert coord._mqtt_missed_pushes_since_connect == 2
        client.async_stop.assert_not_awaited()

        # The timer finds the evidence and acts.
        await coord._async_check_mqtt_silence()
        client.async_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_inbound_message_means_no_action(self, sleeps, issues):
        created, _deleted = issues
        coord, client = _gateway_coord()
        base = _now_ms(-60)
        await _feed(coord, base)
        client.inbound_total = 1
        await _feed(coord, base + 1000)
        await _feed(coord, base + 2000)
        assert coord._mqtt_missed_pushes_since_connect == 0
        client.async_stop.assert_not_awaited()
        created.assert_not_called()

    @pytest.mark.asyncio
    async def test_inbound_before_the_check_clears_the_gate(self, sleeps, issues):
        created, _deleted = issues
        coord, client = _gateway_coord(age=60)
        coord._mqtt_missed_pushes_since_connect = MQTT_SILENT_MIN_MISSED_PUSHES
        client.inbound_total = 3
        await coord._async_check_mqtt_silence()
        client.async_stop.assert_not_awaited()
        created.assert_not_called()

    @pytest.mark.asyncio
    async def test_reading_older_than_the_session_is_not_counted(self, sleeps, issues):
        coord, client = _gateway_coord()
        old = _now_ms(-3 * MQTT_SILENT_CHECK_DELAY)
        await _feed(coord, old)
        await _feed(coord, old + 1000)
        await _feed(coord, old + 2000)
        assert coord._mqtt_missed_pushes_since_connect == 0

    @pytest.mark.asyncio
    async def test_repeated_last_time_is_not_counted(self, sleeps, issues):
        coord, client = _gateway_coord()
        stamp = _now_ms(-60)
        for _ in range(4):
            await _feed(coord, stamp)
        assert coord._mqtt_missed_pushes_since_connect == 0

    @pytest.mark.asyncio
    async def test_missing_or_invalid_last_time_is_not_counted(self, sleeps, issues):
        coord, client = _gateway_coord()
        await _feed(coord, None)
        await _feed(coord, 0)
        assert coord._bff_last_time_seen == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("hub, sno", [("", 1), ("HUB", None)])
    async def test_device_without_hub_or_slot_is_not_counted(self, sleeps, issues, hub, sno):
        coord, client = _gateway_coord()
        base = _now_ms(-60)
        for step in range(3):
            await _feed(coord, base + step * 1000, hub=hub, sno=sno)
        assert coord._mqtt_missed_pushes_since_connect == 0
        client.async_stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_session_start_is_not_counted(self, sleeps, issues):
        coord, client = _gateway_coord()
        client.connected_since = None
        base = _now_ms(-60)
        for step in range(3):
            await _feed(coord, base + step * 1000)
        assert coord._mqtt_missed_pushes_since_connect == 0

    @pytest.mark.asyncio
    async def test_sweep_off_still_raises_via_push_evidence(self, sleeps, issues):
        created, _deleted = issues
        coord, client = _gateway_coord()
        coord._config_entry.options = {coord_mod.CONF_MQTT_STATUS_INTERVAL: 0}
        coord._mqtt_silent_reconnected = True
        base = _now_ms(-60)
        for step in range(3):
            await _feed(coord, base + step * 1000)
        created.assert_called_once()
        assert coord._mqtt_queries_since_connect == 0

    @pytest.mark.asyncio
    async def test_no_client_is_a_no_op(self, sleeps, issues):
        created, _deleted = issues
        coord, client = _gateway_coord()
        coord._mqtt_client = None
        base = _now_ms(-60)
        for step in range(3):
            await _feed(coord, base + step * 1000)
        assert coord._mqtt_missed_pushes_since_connect == 0
        created.assert_not_called()

    @pytest.mark.asyncio
    async def test_disconnected_session_is_not_counted(self, sleeps, issues):
        coord, client = _gateway_coord()
        client.connected = False
        base = _now_ms(-60)
        for step in range(3):
            await _feed(coord, base + step * 1000)
        assert coord._mqtt_missed_pushes_since_connect == 0

    @pytest.mark.asyncio
    async def test_quiet_account_without_prompts_never_raises(self, sleeps, issues):
        created, _deleted = issues
        coord, client = _gateway_coord()
        await coord._async_check_mqtt_silence()
        client.async_stop.assert_not_awaited()
        created.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_session_already_being_handled_is_not_handled_twice(self, sleeps, issues):
        coord, client = _gateway_coord()
        coord._mqtt_silence_handling = True
        coord._mqtt_missed_pushes_since_connect = MQTT_SILENT_MIN_MISSED_PUSHES
        await coord._async_check_mqtt_silence()
        await coord._async_check_missed_pushes()
        client.async_stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handler_without_a_client_returns(self, issues):
        coord, _client = _gateway_coord()
        coord._mqtt_client = None
        await coord._async_handle_silent_session("x")
        assert coord._mqtt_silence_handling is False

    @pytest.mark.asyncio
    async def test_rearm_resets_the_missed_push_counter(self):
        coord, _client = _gateway_coord()
        coord._mqtt_missed_pushes_since_connect = 5
        with patch.object(coord_mod, "async_call_later"):
            coord._arm_mqtt_silence_check()
        assert coord._mqtt_missed_pushes_since_connect == 0


class TestDiagnosticsAccessors:
    def test_topic_and_missed_push_accessors(self):
        coordinator, _client = _gateway_coord()
        coordinator._device_topics = {"OWN": "GD/topic"}
        coordinator._mqtt_missed_pushes_since_connect = 2
        assert coordinator.has_device_topic("OWN") is True
        assert coordinator.has_device_topic("CHILD") is False
        assert coordinator.mqtt_missed_pushes_since_connect == 2


class TestRepairIssue:
    async def test_create_and_delete(self, hass: HomeAssistant) -> None:
        entry = MockConfigEntry(domain=DOMAIN, title="Govee", data={})
        entry.add_to_hass(hass)
        issue_id = f"{ISSUE_MQTT_SILENT}_{entry.entry_id}"

        async_create_mqtt_silent_issue(hass, entry, 30)
        issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.translation_key == ISSUE_MQTT_SILENT
        assert issue.translation_placeholders == {"entry_title": "Govee", "minutes": "30"}
        assert issue.is_fixable is False

        async_delete_mqtt_silent_issue(hass, entry)
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
