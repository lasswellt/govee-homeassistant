"""Test Govee API client."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.govee.api.client import GoveeApiClient
from custom_components.govee.api.exceptions import (
    GoveeApiError,
    GoveeAuthError,
    GoveeConnectionError,
    GoveeDeviceNotFoundError,
    GoveeRateLimitError,
)
from custom_components.govee.models import PowerCommand, ToggleCommand

# ==============================================================================
# Exception Tests
# ==============================================================================


class TestExceptions:
    """Test API exceptions."""

    def test_govee_api_error(self):
        """Test base API error."""
        err = GoveeApiError("Test error", code=500)
        assert str(err) == "Test error"
        assert err.code == 500

    def test_govee_api_error_no_code(self):
        """Test API error without code."""
        err = GoveeApiError("Test error")
        assert err.code is None

    def test_govee_auth_error(self):
        """Test auth error."""
        err = GoveeAuthError()
        assert "Invalid API key" in str(err)
        assert err.code == 401

    def test_govee_auth_error_custom_message(self):
        """Test auth error with custom message."""
        err = GoveeAuthError("Token expired")
        assert str(err) == "Token expired"
        assert err.code == 401

    def test_govee_rate_limit_error(self):
        """Test rate limit error."""
        err = GoveeRateLimitError()
        assert "Rate limit" in str(err)
        assert err.code == 429
        assert err.retry_after is None

    def test_govee_rate_limit_error_with_retry(self):
        """Test rate limit error with retry_after."""
        err = GoveeRateLimitError(retry_after=30.0)
        assert err.retry_after == 30.0

    def test_govee_connection_error(self):
        """Test connection error."""
        err = GoveeConnectionError()
        assert "connect" in str(err).lower()
        assert err.code is None

    def test_govee_device_not_found_error(self):
        """Test device not found error."""
        err = GoveeDeviceNotFoundError("devices not exist")
        assert "devices not exist" in str(err)
        assert err.code == 400

    def test_govee_device_not_found_error_default(self):
        """Test device not found error with default message."""
        err = GoveeDeviceNotFoundError()
        assert "Device not found" in str(err)
        assert err.code == 400


# ==============================================================================
# API Client Tests
# ==============================================================================


class TestGoveeApiClient:
    """Test GoveeApiClient."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock aiohttp session."""
        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        return session

    @pytest.fixture
    def client(self, mock_session):
        """Create an API client with mock session."""
        return GoveeApiClient("test_api_key", session=mock_session)

    def test_client_creation(self):
        """Test creating a client."""
        client = GoveeApiClient("test_key")
        assert client._api_key == "test_key"

    def test_get_headers(self):
        """Test request headers."""
        client = GoveeApiClient("test_api_key")
        headers = client._get_headers()
        assert headers["Govee-API-Key"] == "test_api_key"
        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"

    def test_rate_limit_tracking(self):
        """Test rate limit header parsing."""
        client = GoveeApiClient("test_key")
        headers = {
            "X-RateLimit-Remaining": "50",
            "X-RateLimit-Limit": "100",
            "X-RateLimit-Reset": "1699999999",
        }
        client._update_rate_limits(headers)
        assert client.rate_limit_remaining == 50
        assert client.rate_limit_total == 100
        assert client.rate_limit_reset == 1699999999

    def test_rate_limit_tracking_invalid(self):
        """Test rate limit with invalid values."""
        client = GoveeApiClient("test_key")
        original_remaining = client.rate_limit_remaining
        headers = {
            "X-RateLimit-Remaining": "invalid",
            "X-RateLimit-Limit": "not_a_number",
        }
        client._update_rate_limits(headers)
        # Should not change on invalid values
        assert client.rate_limit_remaining == original_remaining

    def test_rate_limit_initial_values(self):
        """Test initial rate limit values."""
        client = GoveeApiClient("test_key")
        assert client.rate_limit_remaining == 100
        assert client.rate_limit_total == 100
        assert client.rate_limit_reset == 0

    @pytest.mark.asyncio
    async def test_close_does_not_close_external_session(self):
        """Regression: when constructed with an external session (e.g. HA's
        shared aiohttp_client.async_get_clientsession), close() must NOT
        close the underlying session. RetryClient.close() unconditionally
        forwards to the wrapped session, so we drop the reference instead.

        Reported via HA frame-helper warning in #80 follow-up logs."""
        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        client = GoveeApiClient("test_key", session=session)
        # Simulate the retry_client being initialized
        retry_client = AsyncMock()
        client._retry_client = retry_client

        await client.close()

        retry_client.close.assert_not_awaited()
        session.close.assert_not_awaited()
        assert client._retry_client is None
        # Session reference preserved (HA owns it)
        assert client._session is session

    @pytest.mark.asyncio
    async def test_close_closes_owned_session(self):
        """When the client created its own session, close() must release it."""
        client = GoveeApiClient.__new__(GoveeApiClient)
        client._api_key = "test_key"
        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        client._session = session
        client._owns_session = True
        retry_client = AsyncMock()
        client._retry_client = retry_client

        await client.close()

        retry_client.close.assert_awaited_once()
        session.close.assert_awaited_once()
        assert client._retry_client is None
        assert client._session is None


