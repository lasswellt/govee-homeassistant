"""H7175 brew modes: stable keys, the selected mode, and the custom-slot restore.

Modes come from the ``workMode`` capability (see
:meth:`GoveeDevice.get_kettle_mode_options`), keyed by a stable snake_case
state (``custom_1``..``custom_4``, ``green_tea``, ``oolong_tea``, ``coffee``,
``black_tea_boil``) that the entities translate. ``manual`` is added: the
kettle reports workMode 6, outside the capability, once its target is set
directly (inferred from live observation). Selecting it re-sends the current
target, which is what puts the kettle in workMode 6.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import UnitOfTemperature
from homeassistant.util.unit_conversion import TemperatureConverter

from ..models import TemperatureSettingCommand
from ..models.device import INSTANCE_SLIDER_TEMPERATURE
from .frames import KETTLE_MANUAL_WORK_MODE

if TYPE_CHECKING:
    from ..models import GoveeDevice, GoveeDeviceState

KETTLE_MANUAL_MODE = "manual"


def kettle_modes(device: GoveeDevice) -> dict[str, tuple[int, int]]:
    """Mode key -> (work_mode, mode_value), in capability order, then manual."""
    modes = {
        str(opt["key"]): (int(opt["work_mode"]), int(opt["mode_value"])) for opt in device.get_kettle_mode_options()
    }
    if modes and device.supports_kettle_temperature and all(wm != KETTLE_MANUAL_WORK_MODE for wm, _ in modes.values()):
        modes[KETTLE_MANUAL_MODE] = (KETTLE_MANUAL_WORK_MODE, 0)
    return modes


def is_slotted(modes: dict[str, tuple[int, int]], work_mode: int) -> bool:
    """Whether a workMode has several modes, told apart by modeValue."""
    return sum(1 for wm, _ in modes.values() if wm == work_mode) > 1


def selected_mode(modes: dict[str, tuple[int, int]], state: GoveeDeviceState | None) -> str | None:
    """The mode the kettle has selected, whether or not it is on.

    A workMode with one mode is identified by workMode alone. For one with
    slots the poll's modeValue wins, then the slot remembered from a push, a
    command or a restore. None when unknown, including a workMode that is not
    among the modes.
    """
    if state is None or state.work_mode is None:
        return None
    candidates = {key: value for key, (wm, value) in modes.items() if wm == state.work_mode}
    if len(candidates) == 1:
        return next(iter(candidates))
    for known in (state.mode_value, state.kettle_mode_value):
        for key, value in candidates.items():
            if known is not None and value == known:
                return key
    return None


def mode_key(modes: dict[str, tuple[int, int]], work_mode: int, mode_value: int) -> str | None:
    """The key of a (work_mode, mode_value), if it is one of the modes."""
    return next((key for key, value in modes.items() if value == (work_mode, mode_value)), None)


def manual_command(target: float, fahrenheit: bool) -> TemperatureSettingCommand:
    """The command that selects manual: ``target`` (in the kettle's unit) re-sent."""
    return TemperatureSettingCommand(
        temperature=int(round(target)),
        unit="Fahrenheit" if fahrenheit else "Celsius",
        auto_stop=None,
        setting_instance=INSTANCE_SLIDER_TEMPERATURE,
    )


def to_kettle_unit(temperature: float, from_unit: str, fahrenheit: bool) -> float:
    """``temperature`` in ``from_unit`` converted to the kettle's unit."""
    to_unit = UnitOfTemperature.FAHRENHEIT if fahrenheit else UnitOfTemperature.CELSIUS
    return round(TemperatureConverter.convert(temperature, from_unit, to_unit), 1)


def slot_restore_data(modes: dict[str, tuple[int, int]], state: GoveeDeviceState | None) -> dict[str, int] | None:
    """The selected custom slot as restore data; None for anything but a slot."""
    key = selected_mode(modes, state)
    if key is None or not is_slotted(modes, modes[key][0]):
        return None
    work_mode, mode_value = modes[key]
    return {"work_mode": work_mode, "mode_value": mode_value}


def restore_slot(modes: dict[str, tuple[int, int]], state: GoveeDeviceState, data: dict[str, Any]) -> None:
    """Remember a restored custom slot when nothing fresher is known.

    After a restart a kettle in a custom slot is reported as ``{"workMode":
    1}`` until it pushes, which API-only setups never see.
    """
    work_mode, mode_value = data.get("work_mode"), data.get("mode_value")
    if state.kettle_mode_value is not None or not isinstance(work_mode, int) or not isinstance(mode_value, int):
        return
    if (
        (work_mode, mode_value) in modes.values()
        and is_slotted(modes, work_mode)
        and state.work_mode in (None, work_mode)
    ):
        state.kettle_mode_value = mode_value
