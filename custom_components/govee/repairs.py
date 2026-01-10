"""Repairs framework integration for Govee.

Provides actionable repair notifications for common issues:
- Expired or invalid API credentials
- Rate limit exceeded
- Offline devices
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


# Issue IDs
ISSUE_AUTH_FAILED = "auth_failed"
ISSUE_RATE_LIMITED = "rate_limited"
ISSUE_MQTT_DISCONNECTED = "mqtt_disconnected"


async def async_create_auth_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Create a repair issue for authentication failure.

    This issue is fixable - user can re-authenticate via the repair flow.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_AUTH_FAILED}_{entry.entry_id}",
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_AUTH_FAILED,
        translation_placeholders={
            "entry_title": entry.title,
        },
        data={"entry_id": entry.entry_id},
    )
    _LOGGER.info("Created auth_failed repair issue for entry %s", entry.entry_id)


async def async_delete_auth_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Delete the auth failure issue when resolved."""
    ir.async_delete_issue(
        hass,
        DOMAIN,
        f"{ISSUE_AUTH_FAILED}_{entry.entry_id}",
    )
    _LOGGER.debug("Deleted auth_failed repair issue for entry %s", entry.entry_id)


async def async_create_rate_limit_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    reset_time: str,
) -> None:
    """Create a repair issue for rate limiting.

    This issue is informational - not directly fixable, but provides guidance.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_RATE_LIMITED}_{entry.entry_id}",
        is_fixable=False,
        is_persistent=False,  # Will auto-dismiss on next successful update
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_RATE_LIMITED,
        translation_placeholders={
            "reset_time": reset_time,
            "entry_title": entry.title,
        },
    )
    _LOGGER.info("Created rate_limited repair issue for entry %s", entry.entry_id)


async def async_delete_rate_limit_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Delete the rate limit issue when resolved."""
    ir.async_delete_issue(
        hass,
        DOMAIN,
        f"{ISSUE_RATE_LIMITED}_{entry.entry_id}",
    )


async def async_create_mqtt_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    reason: str,
) -> None:
    """Create a repair issue for MQTT disconnection.

    This issue provides guidance on MQTT connectivity issues.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_MQTT_DISCONNECTED}_{entry.entry_id}",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_MQTT_DISCONNECTED,
        translation_placeholders={
            "reason": reason,
            "entry_title": entry.title,
        },
    )
    _LOGGER.info("Created mqtt_disconnected repair issue for entry %s", entry.entry_id)


async def async_delete_mqtt_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Delete the MQTT issue when resolved."""
    ir.async_delete_issue(
        hass,
        DOMAIN,
        f"{ISSUE_MQTT_DISCONNECTED}_{entry.entry_id}",
    )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create repair flow for fixable issues."""
    if issue_id.startswith(ISSUE_AUTH_FAILED):
        return AuthRepairFlow()

    # Default to confirm-only flow for non-fixable issues
    return ConfirmRepairFlow()


class AuthRepairFlow(RepairsFlow):
    """Repair flow for authentication issues.

    Guides user through re-authentication process.
    """

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the initial step of the repair flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle confirmation and redirect to reauth flow."""
        if user_input is not None:
            # Get the entry ID from the issue data
            entry_id = str(self.data.get("entry_id", "")) if self.data else ""
            if entry_id:
                entry = self.hass.config_entries.async_get_entry(entry_id)
                if entry:
                    # Trigger reauth flow
                    self.hass.async_create_task(
                        self.hass.config_entries.flow.async_init(
                            DOMAIN,
                            context={"source": "reauth", "entry_id": entry_id},
                            data=dict(entry.data),
                        )
                    )
                    return self.async_create_entry(data={})

        entry_title = "Govee"
        if self.data:
            entry_title = str(self.data.get("entry_title", "Govee"))

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"entry_title": entry_title},
        )
