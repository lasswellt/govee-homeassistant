"""Select entity descriptions for Govee integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntityDescription


@dataclass(frozen=True, kw_only=True)
class GoveeSelectEntityDescription(SelectEntityDescription):
    """Describes a Govee select entity.

    Extends SelectEntityDescription with Govee-specific configuration.
    This allows centralized entity configuration separate from entity logic.
    """


# Select entity descriptions for Govee devices
# Keys match scene_type parameter: "dynamic" for regular scenes, "diy" for DIY scenes
SELECT_DESCRIPTIONS: dict[str, GoveeSelectEntityDescription] = {
    "dynamic": GoveeSelectEntityDescription(
        key="scene",
        translation_key="scene",
        entity_registry_enabled_default=True,
    ),
    "diy": GoveeSelectEntityDescription(
        key="diy_scene",
        translation_key="diy_scene",
        entity_registry_enabled_default=False,  # Disabled by default (opt-in)
    ),
}
