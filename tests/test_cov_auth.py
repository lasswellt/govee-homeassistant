"""Coverage tests for the Govee account/BFF client (``api/auth.py``).

Complements ``test_auth.py``: the helpers that keep secrets and identities out
of logs and diagnostics, PKCS#12 extraction against real containers built with
``cryptography``, every failure branch of the account and BFF fetches, and
session ownership. Everything runs offline over fake aiohttp sessions.
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
import datetime
from functools import lru_cache
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, NoEncryption, pkcs12
from cryptography.x509.oid import NameOID
import pytest

import custom_components.govee.api.auth as auth_mod
from custom_components.govee.api.auth import (
    _BFF_HUMIDITY_KEYS,
    _BFF_TEMP_KEYS,
    GOVEE_LEAK_WARN_URL,
    GoveeAuthClient,
    _bff_reading,
    _extract_p12_credentials,
    _raise_for_bff_status,
    _redact_bff_values,
    _safe_int,
    _sanitize_response_for_logging,
    _shape_skeleton,
    validate_govee_credentials,
)
from custom_components.govee.api.exceptions import (
    Govee2FACodeInvalidError,
    Govee2FARequiredError,
    GoveeApiError,
    GoveeAuthError,
    GoveeLoginRejectedError,
)

LOGGER_NAME = "custom_components.govee.api.auth"
CERT_PEM = "-----BEGIN CERTIFICATE-----\nMIItest\n-----END CERTIFICATE-----\n"
KEY_PEM = "-----BEGIN PRIVATE KEY-----\nMIItest\n-----END PRIVATE KEY-----\n"


# ==============================================================================
# Fakes and factories
# ==============================================================================


@asynccontextmanager
async def _entering(item: Any):
    """Yield ``item`` — or raise it on entry, the way aiohttp raises ``ClientError``."""
    if isinstance(item, BaseException):
        raise item
    yield item


def _queued(items: Any) -> MagicMock:
    """A ``get``/``post`` stand-in handing out ``items`` in order (the last one repeats)."""
    pending = list(items)

    def _call(*_args: Any, **_kwargs: Any):
        item = pending.pop(0) if len(pending) > 1 else pending[0]
        return _entering(item)

    return MagicMock(side_effect=_call)


def _response(status: int = 200, body: Any = None) -> MagicMock:
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value={} if body is None else body)
    return response


def _session(*, get: Any = (), post: Any = ()) -> MagicMock:
    """A fake session; ``get``/``post`` are sequences of responses or exceptions."""
    session = MagicMock(spec=aiohttp.ClientSession)
    session.close = AsyncMock()
    if get:
        session.get = _queued(get)
    if post:
        session.post = _queued(post)
    return session


def _login_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "status": 200,
        "message": "success",
        "client": {"token": "tok-1", "refreshToken": "ref-1", "topic": "GA/acct", "accountId": 4242},
    }
    body.update(overrides)
    return body


def _bff_list(*devices: dict[str, Any]) -> dict[str, Any]:
    return {"data": {"devices": list(devices)}}


@lru_cache(maxsize=1)
def _key_and_cert() -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    """One self-signed EC key/cert pair for the whole module."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "govee-test")])
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(start)
        .not_valid_after(start + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _p12_b64(*, key: bool = True, cert: bool = True, password: str | None = None) -> str:
    """A real base64-encoded PKCS#12 container, optionally missing the key or cert."""
    private_key, certificate = _key_and_cert()
    encryption = BestAvailableEncryption(password.encode()) if password else NoEncryption()
    blob = pkcs12.serialize_key_and_certificates(
        b"govee",
        private_key if key else None,
        certificate if cert else None,
        None,
        encryption,
    )
    return base64.b64encode(blob).decode()


# ==============================================================================
# Module helpers
# ==============================================================================


class TestSafeInt:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(None, None), (True, None), (False, None), (7, 7), ("12", 12), (3.9, 3), ("abc", None), ("", None)],
    )
    def test_coerces_or_returns_none(self, value, expected):
        assert _safe_int(value) == expected

    def test_uncoercible_objects_return_none(self):
        assert _safe_int(object()) is None


class TestSanitizeResponseForLogging:
    def test_non_dict_values_pass_through_untouched(self):
        assert _sanitize_response_for_logging(["token"]) == ["token"]
        assert _sanitize_response_for_logging("token=abc") == "token=abc"
        assert _sanitize_response_for_logging(None) is None

    def test_secrets_are_redacted_at_every_depth_without_mutating_the_input(self):
        data = {
            "status": 200,
            "token": "top-secret",
            "client": {"token": "nested-secret", "accountId": 7, "p12Pass": "pw"},
        }

        out = _sanitize_response_for_logging(data)

        assert out == {
            "status": 200,
            "token": "[REDACTED]",
            "client": {"token": "[REDACTED]", "accountId": 7, "p12Pass": "[REDACTED]"},
        }
        assert data["token"] == "top-secret"

    def test_long_strings_are_truncated_and_short_ones_kept(self):
        out = _sanitize_response_for_logging({"blob": "A" * 150, "message": "ok"})

        assert out["blob"] == f"{'A' * 50}...[truncated, 150 chars]"
        assert out["message"] == "ok"


