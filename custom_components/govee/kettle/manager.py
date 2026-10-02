"""Coordinator-owned handling of the H7175 kettle's AWS IoT pushes.

The H7175 pushes its water and target temperature under ``sta`` and as
BLE-format frames (:mod:`.frames`). Without decoding them a push moved only
``onOff`` yet still marked the kettle locally fresh, so cloud polls were
skipped and the temperature went stale.

The push carries no unit. Until a poll has told us the kettle's unit, pushed
temperatures are withheld (a °F reading stored as °C showed 187 and 349 on a
real kettle), and that poll is never skipped as locally fresh.

Only kettles in ``KETTLE_FRAME_SKUS`` are handled here; any other kettle's
push is handled exactly as before.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..const import (
    CONF_API_TEMPERATURE_UNIT,
    DEFAULT_API_TEMPERATURE_UNIT,
    FAHRENHEIT_REPORTING_SKUS,
    resolve_fahrenheit_conversion,
)
from .frames import decode_kettle_frames

if TYPE_CHECKING:
    from ..coordinator import GoveeCoordinator
    from ..models import GoveeDeviceState

_LOGGER = logging.getLogger(__name__)


def _sta_value(sta: dict[str, Any], key: str) -> float | None:
    """A ``sta`` temperature (hundredths) as degrees, None when absent or not numeric."""
    raw = sta.get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return raw / 100.0


class KettleManager:
    """Push decoding, unit decisions and poll rules for H7175 kettles."""

    def __init__(self, coordinator: GoveeCoordinator) -> None:
        """Initialize for a coordinator."""
        self._coordinator = coordinator

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

    def on_push(self, device_id: str, state: GoveeDeviceState, data: dict[str, Any], frames: list[bytes]) -> None:
        """Apply a push's temperatures (``sta``, then ``aa 10`` as a fallback)."""
        if not self.unit_known(device_id):
            _LOGGER.debug("Withholding pushed temperatures for %s until its unit is known", device_id)
            return
        fahrenheit = self.reports_fahrenheit(device_id)
        report = decode_kettle_frames(frames)
        sta = data.get("sta")
        sta = sta if isinstance(sta, dict) else {}
        current = _sta_value(sta, "curTem")
        if current is None and fahrenheit:
            # aa 10 is always °F, so it is used only on a kettle that reports °F.
            current = report.current_temperature
        if current is not None:
            state.sensor_temperature = current
        target = _sta_value(sta, "setTem")
        if target is not None:
            state.kettle_target_temperature = target

    @staticmethod
    def merge_poll(existing: GoveeDeviceState, polled: GoveeDeviceState) -> None:
        """Carry over what a cloud poll did not report."""
        if polled.kettle_target_temperature is None:
            polled.kettle_target_temperature = existing.kettle_target_temperature
