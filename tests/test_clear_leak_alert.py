"""Clear leak alert for standalone water detectors (H5054).

An H5054's trip is read from the account warnMessage history and latches wet
until the alert is marked read (issue #62). The Govee app's "Read" button
sends ``warnLifted``; these tests cover the same request sent from Home
Assistant:

- Coordinator: ``async_clear_water_leak`` — the lift call, the immediate
  state clear, the re-login retry, and every refusal path.
- Button: platform wiring (standalone detectors only), availability, and a
  rejected lift surfacing as a translated HomeAssistantError.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.govee import button as button_mod
from custom_components.govee.api.exceptions import GoveeApiError, GoveeAuthError
from custom_components.govee.button import GoveeClearLeakAlertButton
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import CAPABILITY_EVENT, INSTANCE_BODY_APPEARED_EVENT

DETECTOR_ID = "DA:BF:C0:D6:A5:FE:00:08:E8"


def _detector(*, device_id: str = DETECTOR_ID, is_group: bool = False) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku="H5054",
        name="Washing Machine",
        device_type="devices.types.sensor",
        capabilities=(GoveeCapability(type=CAPABILITY_EVENT, instance=INSTANCE_BODY_APPEARED_EVENT, parameters={}),),
        is_group=is_group,
    )


class _AsyncCM:
    """Async context manager yielding a fixed client (stands in for GoveeAuthClient)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def __aenter__(self) -> Any:
        return self._inner

    async def __aexit__(self, *exc: Any) -> None:
        return None


# --------------------------------------------------------------------------- #
# Coordinator
# --------------------------------------------------------------------------- #


class TestAsyncClearWaterLeak:
    def _coord(self, *, credentials: Any = "default") -> Any:
        import custom_components.govee.coordinator as coord_mod

        coord = coord_mod.GoveeCoordinator(
            hass=MagicMock(),
            config_entry=MagicMock(entry_id="test_entry"),
            api_client=MagicMock(),
            iot_credentials=MagicMock(token="tok") if credentials == "default" else credentials,
            poll_interval=60,
        )
        device = _detector()
        coord._devices[device.device_id] = device
        coord._states[device.device_id] = GoveeDeviceState.create_empty(device.device_id)
        coord.async_update_listeners = MagicMock()
        return coord

    @staticmethod
    def _patch_client(monkeypatch: pytest.MonkeyPatch, inner: Any) -> None:
        import custom_components.govee.coordinator as coord_mod

        monkeypatch.setattr(coord_mod, "GoveeAuthClient", lambda **kw: _AsyncCM(inner))

    async def test_lifts_and_clears_a_latched_leak_at_once(self, monkeypatch):
        coord = self._coord()
        coord._states[DETECTOR_ID].water_leak = True
        inner = MagicMock()
        inner.lift_leak_warning = AsyncMock(return_value=True)
        self._patch_client(monkeypatch, inner)

        assert await coord.async_clear_water_leak(DETECTOR_ID) is True

        inner.lift_leak_warning.assert_awaited_once_with("tok", DETECTOR_ID, "H5054")
        assert coord._states[DETECTOR_ID].water_leak is False
        coord.async_update_listeners.assert_called_once()

    async def test_an_already_dry_detector_is_lifted_without_a_state_write(self, monkeypatch):
        coord = self._coord()
        inner = MagicMock()
        inner.lift_leak_warning = AsyncMock(return_value=True)
        self._patch_client(monkeypatch, inner)

        assert await coord.async_clear_water_leak(DETECTOR_ID) is True

        inner.lift_leak_warning.assert_awaited_once()
        coord.async_update_listeners.assert_not_called()

    async def test_an_expired_token_is_refreshed_once_and_retried(self, monkeypatch):
        coord = self._coord()
        coord._states[DETECTOR_ID].water_leak = True
        inner = MagicMock()
        inner.lift_leak_warning = AsyncMock(side_effect=[GoveeAuthError("expired", code=401), True])
        self._patch_client(monkeypatch, inner)
        coord._async_refresh_iot_credentials = AsyncMock(return_value=True)

        assert await coord.async_clear_water_leak(DETECTOR_ID) is True

        assert inner.lift_leak_warning.await_count == 2
        assert coord._states[DETECTOR_ID].water_leak is False

    async def test_a_failed_refresh_leaves_the_leak_latched(self, monkeypatch):
        coord = self._coord()
        coord._states[DETECTOR_ID].water_leak = True
        inner = MagicMock()
        inner.lift_leak_warning = AsyncMock(side_effect=GoveeAuthError("expired", code=401))
        self._patch_client(monkeypatch, inner)
        coord._async_refresh_iot_credentials = AsyncMock(return_value=False)

        assert await coord.async_clear_water_leak(DETECTOR_ID) is False

        assert coord._states[DETECTOR_ID].water_leak is True
        coord.async_update_listeners.assert_not_called()

    async def test_a_rejected_lift_leaves_the_leak_latched(self, monkeypatch):
        coord = self._coord()
        coord._states[DETECTOR_ID].water_leak = True
        inner = MagicMock()
        inner.lift_leak_warning = AsyncMock(side_effect=GoveeApiError("warnLifted failed: meh", code=500))
        self._patch_client(monkeypatch, inner)

        assert await coord.async_clear_water_leak(DETECTOR_ID) is False

        assert coord._states[DETECTOR_ID].water_leak is True
        coord.async_update_listeners.assert_not_called()

    async def test_a_device_that_is_not_a_standalone_detector_is_refused(self, monkeypatch):
        coord = self._coord()
        inner = MagicMock()
        inner.lift_leak_warning = AsyncMock(return_value=True)
        self._patch_client(monkeypatch, inner)

        assert await coord.async_clear_water_leak("AA:BB:CC:DD:EE:FF:00:11") is False

        inner.lift_leak_warning.assert_not_awaited()

    async def test_without_account_login_nothing_is_sent(self, monkeypatch):
        coord = self._coord(credentials=None)
        inner = MagicMock()
        inner.lift_leak_warning = AsyncMock(return_value=True)
        self._patch_client(monkeypatch, inner)

        assert await coord.async_clear_water_leak(DETECTOR_ID) is False

        inner.lift_leak_warning.assert_not_awaited()

    async def test_a_lift_on_a_detector_with_no_state_still_succeeds(self, monkeypatch):
        coord = self._coord()
        del coord._states[DETECTOR_ID]
        inner = MagicMock()
        inner.lift_leak_warning = AsyncMock(return_value=True)
        self._patch_client(monkeypatch, inner)

        assert await coord.async_clear_water_leak(DETECTOR_ID) is True

        coord.async_update_listeners.assert_not_called()