# ==============================================================================
# Response Handling Tests
# ==============================================================================


class TestResponseHandling:
    """Test API response handling patterns."""

    def test_device_response_structure(self):
        """Test expected device response structure."""
        response = {
            "code": 200,
            "data": [
                {
                    "device": "AA:BB:CC:DD:EE:FF:00:11",
                    "sku": "H6072",
                    "deviceName": "Living Room Light",
                    "type": "devices.types.light",
                    "capabilities": [],
                },
            ],
        }

        assert response["code"] == 200
        assert len(response["data"]) == 1
        assert response["data"][0]["device"] == "AA:BB:CC:DD:EE:FF:00:11"

    def test_state_response_structure(self):
        """Test expected state response structure."""
        response = {
            "code": 200,
            "payload": {
                "capabilities": [
                    {
                        "type": "devices.capabilities.online",
                        "instance": "online",
                        "state": {"value": True},
                    },
                    {
                        "type": "devices.capabilities.on_off",
                        "instance": "powerSwitch",
                        "state": {"value": 1},
                    },
                ],
            },
        }

        assert response["code"] == 200
        assert "capabilities" in response["payload"]

    def test_scenes_response_structure(self):
        """Test expected scenes response structure."""
        response = {
            "code": 200,
            "payload": {
                "capabilities": [
                    {
                        "type": "devices.capabilities.dynamic_scene",
                        "instance": "lightScene",
                        "parameters": {
                            "options": [
                                {"name": "Sunrise", "value": {"id": 1}},
                                {"name": "Sunset", "value": {"id": 2}},
                            ],
                        },
                    },
                ],
            },
        }

        scenes = response["payload"]["capabilities"][0]["parameters"]["options"]
        assert len(scenes) == 2
        assert scenes[0]["name"] == "Sunrise"


# ==============================================================================
# Command Payload Tests
# ==============================================================================


class TestCommandPayloads:
    """Test command payload generation."""

    def test_power_command_payload(self):
        """Test power command payload structure matches Govee API v2.0."""
        cmd = PowerCommand(power_on=True)
        payload = cmd.to_api_payload()

        assert payload["type"] == "devices.capabilities.on_off"
        assert payload["instance"] == "powerSwitch"
        assert payload["value"] == 1

    def test_power_off_command_payload(self):
        """Test power off command payload."""
        cmd = PowerCommand(power_on=False)
        assert cmd.get_value() == 0

    def test_power_on_command_payload(self):
        """Test power on command payload."""
        cmd = PowerCommand(power_on=True)
        assert cmd.get_value() == 1


# ==============================================================================
# Error Response Tests
# ==============================================================================


class TestErrorResponses:
    """Test error response handling patterns."""

    def test_auth_error_response(self):
        """Test 401 auth error response."""
        response_code = 401
        assert response_code == 401

        # This should trigger GoveeAuthError
        err = GoveeAuthError("Invalid API key")
        assert err.code == 401

    def test_rate_limit_response(self):
        """Test 429 rate limit response."""
        retry_after = 60

        err = GoveeRateLimitError(retry_after=float(retry_after))
        assert err.code == 429
        assert err.retry_after == 60.0

    def test_device_not_found_response(self):
        """Test 400 device not found response."""
        message = "devices not exist"

        # Check if message indicates device not found
        is_device_not_found = "not exist" in message.lower()
        assert is_device_not_found

        err = GoveeDeviceNotFoundError("test_device")
        assert err.code == 400

    def test_server_error_response(self):
        """Test 500 server error response."""
        response_code = 500

        err = GoveeApiError("Server error", code=response_code)
        assert err.code == 500


# ==============================================================================
# Command History Tests (#127)
# ==============================================================================


def _make_response(status: int = 200, body: dict | None = None) -> MagicMock:
    """Build a mock aiohttp response with a cached JSON body."""
    response = MagicMock()
    response.status = status
    response.headers = {}
    response.json = AsyncMock(return_value=body if body is not None else {"code": 200, "message": "Success"})
    response.text = AsyncMock(return_value=str(body or {}))
    return response


