"""Test Govee repairs module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from homeassistant.core import HomeAssistant

from custom_components.govee.repairs import (
    async_create_group_device_issue,
    async_delete_group_device_issue,
    async_create_rate_limit_minute_warning,
    async_delete_rate_limit_minute_warning,
    async_create_rate_limit_day_warning,
    async_delete_rate_limit_day_warning,
    async_create_auth_issue,
    async_delete_auth_issue,
    async_create_fix_flow,
    GoveeAuthRepairFlow,
)
from custom_components.govee.const import DOMAIN


class TestGroupDeviceIssue:
    """Test group device issue functions."""

    @pytest.mark.asyncio
    async def test_create_group_device_issue(self, hass: HomeAssistant):
        """Test creating group device issue."""
        with patch(
            "custom_components.govee.repairs.ir.async_create_issue"
        ) as mock_create:
            await async_create_group_device_issue(
                hass, "entry_123", "Living Room Group"
            )

            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["translation_key"] == "group_device_limitation"
            assert call_kwargs["is_fixable"] is False
            assert "Living Room Group" in call_kwargs["translation_placeholders"]["device_name"]

    @pytest.mark.asyncio
    async def test_delete_group_device_issue(self, hass: HomeAssistant):
        """Test deleting group device issue."""
        with patch(
            "custom_components.govee.repairs.ir.async_delete_issue"
        ) as mock_delete:
            await async_delete_group_device_issue(
                hass, "entry_123", "Living Room Group"
            )

            mock_delete.assert_called_once_with(
                hass, DOMAIN, "group_device_entry_123_Living Room Group"
            )


class TestRateLimitMinuteWarning:
    """Test rate limit minute warning functions."""

    @pytest.mark.asyncio
    async def test_create_rate_limit_minute_warning(self, hass: HomeAssistant):
        """Test creating rate limit minute warning."""
        with patch(
            "custom_components.govee.repairs.ir.async_create_issue"
        ) as mock_create:
            await async_create_rate_limit_minute_warning(hass, "entry_123", 5)

            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["translation_key"] == "rate_limit_minute_warning"
            assert call_kwargs["translation_placeholders"]["remaining"] == "5"

    @pytest.mark.asyncio
    async def test_delete_rate_limit_minute_warning(self, hass: HomeAssistant):
        """Test deleting rate limit minute warning."""
        with patch(
            "custom_components.govee.repairs.ir.async_delete_issue"
        ) as mock_delete:
            await async_delete_rate_limit_minute_warning(hass, "entry_123")

            mock_delete.assert_called_once_with(
                hass, DOMAIN, "rate_limit_minute_entry_123"
            )


class TestRateLimitDayWarning:
    """Test rate limit day warning functions."""

    @pytest.mark.asyncio
    async def test_create_rate_limit_day_warning(self, hass: HomeAssistant):
        """Test creating rate limit day warning."""
        with patch(
            "custom_components.govee.repairs.ir.async_create_issue"
        ) as mock_create:
            await async_create_rate_limit_day_warning(hass, "entry_123", 50)

            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["translation_key"] == "rate_limit_day_warning"
            assert call_kwargs["translation_placeholders"]["remaining"] == "50"

    @pytest.mark.asyncio
    async def test_delete_rate_limit_day_warning(self, hass: HomeAssistant):
        """Test deleting rate limit day warning."""
        with patch(
            "custom_components.govee.repairs.ir.async_delete_issue"
        ) as mock_delete:
            await async_delete_rate_limit_day_warning(hass, "entry_123")

            mock_delete.assert_called_once_with(
                hass, DOMAIN, "rate_limit_day_entry_123"
            )


class TestAuthIssue:
    """Test auth issue functions."""

    @pytest.mark.asyncio
    async def test_create_auth_issue(self, hass: HomeAssistant):
        """Test creating auth issue."""
        with patch(
            "custom_components.govee.repairs.ir.async_create_issue"
        ) as mock_create:
            await async_create_auth_issue(hass, "entry_123")

            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["translation_key"] == "auth_failed"
            assert call_kwargs["is_fixable"] is True
            assert call_kwargs["data"]["entry_id"] == "entry_123"

    @pytest.mark.asyncio
    async def test_delete_auth_issue(self, hass: HomeAssistant):
        """Test deleting auth issue."""
        with patch(
            "custom_components.govee.repairs.ir.async_delete_issue"
        ) as mock_delete:
            await async_delete_auth_issue(hass, "entry_123")

            mock_delete.assert_called_once_with(
                hass, DOMAIN, "auth_failed_entry_123"
            )


class TestGoveeAuthRepairFlow:
    """Test GoveeAuthRepairFlow class."""

    @pytest.mark.asyncio
    async def test_repair_flow_init_step(self):
        """Test repair flow init step."""
        flow = GoveeAuthRepairFlow("entry_123")

        result = await flow.async_step_init(None)

        assert result["type"] == "create_entry"


class TestAsyncCreateFixFlow:
    """Test async_create_fix_flow function."""

    @pytest.mark.asyncio
    async def test_create_fix_flow_auth(self, hass: HomeAssistant):
        """Test creating fix flow for auth issue."""
        flow = await async_create_fix_flow(
            hass, "auth_failed_entry_123", {"entry_id": "entry_123"}
        )

        assert isinstance(flow, GoveeAuthRepairFlow)

    @pytest.mark.asyncio
    async def test_create_fix_flow_unknown(self, hass: HomeAssistant):
        """Test creating fix flow for unknown issue raises error."""
        with pytest.raises(ValueError, match="Unknown issue_id"):
            await async_create_fix_flow(hass, "unknown_issue", None)