# --------------------------------------------------------------------------- #
# Button
# --------------------------------------------------------------------------- #


def _button_coordinator(*, bff: bool = False, last_update_success: bool = True, iot: bool = True) -> MagicMock:
    coordinator = MagicMock()
    coordinator.is_bff_leak_sensor = MagicMock(return_value=bff)
    coordinator.last_update_success = last_update_success
    coordinator.has_iot_credentials = iot
    coordinator.async_clear_water_leak = AsyncMock(return_value=True)
    return coordinator


class TestClearLeakAlertButtonSetup:
    async def _setup(self, coordinator: MagicMock, *devices: GoveeDevice) -> list:
        coordinator.devices = {d.device_id: d for d in devices}
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {}
        added: list = []
        await button_mod.async_setup_entry(MagicMock(), entry, added.extend)
        return added

    async def test_standalone_detectors_get_the_button_and_groups_do_not(self):
        added = await self._setup(_button_coordinator(), _detector(), _detector(device_id="11825917", is_group=True))
        assert [type(e).__name__ for e in added] == ["GoveeClearLeakAlertButton"]
        assert added[0]._device.device_id == DETECTOR_ID

    async def test_hub_attached_leak_sensors_do_not_get_the_button(self):
        added = await self._setup(_button_coordinator(bff=True), _detector())
        assert added == []


class TestClearLeakAlertButton:
    def _entity(self, **kwargs: Any) -> tuple[GoveeClearLeakAlertButton, MagicMock]:
        coordinator = _button_coordinator(**kwargs)
        return GoveeClearLeakAlertButton(coordinator, _detector()), coordinator

    def test_identity(self):
        entity, _ = self._entity()
        assert entity.unique_id == f"{DETECTOR_ID}_clear_leak_alert"
        assert entity.translation_key == "clear_leak_alert"
        assert entity.entity_category is None

    @pytest.mark.parametrize(
        ("last_update_success", "iot", "expected"),
        [(True, True, True), (False, True, False), (True, False, False)],
    )
    def test_available_needs_the_coordinator_and_account_login(self, last_update_success, iot, expected):
        entity, _ = self._entity(last_update_success=last_update_success, iot=iot)
        assert entity.available is expected

    async def test_press_lifts_the_alert(self):
        entity, coordinator = self._entity()
        await entity.async_press()
        coordinator.async_clear_water_leak.assert_awaited_once_with(DETECTOR_ID)

    async def test_a_rejected_lift_raises_a_translated_error(self):
        entity, coordinator = self._entity()
        coordinator.async_clear_water_leak = AsyncMock(return_value=False)

        with pytest.raises(HomeAssistantError) as exc_info:
            await entity.async_press()

        assert exc_info.value.translation_key == "command_failed"