def _client_with_response(response: MagicMock | None, enter_error: Exception | None = None) -> GoveeApiClient:
    """Build a GoveeApiClient whose retry client returns the given response."""
    client = GoveeApiClient("test_key", session=MagicMock(spec=aiohttp.ClientSession))
    ctx = MagicMock()
    if enter_error is not None:
        ctx.__aenter__ = AsyncMock(side_effect=enter_error)
    else:
        ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    retry_client = MagicMock()
    retry_client.post = MagicMock(return_value=ctx)
    client._retry_client = retry_client
    return client


class TestCommandHistory:
    """control_device keeps a ring buffer of sends for diagnostics (#127)."""

    @pytest.mark.asyncio
    async def test_success_recorded(self):
        """A successful send records payload, HTTP status, and response body."""
        body = {"requestId": "r1", "code": 200, "message": "Success"}
        client = _client_with_response(_make_response(200, body))

        result = await client.control_device("AA:BB:CC:DD:EE:FF:00:11", "H60A6", PowerCommand(power_on=True))

        assert result is True
        assert len(client.recent_commands) == 1
        record = client.recent_commands[0]
        assert record["device"] == "AA:BB:CC:DD:EE:FF:00:11"
        assert record["sku"] == "H60A6"
        assert record["capability"]["type"] == "devices.capabilities.on_off"
        assert record["capability"]["value"] == 1
        assert record["http_status"] == 200
        assert record["response"] == body
        assert record["error"] is None
        assert "sent_at" in record

    @pytest.mark.asyncio
    async def test_api_rejection_recorded(self):
        """A Govee rejection records the response body AND the raised error."""
        body = {"code": 400, "message": "device offline"}
        client = _client_with_response(_make_response(400, body))

        with pytest.raises(GoveeApiError):
            await client.control_device("AA:BB:CC:DD:EE:FF:00:11", "H60A6", PowerCommand(power_on=True))

        record = client.recent_commands[0]
        assert record["http_status"] == 400
        assert record["response"] == body
        assert record["error"] == "device offline"

    @pytest.mark.asyncio
    async def test_connection_error_recorded(self):
        """A transport failure records the error with no HTTP status."""
        client = _client_with_response(None, enter_error=aiohttp.ClientError("boom"))

        with pytest.raises(GoveeConnectionError):
            await client.control_device("AA:BB:CC:DD:EE:FF:00:11", "H60A6", PowerCommand(power_on=True))

        record = client.recent_commands[0]
        assert record["http_status"] is None
        assert record["response"] is None
        assert record["error"] == "connection: boom"

    @pytest.mark.asyncio
    async def test_non_json_body_captured_as_text(self):
        """A non-JSON response body is captured as truncated text."""
        response = _make_response(502)
        response.json = AsyncMock(side_effect=aiohttp.ContentTypeError(MagicMock(), ()))
        response.text = AsyncMock(return_value="<html>Bad Gateway</html>")
        client = _client_with_response(response)

        with pytest.raises(GoveeApiError):
            await client.control_device("AA:BB:CC:DD:EE:FF:00:11", "H60A6", PowerCommand(power_on=True))

        record = client.recent_commands[0]
        assert record["http_status"] == 502
        assert record["response"] == "<html>Bad Gateway</html>"
        assert record["error"] is not None

    @pytest.mark.asyncio
    async def test_ring_buffer_trims_oldest(self):
        """The history is bounded and keeps the newest sends."""
        from custom_components.govee.api.client import COMMAND_BUFFER_SIZE

        client = _client_with_response(_make_response())
        for i in range(COMMAND_BUFFER_SIZE + 5):
            await client.control_device("AA:BB:CC:DD:EE:FF:00:11", f"SKU{i}", PowerCommand(power_on=True))

        assert len(client.recent_commands) == COMMAND_BUFFER_SIZE
        assert client.recent_commands[-1]["sku"] == f"SKU{COMMAND_BUFFER_SIZE + 4}"
        assert client.recent_commands[0]["sku"] == "SKU5"


# ==============================================================================
# Control-Command Logging Tests (#127)
# ==============================================================================


