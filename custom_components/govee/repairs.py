"""Repair flows for Govee integration.

Provides repair flows for common issues:
- Group device limitations (informational)
- Rate limit warnings
- Authentication failures (fixable)
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_create_group_device_issue(
    hass: HomeAssistant,
    entry_id: str,
    device_name: str,
) -> None:
    """Create informational issue about group device limitations.

    Group devices can be controlled but state queries fail due to
    Govee API limitations. This creates a non-fixable warning to
    inform users about expected behavior.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"group_device_{entry_id}_{device_name}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="group_device_limitation",
        translation_placeholders={"device_name": device_name},
    )


async def async_delete_group_device_issue(
    hass: HomeAssistant,
    entry_id: str,
    device_name: str,
) -> None:
    """Delete group device issue when device is removed."""
    ir.async_delete_issue(
        hass,
        DOMAIN,
        f"group_device_{entry_id}_{device_name}",
    )


async def async_create_rate_limit_minute_warning(
    hass: HomeAssistant,
    entry_id: str,
    remaining: int,
) -> None:
    """Create warning when approaching per-minute rate limit.

    Govee API has a 100 requests/minute limit. This warning appears
    when remaining requests drop below 10.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"rate_limit_minute_{entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="rate_limit_minute_warning",
        translation_placeholders={"remaining": str(remaining)},
    )


async def async_delete_rate_limit_minute_warning(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    """Delete rate limit minute warning when limit recovers."""
    ir.async_delete_issue(
        hass,
        DOMAIN,
        f"rate_limit_minute_{entry_id}",
    )


async def async_create_rate_limit_day_warning(
    hass: HomeAssistant,
    entry_id: str,
    remaining: int,
) -> None:
    """Create warning when approaching daily rate limit.

    Govee API has a 10,000 requests/day limit. This warning appears
    when remaining requests drop below 100.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"rate_limit_day_{entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="rate_limit_day_warning",
        translation_placeholders={"remaining": str(remaining)},
    )


async def async_delete_rate_limit_day_warning(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    """Delete rate limit day warning when limit recovers."""
    ir.async_delete_issue(
        hass,
        DOMAIN,
        f"rate_limit_day_{entry_id}",
    )


async def async_create_auth_issue(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    """Create fixable issue for authentication failures.

    This issue guides users to update their API key when
    authentication fails.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"auth_failed_{entry_id}",
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="auth_failed",
        data={"entry_id": entry_id},
    )


async def async_delete_auth_issue(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    """Delete auth issue when re-authentication succeeds."""
    ir.async_delete_issue(
        hass,
        DOMAIN,
        f"auth_failed_{entry_id}",
    )


class GoveeAuthRepairFlow(RepairsFlow):
    """Handler for authentication repair flow."""

    def __init__(self, entry_id: str) -> None:
        """Initialize the repair flow."""
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Handle the initial step - redirect to reconfigure."""
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create repair flow for fixable issues."""
    if issue_id.startswith("auth_failed_"):
        entry_id = data.get("entry_id", "") if data else ""
        return GoveeAuthRepairFlow(entry_id)
    raise ValueError(f"Unknown issue_id: {issue_id}")
