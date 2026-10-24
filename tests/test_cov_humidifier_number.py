"""Coverage tests for the humidifier and number platforms.

Humidifier: platform wiring, RestoreEntity restoration of the H7150 target
humidity, the state-less property fallbacks, malformed capability options,
and the target-humidity write on a device with neither an Auto mode nor a
``range::humidity`` capability.

Number: platform wiring for probe limits, music sensitivity and heater
temperature; the probe-limit entity end to end; and RestoreEntity
restoration of the two slider entities.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.number import NumberDeviceClass, NumberExtraStoredData
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.govee import humidifier as humidifier_mod
from custom_components.govee import number as number_mod
from custom_components.govee.const import DOMAIN
from custom_components.govee.humidifier import MODE_AUTO, MODE_DRYER, MODE_HIGH, GoveeHumidifierEntity
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    ProbeReading,
)
from custom_components.govee.models.device import (
    CAPABILITY_MUSIC_MODE,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_TEMPERATURE_SETTING,
    CAPABILITY_TOGGLE,
    CAPABILITY_WORK_MODE,
    DEVICE_TYPE_DEHUMIDIFIER,
    DEVICE_TYPE_HEATER,
    DEVICE_TYPE_HUMIDIFIER,
    DEVICE_TYPE_LIGHT,
    INSTANCE_HUMIDITY,
    INSTANCE_MUSIC_MODE,
    INSTANCE_POWER,
    INSTANCE_TARGET_TEMPERATURE,
    INSTANCE_THERMOSTAT_TOGGLE,
    INSTANCE_WORK_MODE,
)
from custom_components.govee.number import (
    GoveeHeaterTemperatureNumber,
    GoveeMusicSensitivityNumber,
    GoveeProbeLimitNumber,
)

# --------------------------------------------------------------------------- #
# Device builders
# --------------------------------------------------------------------------- #


def _cap(cap_type: str, instance: str, params: dict | None = None) -> GoveeCapability:
    return GoveeCapability(type=cap_type, instance=instance, parameters=params or {})


def _work_mode(work_modes: list[dict], mode_values: list[dict]) -> GoveeCapability:
    return _cap(
        CAPABILITY_WORK_MODE,
        INSTANCE_WORK_MODE,
        {
            "dataType": "STRUCT",
            "fields": [
                {"fieldName": "workMode", "dataType": "ENUM", "options": work_modes},
                {"fieldName": "modeValue", "dataType": "ENUM", "options": mode_values},
            ],
        },
    )


_GEAR = {"name": "gearMode", "options": [{"name": "Low", "value": 1}, {"name": "High", "value": 3}]}
_HUMIDITY_RANGE = {"unit": "unit.percent", "dataType": "INTEGER", "range": {"min": 30, "max": 80, "precision": 1}}


def _h7150(*, is_group: bool = False, device_type: str = DEVICE_TYPE_DEHUMIDIFIER) -> GoveeDevice:
    """H7150: Auto modeValue is the setpoint (30-80) and range::humidity exists."""
    return GoveeDevice(
        device_id="11825917" if is_group else "0A:E8:D4:AD:FC:7A:05:2A",
        sku="H7150",
        name="Dehumidifier",
        device_type=device_type,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_RANGE, INSTANCE_HUMIDITY, _HUMIDITY_RANGE),
            _work_mode(
                [{"name": "gearMode", "value": 1}, {"name": "Auto", "value": 3}, {"name": "Dryer", "value": 8}],
                [_GEAR, {"name": "Auto", "range": {"min": 30, "max": 80}}, {"name": "Dryer", "value": 0}],
            ),
        ),
        is_group=is_group,
    )


def _h7152() -> GoveeDevice:
    """H7152: Auto modeValue pinned (80..80); setpoint lives in range::humidity."""
    return GoveeDevice(
        device_id="1B:F9:E5:BE:0D:8B:16:3B",
        sku="H7152",
        name="Dehumidifier Pro",
        device_type=DEVICE_TYPE_DEHUMIDIFIER,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_RANGE, INSTANCE_HUMIDITY, _HUMIDITY_RANGE),
            _work_mode(
                [{"name": "gearMode", "value": 1}, {"name": "Auto", "value": 3}],
                [_GEAR, {"name": "Auto", "range": {"min": 80, "max": 80}}],
            ),
        ),
        is_group=False,
    )


def _gear_only_humidifier() -> GoveeDevice:
    """A humidifier with neither an Auto mode nor a range::humidity capability."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:71:60",
        sku="H7160",
        name="Humidifier",
        device_type=DEVICE_TYPE_HUMIDIFIER,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _work_mode([{"name": "gearMode", "value": 1}, {"name": "Dryer", "value": 8}], [_GEAR]),
        ),
        is_group=False,
    )


