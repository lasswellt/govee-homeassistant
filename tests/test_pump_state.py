"""Pump state and hose-connection mode for pump-model dehumidifiers (H7152
"Max") — issue #114 follow-up.

Reverse-engineered from a live device:

- Pump-fault flag: a clogged-drain capture showed neither the OpenAPI
  event-push channel nor the flat MQTT ``state`` keys carried any trace of
  the fault (confirmed empty across two captures, two minutes apart, while
  the app showed the fault active). The only signal is byte offset 12 of an
  ``aa 17`` status frame riding in the AWS IoT push's ``op.command`` list —
  ``0x00`` normally, ``0x01`` while the fault is active.
- Hose-connection mode: repeatedly pressing and holding the device's own
  hose-connection button (~5s per hold) toggled the app's Mode label in
  exact lockstep with byte offset 9 of an ``aa 19`` status frame on every
  single transition — ``0x01`` = "Pump Mode", ``0x00`` = "Water Tank Mode".

The frames below are taken verbatim from real diagnostics downloads and
live debug-log captures.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import CAPABILITY_ON_OFF, INSTANCE_POWER
from custom_components.govee.transport_health import TransportHealthTracker

DEVICE_ID = "11:66:C0:EB:1D:75:5C:E7"

# Verbatim ``aa 17`` frames from real diagnostics captures.
FRAME_PUMP_OK = bytes.fromhex("aa170000000000000000000000000000000000bd")
FRAME_PUMP_FAULT = bytes.fromhex("aa170000000000000000000001000000000000bc")
FRAME_PUMP_RECOVERED = bytes.fromhex("aa170000000000000000000000000000000000bd")

# An unrelated frame from the same push, to prove the scan doesn't false-match.
FRAME_UNRELATED = bytes.fromhex("aa050003000000000000000000000000000000ac")

# Verbatim ``aa 19`` frames from a live hose-button test on 2026-09-11 — byte
# offset 9 confirmed against the app's own Mode label toggling in lockstep
# across five press-and-hold cycles.
FRAME_MODE_PUMP = bytes.fromhex("aa190000000000010101000000000000000000b2")
FRAME_MODE_TANK = bytes.fromhex("aa190000000000010100000000000000000000b3")


def _h7152() -> GoveeDevice:
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku="H7152",
        name="Smart Dehumidifier Max",
        device_type="devices.types.dehumidifier",
        capabilities=(GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),),
    )


def _h7150() -> GoveeDevice:
    """Non-pump variant — must NOT be treated as pump-capable."""
    return GoveeDevice(
        device_id="11:66:C0:EB:1D:75:5C:E8",
        sku="H7150",
        name="Smart Dehumidifier",
        device_type="devices.types.dehumidifier",
        capabilities=(GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),),
    )


class TestSupportsPumpState:
    def test_h7152_supports_pump_state(self):
        assert _h7152().supports_pump_state is True

    def test_h7150_does_not_support_pump_state(self):
        """H7150 is a non-pump variant — no confirmed frame layout for it yet."""
        assert _h7150().supports_pump_state is False


class TestUpdatePumpStateFromFrames:
    def test_normal_frame_clears_flag(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_pump_state_from_frames([FRAME_PUMP_OK]) is True
        assert state.pump_state is False

    def test_fault_frame_sets_flag(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_pump_state_from_frames([FRAME_PUMP_FAULT]) is True
        assert state.pump_state is True

    def test_flag_clears_again_on_recovery(self):
        """Live/level flag, not edge-latched like water_full — it tracks live."""
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.update_pump_state_from_frames([FRAME_PUMP_FAULT])
        assert state.pump_state is True

        state.update_pump_state_from_frames([FRAME_PUMP_RECOVERED])
        assert state.pump_state is False

    def test_unrelated_frame_is_not_recognised(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_pump_state_from_frames([FRAME_UNRELATED]) is False
        assert state.pump_state is None

    def test_short_frame_is_ignored(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        short = bytes([0xAA, 0x17, 0x00])
        assert state.update_pump_state_from_frames([short]) is False
        assert state.pump_state is None

    def test_picks_the_right_frame_out_of_a_full_push(self):
        """A real push carries ~15 frames; the scan must find the aa 17 one."""
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        frames = [FRAME_UNRELATED, FRAME_PUMP_FAULT]
        assert state.update_pump_state_from_frames(frames) is True
        assert state.pump_state is True


class TestUpdateDehumidifierModeFromFrames:
    def test_pump_frame_reports_pump(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_dehumidifier_mode_from_frames([FRAME_MODE_PUMP]) is True
        assert state.dehumidifier_mode == "pump"

    def test_tank_frame_reports_tank(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_dehumidifier_mode_from_frames([FRAME_MODE_TANK]) is True
        assert state.dehumidifier_mode == "tank"

    def test_toggles_both_ways(self):
        """Live/level flag, not edge-latched — tracks the current mode."""
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.update_dehumidifier_mode_from_frames([FRAME_MODE_TANK])
        assert state.dehumidifier_mode == "tank"

        state.update_dehumidifier_mode_from_frames([FRAME_MODE_PUMP])
        assert state.dehumidifier_mode == "pump"

    def test_unrelated_frame_is_not_recognised(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        assert state.update_dehumidifier_mode_from_frames([FRAME_UNRELATED]) is False
        assert state.dehumidifier_mode is None

    def test_short_frame_is_ignored(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        short = bytes([0xAA, 0x19, 0x00])
        assert state.update_dehumidifier_mode_from_frames([short]) is False
        assert state.dehumidifier_mode is None

    def test_picks_the_right_frame_out_of_a_full_push(self):
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        frames = [FRAME_UNRELATED, FRAME_PUMP_OK, FRAME_MODE_TANK]
        assert state.update_dehumidifier_mode_from_frames(frames) is True
        assert state.dehumidifier_mode == "tank"


def _coordinator_for_mqtt_push() -> GoveeCoordinator:
    """A minimal coordinator, bypassing __init__, for driving
    _on_mqtt_state_update directly — mirrors
    test_ceiling_fan_state.py's _coordinator() helper.
    """
    coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
    coordinator._devices = {DEVICE_ID: _h7152()}
    coordinator._states = {DEVICE_ID: GoveeDeviceState.create_empty(DEVICE_ID)}
    coordinator._transport = TransportHealthTracker()
    coordinator.async_set_updated_data = MagicMock()
    return coordinator


class TestCoordinatorAppliesBothFieldsFromAnMqttPush:
    """_on_mqtt_state_update must actually call both decoders for an H7152
    push — the decoders themselves are covered above, this exercises the
    wiring in the coordinator that calls them.
    """

    def test_pump_fault_and_tank_mode_applied_together(self):
        coordinator = _coordinator_for_mqtt_push()

        coordinator._on_mqtt_state_update(
            DEVICE_ID,
            {
                "onOff": 1,
                "_op_frames": [FRAME_PUMP_FAULT.hex(), FRAME_MODE_TANK.hex()],
            },
        )

        state = coordinator._states[DEVICE_ID]
        assert state.pump_state is True
        assert state.dehumidifier_mode == "tank"
        coordinator.async_set_updated_data.assert_called_once()

    def test_pump_ok_and_pump_mode_applied_together(self):
        coordinator = _coordinator_for_mqtt_push()

        coordinator._on_mqtt_state_update(
            DEVICE_ID,
            {
                "onOff": 1,
                "_op_frames": [FRAME_PUMP_OK.hex(), FRAME_MODE_PUMP.hex()],
            },
        )

        state = coordinator._states[DEVICE_ID]
        assert state.pump_state is False
        assert state.dehumidifier_mode == "pump"

    def test_non_pump_device_is_left_untouched(self):
        """A non-H7152 device must never reach either decoder, even if it
        somehow carried a matching frame shape."""
        coordinator = GoveeCoordinator.__new__(GoveeCoordinator)
        other_id = _h7150().device_id
        coordinator._devices = {other_id: _h7150()}
        coordinator._states = {other_id: GoveeDeviceState.create_empty(other_id)}
        coordinator._transport = TransportHealthTracker()
        coordinator.async_set_updated_data = MagicMock()

        coordinator._on_mqtt_state_update(
            other_id,
            {"onOff": 1, "_op_frames": [FRAME_PUMP_FAULT.hex(), FRAME_MODE_TANK.hex()]},
        )

        state = coordinator._states[other_id]
        assert state.pump_state is None
        assert state.dehumidifier_mode is None


class TestPreservedAcrossDeveloperPoll:
    """The Developer /device/state poll has no field for either value at
    all — they only ever come from AWS IoT push frames — so a naive poll
    would flicker the sensors to "unknown" every ~60s (same bug class as
    water_full/presence, issues #118/#124).
    """

    def _coord(self):
        import custom_components.govee.coordinator as coord_mod

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        config_entry.async_create_background_task = MagicMock()
        coord = coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=None,
            poll_interval=60,
        )
        coord._devices[DEVICE_ID] = _h7152()
        return coord

    @pytest.mark.asyncio
    async def test_pump_state_survives_a_poll_that_knows_nothing_about_it(self):
        coord = self._coord()
        existing = GoveeDeviceState.create_empty(DEVICE_ID)
        existing.pump_state = True
        coord._states[DEVICE_ID] = existing

        # What the Developer poll actually returns: no pump_state field at
        # all, so the fresh state has it as None.
        fresh = GoveeDeviceState.create_empty(DEVICE_ID)
        coord._api_client.get_device_state = AsyncMock(return_value=fresh)

        result = await coord._fetch_device_state(DEVICE_ID, coord._devices[DEVICE_ID])

        assert result.pump_state is True

    @pytest.mark.asyncio
    async def test_dehumidifier_mode_survives_a_poll_that_knows_nothing_about_it(self):
        coord = self._coord()
        existing = GoveeDeviceState.create_empty(DEVICE_ID)
        existing.dehumidifier_mode = "tank"
        coord._states[DEVICE_ID] = existing

        fresh = GoveeDeviceState.create_empty(DEVICE_ID)
        coord._api_client.get_device_state = AsyncMock(return_value=fresh)

        result = await coord._fetch_device_state(DEVICE_ID, coord._devices[DEVICE_ID])

        assert result.dehumidifier_mode == "tank"

    @pytest.mark.asyncio
    async def test_recovery_is_not_masked_by_a_stale_preserved_value(self):
        """Preservation only fills a None gap — it must never overwrite a
        push-derived value the poll legitimately doesn't touch."""
        coord = self._coord()
        existing = GoveeDeviceState.create_empty(DEVICE_ID)
        existing.pump_state = True
        coord._states[DEVICE_ID] = existing

        fresh = GoveeDeviceState.create_empty(DEVICE_ID)
        fresh.pump_state = False  # e.g. a push landed between poll start and finish
        coord._api_client.get_device_state = AsyncMock(return_value=fresh)

        result = await coord._fetch_device_state(DEVICE_ID, coord._devices[DEVICE_ID])

        assert result.pump_state is False


class TestPumpStateBinarySensor:
    def _coord(self):
        import custom_components.govee.coordinator as coord_mod

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        coord = coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=None,
            poll_interval=60,
        )
        coord._devices[DEVICE_ID] = _h7152()
        return coord

    def test_is_on_reflects_state(self):
        from custom_components.govee.binary_sensor import GoveePumpStateBinarySensor

        coord = self._coord()
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.pump_state = True
        coord._states[DEVICE_ID] = state

        sensor = GoveePumpStateBinarySensor(coord, coord._devices[DEVICE_ID])
        assert sensor.is_on is True

    def test_is_on_none_before_any_push(self):
        from custom_components.govee.binary_sensor import GoveePumpStateBinarySensor

        coord = self._coord()
        sensor = GoveePumpStateBinarySensor(coord, coord._devices[DEVICE_ID])
        assert sensor.is_on is None


