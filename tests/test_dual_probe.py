"""Second temperature probe on dual-probe SKUs (issue #150).

The H5112 fridge/freezer thermometer carries two independent probes and reports
them separately: ``tem`` is probe 1 and ``tem2`` is probe 2, with a matching
second set of ``probeName2`` / ``temMin2`` / ``temMax2`` / ``temCali2``
settings. Only ``tem`` was ever read.

Either probe can be absent on its own. Reporter diagnostics on #150 showed
three H5112s behind one H5044 where two had probe 1 unplugged — reporting the
``-1`` sentinel that correctly decodes to "no reading" — while probe 2 read
normally. Those two devices surfaced no temperature at all, despite carrying a
perfectly good reading the integration never looked at.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from custom_components.govee.api.auth import GoveeAuthClient
from custom_components.govee.sensor import (
    GoveeSecondProbeTemperatureSensor,
    GoveeTemperatureSensor,
)

from .test_auth import _bff_response, make_mock_response, make_session_get


def _h5112(*, tem, hum, tem2, sno=1):
    """A BFF device entry shaped like the #150 reporter's H5112s."""
    return {
        "sku": "H5112",
        "device": f"00:00:00:00:00:00:00:00:0{sno}:8D:DB:48:C2:06:12:5D",
        "deviceName": f"Freezer {sno}",
        "deviceExt": json.dumps(
            {
                "deviceSettings": json.dumps(
                    {
                        "battery": 100,
                        "sno": sno,
                        "fahOpen": False,
                        "gatewayInfo": {
                            "device": "11:22:33:44:55:66:77:88",
                            "sku": "H5044",
                        },
                    }
                ),
                "lastDeviceData": json.dumps(
                    {"online": False, "tem": tem, "hum": hum, "tem2": tem2}
                ),
            }
        ),
    }


class TestSecondProbeParsing:
    """``tem2`` is read alongside ``tem``, and each can be absent alone."""

    @pytest.mark.asyncio
    async def test_both_probes_present(self):
        """The unit whose probe 1 works also has a probe 2 we ignored before."""
        session = make_session_get(
            make_mock_response(200, _bff_response([_h5112(tem=-1710, hum=6920, tem2=660, sno=3)]))
        )
        sensor = (await GoveeAuthClient(session=session).fetch_bff_thermo_hygrometers(token="t"))[0]
        assert sensor["temperature"] == -17.1
        assert sensor["temperature_2"] == 6.6
        assert sensor["humidity"] == 69.2

    @pytest.mark.asyncio
    async def test_probe_one_unplugged_still_yields_probe_two(self):
        """The #150 case: tem is the -1 sentinel, tem2 holds the real value."""
        session = make_session_get(
            make_mock_response(200, _bff_response([_h5112(tem=-1, hum=65535, tem2=-420, sno=1)]))
        )
        sensor = (await GoveeAuthClient(session=session).fetch_bff_thermo_hygrometers(token="t"))[0]
        # Both sentinels correctly decode to "absent"...
        assert sensor["temperature"] is None
        assert sensor["humidity"] is None
        # ...and the reading that does exist is no longer thrown away.
        assert sensor["temperature_2"] == -4.2

    @pytest.mark.asyncio
    async def test_single_probe_device_has_no_second_reading(self):
        """A device with no tem2 must not gain a phantom probe."""
        device = _h5112(tem=2350, hum=4500, tem2=None)
        ext = json.loads(device["deviceExt"])
        last = json.loads(ext["lastDeviceData"])
        del last["tem2"]
        ext["lastDeviceData"] = json.dumps(last)
        device["deviceExt"] = json.dumps(ext)

        session = make_session_get(make_mock_response(200, _bff_response([device])))
        sensor = (await GoveeAuthClient(session=session).fetch_bff_thermo_hygrometers(token="t"))[0]
        assert sensor["temperature"] == 23.5
        assert sensor["temperature_2"] is None

    @pytest.mark.asyncio
    async def test_second_probe_sentinel_is_absent_too(self):
        session = make_session_get(
            make_mock_response(200, _bff_response([_h5112(tem=2350, hum=4500, tem2=-1)]))
        )
        sensor = (await GoveeAuthClient(session=session).fetch_bff_thermo_hygrometers(token="t"))[0]
        assert sensor["temperature_2"] is None


class TestSecondProbeEntity:
    """The probe-2 entity reuses probe 1's conversion, reading a different field."""

    def _stub(self, *, temperature, temperature_2, sku="H5112", bff=True):
        state = SimpleNamespace(
            sensor_temperature=temperature,
            sensor_temperature_2=temperature_2,
        )
        coordinator = SimpleNamespace(
            config_entry=SimpleNamespace(options={}),
            is_bff_thermometer=lambda _device_id: bff,
            account_temperature_unit=lambda _device_id: None,
        )
        return SimpleNamespace(
            device_state=state,
            coordinator=coordinator,
            _device=SimpleNamespace(sku=sku),
            _device_id="AA:BB:CC:DD:EE:FF:00:11",
        )

    def test_reads_the_second_field_not_the_first(self):
        stub = self._stub(temperature=-17.1, temperature_2=6.6)
        raw = GoveeSecondProbeTemperatureSensor._raw_reading.fget(stub)
        assert raw == 6.6

    def test_primary_entity_still_reads_the_first(self):
        stub = self._stub(temperature=-17.1, temperature_2=6.6)
        assert GoveeTemperatureSensor._raw_reading.fget(stub) == -17.1

    def test_absent_second_probe_reads_none(self):
        stub = self._stub(temperature=23.5, temperature_2=None)
        assert GoveeSecondProbeTemperatureSensor._raw_reading.fget(stub) is None

    def test_unit_conversion_is_shared_with_probe_one(self):
        """A °F-reporting SKU converts probe 2 exactly as it converts probe 1."""
        stub = self._stub(temperature=32.0, temperature_2=212.0, sku="H5109", bff=False)
        stub._raw_reading = 212.0
        assert abs(GoveeSecondProbeTemperatureSensor.native_value.fget(stub) - 100.0) < 1e-6

    def test_distinct_unique_id_from_probe_one(self):
        """Sharing a unique_id would make HA drop one of the two entities."""
        assert (
            GoveeSecondProbeTemperatureSensor._attr_translation_key
            != GoveeTemperatureSensor._attr_translation_key
        )