def _probe() -> GoveeDevice:
    return GoveeDevice.synthetic_probe_thermometer("AA:BB:CC:DD:EE:FF:51:92", "H5192", "Grill Probe")


def _probe_h5194() -> GoveeDevice:
    return GoveeDevice.synthetic_probe_thermometer("AA:BB:CC:DD:EE:FF:51:94", "H5194", "Big Grill Probe")


def _struct_music_light(options: list[dict]) -> GoveeDevice:
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:60:22",
        sku="H6022",
        name="Lava lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(
                CAPABILITY_MUSIC_MODE,
                INSTANCE_MUSIC_MODE,
                {
                    "dataType": "STRUCT",
                    "fields": [
                        {"fieldName": "musicMode", "dataType": "ENUM", "options": options},
                        {"fieldName": "sensitivity", "dataType": "INTEGER", "range": {"min": 1, "max": 99}},
                    ],
                },
            ),
        ),
        is_group=False,
    )


def _heater() -> GoveeDevice:
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:71:30",
        sku="H7130",
        name="Living Room Heater",
        device_type=DEVICE_TYPE_HEATER,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(
                CAPABILITY_TEMPERATURE_SETTING,
                INSTANCE_TARGET_TEMPERATURE,
                {
                    "fields": [
                        {"fieldName": "autoStop", "defaultValue": 0},
                        {"fieldName": "temperature", "range": {"min": 5, "max": 30}},
                        {"fieldName": "unit", "defaultValue": "Celsius"},
                    ],
                },
            ),
            _cap(CAPABILITY_TOGGLE, INSTANCE_THERMOSTAT_TOGGLE),
        ),
        is_group=False,
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _online_state(device: GoveeDevice) -> GoveeDeviceState:
    state = GoveeDeviceState.create_empty(device.device_id)
    state.online = True
    return state


def _coordinator(device: GoveeDevice, state: GoveeDeviceState | None, last_update_success: bool = True) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device}
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.last_update_success = last_update_success
    return coordinator


def _attach(entity):
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity


async def _restore(entity, last_state: State | None) -> None:
    entity.async_get_last_state = AsyncMock(return_value=last_state)
    await entity.async_added_to_hass()


async def _setup(module, *devices: GoveeDevice) -> list:
    coordinator = MagicMock()
    coordinator.devices = {d.device_id: d for d in devices}
    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.options = {}
    added: list = []
    await module.async_setup_entry(MagicMock(), entry, added.extend)
    return added


# =========================================================================== #
# Humidifier
# =========================================================================== #


class TestHumidifierSetup:
    async def test_only_non_group_humidifiers_get_an_entity(self, mock_light_device):
        added = await _setup(humidifier_mod, _h7150(), _h7150(is_group=True), mock_light_device)
        assert [type(e).__name__ for e in added] == ["GoveeHumidifierEntity"]
        assert added[0]._device.is_group is False


class TestHumidifierRestore:
    def _entity(self, device: GoveeDevice) -> GoveeHumidifierEntity:
        return _attach(GoveeHumidifierEntity(_coordinator(device, _online_state(device)), device))

    async def test_pinned_auto_device_does_not_restore(self):
        # H7152 reads its live setpoint from range::humidity on every poll.
        entity = self._entity(_h7152())
        entity.async_get_last_state = AsyncMock(return_value=State("humidifier.pro", "on", {"humidity": 55}))
        await entity.async_added_to_hass()
        entity.async_get_last_state.assert_not_awaited()
        assert entity._optimistic_target is None

    async def test_no_previous_state(self):
        entity = self._entity(_h7150())
        await _restore(entity, None)
        assert entity._optimistic_target is None

    async def test_restores_the_last_target(self):
        entity = self._entity(_h7150())
        await _restore(entity, State("humidifier.dehumidifier", "on", {"humidity": "55"}))
        assert entity._optimistic_target == 55
        # The poll never reports the Auto setpoint, so the restored value is
        # what the humidity dial shows.
        assert entity.target_humidity == 55

    @pytest.mark.parametrize("humidity", [None, "abc"])
    async def test_unparseable_target_is_ignored(self, humidity):
        entity = self._entity(_h7150())
        await _restore(entity, State("humidifier.dehumidifier", "on", {"humidity": humidity}))
        assert entity._optimistic_target is None

    async def test_out_of_range_target_is_ignored(self):
        entity = self._entity(_h7150())
        await _restore(entity, State("humidifier.dehumidifier", "on", {"humidity": 99}))
        assert entity._optimistic_target is None
        assert entity.target_humidity == 30


