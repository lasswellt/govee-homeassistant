from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntityDescription


@dataclass(frozen=True, kw_only=True)
class GoveeSelectEntityDescription(SelectEntityDescription):
    """Describes a Govee select entity."""


SELECT_DESCRIPTIONS: dict[str, GoveeSelectEntityDescription] = {
    "dynamic": GoveeSelectEntityDescription(
        key="scene",
        translation_key="scene",
        entity_registry_enabled_default=True,
    ),
    "diy": GoveeSelectEntityDescription(
        key="diy_scene",
        translation_key="diy_scene",
        entity_registry_enabled_default=False,
    ),
    "snapshot": GoveeSelectEntityDescription(
        key="snapshot_scene",
        translation_key="snapshot_scene",
        entity_registry_enabled_default=False,
    ),
}
