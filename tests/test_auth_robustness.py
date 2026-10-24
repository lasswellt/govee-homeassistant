"""Account/BFF client robustness: malformed device entries, HTTP 401 mapping, login errors.

Complements ``test_cov_auth.py`` with the shapes Govee is not documented to
send but the parsers must survive: a ``deviceExt`` of ``null``, a non-dict
``gatewayInfo``, a non-numeric slot, an HTTP 401 with a non-JSON body, a login
error with a null message, and a verification-code request answered with an
in-body error. Everything runs offline over fake aiohttp sessions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.govee.api.auth import GoveeAuthClient, is_connection_error
from custom_components.govee.api.exceptions import GoveeApiError, GoveeAuthError, GoveeLoginRejectedError
from tests.test_cov_auth import _bff_list, _leak, _login_body, _response, _session, _thermo


def _non_json(status: int) -> MagicMock:
    """A response whose body is not JSON (an HTML/plain-text error page)."""
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(side_effect=aiohttp.ContentTypeError(MagicMock(), (), message="text/html"))
    return response


GOOD_LEAK_EXT = {"deviceSettings": {"sno": 1, "battery": 90, "gatewayInfo": {"device": "GW:1", "sku": "H5044"}}}


class TestMalformedDeviceEntries:
    """One odd device must not take the whole account list down (CLOUD-3)."""

    @pytest.mark.parametrize("bad_ext", [None, "null", 5, ["x"]])
    def test_topics_survive_a_non_dict_device_ext(self, bad_ext):
        devices: list[Any] = [
            {"device": "A", "deviceExt": bad_ext},
            "not-a-device",
            {"device": "B", "deviceExt": {"deviceSettings": {"topic": "GD/b"}}},
        ]

        assert GoveeAuthClient._extract_topics_from_devices(devices) == {"B": "GD/b"}
        assert GoveeAuthClient._extract_gateway_routes(devices) == {}

    async def test_leak_list_skips_only_the_malformed_sensor(self):
        devices: list[Any] = [
            _leak("L1", None),
            _leak("L2", {"deviceSettings": {"sno": "slot-x", "gatewayInfo": {"device": "GW:1"}}}),
            _leak("L3", {"deviceSettings": {"sno": 2, "gatewayInfo": "GW:1"}, "lastDeviceData": "null"}),
            _leak("L4", GOOD_LEAK_EXT),
            {"sku": "H5179", "device": "T9", "deviceExt": None},
            None,
        ]
        client = GoveeAuthClient(session=_session(get=[_response(200, _bff_list(*devices))]))

        sensors, _hubs, _thermo_readings = await client.fetch_bff_leak_sensors("tok")

        by_id = {s["device_id"]: s for s in sensors}
        assert set(by_id) == {"L3", "L4"}
        assert by_id["L3"]["hub_device_id"] == ""
        assert by_id["L3"]["online"] is True
        assert by_id["L4"]["sno"] == 1

    async def test_thermo_list_survives_a_null_device_ext(self):
        devices: list[Any] = [_thermo("T1", None), _thermo("T2", {"deviceSettings": {"battery": 70}}), 7]
        client = GoveeAuthClient(session=_session(get=[_response(200, _bff_list(*devices))]))

        sensors = {s["device_id"]: s for s in await client.fetch_bff_thermo_hygrometers("tok")}

        assert set(sensors) == {"T1", "T2"}
        assert sensors["T2"]["battery"] == 70

    async def test_water_detector_list_survives_a_null_device_ext(self):
        devices: list[Any] = [
            {"device": "AA:BB", "deviceExt": None},
            {"device": "CC:DD", "deviceExt": {"deviceSettings": "null", "lastDeviceData": {"lastTime": 9}}},
        ]
        client = GoveeAuthClient(session=_session(get=[_response(200, _bff_list(*devices))]))

        states = await client.fetch_water_detector_states("tok", {"AA:BB", "CC:DD"})

        assert states["AA:BB"]["online"] is True
        assert states["CC:DD"]["last_time"] == 9
        assert states["CC:DD"]["battery"] is None

    async def test_non_dict_data_envelope_reads_as_no_devices(self):
        client = GoveeAuthClient(session=_session(get=[_response(200, {"data": "null"})]))

        assert await client.fetch_bff_thermo_hygrometers("tok") == []


class TestHttp401IsAnAuthError:
    """HTTP 401 means 'log in again', whatever the body (CLOUD-4)."""

    async def test_legacy_device_list(self):
        client = GoveeAuthClient(session=_session(post=[_non_json(401)]))

        with pytest.raises(GoveeAuthError):
            await client.fetch_device_topics("tok", client_id="cid")

    async def test_legacy_device_list_json_401(self):
        client = GoveeAuthClient(session=_session(post=[_response(401, {"message": "expired"})]))

        with pytest.raises(GoveeAuthError, match="expired"):
            await client.fetch_device_topics("tok", client_id="cid")

    async def test_iot_key(self):
        client = GoveeAuthClient(session=_session(get=[_non_json(401)]))

        with pytest.raises(GoveeAuthError):
            await client.get_iot_key("tok", client_id="cid")

    @pytest.mark.parametrize(
        "call",
        [
            lambda c: c.fetch_bff_leak_sensors("tok"),
            lambda c: c.fetch_bff_thermo_hygrometers("tok"),
            lambda c: c.fetch_water_detector_states("tok", {"AA"}),
            lambda c: c._fetch_bff_device_topics("tok"),
        ],
    )
    async def test_bff_lists_with_non_json_body(self, call):
        client = GoveeAuthClient(session=_session(get=[_non_json(401)]))

        with pytest.raises(GoveeAuthError):
            await call(client)

    async def test_bff_list_non_dict_error_body(self):
        client = GoveeAuthClient(session=_session(get=[_response(500, ["oops"])]))

        with pytest.raises(GoveeApiError, match="HTTP 500"):
            await client.fetch_bff_leak_sensors("tok")


class TestLoginErrorParsing:
    """A null message or a non-dict body is a rejection, not a crash (CLOUD-8)."""

    async def test_in_body_error_with_null_message(self):
        client = GoveeAuthClient(session=_session(post=[_response(200, {"status": 400, "message": None})]))

        with pytest.raises(GoveeLoginRejectedError, match="status 400"):
            await client.login("user@example.com", "pw")

    async def test_in_body_error_with_non_string_message(self):
        client = GoveeAuthClient(session=_session(post=[_response(200, {"status": 400, "message": 12})]))

        with pytest.raises(GoveeLoginRejectedError):
            await client.login("user@example.com", "pw")

    @pytest.mark.parametrize("status", [200, 503])
    async def test_non_dict_body(self, status):
        client = GoveeAuthClient(session=_session(post=[_response(status, ["unexpected"])]))

        with pytest.raises(GoveeLoginRejectedError):
            await client.login("user@example.com", "pw")

    async def test_null_client_block_is_a_missing_token(self):
        body = {"status": 200, "message": "success", "client": None}
        client = GoveeAuthClient(session=_session(post=[_response(200, body)]))

        with pytest.raises(GoveeApiError, match="No token"):
            await client.login("user@example.com", "pw")

    async def test_http_401_with_non_json_body_is_bad_credentials(self):
        client = GoveeAuthClient(session=_session(post=[_non_json(401)]))

        with pytest.raises(GoveeAuthError):
            await client.login("user@example.com", "pw")

    async def test_iot_key_non_dict_error_body(self):
        client = GoveeAuthClient(session=_session(get=[_response(500, ["oops"])]))

        with pytest.raises(GoveeApiError, match="HTTP 500"):
            await client.get_iot_key("tok", client_id="cid")

    async def test_iot_key_401_inside_login_is_a_rejection_not_bad_credentials(self):
        """The password was just accepted, so a 401 on the fresh token is not 'wrong password'."""
        session = _session(post=[_response(200, _login_body())], get=[_response(401, {"message": "denied"})])
        client = GoveeAuthClient(session=session)

        with pytest.raises(GoveeLoginRejectedError, match="IoT key rejected") as exc_info:
            await client.login("user@example.com", "pw")

        assert not isinstance(exc_info.value, GoveeAuthError)


class TestConnectionErrorClassification:
    def test_only_network_failures_count(self):
        wrapped = GoveeApiError("Connection error during login: dns")
        wrapped.__cause__ = aiohttp.ClientConnectionError("dns")
        timed_out = GoveeApiError("Connection error during login: TimeoutError")
        timed_out.__cause__ = TimeoutError()

        assert is_connection_error(wrapped)
        assert is_connection_error(timed_out)
        assert is_connection_error(TimeoutError())
        assert not is_connection_error(GoveeApiError("No token in login response"))
        assert not is_connection_error(RuntimeError("boom"))


class TestVerificationCodeRequest:
    """HTTP 200 carrying an in-body error must not read as 'code sent' (CLOUD-9)."""

    @pytest.mark.parametrize("status", [429, "429"])
    async def test_in_body_error_raises(self, status):
        body = {"status": status, "message": "Too many requests"}
        client = GoveeAuthClient(session=_session(post=[_response(200, body)]))

        with pytest.raises(GoveeLoginRejectedError, match="Too many requests"):
            await client.request_verification_code("user@example.com", "cid")

    async def test_string_success_status_passes(self):
        client = GoveeAuthClient(session=_session(post=[_response(200, {"status": "200"})]))

        await client.request_verification_code("user@example.com", "cid")

    @pytest.mark.parametrize("body", [{"status": 200, "message": "success"}, {}])
    async def test_success_body_passes(self, body):
        client = GoveeAuthClient(session=_session(post=[_response(200, body)]))

        await client.request_verification_code("user@example.com", "cid")

    async def test_non_json_200_still_counts_as_sent(self):
        client = GoveeAuthClient(session=_session(post=[_non_json(200)]))

        await client.request_verification_code("user@example.com", "cid")