class TestHumidifierProperties:
    def test_mode_and_target_unknown_without_state(self):
        device = _h7150()
        entity = GoveeHumidifierEntity(_coordinator(device, None), device)
        assert entity.mode is None
        assert entity.target_humidity is None

    def test_mode_unknown_without_work_mode(self):
        device = _h7150()
        entity = GoveeHumidifierEntity(_coordinator(device, _online_state(device)), device)
        assert entity.mode is None

    def test_options_without_a_value_are_skipped(self):
        # The device model already filters these, so patch it to prove the
        # entity copes with a malformed option list on its own.
        device = _h7150()
        with (
            patch.object(
                GoveeDevice,
                "get_humidifier_work_mode_options",
                return_value=[
                    {"name": "Auto", "value": None},
                    {"name": "gearMode", "value": 1},
                    {"name": "Dryer", "value": 8},
                ],
            ),
            patch.object(
                GoveeDevice,
                "get_humidifier_gear_options",
                return_value=[{"name": "Low", "value": None}, {"name": "High", "value": 3}],
            ),
        ):
            entity = GoveeHumidifierEntity(_coordinator(device, None), device)
        assert entity.available_modes == [MODE_HIGH, MODE_DRYER]
        assert MODE_AUTO not in entity._mode_to_work_mode


class TestHumidifierSetHumidityWithoutAuto:
    async def test_raises_when_the_device_cannot_take_a_target(self):
        device = _gear_only_humidifier()
        coordinator = _coordinator(device, _online_state(device))
        entity = _attach(GoveeHumidifierEntity(coordinator, device))
        assert entity.available_modes == ["low", "high", "dryer"]

        with pytest.raises(ServiceValidationError) as err:
            await entity.async_set_humidity(50)

        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "unsupported_target_humidity"
        assert err.value.translation_placeholders == {"device": device.name}
        coordinator.async_control_device.assert_not_awaited()
        entity.async_write_ha_state.assert_not_called()


# =========================================================================== #
# Number
# =========================================================================== #


class TestNumberSetup:
    async def test_probe_thermometer_gets_four_limits_per_probe(self):
        added = await _setup(number_mod, _probe())
        assert all(isinstance(e, GoveeProbeLimitNumber) for e in added)
        assert sorted(e.unique_id for e in added) == sorted(
            f"AA:BB:CC:DD:EE:FF:51:92_probe{probe}_{limit}"
            for probe in (1, 2)
            for limit in ("core_max", "core_min", "ambient_max", "ambient_min")
        )

    async def test_h5194_gets_four_probes_worth_of_limits(self):
        """The 4-probe sibling of the H5192 (issue #197) gets 16 limit
        entities, not the H5192's 8 — and the two SKUs' entities never mix.
        """
        added = await _setup(number_mod, _probe_h5194())
        assert all(isinstance(e, GoveeProbeLimitNumber) for e in added)
        assert sorted(e.unique_id for e in added) == sorted(
            f"AA:BB:CC:DD:EE:FF:51:94_probe{probe}_{limit}"
            for probe in (1, 2, 3, 4)
            for limit in ("core_max", "core_min", "ambient_max", "ambient_min")
        )

    async def test_struct_music_device_gets_a_sensitivity_slider(self):
        added = await _setup(number_mod, _struct_music_light([{"name": "Rhythm", "value": 3}]))
        (entity,) = added
        assert isinstance(entity, GoveeMusicSensitivityNumber)
        assert (entity.native_min_value, entity.native_max_value) == (1.0, 99.0)

    async def test_struct_music_device_without_modes_gets_nothing(self):
        assert await _setup(number_mod, _struct_music_light([])) == []

    async def test_heater_gets_a_temperature_slider(self):
        added = await _setup(number_mod, _heater())
        (entity,) = added
        assert isinstance(entity, GoveeHeaterTemperatureNumber)
        assert (entity.native_min_value, entity.native_max_value) == (5.0, 30.0)
        entity.coordinator.get_state = MagicMock(return_value=None)
        assert entity.native_value == 17.0  # midpoint until restored/set