class TestDehumidifierModeSensor:
    def _coord(self):
        import custom_components.govee.coordinator as coord_mod

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        coord = coord_mod.GoveeCoordinator(
            hass=hass,
            config_entry=config_entry,
            api_client=MagicMock(),
            iot_credentials=None,
            poll_interval=60,
        )
        coord._devices[DEVICE_ID] = _h7152()
        return coord

    def test_native_value_reflects_state(self):
        from custom_components.govee.sensor import GoveeDehumidifierModeSensor

        coord = self._coord()
        state = GoveeDeviceState.create_empty(DEVICE_ID)
        state.dehumidifier_mode = "tank"
        coord._states[DEVICE_ID] = state

        sensor = GoveeDehumidifierModeSensor(coord, coord._devices[DEVICE_ID])
        assert sensor.native_value == "tank"

    def test_native_value_none_before_any_push(self):
        from custom_components.govee.sensor import GoveeDehumidifierModeSensor

        coord = self._coord()
        sensor = GoveeDehumidifierModeSensor(coord, coord._devices[DEVICE_ID])
        assert sensor.native_value is None


class TestBinarySensorSetupRegistersPumpState:
    """async_setup_entry must actually create the entity for an H7152 —
    the entity class itself is covered above, this exercises the
    conditional registration in the platform's setup function."""

    @pytest.mark.asyncio
    async def test_h7152_gets_a_pump_state_entity(self):
        from custom_components.govee.binary_sensor import (
            GoveePumpStateBinarySensor,
            async_setup_entry,
        )

        coordinator = MagicMock()
        coordinator.devices = {DEVICE_ID: _h7152()}
        coordinator.is_bff_leak_sensor.return_value = False
        coordinator.leak_sensors = {}

        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options.get.return_value = False  # CONF_EXPOSE_TRANSPORT_ENTITIES off

        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)

        pump_state_entities = [e for e in added if isinstance(e, GoveePumpStateBinarySensor)]
        assert len(pump_state_entities) == 1

    @pytest.mark.asyncio
    async def test_h7150_gets_no_pump_state_entity(self):
        from custom_components.govee.binary_sensor import (
            GoveePumpStateBinarySensor,
            async_setup_entry,
        )

        coordinator = MagicMock()
        coordinator.devices = {_h7150().device_id: _h7150()}
        coordinator.is_bff_leak_sensor.return_value = False
        coordinator.leak_sensors = {}

        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options.get.return_value = False

        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)

        assert not any(isinstance(e, GoveePumpStateBinarySensor) for e in added)