class TestShapeSkeleton:
    def test_scalars_become_type_names(self):
        assert _shape_skeleton({"a": 1, "b": 2.5, "c": True, "d": None, "e": "x"}) == {
            "a": "int",
            "b": "float",
            "c": "bool",
            "d": "null",
            "e": "str",
        }

    def test_lists_report_length_and_first_element_shape(self):
        assert _shape_skeleton([{"sku": "H5058"}, {"sku": "H5054"}]) == ["list[2]", {"sku": "str"}]
        assert _shape_skeleton([]) == ["list[0]"]

    def test_mac_shaped_keys_are_dropped(self):
        out = _shape_skeleton({"AA:BB:CC:DD:EE:FF:00:11": {"tem": 1}, "AABBCCDDEEFF": 1, "devices": []})

        assert out == {"devices": ["list[0]"]}

    def test_json_encoded_strings_are_expanded(self):
        out = _shape_skeleton({"deviceExt": json.dumps({"deviceSettings": {"sno": 1}})})

        assert out == {"deviceExt": {"_json_str": {"deviceSettings": {"sno": "int"}}}}

    def test_strings_that_only_look_like_json_stay_strings(self):
        assert _shape_skeleton("{not json") == "str"
        assert _shape_skeleton("[1, 2") == "str"

    def test_recursion_is_capped(self):
        obj: Any = "leaf"
        for _ in range(14):
            obj = {"k": obj}

        out = _shape_skeleton(obj)

        for _ in range(13):
            out = out["k"]
        assert out == "..."


class TestRedactBffValues:
    def test_readings_survive_and_identity_keys_are_masked(self):
        raw = {
            "battery": 90,
            "tem": 2150,
            "online": True,
            "signal": None,
            "rssi": -60,
            "wifiName": "home",
            "bleMac": "x",
            "deviceName": "Lamp",
            "topic": "GD/abc",
        }

        assert _redact_bff_values(raw) == {
            "battery": 90,
            "tem": 2150,
            "online": True,
            "signal": None,
            "rssi": -60,
            "wifiName": "[REDACTED]",
            "bleMac": "[REDACTED]",
            "deviceName": "[REDACTED]",
            "topic": "[REDACTED]",
        }

    def test_non_string_and_mac_shaped_keys_are_dropped(self):
        assert _redact_bff_values({1: "one", "AA:BB:CC:DD:EE:FF": {"tem": 1}, "hum": 40}) == {"hum": 40}

    def test_mac_and_ip_shaped_values_are_masked(self):
        out = _redact_bff_values({"gwip": "192.168.1.20", "hw": "AA:BB:CC:DD", "ver": "1.02.03"})

        assert out == {"gwip": "[REDACTED]", "hw": "[REDACTED]", "ver": "1.02.03"}

    def test_long_strings_are_truncated(self):
        assert _redact_bff_values({"cert": "x" * 41}) == {"cert": "[truncated, 41 chars]"}
        assert _redact_bff_values({"note": "x" * 40}) == {"note": "x" * 40}

    def test_json_encoded_strings_are_parsed_and_redacted(self):
        out = _redact_bff_values({"lastDeviceData": json.dumps({"battery": 80, "wifiSsid": "home"})})

        assert out == {"lastDeviceData": {"battery": 80, "wifiSsid": "[REDACTED]"}}

    def test_string_that_only_looks_like_json_is_kept_as_text(self):
        assert _redact_bff_values("[not json") == "[not json"

    def test_lists_are_capped_at_twenty_entries(self):
        assert _redact_bff_values(list(range(30))) == list(range(20))

    def test_recursion_is_capped(self):
        obj: Any = 1
        for _ in range(10):
            obj = {"k": obj}

        out = _redact_bff_values(obj)

        for _ in range(9):
            out = out["k"]
        assert out == "..."


class TestBffReadingSentinels:
    @pytest.mark.parametrize("sentinel", [65535, 32767, -1])
    def test_centi_sentinels_mean_no_reading(self, sentinel):
        assert _bff_reading({"hum": sentinel}, _BFF_HUMIDITY_KEYS) is None
        assert _bff_reading({"tem": sentinel}, _BFF_TEMP_KEYS) is None

    def test_sentinel_on_the_centi_key_falls_through_to_a_plain_key(self):
        assert _bff_reading({"tem": -1, "temperature": 21.5}, _BFF_TEMP_KEYS) == 21.5


