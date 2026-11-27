"""Repairs framework integration for Govee.

Repair issues are raised only where the user can act on them (quality-scale
rule ``repair-issues``):

- ``rate_limited``: fixable; the flow raises the polling interval so the
  integration stops exceeding Govee's request budget.
- ``mqtt_disconnected``: fixable; the flow clears the stored sign-in failure
  and reloads the entry so the account login and the real-time session are
  retried.
- ``mqtt_2fa_required`` and ``mqtt_token_expired`` (raised by the entry
  setup and the coordinator) cannot be fixed in place; their text tells the
  user to reconfigure the integration.

An invalid API key is not a repair issue here: the coordinator raises
``ConfigEntryAuthFailed`` and Home Assistant starts its own reauth flow.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # HA 2026.10 validates with probatio and aliases ``voluptuous`` to it at startup;
    # older HA ships voluptuous itself. Type against probatio, import voluptuous.
    import probatio as vol
    from homeassistant.components.repairs import RepairsFlowResult
else:
    import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    KEY_IOT_LOGIN_FAILED,
    MAX_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Issue IDs
ISSUE_RATE_LIMITED = "rate_limited"
ISSUE_MQTT_DISCONNECTED = "mqtt_disconnected"
ISSUE_MQTT_SILENT = "mqtt_silent"

# Issue-id prefixes retired when the rate-limit repair was consolidated into
# ISSUE_RATE_LIMITED. An install that hit either one before the rename carries
# the orphaned registry entry forever, since nothing today ever creates an
# issue with these ids again to let it auto-clear.
_LEGACY_ISSUE_PREFIXES = ("rate_limit_minute", "poll_interval_unsustainable")


@callback
def async_cleanup_legacy_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete pre-rename issue-registry entries left behind for this entry.

    ``ir.async_delete_issue`` is a no-op when the issue does not exist, so
    this is safe to call unconditionally on every setup.
    """
    for prefix in _LEGACY_ISSUE_PREFIXES:
        ir.async_delete_issue(hass, DOMAIN, f"{prefix}_{entry.entry_id}")


@callback
def async_create_rate_limit_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    reset_time: str,
) -> None:
    """Create a repair issue for rate limiting.

    Fixable: the repair flow raises the polling interval.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_RATE_LIMITED}_{entry.entry_id}",
        is_fixable=True,
        is_persistent=False,  # Will auto-dismiss on next successful update
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_RATE_LIMITED,
        translation_placeholders={"entry_title": entry.title},
        data={"entry_id": entry.entry_id, "reset_time": reset_time},
    )
    _LOGGER.debug("Created rate_limited repair issue for entry %s", entry.entry_id)


@callback
def async_delete_rate_limit_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Delete the rate limit issue when resolved."""
    ir.async_delete_issue(
        hass,
        DOMAIN,
        f"{ISSUE_RATE_LIMITED}_{entry.entry_id}",
    )


@callback
def async_create_mqtt_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    reason: str,
) -> None:
    """Create a repair issue for a failed or lost real-time (MQTT) session.

    Fixable: the repair flow clears the stored sign-in failure and reloads
    the entry, which retries the account login and the connection.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_MQTT_DISCONNECTED}_{entry.entry_id}",
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_MQTT_DISCONNECTED,
        translation_placeholders={"entry_title": entry.title},
        data={"entry_id": entry.entry_id, "reason": reason},
    )
    _LOGGER.debug("Created mqtt_disconnected repair issue for entry %s", entry.entry_id)


@callback
def async_delete_mqtt_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Delete the MQTT issue when resolved."""
    ir.async_delete_issue(
        hass,
        DOMAIN,
        f"{ISSUE_MQTT_DISCONNECTED}_{entry.entry_id}",
    )


@callback
def async_create_mqtt_silent_issue(hass: HomeAssistant, entry: ConfigEntry, minutes: int) -> None:
    """Create a repair for a connected real-time session that receives nothing (#222).

    Not fixable here: only a fresh account login (Reconfigure) issues new
    AWS IoT credentials, and that needs the user's password and possibly a
    verification code.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_MQTT_SILENT}_{entry.entry_id}",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_MQTT_SILENT,
        translation_placeholders={"entry_title": entry.title, "minutes": str(minutes)},
    )
    _LOGGER.debug("Created mqtt_silent repair issue for entry %s", entry.entry_id)


@callback
def async_delete_mqtt_silent_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the silent-session repair once a message arrives."""
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_MQTT_SILENT}_{entry.entry_id}")


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create the repair flow for a fixable issue."""
    if issue_id.startswith(ISSUE_RATE_LIMITED):
        return RateLimitRepairFlow()
    return MqttReconnectRepairFlow()


class _GoveeRepairFlow(RepairsFlow):
    """Shared plumbing: resolve the config entry the issue was raised for."""

    def _entry(self) -> ConfigEntry | None:
        """Return the entry named in the issue data, or None if it is gone."""
        entry_id = str(self.data.get("entry_id", "")) if self.data else ""
        if not entry_id:
            return None
        return self.hass.config_entries.async_get_entry(entry_id)

    def _issue_text(self, key: str, default: str) -> str:
        """Return a string the issue stored for its fix flow, or ``default``."""
        value = self.data.get(key) if self.data else None
        return str(value) if value else default

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> RepairsFlowResult:
        """Handle the initial step of the repair flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> RepairsFlowResult:
        """Show the proposed fix and apply it on submit (subclasses implement)."""
        raise NotImplementedError


class RateLimitRepairFlow(_GoveeRepairFlow):
    """Raise the polling interval so the API budget lasts the whole day."""

    async def async_step_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> RepairsFlowResult:
        """Show the proposed interval; apply it on submit."""
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason="entry_not_found")

        current = int(entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
        proposed = min(MAX_POLL_INTERVAL, current * 2)

        if user_input is not None:
            # The entry's update listener reloads it when options change.
            self.hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_POLL_INTERVAL: proposed})
            _LOGGER.info(
                "Polling interval for %s raised from %ss to %ss via repair",
                entry.title,
                current,
                proposed,
            )
            return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "entry_title": entry.title,
                "reset_time": self._issue_text("reset_time", "a few minutes"),
                "current": str(current),
                "proposed": str(proposed),
            },
        )


class MqttReconnectRepairFlow(_GoveeRepairFlow):
    """Retry the account sign-in and the real-time session."""

    async def async_step_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> RepairsFlowResult:
        """Explain the retry; on submit clear the failure marker and reload."""
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason="entry_not_found")

        if user_input is not None:
            new_data = dict(entry.data)
            new_data.pop(KEY_IOT_LOGIN_FAILED, None)
            self.hass.config_entries.async_update_entry(entry, data=new_data)
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "entry_title": entry.title,
                "reason": self._issue_text("reason", "connection lost"),
            },
        )