class TestControlDeviceLogging:
    """control_device emits payload-level debug/warning log lines (#127)."""

    @pytest.mark.asyncio
    async def test_debug_logs_on_success(self, caplog):
        """A successful send emits Control send/response lines at DEBUG."""
        client = _client_with_response(_make_response(200))

        with caplog.at_level(logging.DEBUG, logger="custom_components.govee.api.client"):
            await client.control_device("AA:BB:CC:DD:EE:FF:00:11", "H60A6", PowerCommand(power_on=True))

        send_lines = [r for r in caplog.records if r.getMessage().startswith("Control send:")]
        assert len(send_lines) == 1
        assert send_lines[0].levelno == logging.DEBUG
        assert "type=devices.capabilities.on_off" in send_lines[0].getMessage()
        assert "instance=powerSwitch" in send_lines[0].getMessage()
        assert "value=1" in send_lines[0].getMessage()

        response_lines = [r for r in caplog.records if r.getMessage().startswith("Control response:")]
        assert len(response_lines) == 1
        assert response_lines[0].levelno == logging.DEBUG
        assert "HTTP 200" in response_lines[0].getMessage()

    @pytest.mark.asyncio
    async def test_warning_on_rejection(self, caplog):
        """A Govee rejection emits a WARNING with payload context and still raises."""
        body = {"code": 400, "message": "device offline"}
        client = _client_with_response(_make_response(400, body))

        with caplog.at_level(logging.DEBUG, logger="custom_components.govee.api.client"):
            with pytest.raises(GoveeApiError):
                await client.control_device("AA:BB:CC:DD:EE:FF:00:11", "H60A6", PowerCommand(power_on=True))

        warning_lines = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and r.getMessage().startswith("Control rejected by Govee:")
        ]
        assert len(warning_lines) == 1
        message = warning_lines[0].getMessage()
        assert "device offline" in message
        assert "HTTP 400" in message
        assert "instance=powerSwitch" in message

    @pytest.mark.asyncio
    async def test_toggle_command_payload_shape(self):
        """ToggleCommand serializes to the documented devices.capabilities.toggle wire shape."""
        client = _client_with_response(_make_response(200))
        command = ToggleCommand(toggle_instance="backgroundLightToggle", enabled=True)

        await client.control_device("AA:BB:CC:DD:EE:FF:00:11", "H60A6", command)

        assert client.recent_commands[0]["capability"] == {
            "type": "devices.capabilities.toggle",
            "instance": "backgroundLightToggle",
            "value": 1,
        }


class TestRecordLocalCommand:
    """LAN/MQTT/BLE writes belong in the same history diagnostics reads.

    Issues #149/#158: a colour write that went out over LAN or MQTT left no
    trace at all in a diagnostics download, which is exactly the report where
    the command history is the evidence.
    """

    def _client(self):
        from custom_components.govee.api.client import GoveeApiClient

        return GoveeApiClient(api_key="k")

    def test_delivered_command_is_recorded(self):
        client = self._client()
        client.record_local_command(
            "AA:BB", "H6159", "lan", {"instance": "colorRgb"}, delivered=True
        )
        record = client.recent_commands[-1]
        assert record["transport"] == "lan"
        assert record["device"] == "AA:BB"
        assert record["response"] == {"delivered": True, "detail": None}
        assert record["error"] is None

    def test_unconfirmed_command_records_the_reason_as_an_error(self):
        client = self._client()
        client.record_local_command(
            "AA:BB",
            "H6159",
            "lan",
            {"instance": "colorRgb"},
            delivered=False,
            detail="device reported a different value than was sent",
        )
        record = client.recent_commands[-1]
        assert record["error"] == "device reported a different value than was sent"
        assert record["response"]["delivered"] is False

    def test_local_records_share_the_buffer_with_cloud_records(self):
        client = self._client()
        client.record_local_command(
            "AA:BB", "H6159", "mqtt", {"instance": "colorRgb"}, delivered=True
        )
        client.record_local_command(
            "AA:BB", "H6159", "lan", {"instance": "brightness"}, delivered=True
        )
        assert [r["capability"]["instance"] for r in client.recent_commands] == [
            "colorRgb",
            "brightness",
        ]


# ==============================================================================
# Daily request accounting
# ==============================================================================


