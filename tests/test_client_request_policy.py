"""How the REST client spends requests: what is retried, and how answers are read.

These run the real aiohttp-retry client over a fake session so the number of
requests actually sent is what is asserted, not how the retry client was
configured.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.govee.api.client import GoveeApiClient
from custom_components.govee.api.exceptions import (
    GoveeApiError,
    GoveeAuthError,
    GoveeConnectionError,
    GoveeRateLimitError,
)
from custom_components.govee.models import PowerCommand

DEVICE = "AA:BB:CC:DD:EE:FF:00:11"


def _response(method: str, status: int, body: Any = None, headers: dict[str, str] | None = None) -> MagicMock:
    response = MagicMock()
    response.method = method
    response.status = status
    response.headers = headers or {}
    response.closed = False
    response.json = AsyncMock(return_value=body if body is not None else {"code": status, "message": "error"})
    response.text = AsyncMock(return_value="")
    return response


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    """Skip aiohttp-retry's back-off sleeps."""
    import aiohttp_retry.client as retry_module

    monkeypatch.setattr(retry_module.asyncio, "sleep", AsyncMock())


def _client(request: AsyncMock) -> GoveeApiClient:
    session = MagicMock()
    session.request = request
    return GoveeApiClient("test-key", session=session)


@pytest.mark.asyncio
async def test_control_command_is_sent_once_on_timeout():
    request = AsyncMock(side_effect=TimeoutError())
    client = _client(request)

    with pytest.raises(GoveeConnectionError):
        await client.control_device(DEVICE, "H6159", PowerCommand(power_on=True))

    assert request.await_count == 1


@pytest.mark.asyncio
async def test_control_command_is_sent_once_on_server_error():
    request = AsyncMock(return_value=_response("POST", 503))
    client = _client(request)

    with pytest.raises(GoveeApiError):
        await client.control_device(DEVICE, "H6159", PowerCommand(power_on=True))

    assert request.await_count == 1


@pytest.mark.asyncio
async def test_state_read_still_retries_on_timeout():
    request = AsyncMock(side_effect=TimeoutError())
    client = _client(request)

    with pytest.raises(GoveeConnectionError):
        await client.get_device_state(DEVICE, "H6159")

    assert request.await_count == 3


# --- Reading an answer: status first, body defensively ---------------------


def _undecodable(status: int, headers: dict[str, str] | None = None) -> MagicMock:
    response = _response("GET", status, headers=headers)
    response.json = AsyncMock(side_effect=aiohttp.ContentTypeError(MagicMock(), ()))
    response.text = AsyncMock(return_value="<html>gateway says no</html>")
    return response


@pytest.mark.asyncio
async def test_401_with_a_non_json_body_is_an_auth_error():
    client = _client(AsyncMock())

    with pytest.raises(GoveeAuthError):
        await client._handle_response(_undecodable(401))


@pytest.mark.asyncio
async def test_429_with_a_non_json_body_is_a_rate_limit():
    client = _client(AsyncMock())

    with pytest.raises(GoveeRateLimitError) as exc_info:
        await client._handle_response(_undecodable(429, headers={"Retry-After": "120"}))

    assert exc_info.value.retry_after == 120.0


@pytest.mark.asyncio
async def test_429_with_an_http_date_retry_after():
    client = _client(AsyncMock())
    when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=300), usegmt=True)

    with pytest.raises(GoveeRateLimitError) as exc_info:
        await client._handle_response(_response("GET", 429, headers={"Retry-After": when}))

    assert exc_info.value.retry_after is not None
    assert 290 <= exc_info.value.retry_after <= 300


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [None, "naive", -300], ids=["unreadable", "no_zone_in_the_past", "in_the_past"])
async def test_429_with_an_unusable_retry_after(delta):
    client = _client(AsyncMock())
    if delta is None:
        value = "soon"
    elif delta == "naive":
        value = "Wed, 21 Oct 2015 07:28:00 -0000"
    else:
        value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=delta), usegmt=True)

    with pytest.raises(GoveeRateLimitError) as exc_info:
        await client._handle_response(_response("GET", 429, headers={"Retry-After": value}))

    assert exc_info.value.retry_after is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body_error",
    [None, json.JSONDecodeError("Expecting value", "{", 1)],
    ids=["empty_body", "malformed_json"],
)
async def test_unreadable_success_body_is_a_govee_error(body_error):
    client = _client(AsyncMock())
    response = _response("GET", 200)
    if body_error is None:
        response.json = AsyncMock(return_value=None)
    else:
        response.json = AsyncMock(side_effect=body_error)

    with pytest.raises(GoveeApiError, match="Invalid JSON response"):
        await client._handle_response(response)


@pytest.mark.asyncio
async def test_non_json_server_error_keeps_its_status_code():
    """A proxy's HTML 503 still reads as a 5xx, so the outage check sees it."""
    client = _client(AsyncMock())

    with pytest.raises(GoveeApiError) as exc_info:
        await client._handle_response(_undecodable(503))

    assert exc_info.value.code == 503


@pytest.mark.asyncio
async def test_error_body_that_is_not_an_object():
    client = _client(AsyncMock())

    with pytest.raises(GoveeApiError) as exc_info:
        await client._handle_response(_response("GET", 400, body=["unexpected"]))

    assert exc_info.value.code == 400
