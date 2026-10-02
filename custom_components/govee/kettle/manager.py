"""Coordinator-owned handling of the H7175 kettle's AWS IoT pushes.

The H7175 pushes its water and target temperature under ``sta`` and its
mode, preset temperatures, heating status and keep warm as BLE-format frames
(:mod:`.frames`). Without decoding them a push moved only ``onOff`` yet still
marked the kettle locally fresh, so cloud polls were skipped and the
temperature went stale.

The push carries no unit. Until a poll has told us the kettle's unit, pushed
temperatures are withheld (a °F reading stored as °C showed 187 and 349 on a
real kettle), and that poll is never skipped as locally fresh.

Only kettles in ``KETTLE_FRAME_SKUS`` are handled here; any other kettle's
push is handled exactly as before.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Any

from homeassistant.const import UnitOfTemperature
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_API_TEMPERATURE_UNIT,
    DEFAULT_API_TEMPERATURE_UNIT,
    FAHRENHEIT_REPORTING_SKUS,
    KETTLE_FRAME_HISTORY,
    resolve_fahrenheit_conversion,
)
from ..models import TemperatureSettingCommand, WorkModeCommand
from .frames import COMMAND_PREFIX, KETTLE_MANUAL_WORK_MODE, KettleFrameReport, decode_kettle_frames
from .modes import kettle_modes, to_kettle_unit

if TYPE_CHECKING:
    from ..coordinator import GoveeCoordinator
    from ..models import GoveeDeviceState

_LOGGER = logging.getLogger(__name__)

# State fields only a push sets; a poll result carries them over.
_PUSH_ONLY_FIELDS = (
    "kettle_diy_slot",
    "kettle_heating_status",
    "kettle_keep_warm_enabled",
    "kettle_keep_warm_minutes",
    "kettle_keep_warm_remaining",
)


def _sta_value(sta: dict[str, Any], key: str) -> float | None:
    """A ``sta`` temperature (hundredths) as degrees, None when absent or not numeric."""
    raw = sta.get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return raw / 100.0


def _diy_slot(previous: int | None, slot_flags: dict[int, bool]) -> int | None:
    """The DIY slot after a push's slot pages (``{slot: flag set}``).

    A push may carry one page only, so a known DIY slot changes only when a
    slot in this push is clear (it becomes the DIY slot, if it is the only
    clear one) or the known one is now flagged.
    """
    clear = [slot for slot, flagged in slot_flags.items() if not flagged]
    if clear:
        return clear[0] if len(clear) == 1 else None
    if previous is not None and slot_flags.get(previous):
        return None
    return previous


class KettleManager:
    """Push decoding, unit decisions and poll rules for H7175 kettles."""

    def __init__(self, coordinator: GoveeCoordinator) -> None:
        """Initialize for a coordinator."""
        self._coordinator = coordinator
        # Diagnostics: recent distinct status frames, command echoes apart so
        # a heating kettle's changing temperature frames cannot flush them.
        self._frames: dict[str, deque[dict[str, str]]] = {}
        self._command_frames: dict[str, deque[dict[str, str]]] = {}
        self._last_frame: dict[str, dict[bytes, bytes]] = {}
        # workModes outside a kettle's modes, logged once each.
        self._unlisted_logged: set[tuple[str, int]] = set()

    # ------------------------------------------------------------------ #
    # Units
    # ------------------------------------------------------------------ #

    def _api_unit(self) -> str:
        entry = self._coordinator.config_entry
        if entry is None:
            return DEFAULT_API_TEMPERATURE_UNIT
        return str(entry.options.get(CONF_API_TEMPERATURE_UNIT, DEFAULT_API_TEMPERATURE_UNIT))

    def _declared_unit(self, device_id: str) -> str | None:
        state = self._coordinator.get_state(device_id)
        return state.device_temperature_unit if state is not None else None

    def reports_fahrenheit(self, device_id: str) -> bool:
        """Whether the kettle's raw temperatures are in °F.

        The unit the kettle declares in ``sliderTemperature`` wins. The
        integration's API-unit option exists for thermometers that report
        no unit, so it only applies while the kettle has declared none; then
        come the account's fahOpen hint and the Fahrenheit SKU list.
        """
        declared = self._declared_unit(device_id)
        if declared is not None:
            return declared.lower() == "fahrenheit"
        device = self._coordinator.devices.get(device_id)
        sku = device.sku if device is not None else ""
        return resolve_fahrenheit_conversion(
            sku, self._api_unit(), self._coordinator.account_temperature_unit(device_id)
        )

    def unit_known(self, device_id: str) -> bool:
        """Whether :meth:`reports_fahrenheit` decides on evidence rather than a guess."""
        if self._declared_unit(device_id) is not None or self._api_unit() != "auto":
            return True
        if self._coordinator.account_temperature_unit(device_id) is not None:
            return True
        device = self._coordinator.devices.get(device_id)
        return device is not None and device.sku.upper() in FAHRENHEIT_REPORTING_SKUS

    def must_poll(self, device_id: str) -> bool:
        """Whether this cycle's cloud read must not be skipped as locally fresh."""
        return not self.unit_known(device_id)

    # ------------------------------------------------------------------ #
    # Pushes and polls
    # ------------------------------------------------------------------ #

    def on_push(
        self, device_id: str, state: GoveeDeviceState, data: dict[str, Any], frames: list[bytes]
    ) -> KettleFrameReport:
        """Apply a push: mode, heating, keep warm and DIY slot, then temperatures."""
        report = decode_kettle_frames(frames)
        self._record_frames(device_id, frames)
        if report.work_mode is not None:
            state.work_mode = report.work_mode
            state.mode_value = report.mode_value
            state.kettle_mode_value = report.mode_value
            self._check_work_mode(device_id, report.work_mode)
        if report.heating_seen:
            state.kettle_heating_status = report.heating_status
        if report.keep_warm is not None:
            state.kettle_keep_warm_enabled, state.kettle_keep_warm_minutes = report.keep_warm
        if report.keep_warm_remaining is not None:
            state.kettle_keep_warm_remaining = report.keep_warm_remaining
        if report.slot_flags:
            state.kettle_diy_slot = _diy_slot(state.kettle_diy_slot, report.slot_flags)
        if not self.unit_known(device_id):
            _LOGGER.debug("Withholding pushed temperatures for %s until its unit is known", device_id)
            return report
        fahrenheit = self.reports_fahrenheit(device_id)
        if report.preset_temperatures:
            merged = {wm: dict(slots) for wm, slots in state.kettle_preset_temperatures.items()}
            for wm, slots in report.preset_temperatures.items():
                merged.setdefault(wm, {}).update(slots)
            state.kettle_preset_temperatures = merged
        sta = data.get("sta")
        sta = sta if isinstance(sta, dict) else {}
        current = _sta_value(sta, "curTem")
        if current is None and fahrenheit:
            # aa 10 is always °F, so it is used only on a kettle that reports °F.
            current = report.current_temperature
        if current is not None:
            state.sensor_temperature = current
        target = _sta_value(sta, "setTem")
        if target is None:
            target = report.manual_target
        if target is not None:
            state.kettle_target_temperature = target
        return report

    def merge_poll(self, device_id: str, existing: GoveeDeviceState, polled: GoveeDeviceState) -> None:
        """Carry over what a cloud poll does not report."""
        if polled.kettle_target_temperature is None:
            polled.kettle_target_temperature = existing.kettle_target_temperature
        # The remembered slot belongs to the workMode it was seen in.
        if polled.kettle_mode_value is None and polled.work_mode in (None, existing.work_mode):
            polled.kettle_mode_value = existing.kettle_mode_value
        if not polled.kettle_preset_temperatures:
            polled.kettle_preset_temperatures = existing.kettle_preset_temperatures
        for name in _PUSH_ONLY_FIELDS:
            if getattr(polled, name) is None:
                setattr(polled, name, getattr(existing, name))
        if polled.work_mode is not None:
            self._check_work_mode(device_id, polled.work_mode)

    def apply_command(
        self, device_id: str, state: GoveeDeviceState, command: TemperatureSettingCommand | WorkModeCommand
    ) -> None:
        """Apply a target or mode command optimistically."""
        if isinstance(command, WorkModeCommand):
            state.apply_optimistic_work_mode(command.work_mode, command.mode_value)
            state.kettle_mode_value = command.mode_value
            return
        unit = UnitOfTemperature.FAHRENHEIT if command.unit.lower() == "fahrenheit" else UnitOfTemperature.CELSIUS
        target = to_kettle_unit(command.temperature, unit, self.reports_fahrenheit(device_id))
        # Setting the target directly puts the kettle in workMode 6 (manual).
        state.apply_optimistic_work_mode(KETTLE_MANUAL_WORK_MODE, 0)
        state.kettle_target_temperature = target
        state.kettle_mode_value = None

    def _check_work_mode(self, device_id: str, work_mode: int) -> None:
        """Log, once, a workMode that is none of the kettle's modes (shown as unknown)."""
        device = self._coordinator.devices.get(device_id)
        if device is None or (device_id, work_mode) in self._unlisted_logged:
            return
        if all(wm != work_mode for wm, _ in kettle_modes(device).values()):
            self._unlisted_logged.add((device_id, work_mode))
            _LOGGER.debug("Kettle %s reports workMode %s, which is not one of its modes", device_id, work_mode)

    # ------------------------------------------------------------------ #
    # Diagnostics
    # ------------------------------------------------------------------ #

    def _record_frames(self, device_id: str, frames: list[bytes]) -> None:
        """Keep distinct status frames (per kind) and every command echo."""
        statuses = self._frames.setdefault(device_id, deque(maxlen=KETTLE_FRAME_HISTORY))
        commands = self._command_frames.setdefault(device_id, deque(maxlen=KETTLE_FRAME_HISTORY))
        last = self._last_frame.setdefault(device_id, {})
        now = dt_util.utcnow().isoformat()
        for frame in frames:
            if frame[:1] == bytes([COMMAND_PREFIX]):
                commands.append({"at": now, "frame": frame.hex()})
                continue
            # The custom-slot pages (aa 05 01 <page>) are told apart by page.
            kind = bytes(frame[:4]) if frame[:3] == b"\xaa\x05\x01" else bytes(frame[:3])
            if last.get(kind) != frame:
                last[kind] = bytes(frame)
                statuses.append({"at": now, "frame": frame.hex()})

    def recent_frames(self, device_id: str) -> list[dict[str, str]]:
        """Recent distinct pushed frames, oldest first."""
        merged = [*self._frames.get(device_id, ()), *self._command_frames.get(device_id, ())]
        return sorted(merged, key=lambda entry: entry["at"])
