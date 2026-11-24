"""Device-registry lookups across Home Assistant versions (registry.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.govee import registry
from custom_components.govee.registry import (
    device_config_entry_ids,
    get_registry_device,
    hub_link_info,
    supports_via_device_id,
)

IDENTIFIER = ("govee", "AA:BB:CC:DD:EE:FF:00:11")


class TestGetRegistryDevice:
    def test_uses_the_per_entry_lookup_when_present(self):
        """Home Assistant 2026.10+: async_get_device (deprecated) is never called."""
        device = SimpleNamespace(config_entry_id="entry")
        registry = MagicMock(spec=["async_get_device_by_identifier", "async_get_device"])
        registry.async_get_device_by_identifier.return_value = device

        assert get_registry_device(registry, IDENTIFIER, "entry") is device
        registry.async_get_device_by_identifier.assert_called_once_with(IDENTIFIER, "entry")
        registry.async_get_device.assert_not_called()

    def test_falls_back_and_filters_by_entry_on_older_versions(self):
        device = SimpleNamespace(config_entries={"entry", "other"})
        registry = MagicMock(spec=["async_get_device"])
        registry.async_get_device.return_value = device

        assert get_registry_device(registry, IDENTIFIER, "entry") is device
        assert get_registry_device(registry, IDENTIFIER, "third") is None
        registry.async_get_device.assert_called_with(identifiers={IDENTIFIER})

    def test_fallback_without_a_device(self):
        registry = MagicMock(spec=["async_get_device"])
        registry.async_get_device.return_value = None

        assert get_registry_device(registry, IDENTIFIER, "entry") is None


class TestDeviceConfigEntryIds:
    def test_single_entry_devices(self):
        assert device_config_entry_ids(SimpleNamespace(config_entry_id="entry")) == {"entry"}

    def test_older_versions_read_the_set(self):
        assert device_config_entry_ids(SimpleNamespace(config_entries={"a", "b"})) == {"a", "b"}


class TestHubLinkInfo:
    def test_new_ha_links_by_registry_id(self, monkeypatch):
        monkeypatch.setattr(registry, "supports_via_device_id", lambda: True)
        fake = MagicMock(spec=["async_get_device_by_identifier"])
        fake.async_get_device_by_identifier.return_value = SimpleNamespace(id="hub-id")
        monkeypatch.setattr(registry.dr, "async_get", lambda hass: fake)

        assert hub_link_info(MagicMock(), IDENTIFIER, "entry") == {"via_device_id": "hub-id"}
        fake.async_get_device_by_identifier.assert_called_once_with(IDENTIFIER, "entry")

    def test_new_ha_without_registered_hub_links_nothing(self, monkeypatch):
        monkeypatch.setattr(registry, "supports_via_device_id", lambda: True)
        fake = MagicMock(spec=["async_get_device_by_identifier"])
        fake.async_get_device_by_identifier.return_value = None
        monkeypatch.setattr(registry.dr, "async_get", lambda hass: fake)

        assert hub_link_info(MagicMock(), IDENTIFIER, "entry") == {}

    def test_old_ha_keeps_via_device_without_a_lookup(self, monkeypatch):
        monkeypatch.setattr(registry, "supports_via_device_id", lambda: False)
        async_get = MagicMock()
        monkeypatch.setattr(registry.dr, "async_get", async_get)

        assert hub_link_info(MagicMock(), IDENTIFIER, "entry") == {"via_device": IDENTIFIER}
        async_get.assert_not_called()

    @pytest.mark.skipif(
        not hasattr(registry.dr.DeviceInfo, "__required_keys__"), reason="needs the TypedDict DeviceInfo"
    )
    def test_installed_ha_lacks_via_device_id(self):
        assert supports_via_device_id() is False