class TestRaiseForBffStatus:
    @pytest.mark.parametrize(
        "data",
        [None, [], "status 401", 42, {"status": None}, {"status": 200, "message": "ok"}, {"data": {}}],
    )
    def test_non_error_bodies_are_accepted(self, data):
        assert _raise_for_bff_status(data, "device list") is None

    def test_body_401_is_an_auth_error_naming_the_call(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            with pytest.raises(GoveeAuthError) as exc_info:
                _raise_for_bff_status({"status": 401, "message": "token expired"}, "leak-sensor list")

        assert exc_info.value.code == 401
        assert "leak-sensor list" in str(exc_info.value)
        assert "token expired" in str(exc_info.value)
        assert any("in-body error: status=401" in r.getMessage() for r in caplog.records)

    def test_other_statuses_are_api_errors_carrying_the_status(self):
        with pytest.raises(GoveeApiError) as exc_info:
            _raise_for_bff_status({"status": 503}, "device topics")

        assert type(exc_info.value) is GoveeApiError
        assert exc_info.value.code == 503
        assert "status 503" in str(exc_info.value)


# ==============================================================================
# PKCS#12 extraction
# ==============================================================================


class TestExtractP12Credentials:
    def test_empty_input_is_rejected(self):
        with pytest.raises(GoveeApiError, match="Empty P12"):
            _extract_p12_credentials("")

    def test_unrecoverable_base64_is_reported_as_a_decode_failure(self):
        # A single character can never be valid base64, even after padding is repaired.
        with pytest.raises(GoveeApiError, match="Base64 decode failed"):
            _extract_p12_credentials("A")

    def test_bytes_that_are_not_a_container_are_reported(self):
        garbage = base64.b64encode(b"definitely not a pkcs12 container").decode()

        with pytest.raises(GoveeApiError, match="P12 container parse failed"):
            _extract_p12_credentials(garbage)

    def test_wrong_password_is_a_parse_failure(self):
        with pytest.raises(GoveeApiError, match="P12 container parse failed"):
            _extract_p12_credentials(_p12_b64(password="right"), "wrong")

    def test_container_without_a_private_key_is_rejected(self):
        with pytest.raises(GoveeApiError, match="No private key"):
            _extract_p12_credentials(_p12_b64(key=False))

    def test_container_without_a_certificate_is_rejected(self):
        with pytest.raises(GoveeApiError, match="No certificate"):
            _extract_p12_credentials(_p12_b64(cert=False))

    def test_unexpected_failures_are_wrapped_not_leaked(self):
        # A password that cannot be encoded blows up outside the inner guards;
        # the catch-all must still surface it as GoveeApiError, never raw.
        with pytest.raises(GoveeApiError, match="Failed to parse P12 certificate"):
            _extract_p12_credentials(_p12_b64(), password=1234)

    def test_extracts_the_pem_pair_from_a_real_container(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            cert_pem, key_pem = _extract_p12_credentials(_p12_b64(password="s3cret"), "s3cret")

        assert cert_pem.startswith("-----BEGIN CERTIFICATE-----")
        assert key_pem.startswith("-----BEGIN PRIVATE KEY-----")
        _key, cert = _key_and_cert()
        assert x509.load_pem_x509_certificate(cert_pem.encode()) == cert
        assert any("Successfully extracted" in r.getMessage() for r in caplog.records)

    def test_accepts_url_safe_and_whitespace_wrapped_base64(self):
        expected = _extract_p12_credentials(_p12_b64())
        mangled = _p12_b64().replace("+", "-").replace("/", "_").rstrip("=")
        wrapped = "\n".join(mangled[i : i + 64] for i in range(0, len(mangled), 64)) + " \r\n"

        assert _extract_p12_credentials(wrapped) == expected


# ==============================================================================
# Session ownership
# ==============================================================================


class TestSessionOwnership:
    def test_without_session_or_hass_the_client_refuses_to_run(self):
        with pytest.raises(RuntimeError, match="hass=hass"):
            GoveeAuthClient()._require_session()

    async def test_entering_the_context_manager_needs_a_session(self):
        with pytest.raises(RuntimeError, match="hass=hass"):
            async with GoveeAuthClient():
                pass

    async def test_requests_fail_fast_without_a_session(self):
        with pytest.raises(RuntimeError, match="hass=hass"):
            await GoveeAuthClient().get_iot_key("tok")

    async def test_hass_session_is_borrowed(self, monkeypatch):
        session = _session()
        hass = MagicMock()
        getter = MagicMock(return_value=session)
        monkeypatch.setattr(auth_mod, "async_get_clientsession", getter)

        client = GoveeAuthClient(hass=hass)

        getter.assert_called_once_with(hass)
        assert client._require_session() is session
        await client.close()
        session.close.assert_not_awaited()
        assert client._session is session

    def test_explicit_session_wins_over_hass(self, monkeypatch):
        getter = MagicMock()
        monkeypatch.setattr(auth_mod, "async_get_clientsession", getter)
        session = _session()

        client = GoveeAuthClient(session=session, hass=MagicMock())

        getter.assert_not_called()
        assert client._require_session() is session
        assert client._owns_session is False

    async def test_owned_session_is_closed_once(self):
        session = _session()
        client = GoveeAuthClient()
        client._session = session  # an owned session only exists when attached after construction

        async with client:
            pass

        session.close.assert_awaited_once()
        assert client._session is None
        await client.close()  # idempotent
        session.close.assert_awaited_once()

    async def test_borrowed_session_survives_the_context_manager(self):
        session = _session()

        async with GoveeAuthClient(session=session) as client:
            assert client._require_session() is session

        session.close.assert_not_awaited()
        assert client._session is session


# ==============================================================================
# get_iot_key
# ==============================================================================


class TestGetIotKey:
    async def test_failure_is_logged_without_leaking_secrets(self, caplog):
        body = {"status": 401, "message": "token expired", "token": "SECRET-TOKEN-XYZ"}
        client = GoveeAuthClient(session=_session(get=[_response(401, body)]))

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            with pytest.raises(GoveeApiError) as exc_info:
                await client.get_iot_key("tok")

        assert exc_info.value.code == 401
        assert "token expired" in str(exc_info.value)
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "[REDACTED]" in logged
        assert "SECRET-TOKEN-XYZ" not in logged

    async def test_non_dict_success_body_yields_no_credentials(self):
        client = GoveeAuthClient(session=_session(get=[_response(200, ["unexpected"])]))

        assert await client.get_iot_key("tok") == {}

    async def test_connection_error_is_wrapped_and_logged(self, caplog):
        client = GoveeAuthClient(session=_session(get=[aiohttp.ClientConnectionError("reset")]))

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            with pytest.raises(GoveeApiError, match="Connection error getting IoT key") as exc_info:
                await client.get_iot_key("tok")

        assert isinstance(exc_info.value.__cause__, aiohttp.ClientConnectionError)
        assert any("ClientConnectionError" in r.getMessage() for r in caplog.records)


# ==============================================================================
# Device-list parsing and topic fetches
# ==============================================================================


def _topic_entry(device_id: str, topic: str, gateway: dict[str, str] | None = None) -> dict[str, Any]:
    settings: dict[str, Any] = {"topic": topic}
    if gateway:
        settings["gatewayInfo"] = gateway
    return {"device": device_id, "sku": "H5901", "deviceExt": {"deviceSettings": settings}}


class TestTopicExtraction:
    """The static device-list parsers tolerate Govee's JSON-as-text nesting."""

    def test_topics_skip_entries_whose_json_text_is_broken(self):
        devices = [
            {"device": "D1", "deviceExt": "{broken"},
            {"device": "D2", "deviceExt": {"deviceSettings": "{broken"}},
            {"device": "D3", "deviceExt": {"deviceSettings": "[1, 2]"}},
            {"device": "D4", "deviceExt": {"deviceSettings": 5}},
            {"device": "D5", "deviceExt": {"deviceSettings": json.dumps({"topic": "GD/d5"})}},
            {"device": "", "deviceExt": {"deviceSettings": {"topic": "GD/anon"}}},
        ]

        assert GoveeAuthClient._extract_topics_from_devices(devices) == {"D5": "GD/d5"}

    def test_gateway_routes_skip_non_object_ext_and_settings(self):
        devices = [
            {"device": "D1", "deviceExt": "[1]"},
            {"device": "D2", "deviceExt": 7},
            {"device": "D3", "deviceExt": {"deviceSettings": "[1]"}},
            {"device": "D4", "deviceExt": {"deviceSettings": 3}},
            {"device": "D5", "deviceExt": {"deviceSettings": {"gatewayInfo": {"device": "GW", "topic": "GD/gw"}}}},
        ]

        assert GoveeAuthClient._extract_gateway_routes(devices) == {
            "D5": {"device": "GW", "sku": "", "topic": "GD/gw"},
        }


class TestFetchDeviceTopics:
    def test_no_routes_before_any_fetch(self):
        assert GoveeAuthClient(session=_session()).gateway_routes() == {}

    async def test_bff_http_failure_is_non_fatal(self, caplog):
        legacy = _response(200, {"status": 200, "devices": [_topic_entry("D1", "GD/d1")]})
        bff = _response(500, {"message": "upstream down"})
        client = GoveeAuthClient(session=_session(post=[legacy], get=[bff]))

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            topics = await client.fetch_device_topics("tok")

        assert topics == {"D1": "GD/d1"}
        failures = [r.getMessage() for r in caplog.records if "BFF topic fetch failed (non-fatal)" in r.getMessage()]
        assert len(failures) == 1
        assert "upstream down" in failures[0]
        assert client.gateway_routes() == {}

    async def test_bff_connection_error_is_non_fatal(self):
        legacy = _response(200, {"status": 200, "devices": [_topic_entry("D1", "GD/d1")]})
        client = GoveeAuthClient(session=_session(post=[legacy], get=[aiohttp.ClientOSError("eek")]))

        assert await client.fetch_device_topics("tok") == {"D1": "GD/d1"}

    async def test_bff_supplies_missing_topics_and_gateway_routes(self):
        gateway = {"device": "GW:1", "sku": "H5044", "topic": "GD/gw"}
        legacy = _response(200, {"status": 200, "devices": [_topic_entry("D1", "GD/d1")]})
        bff = _response(200, _bff_list(_topic_entry("D1", "GD/other"), _topic_entry("D2", "GD/d2", gateway)))
        client = GoveeAuthClient(session=_session(post=[legacy], get=[bff]))

        topics = await client.fetch_device_topics("tok")

        assert topics == {"D1": "GD/d1", "D2": "GD/d2"}  # legacy wins, BFF adds
        routes = client.gateway_routes()
        assert routes == {"D2": {"device": "GW:1", "sku": "H5044", "topic": "GD/gw"}}
        routes.clear()
        assert "D2" in client.gateway_routes()

    async def test_legacy_in_body_401_raises_before_bff_is_consulted(self):
        session = _session(post=[_response(200, {"status": 401, "message": "token invalid"})])
        client = GoveeAuthClient(session=session)

        with pytest.raises(GoveeAuthError):
            await client.fetch_device_topics("tok")

        assert session.get.call_count == 0

    async def test_legacy_http_failure_raises(self):
        client = GoveeAuthClient(session=_session(post=[_response(502, {"message": "bad gateway"})]))

        with pytest.raises(GoveeApiError) as exc_info:
            await client.fetch_device_topics("tok")

        assert exc_info.value.code == 502
        assert "bad gateway" in str(exc_info.value)

    async def test_bff_topics_http_failure_carries_status_and_message(self):
        client = GoveeAuthClient(session=_session(get=[_response(403, {"message": "forbidden"})]))

        with pytest.raises(GoveeApiError) as exc_info:
            await client._fetch_bff_device_topics("tok")

        assert exc_info.value.code == 403
        assert "forbidden" in str(exc_info.value)

    async def test_bff_topics_in_body_401_is_an_auth_error(self):
        client = GoveeAuthClient(session=_session(get=[_response(200, {"status": 401, "message": "expired"})]))

        with pytest.raises(GoveeAuthError):
            await client._fetch_bff_device_topics("tok")


# ==============================================================================
# BFF thermo-hygrometer list
# ==============================================================================


def _thermo(device_id: str, ext: Any, sku: str = "H5301") -> dict[str, Any]:
    return {"sku": sku, "device": device_id, "deviceName": "Pool", "deviceExt": ext}


class TestFetchBffThermoHygrometers:
    async def test_http_401_is_an_auth_error(self):
        client = GoveeAuthClient(session=_session(get=[_response(401, {"message": "nope"})]))

        with pytest.raises(GoveeAuthError, match="nope"):
            await client.fetch_bff_thermo_hygrometers("tok")

    async def test_other_http_failures_carry_the_status(self):
        client = GoveeAuthClient(session=_session(get=[_response(503, {})]))

        with pytest.raises(GoveeApiError) as exc_info:
            await client.fetch_bff_thermo_hygrometers("tok")

        assert type(exc_info.value) is GoveeApiError
        assert exc_info.value.code == 503
        assert "HTTP 503" in str(exc_info.value)

    async def test_connection_error_is_wrapped(self):
        client = GoveeAuthClient(session=_session(get=[aiohttp.ClientConnectionError("reset")]))

        with pytest.raises(GoveeApiError, match="Connection error fetching BFF device list"):
            await client.fetch_bff_thermo_hygrometers("tok")

    async def test_broken_json_text_degrades_to_defaults_not_errors(self):
        devices = [
            _thermo("T1", "{broken"),
            _thermo("T2", {"deviceSettings": "{broken", "lastDeviceData": {"tem": 2350, "hum": 4800}}),
            _thermo("T3", {"deviceSettings": {"battery": 80, "sno": 2}, "lastDeviceData": "{broken"}),
        ]
        client = GoveeAuthClient(session=_session(get=[_response(200, _bff_list(*devices))]))

        sensors = {s["device_id"]: s for s in await client.fetch_bff_thermo_hygrometers("tok")}

        assert set(sensors) == {"T1", "T2", "T3"}
        assert sensors["T1"]["temperature"] is None
        assert sensors["T1"]["battery"] is None
        assert sensors["T1"]["online"] is True
        assert sensors["T2"]["temperature"] == 23.5
        assert sensors["T2"]["humidity"] == 48.0
        assert sensors["T2"]["sw_version"] == ""
        assert sensors["T3"]["battery"] == 80
        assert sensors["T3"]["sno"] == 2
        assert sensors["T3"]["temperature"] is None


# ==============================================================================
# BFF leak-sensor list
# ==============================================================================


def _leak(device_id: str, ext: Any, sku: str = "H5058") -> dict[str, Any]:
    return {"sku": sku, "device": device_id, "deviceName": "Kitchen Sink", "deviceExt": ext}


class TestFetchBffLeakSensors:
    async def test_http_401_is_an_auth_error(self):
        client = GoveeAuthClient(session=_session(get=[_response(401, {"message": "nope"})]))

        with pytest.raises(GoveeAuthError, match="nope"):
            await client.fetch_bff_leak_sensors("tok")

    async def test_other_http_failures_carry_the_status(self):
        client = GoveeAuthClient(session=_session(get=[_response(500, {"message": "meh"})]))

        with pytest.raises(GoveeApiError) as exc_info:
            await client.fetch_bff_leak_sensors("tok")

        assert exc_info.value.code == 500
        assert "meh" in str(exc_info.value)

    async def test_connection_error_is_wrapped(self):
        client = GoveeAuthClient(session=_session(get=[aiohttp.ClientConnectionError("reset")]))

        with pytest.raises(GoveeApiError, match="Connection error fetching BFF device list"):
            await client.fetch_bff_leak_sensors("tok")

    async def test_broken_json_text_skips_that_sensor_and_keeps_the_rest(self, caplog):
        good = {"sno": 1, "battery": 90, "gatewayInfo": {"device": "GW:1", "sku": "H5044"}}
        devices = [
            _leak("L1", "{broken"),  # deviceExt unreadable -> no sno -> skipped
            _leak("L2", {"deviceSettings": "{broken"}),  # settings unreadable -> skipped
            _leak(
                "L3",
                {
                    "deviceSettings": json.dumps(good),
                    "lastDeviceData": json.dumps({"online": False, "lastTime": 5, "read": False}),
                },
            ),
            _leak("L4", {"deviceSettings": dict(good, sno=2), "lastDeviceData": "{broken"}),
        ]
        client = GoveeAuthClient(session=_session(get=[_response(200, _bff_list(*devices))]))

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            sensors, hubs, thermo = await client.fetch_bff_leak_sensors("tok")

        by_id = {s["device_id"]: s for s in sensors}
        assert set(by_id) == {"L3", "L4"}
        assert by_id["L3"]["sno"] == 1
        assert by_id["L3"]["hub_device_id"] == "GW:1"
        assert by_id["L3"]["battery"] == 90
        assert by_id["L3"]["online"] is False
        assert by_id["L3"]["last_wet_time"] == 5
        assert by_id["L3"]["read"] is False
        assert by_id["L4"]["sno"] == 2
        assert by_id["L4"]["online"] is True
        assert by_id["L4"]["last_wet_time"] is None
        assert len([r for r in caplog.records if "has no sno, skipping" in r.getMessage()]) == 2
        assert hubs == {}
        assert thermo == {}

    async def test_hubs_need_a_device_id(self):
        devices = [
            {"sku": "H5044", "deviceName": "Anonymous hub"},
            {"sku": "H5044", "device": "GW:1", "deviceName": "Kitchen hub"},
        ]
        client = GoveeAuthClient(session=_session(get=[_response(200, _bff_list(*devices))]))

        _sensors, hubs, _thermo = await client.fetch_bff_leak_sensors("tok")

        assert hubs == {"GW:1": {"sku": "H5044", "name": "Kitchen hub"}}

    async def test_thermometer_readings_survive_broken_json_text_per_field(self):
        devices = [
            {"sku": "H5075", "deviceExt": {"lastDeviceData": {"tem": 2100}}},  # no id -> skipped
            {"sku": "H5075", "device": "X1", "deviceExt": "{broken"},
            {"sku": "H5110", "device": "X2", "deviceExt": {"lastDeviceData": "{broken", "deviceSettings": "{broken"}},
            {
                "sku": "H5110",
                "device": "X3",
                "deviceExt": {"lastDeviceData": "{broken", "deviceSettings": {"battery": 55}},
            },
            {
                "sku": "H5179",
                "device": "X4",
                "deviceExt": {"lastDeviceData": {"tem": 2100, "hum": 4500}, "deviceSettings": "{broken"},
            },
        ]
        client = GoveeAuthClient(session=_session(get=[_response(200, _bff_list(*devices))]))

        _sensors, _hubs, thermo = await client.fetch_bff_leak_sensors("tok")

        assert thermo == {
            "X3": {"tem": None, "hum": None, "battery": 55, "water_full": None},
            "X4": {"tem": 2100, "hum": 4500, "battery": None, "water_full": None},
        }


# ==============================================================================
# Standalone water detectors (H5054 via H5040)
# ==============================================================================


class TestFetchWaterDetectorStates:
    async def test_http_401_is_an_auth_error(self):
        client = GoveeAuthClient(session=_session(get=[_response(401, {})]))

        with pytest.raises(GoveeAuthError, match="401"):
            await client.fetch_water_detector_states("tok", {"AA:BB"})

    async def test_other_http_failures_carry_the_status(self):
        client = GoveeAuthClient(session=_session(get=[_response(500, {"message": "meh"})]))

        with pytest.raises(GoveeApiError) as exc_info:
            await client.fetch_water_detector_states("tok", {"AA:BB"})

        assert exc_info.value.code == 500
        assert "meh" in str(exc_info.value)

    async def test_connection_error_is_wrapped(self):
        client = GoveeAuthClient(session=_session(get=[aiohttp.ClientConnectionError("reset")]))

        with pytest.raises(GoveeApiError, match="Connection error fetching water-detector states"):
            await client.fetch_water_detector_states("tok", {"AA:BB"})

    async def test_json_text_fields_are_parsed_and_broken_ones_default(self):
        devices = [
            {"device": "AABBCCDD", "sku": "H5054", "deviceExt": "{broken"},
            {
                "device": "11223344",
                "sku": "H5054",
                "deviceExt": {
                    "deviceSettings": json.dumps({"battery": "77"}),
                    "lastDeviceData": json.dumps({"online": 0, "gwonline": 1, "lastTime": "1700000000000"}),
                },
            },
            {
                "device": "55667788",
                "sku": "H5054",
                "deviceExt": {"deviceSettings": "{broken", "lastDeviceData": "[broken"},
            },
            {
                "device": "99999999",
                "sku": "H5054",
                "deviceExt": {"lastDeviceData": {"online": False}},
            },  # not asked for
        ]
        client = GoveeAuthClient(session=_session(get=[_response(200, _bff_list(*devices))]))

        result = await client.fetch_water_detector_states("tok", {"AA:BB:CC:DD", "11:22:33:44", "55:66:77:88"})

        assert result == {
            "AA:BB:CC:DD": {"online": True, "gateway_online": True, "battery": None, "last_time": None},
            "11:22:33:44": {"online": False, "gateway_online": True, "battery": 77, "last_time": 1700000000000},
            "55:66:77:88": {"online": True, "gateway_online": True, "battery": None, "last_time": None},
        }


class TestFetchLeakWarning:
    async def test_request_targets_the_colon_stripped_device(self):
        session = _session(post=[_response(200, {"data": []})])
        client = GoveeAuthClient(session=session)

        assert await client.fetch_leak_warning("tok", "AA:BB:CC:DD", "H5054") is False

        call = session.post.call_args
        assert call.args[0] == GOVEE_LEAK_WARN_URL
        assert call.kwargs["json"] == {"device": "AABBCCDD", "limit": 50, "sku": "H5054"}
        assert call.kwargs["headers"]["Authorization"] == "Bearer tok"
        assert "clientId" not in call.kwargs["headers"]

    async def test_http_401_is_an_auth_error(self):
        client = GoveeAuthClient(session=_session(post=[_response(401, {})]))

        with pytest.raises(GoveeAuthError, match="warnMessage auth failed"):
            await client.fetch_leak_warning("tok", "AABB", "H5054")

    async def test_other_http_failures_carry_the_status(self):
        client = GoveeAuthClient(session=_session(post=[_response(500, {"message": "meh"})]))

        with pytest.raises(GoveeApiError, match="warnMessage failed: meh") as exc_info:
            await client.fetch_leak_warning("tok", "AABB", "H5054")

        assert exc_info.value.code == 500

    async def test_connection_error_is_wrapped(self):
        client = GoveeAuthClient(session=_session(post=[aiohttp.ClientConnectionError("reset")]))

        with pytest.raises(GoveeApiError, match="Connection error fetching leak warning"):
            await client.fetch_leak_warning("tok", "AABB", "H5054")

    async def test_a_bare_timeout_is_wrapped_too(self):
        client = GoveeAuthClient(session=_session(post=[TimeoutError()]))

        with pytest.raises(GoveeApiError, match="Connection error fetching leak warning: TimeoutError"):
            await client.fetch_leak_warning("tok", "AABB", "H5054")

    @pytest.mark.parametrize("body", [{"data": {"unexpected": True}}, {"data": None}, {}])
    async def test_a_history_that_is_not_a_list_reads_as_dry(self, body):
        client = GoveeAuthClient(session=_session(post=[_response(200, body)]))

        assert await client.fetch_leak_warning("tok", "AABB", "H5054") is False

    async def test_only_unread_leakage_alerts_count(self):
        messages = [
            "not a dict",
            {"message": "Leakage Alert", "read": True},
            {"message": "Low battery", "read": False},
            {"message": " leakage\nalert: kitchen", "read": False},
        ]
        client = GoveeAuthClient(session=_session(post=[_response(200, {"data": messages})]))

        assert await client.fetch_leak_warning("tok", "AABB", "H5054") is True


# ==============================================================================
# Diagnostics views over the last BFF fetch (#87, #114)
# ==============================================================================


class TestDiagnosticsViews:
    @staticmethod
    async def _fetched(*devices: dict[str, Any]) -> GoveeAuthClient:
        client = GoveeAuthClient(session=_session(get=[_response(200, _bff_list(*devices))]))
        await client.fetch_bff_leak_sensors("tok")
        return client

    async def test_census_tolerates_broken_json_text_and_carries_no_identity(self):
        client = await self._fetched(
            {
                "sku": "H5058",
                "device": "AA:11:22:33:44:55:66:77",
                "deviceName": "Kitchen Sink",
                "deviceExt": "{broken",
            },
            {
                "sku": "H5059",
                "device": "AA:11:22:33:44:55:66:88",
                "deviceExt": {
                    "deviceSettings": json.dumps({"sno": 3, "gatewayInfo": {"sku": "H5044", "device": "GW"}})
                },
            },
            {"sku": "H5054", "device": "AA:11:22:33:44:55:66:99", "deviceExt": {"deviceSettings": "{broken"}},
            {
                "sku": "H7152",
                "device": "AA:11:22:33:44:55:66:AA",
                "deviceExt": {"deviceSettings": {"gatewayInfo": "odd"}},
            },
        )

        census = client.bff_device_census()

        assert [c["sku"] for c in census] == ["H5058", "H5059", "H5054", "H7152"]
        assert census[0] == {
            "sku": "H5058",
            "in_leak_sensor_skus": True,
            "in_leak_hub_skus": False,
            "in_thermo_hygro_skus": False,
            "in_probe_thermometer_skus": False,
            "has_sno": False,
            "sno": None,
            "has_gateway_info": False,
            "gateway_sku": None,
        }
        assert census[1]["has_sno"] is True
        assert census[1]["sno"] == 3
        assert census[1]["gateway_sku"] == "H5044"
        assert census[2]["has_sno"] is False
        assert census[3]["has_gateway_info"] is True
        assert census[3]["gateway_sku"] is None
        dump = json.dumps(census)
        assert "AA:11" not in dump
        assert "Kitchen Sink" not in dump
        assert "GW" not in dump

    def test_census_and_values_skip_entries_that_are_not_objects(self):
        # A malformed entry cannot reach the retained list through the fetch (it
        # would fail on it), but the diagnostics views must never raise on what they hold.
        client = GoveeAuthClient(session=_session())
        client._last_bff_raw_devices = [
            "garbage",
            42,
            {"sku": "H5100", "deviceExt": [1, 2]},
            {"sku": "H5106", "deviceExt": {"lastDeviceData": {"pm25": 12}}},
        ]

        assert [c["sku"] for c in client.bff_device_census()] == ["H5100", "H5106"]
        assert client.bff_device_values() == [{"sku": "H5100"}, {"sku": "H5106", "lastDeviceData": {"pm25": 12}}]

    async def test_values_drop_sections_that_are_not_objects(self):
        client = await self._fetched(
            {"sku": "H5220", "device": "D1", "deviceExt": "{broken"},
            {"sku": "H5106", "device": "D2", "deviceExt": {"deviceSettings": "{broken", "lastDeviceData": "[1, 2]"}},
            {
                "sku": "H7152",
                "device": "D3",
                "deviceExt": {
                    "deviceSettings": {"battery": 90, "wifiName": "home", "ip": "10.0.0.5"},
                    "lastDeviceData": json.dumps({"hum": 5500, "online": True}),
                },
            },
        )

        assert client.bff_device_values() == [
            {"sku": "H5220"},
            {"sku": "H5106"},
            {
                "sku": "H7152",
                "deviceSettings": {"battery": 90, "wifiName": "[REDACTED]", "ip": "[REDACTED]"},
                "lastDeviceData": {"hum": 5500, "online": True},
            },
        ]

    async def test_skeleton_expands_json_text_and_shows_no_values(self):
        client = await self._fetched(
            {
                "sku": "H5058",
                "device": "AA:BB:CC:DD:EE:FF:00:11",
                "deviceExt": json.dumps({"deviceSettings": {"sno": 1}}),
            }
        )

        assert client.bff_response_skeleton() == {
            "data": {
                "devices": [
                    "list[1]",
                    {"sku": "str", "device": "str", "deviceExt": {"_json_str": {"deviceSettings": {"sno": "int"}}}},
                ]
            }
        }


# ==============================================================================
# Verification code and login
# ==============================================================================


class TestRequestVerificationCode:
    async def test_connection_error_is_wrapped(self):
        client = GoveeAuthClient(session=_session(post=[aiohttp.ClientConnectionError("reset")]))

        with pytest.raises(GoveeApiError, match="Connection error requesting verification code") as exc_info:
            await client.request_verification_code("user@example.com", "cid")

        assert isinstance(exc_info.value.__cause__, aiohttp.ClientError)

    async def test_http_failure_names_the_status(self):
        client = GoveeAuthClient(session=_session(post=[_response(429, {})]))

        with pytest.raises(GoveeApiError, match="HTTP 429"):
            await client.request_verification_code("user@example.com", "cid")


def _login_client(login: Any, iot: Any = None) -> tuple[GoveeAuthClient, MagicMock]:
    session = _session(post=[login], get=[iot] if iot is not None else ())
    return GoveeAuthClient(session=session), session


class TestLogin:
    async def test_http_401_is_bad_credentials_logged_without_secrets(self, caplog):
        client, _ = _login_client(_response(401, {"message": "wrong", "token": "SECRET-TOKEN-XYZ"}))

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            with pytest.raises(GoveeAuthError) as exc_info:
                await client.login("user@example.com", "pw")

        assert exc_info.value.code == 401
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "[REDACTED]" in logged
        assert "SECRET-TOKEN-XYZ" not in logged

    async def test_other_http_status_is_a_rejection(self):
        client, _ = _login_client(_response(503, {"message": "maintenance"}))

        with pytest.raises(GoveeLoginRejectedError, match=r"HTTP 503.*maintenance"):
            await client.login("user@example.com", "pw")

    @pytest.mark.parametrize(
        ("code", "expected"),
        [(None, Govee2FARequiredError), ("1234", Govee2FACodeInvalidError)],
    )
    async def test_in_body_454_depends_on_whether_a_code_was_sent(self, code, expected):
        client, session = _login_client(_response(200, {"status": 454, "message": "verify"}))

        with pytest.raises(expected):
            await client.login("user@example.com", "pw", code=code)

        payload = session.post.call_args.kwargs["json"]
        assert ("code" in payload) is (code is not None)

    @pytest.mark.parametrize(
        ("body", "expected_code"),
        [({"status": 401, "message": "Unauthorized"}, 401), ({"status": 400, "message": "Password incorrect"}, 400)],
    )
    async def test_in_body_credential_rejections_are_auth_errors(self, body, expected_code):
        client, _ = _login_client(_response(200, body))

        with pytest.raises(GoveeAuthError) as exc_info:
            await client.login("user@example.com", "pw")

        assert exc_info.value.code == expected_code
        assert str(exc_info.value) == body["message"]

    async def test_other_in_body_status_is_a_rejection_logged_without_secrets(self, caplog):
        body = {"status": 500, "message": "try later", "client": {"token": "SECRET-TOKEN-XYZ"}}
        client, _ = _login_client(_response(200, body))

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            with pytest.raises(GoveeLoginRejectedError, match=r"status 500.*try later"):
                await client.login("user@example.com", "pw")

        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "[REDACTED]" in logged
        assert "SECRET-TOKEN-XYZ" not in logged

    async def test_in_body_error_without_message_still_rejects(self):
        client, _ = _login_client(_response(200, {"status": 500}))

        with pytest.raises(GoveeLoginRejectedError, match="Login failed"):
            await client.login("user@example.com", "pw")

    async def test_missing_token_is_an_api_error(self):
        client, _ = _login_client(_response(200, _login_body(client={"accountId": 1})))

        with pytest.raises(GoveeApiError, match="No token"):
            await client.login("user@example.com", "pw")

    async def test_pem_credentials_are_used_directly(self):
        body = _login_body()
        body["client"]["caCertificate"] = "CA-PEM"
        iot = {"certificatePem": CERT_PEM, "privateKey": KEY_PEM, "endpoint": "iot.example"}
        client, _ = _login_client(_response(200, body), _response(200, {"data": iot}))

        creds = await client.login("user@example.com", "pw", client_id="cid")

        assert creds.iot_cert == CERT_PEM
        assert creds.iot_key == KEY_PEM
        assert creds.iot_ca == "CA-PEM"
        assert creds.endpoint == "iot.example"
        assert creds.client_id == "AP/4242/cid"
        assert creds.account_topic == "GA/acct"
        assert creds.refresh_token == "ref-1"
        assert creds.is_valid

    @pytest.mark.parametrize("password_key", ["p12Pass", "p12_pass"])
    async def test_p12_credentials_are_extracted_to_pem(self, password_key):
        iot = {"p12": _p12_b64(password="pw"), password_key: "pw"}
        client, _ = _login_client(_response(200, _login_body()), _response(200, {"data": iot}))

        creds = await client.login("user@example.com", "pw")

        assert creds.iot_cert.startswith("-----BEGIN CERTIFICATE-----")
        assert creds.iot_key.startswith("-----BEGIN PRIVATE KEY-----")
        assert creds.endpoint == "aqm3wd1qlc3dy-ats.iot.us-east-1.amazonaws.com"
        assert creds.is_valid

    async def test_p12_is_the_fallback_when_only_half_a_pem_pair_arrives(self):
        iot = {"certificatePem": CERT_PEM, "p12": _p12_b64()}  # privateKey missing -> P12 wins
        client, _ = _login_client(_response(200, _login_body()), _response(200, {"data": iot}))

        creds = await client.login("user@example.com", "pw")

        assert creds.iot_cert != CERT_PEM
        assert creds.iot_cert.startswith("-----BEGIN CERTIFICATE-----")

    async def test_missing_certificate_data_is_an_api_error(self):
        client, _ = _login_client(_response(200, _login_body()), _response(200, {"data": {"endpoint": "x"}}))

        with pytest.raises(GoveeApiError, match="No certificate data"):
            await client.login("user@example.com", "pw")

    async def test_unusable_p12_surfaces_as_an_api_error(self):
        garbage = base64.b64encode(b"nope").decode()
        client, _ = _login_client(_response(200, _login_body()), _response(200, {"data": {"p12": garbage}}))

        with pytest.raises(GoveeApiError, match="P12 container parse failed"):
            await client.login("user@example.com", "pw")

    async def test_credentials_without_an_account_topic_are_rejected(self):
        body = _login_body()
        body["client"]["topic"] = ""
        iot = {"certificatePem": CERT_PEM, "privateKey": KEY_PEM}
        client, _ = _login_client(_response(200, body), _response(200, {"data": iot}))

        with pytest.raises(GoveeApiError, match="Missing IoT credentials"):
            await client.login("user@example.com", "pw")

    async def test_iot_key_failure_propagates_from_login(self):
        client, _ = _login_client(_response(200, _login_body()), _response(500, {"message": "iot down"}))

        with pytest.raises(GoveeApiError, match="iot down") as exc_info:
            await client.login("user@example.com", "pw")

        assert exc_info.value.code == 500


class TestValidateGoveeCredentials:
    async def test_uses_the_ha_session_and_leaves_it_open(self, monkeypatch):
        iot = {"certificatePem": CERT_PEM, "privateKey": KEY_PEM}
        session = _session(post=[_response(200, _login_body())], get=[_response(200, {"data": iot})])
        hass = MagicMock()
        getter = MagicMock(return_value=session)
        monkeypatch.setattr(auth_mod, "async_get_clientsession", getter)

        creds = await validate_govee_credentials("user@example.com", "pw", code="1234", client_id="cid", hass=hass)

        getter.assert_called_once_with(hass)
        assert creds.token == "tok-1"
        payload = session.post.call_args.kwargs["json"]
        assert payload["code"] == "1234"
        assert payload["client"] == "cid"
        session.close.assert_not_awaited()

    async def test_without_session_or_hass_fails_fast(self):
        with pytest.raises(RuntimeError, match="hass=hass"):
            await validate_govee_credentials("user@example.com", "pw")