class TestRequestAccounting:
    """Local counting of requests against Govee's undocumented-in-headers cap.

    Govee reports the per-minute allowance in response headers but never the
    daily one, so an install has no way to know what it spends per day unless
    it counts locally. These counters are the measurement that has to exist
    before any polling behaviour is tuned.
    """

    HOUR = 3600
    DAY = 86400

    def _client(self, monkeypatch, now: float):
        """A client whose clock is fixed at ``now`` and movable."""
        import custom_components.govee.api.client as client_mod

        clock = {"now": now}
        monkeypatch.setattr(client_mod.time, "time", lambda: clock["now"])
        return GoveeApiClient("test_key", session=MagicMock()), clock

    def test_starts_at_zero(self, monkeypatch):
        client, _ = self._client(monkeypatch, 1_000_000.0)

        assert client.requests_today == 0
        assert client.requests_last_24h == 0
        assert client.requests_per_hour == 0.0

    def test_counts_each_request(self, monkeypatch):
        client, _ = self._client(monkeypatch, 1_000_000.0)

        for _ in range(5):
            client._note_request()

        assert client.requests_today == 5
        assert client.requests_last_24h == 5

    def test_buckets_by_hour(self, monkeypatch):
        client, clock = self._client(monkeypatch, 1_000_000.0)

        client._note_request()
        clock["now"] += self.HOUR
        client._note_request()
        client._note_request()

        assert client.requests_last_24h == 3
        assert len(client._request_buckets) == 2

    def test_drops_history_older_than_24h(self, monkeypatch):
        client, clock = self._client(monkeypatch, 1_000_000.0)

        client._note_request()
        # Walk forward a full day; the first hour must fall out of the window.
        clock["now"] += 24 * self.HOUR
        client._note_request()

        assert client.requests_last_24h == 1
        assert len(client._request_buckets) == 1

    def test_history_is_bounded(self, monkeypatch):
        """A week of traffic must not grow the history without limit."""
        client, clock = self._client(monkeypatch, 1_000_000.0)

        for _ in range(24 * 7):
            client._note_request()
            clock["now"] += self.HOUR

        assert len(client._request_buckets) <= 24

    def test_requests_today_resets_at_utc_midnight(self, monkeypatch):
        # Start mid-day so the rollover is unambiguous.
        start = 1_000_000.0
        client, clock = self._client(monkeypatch, start)

        client._note_request()
        client._note_request()
        assert client.requests_today == 2

        # Cross into the next UTC day.
        clock["now"] = (int(start // self.DAY) + 1) * self.DAY + 10
        assert client.requests_today == 0

        client._note_request()
        assert client.requests_today == 1

    def test_rolling_window_survives_the_day_rollover(self, monkeypatch):
        """The trailing-24h figure is independent of the calendar reset."""
        start = 1_000_000.0
        client, clock = self._client(monkeypatch, start)

        client._note_request()
        clock["now"] = (int(start // self.DAY) + 1) * self.DAY + 10
        client._note_request()

        assert client.requests_today == 1
        assert client.requests_last_24h == 2

    def test_requests_per_hour_averages_held_history(self, monkeypatch):
        client, clock = self._client(monkeypatch, 1_000_000.0)

        for _ in range(10):
            client._note_request()
        clock["now"] += self.HOUR
        for _ in range(20):
            client._note_request()

        # Two hourly buckets holding 30 requests.
        assert client.requests_per_hour == 15.0

    def test_idle_hours_count_against_the_average(self, monkeypatch):
        """An hour with no traffic still happened.

        Buckets only exist for hours that saw requests, so dividing by buckets
        held would drop a restart or a quiet spell out of the denominator and
        report a rate higher than the install actually ran at.
        """
        client, clock = self._client(monkeypatch, 1_000_000.0)

        for _ in range(10):
            client._note_request()
        # Three silent hours - a restart, say - then traffic again.
        clock["now"] += 4 * self.HOUR
        for _ in range(10):
            client._note_request()

        # 20 requests across a 5-hour span, not across the 2 hours recorded.
        assert client.requests_per_hour == 4.0

    def test_single_hour_of_traffic_averages_to_itself(self, monkeypatch):
        client, _ = self._client(monkeypatch, 1_000_000.0)

        for _ in range(7):
            client._note_request()

        assert client.requests_per_hour == 7.0

    @pytest.mark.asyncio
    async def test_handle_response_counts_the_request(self, monkeypatch):
        """Every answered request is counted, whatever the status."""
        client, _ = self._client(monkeypatch, 1_000_000.0)

        response = MagicMock()
        response.headers = {}
        response.status = 200
        response.json = AsyncMock(return_value={"code": 200, "data": {}})

        await client._handle_response(response)

        assert client.requests_today == 1

    @pytest.mark.asyncio
    async def test_rejected_request_still_counts(self, monkeypatch):
        """A 429 consumed the allowance too — not counting it hides the spend."""
        client, _ = self._client(monkeypatch, 1_000_000.0)

        response = MagicMock()
        response.headers = {}
        response.status = 429
        response.json = AsyncMock(return_value={"message": "rate limited"})

        with pytest.raises(GoveeRateLimitError):
            await client._handle_response(response)

        assert client.requests_today == 1