class TestSensorSetupRegistersDehumidifierMode:
    """async_setup_entry must actually create the Mode entity for an
    H7152 — the entity class itself is covered above, this exercises the
    conditional registration in the platform's setup function."""

    @pytest.mark.asyncio
    async def test_h7152_gets_a_mode_entity(self):
        from custom_components.govee.sensor import (
            GoveeDehumidifierModeSensor,
            async_setup_entry,
        )

        device = _h7152()
        coordinator = MagicMock()
        coordinator.devices = {DEVICE_ID: device}
        coordinator.mqtt_client = None
        coordinator.get_state.return_value = None
        coordinator.is_bff_leak_sensor.return_value = False
        coordinator.leak_sensors = {}

        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.entry_id = "test_entry"

        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)

        mode_entities = [e for e in added if isinstance(e, GoveeDehumidifierModeSensor)]
        assert len(mode_entities) == 1

    @pytest.mark.asyncio
    async def test_h7150_gets_no_mode_entity(self):
        from custom_components.govee.sensor import (
            GoveeDehumidifierModeSensor,
            async_setup_entry,
        )

        device = _h7150()
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        coordinator.mqtt_client = None
        coordinator.get_state.return_value = None
        coordinator.is_bff_leak_sensor.return_value = False
        coordinator.leak_sensors = {}

        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.entry_id = "test_entry"

        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)

        assert not any(isinstance(e, GoveeDehumidifierModeSensor) for e in added)
