"""Optional user labels for the H7175's custom slots.

The Developer API calls the custom slots only Custom 1-4; the names given in
the Govee app are in neither the API nor the pushes. Users can label them in
the options ("Kettle slot labels"). Labels are display text only: the mode
state stays ``custom_N`` (Home Assistant select options and water heater
modes must be the state values, and translations cannot carry per-user text),
so automations and dashboards keep working when a label changes. The labels
are exposed as a ``labels`` attribute (``{"custom_1": "Herbal tea", ...}``)
on the water heater and the Brew mode select.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..const import CONF_KETTLE_SLOT_LABELS
from .modes import KETTLE_MANUAL_MODE

if TYPE_CHECKING:
    from ..models import GoveeDevice

MAX_SLOT_LABEL_LENGTH = 40

ERROR_DUPLICATE_SLOT_LABEL = "duplicate_slot_label"
ERROR_RESERVED_SLOT_LABEL = "reserved_slot_label"
ERROR_SLOT_LABEL_TOO_LONG = "slot_label_too_long"


def slot_keys(device: GoveeDevice) -> list[str]:
    """The keys of the device's custom slots, in slot order."""
    return [str(opt["key"]) for opt in device.get_kettle_mode_options() if opt["slotted"]]


def validate_slot_labels(device: GoveeDevice, labels: Mapping[str, str]) -> dict[str, str]:
    """``{slot key: error key}`` for each unacceptable label; blank means none.

    A label may not repeat another label, a built-in mode's name, "Off" or
    "Manual" (ignoring case), or exceed MAX_SLOT_LABEL_LENGTH.
    """
    reserved = {"off", KETTLE_MANUAL_MODE}
    reserved |= {str(opt["name"]).casefold() for opt in device.get_kettle_mode_options() if not opt["slotted"]}
    errors: dict[str, str] = {}
    seen: dict[str, list[str]] = {}
    for key in slot_keys(device):
        label = str(labels.get(key) or "").strip()
        if not label:
            continue
        if len(label) > MAX_SLOT_LABEL_LENGTH:
            errors[key] = ERROR_SLOT_LABEL_TOO_LONG
        elif label.casefold() in reserved:
            errors[key] = ERROR_RESERVED_SLOT_LABEL
        else:
            seen.setdefault(label.casefold(), []).append(key)
    for keys in seen.values():
        if len(keys) > 1:
            errors.update(dict.fromkeys(keys, ERROR_DUPLICATE_SLOT_LABEL))
    return errors


def slot_labels(options: Mapping[str, Any], device: GoveeDevice) -> dict[str, str]:
    """The device's valid labels from the entry options, ``{slot key: label}``."""
    by_device = options.get(CONF_KETTLE_SLOT_LABELS)
    saved = by_device.get(device.device_id) if isinstance(by_device, dict) else None
    if not isinstance(saved, dict):
        return {}
    labels = {str(k): v.strip() for k, v in saved.items() if isinstance(v, str) and v.strip()}
    errors = validate_slot_labels(device, labels)
    return {key: labels[key] for key in slot_keys(device) if key in labels and key not in errors}
