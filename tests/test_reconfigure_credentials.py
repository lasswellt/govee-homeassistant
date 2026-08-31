"""Reconfigure must keep the credentials it just obtained (issue #179).

The flow logged in, cached the fresh IoT credentials into ``entry.data``, and
then wrote a snapshot taken *before* that write back over them via
``data_updates`` — which overrides existing keys. The user saw
"Reconfiguration successful" and kept the expired token.

That mattered beyond the annoyance: reconfiguring is the obvious user-side
recovery from an expired token (#178), and it silently didn't work.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.govee.config_flow import GoveeConfigFlow
from custom_components.govee.const import KEY_IOT_CREDENTIALS

STALE = {"token": "stale-token", "refresh_token": "stale-refresh"}
FRESH = SimpleNamespace(
    token="fresh-token",
    refresh_token="fresh-refresh",
    account_topic="GA/x",
    iot_cert="c",
    iot_key="k",
    iot_ca=None,
    client_id="cid",
    endpoint="ep",
)


def _flow_with_entry():
    """A flow whose entry already holds stale credentials."""
    flow = GoveeConfigFlow()
    flow.hass = MagicMock()

    entry = SimpleNamespace(
        entry_id="e1",
        data={
            "api_key": "k" * 40,
            "email": "old@example.com",
            "password": "old-pw",
            KEY_IOT_CREDENTIALS: dict(STALE),
        },
    )

    def _update(target, data=None, **_kwargs):
        # Mirror HA: the entry object's data is replaced in place.
        target.data = data

    flow.hass.config_entries.async_get_entry.return_value = entry
    flow.hass.config_entries.async_update_entry.side_effect = _update

    flow._iot_credentials = FRESH
    flow._email = "new@example.com"
    flow._password = "new-pw"
    flow._api_key = "n" * 40
    return flow, entry


class TestReconfigureKeepsFreshCredentials:
    def test_cache_then_snapshot_order(self):
        """The snapshot must be taken after the fresh credentials are written.

        This is the whole bug: reading entry.data first captures the stale
        token, and data_updates then puts it back.
        """
        flow, entry = _flow_with_entry()

        flow._cache_iot_credentials(entry.entry_id)
        # Snapshot taken now — as the fixed flow does — sees the fresh token.
        snapshot = dict(entry.data)

        assert snapshot[KEY_IOT_CREDENTIALS]["token"] == "fresh-token"

    def test_snapshot_taken_first_would_carry_the_stale_token(self):
        """Pins why the order matters, so a future edit can't quietly undo it."""
        flow, entry = _flow_with_entry()

        # The old order: snapshot before caching.
        snapshot = dict(entry.data)
        flow._cache_iot_credentials(entry.entry_id)

        # data_updates overrides existing keys, so this snapshot would win.
        assert snapshot[KEY_IOT_CREDENTIALS]["token"] == "stale-token"
        assert entry.data[KEY_IOT_CREDENTIALS]["token"] == "fresh-token"

    def test_no_credentials_leaves_the_entry_alone(self):
        """Nothing to cache means nothing written — the entry keeps what it had."""
        flow, entry = _flow_with_entry()
        flow._iot_credentials = None

        flow._cache_iot_credentials(entry.entry_id)

        assert entry.data[KEY_IOT_CREDENTIALS]["token"] == "stale-token"


class TestReconfigureFlowOrdering:
    """The ordering as it appears in the flow itself, not just the helper."""

    @pytest.mark.asyncio
    async def test_account_step_writes_fresh_credentials(self):
        flow, entry = _flow_with_entry()
        flow._get_reconfigure_entry = lambda: entry
        flow._clear_mqtt_cache = MagicMock()

        captured: dict = {}

        def _abort(target, data_updates=None, **_kwargs):
            captured["data_updates"] = data_updates
            return {"type": "abort", "reason": "reconfigure_successful"}

        with patch.object(
            GoveeConfigFlow, "async_update_reload_and_abort", side_effect=_abort
        ), patch(
            "custom_components.govee.config_flow.validate_api_key",
            return_value=True,
        ), patch(
            "custom_components.govee.config_flow.validate_govee_credentials",
            return_value=FRESH,
        ):
            await flow.async_step_reconfigure(
                {
                    "api_key": flow._api_key,
                    "email": "new@example.com",
                    "password": "new-pw",
                }
            )

        written = captured.get("data_updates") or {}
        creds = written.get(KEY_IOT_CREDENTIALS)
        assert creds is not None, "reconfigure wrote no credentials at all"
        assert creds["token"] == "fresh-token", (
            "reconfigure wrote the stale token back over the fresh one — #179"
        )
