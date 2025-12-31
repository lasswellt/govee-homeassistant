"""Light entity descriptions for Govee integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.light import LightEntityDescription


@dataclass(frozen=True, kw_only=True)
class GoveeLightEntityDescription(LightEntityDescription):
    """Describes a Govee light entity.

    Extends LightEntityDescription with Govee-specific configuration.
    This allows centralized entity configuration separate from entity logic.
    """


@dataclass(frozen=True, kw_only=True)
class GoveeSegmentLightDescription(GoveeLightEntityDescription):
    """Describes a Govee segment light entity.

    Used for individual RGBIC device segments.
    Segments are automatically created and enabled for RGBIC devices.
    Users can disable individual segments via the entity registry if desired.
    """

    key: str = "segment"
    translation_key: str = "segment"
    entity_registry_enabled_default: bool = True  # Enabled by default


# Light entity descriptions for Govee devices
LIGHT_DESCRIPTIONS: dict[str, GoveeLightEntityDescription] = {
    "main": GoveeLightEntityDescription(
        key="main",
        # No translation_key - inherits device name from DeviceInfo
        entity_registry_enabled_default=True,
    ),
}

# Segment light description (for Phase 4: Segment Control)
SEGMENT_LIGHT_DESCRIPTION = GoveeSegmentLightDescription()