class TestProbeLimitNumber:
    def _entity(self, state: GoveeDeviceState | None, limit: str = "core_max", last_update_success: bool = True):
        device = _probe()
        coordinator = _coordinator(device, state, last_update_success=last_update_success)
        coordinator.async_set_probe_limits = AsyncMock(return_value=True)
        return GoveeProbeLimitNumber(coordinator, device, 1, limit), coordinator, device

    def test_identity(self):
        entity, _, device = self._entity(None, limit="ambient_min")
        assert entity.unique_id == f"{device.device_id}_probe1_ambient_min"
        assert entity.translation_key == "probe_ambient_min"
        assert entity.translation_placeholders == {"probe": "1"}
        assert entity.entity_category is EntityCategory.CONFIG
        assert entity.device_class is NumberDeviceClass.TEMPERATURE
        assert entity.native_unit_of_measurement == UnitOfTemperature.CELSIUS

    def test_available_as_soon_as_state_exists_even_offline(self):
        state = GoveeDeviceState.create_empty(_probe().device_id)
        state.online = False  # this SKU reports offline permanently
        entity, _, _ = self._entity(state)
        assert entity.available is True

    def test_unavailable_without_state_or_after_a_failed_update(self):
        entity, _, _ = self._entity(None)
        assert entity.available is False
        entity, _, _ = self._entity(GoveeDeviceState.create_empty(_probe().device_id), last_update_success=False)
        assert entity.available is False

    def test_native_value_unknown_without_state_or_reading(self):
        entity, _, _ = self._entity(None)
        assert entity.native_value is None
        state = GoveeDeviceState.create_empty(_probe().device_id)
        entity, _, _ = self._entity(state)
        assert entity.native_value is None

    def test_native_value_reads_the_stored_limit(self):
        state = GoveeDeviceState.create_empty(_probe().device_id)
        state.probes[1] = ProbeReading(core=34.0, core_max=88.0, ambient_min=None)
        entity, _, _ = self._entity(state)
        assert entity.native_value == 88.0
        entity, _, _ = self._entity(state, limit="ambient_min")
        assert entity.native_value is None

    async def test_set_value_writes_that_one_limit(self):
        entity, coordinator, device = self._entity(GoveeDeviceState.create_empty(_probe().device_id))
        await entity.async_set_native_value(75.0)
        coordinator.async_set_probe_limits.assert_awaited_once_with(device.device_id, 1, core_max=75.0)

    async def test_rejected_write_raises(self):
        entity, coordinator, _ = self._entity(GoveeDeviceState.create_empty(_probe().device_id))
        coordinator.async_set_probe_limits.return_value = False
        with pytest.raises(HomeAssistantError) as err:
            await entity.async_set_native_value(75.0)
        assert err.value.translation_key == "command_failed"


class TestMusicSensitivityRestore:
    def _entity(self) -> GoveeMusicSensitivityNumber:
        device = _struct_music_light([{"name": "Rhythm", "value": 3}])
        return _attach(GoveeMusicSensitivityNumber(_coordinator(device, None), device))

    async def test_restores_the_previous_value(self):
        entity = self._entity()
        await _restore(entity, State("number.lava_lamp_music_sensitivity", "75"))
        assert entity.native_value == 75.0

    @pytest.mark.parametrize("previous", [None, "unknown", "unavailable"])
    async def test_keeps_the_default_without_a_usable_state(self, previous):
        entity = self._entity()
        last = State("number.lava_lamp_music_sensitivity", previous) if previous else None
        await _restore(entity, last)
        assert entity.native_value == 50.0

    async def test_invalid_state_is_reported_and_ignored(self, caplog):
        entity = self._entity()
        with caplog.at_level(logging.WARNING, logger="custom_components.govee.number"):
            await _restore(entity, State("number.lava_lamp_music_sensitivity", "loud"))
        assert entity.native_value == 50.0
        assert "Could not restore music sensitivity for Lava lamp: invalid state 'loud'" in caplog.text


class TestHeaterTemperatureRestore:
    def _entity(self) -> GoveeHeaterTemperatureNumber:
        device = _heater()
        return _attach(GoveeHeaterTemperatureNumber(_coordinator(device, None), device, temp_range=(5, 30)))

    async def test_restores_the_previous_native_value(self):
        entity = self._entity()
        entity.async_get_last_number_data = AsyncMock(
            return_value=NumberExtraStoredData(30.0, 5.0, 1.0, UnitOfTemperature.CELSIUS, 22.0)
        )
        await entity.async_added_to_hass()
        assert entity.native_value == 22.0

    @pytest.mark.parametrize(
        "previous", [None, NumberExtraStoredData(30.0, 5.0, 1.0, UnitOfTemperature.CELSIUS, None)]
    )
    async def test_keeps_the_default_without_a_usable_value(self, previous):
        entity = self._entity()
        entity.async_get_last_number_data = AsyncMock(return_value=previous)
        await entity.async_added_to_hass()
        assert entity.native_value == 17.0
