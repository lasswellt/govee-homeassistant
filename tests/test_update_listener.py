"""The options update listener must not reload on a data-only entry write.

Home Assistant fires update listeners for any ``async_update_entry`` call, not
just an options change. The integration writes ``entry.data`` at runtime to
store a refreshed account token (#132), and reloading for that would tear down
every entity, drop the MQTT connection and re-fetch scenes — on a cadence set
by how often Govee expires a token.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee import _async_update_listener


def _entry(options, snapshot):
    coordinator = SimpleNamespace()
    if snapshot is not None:
        coordinator.options_snapshot = snapshot
    return SimpleNamespace(
        options=options, runtime_data=coordinator, entry_id="e1", title="Govee"
    )


def _hass():
    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    return hass


@pytest.mark.asyncio
async def test_data_only_write_does_not_reload():
    """A refreshed token writes entry.data; options are untouched."""
    hass = _hass()
    entry = _entry({"poll_interval": 60}, {"poll_interval": 60})

    await _async_update_listener(hass, entry)

    hass.config_entries.async_reload.assert_not_called()


@pytest.mark.asyncio
async def test_changed_options_still_reload():
    hass = _hass()
    entry = _entry({"poll_interval": 30}, {"poll_interval": 60})

    await _async_update_listener(hass, entry)

    hass.config_entries.async_reload.assert_awaited_once_with("e1")


@pytest.mark.asyncio
async def test_added_option_reloads():
    hass = _hass()
    entry = _entry({"poll_interval": 60, "enable_groups": True}, {"poll_interval": 60})

    await _async_update_listener(hass, entry)

    hass.config_entries.async_reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_snapshot_falls_back_to_reloading():
    """Without a snapshot we can't tell — reload rather than skip silently.

    Skipping would be the dangerous default: a real options change that never
    took effect is far more confusing than an extra reload.
    """
    hass = _hass()
    entry = _entry({"poll_interval": 60}, None)

    await _async_update_listener(hass, entry)

    hass.config_entries.async_reload.assert_awaited_once()
