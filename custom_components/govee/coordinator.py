"""DataUpdateCoordinator for Govee integration.

Manages device discovery, state polling, and MQTT integration.
Implements IStateProvider protocol for clean architecture.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    GoveeApiClient,
    GoveeApiError,
    GoveeAuthError,
    GoveeAwsIotClient,
    GoveeDeviceNotFoundError,
    GoveeIotCredentials,
    GoveeRateLimitError,
)
from .api.auth import GoveeAuthClient
from .api.ble_packet import DIY_STYLE_NAMES
from .ble_passthrough import BlePassthroughManager

from homeassistant.helpers.event import async_call_later

# BLE direct support — conditionally imported to avoid hard Bluetooth dependency
try:
    from .api.ble import GoveeBLEDevice  # noqa: F401  (used in __init__)

    HAS_BLUETOOTH = True
except ImportError:  # pragma: no cover — HA installs without Bluetooth
    HAS_BLUETOOTH = False
    GoveeBLEDevice = None  # type: ignore[assignment,misc]

from .ble_advertisement import BleAdvertisementHandler
from .ble_advertisement import sku_from_ble_name as _sku_from_ble_name  # noqa: F401
from .api.mqtt_control import command_to_mqtt
from .api.lan import (
    LanTargetError,
    async_get_lan_broadcast_addresses,
    async_get_lan_interface_ips,
    async_scan_lan_devices,
    expand_lan_targets,
)
from .api.lan_client import (
    GoveeLanClient,
    LanDevStatus,
    LanDeviceInfo,
    correlate_scan,
)
from .api.lan_control import command_to_lan, lan_brightness_to_device
from .const import (
    CONF_ENABLE_MQTT_CONTROL,
    CONF_LAN_TARGETS,
    DEFAULT_ENABLE_MQTT_CONTROL,
    DEVICE_REDISCOVERY_INTERVAL,
    DOMAIN,
    LAN_CORRELATION_TTL_SECONDS,
    LAN_READ_MISS_DEMOTE_THRESHOLD,
    LAN_RESCAN_INTERVAL,
    LAN_WRITE_CONFIRM_TIMEOUT,
    LAN_WRITE_SUPPRESS_SECONDS,
    LAN_WRITE_SUPPRESS_THRESHOLD,
    OPTIMISTIC_GRACE_CAP_SECONDS,
)
from .models import (
    GoveeDevice,
    GoveeDeviceState,
    RGBColor,
    TransportHealth,
    TransportKind,
)
from .models.transport import TRANSPORT_KINDS
from .transport_health import TransportHealthTracker
from .models.commands import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    DeviceCommand,
    DIYSceneCommand,
    ModeCommand,
    MusicModeCommand,
    PowerCommand,
    SceneCommand,
    SegmentColorCommand,
    TemperatureSettingCommand,
    ToggleCommand,
    WorkModeCommand,
    create_dreamview_command,
)
from .models.device import (
    INSTANCE_DREAMVIEW,
    INSTANCE_HDMI_SOURCE,
    INSTANCE_THERMOSTAT_TOGGLE,
    MAINS_POWERED_BATTERY_SKUS,
    MAINS_POWERED_DEVICE_TYPES,
)
from .models.device import GoveeLeakSensor, GoveeLeakSensorState
from .scene_cache import SceneCacheManager
from .repairs import (
    async_create_auth_issue,
    async_create_mqtt_issue,
    async_create_rate_limit_issue,
    async_delete_auth_issue,
    async_delete_mqtt_issue,
    async_delete_rate_limit_issue,
)

_LOGGER = logging.getLogger(__name__)

# State fetch timeout per device
STATE_FETCH_TIMEOUT = 30

# Segment command pacing — Govee silently rate-limits bursts of segment
# updates on RGBIC strips (H80A1 has 14 segments). Serialize per-device
# with a small gap so a "scene" that hits every segment doesn't drop
# commands with empty JSON responses (issue #53).
SEGMENT_COMMAND_PACING_SECONDS = 0.12

# Tolerance (device-native brightness units) for confirming a LAN brightness
# write by reading the device back. A LAN devStatus reports brightness on the
# coarse 0-100 scale, so rescaling it into a wider device range (and the
# round-trip back) can drift by a unit or two; ±2 absorbs that without masking
# a genuinely wrong value (issue #57).
LAN_BRIGHTNESS_CONFIRM_TOLERANCE = 2

# BFF polling interval for leak sensor state (seconds)
BFF_POLL_INTERVAL = 300  # 5 minutes

# Standalone water-detector (H5054) leak-poll interval (seconds). These RF-only
# sensors deliver their trip only via the account warnMessage history (issue
# #62); a leak surfaces with up to this much latency. Kept conservative because
# the account API's rate limit is unverified (homebridge issue #543).
WATER_DETECTOR_POLL_INTERVAL = 120  # 2 minutes


@dataclasses.dataclass
class _LanReadOverlay:
    """Device-native LAN ``devStatus`` shaped for ``update_from_lan`` (issue #57).

    The parsed :class:`LanDevStatus` carries brightness on the LAN 0-100 scale
    (``brightness_0_100``), but :meth:`GoveeDeviceState.update_from_lan` expects
    DEVICE-NATIVE brightness under ``brightness`` (the ``LanDevStatusLike``
    protocol). This tiny adapter holds the rescaled value so the coordinator
    rescales exactly once, before the overlay, as the design requires. It is a
    plain (mutable) dataclass so its attributes are settable, satisfying the
    ``LanDevStatusLike`` protocol's read-write members.
    """

    on: bool | None
    brightness: int | None
    color: RGBColor | None
    color_temp_kelvin: int | None


class GoveeCoordinator(DataUpdateCoordinator[dict[str, GoveeDeviceState]]):
    """Coordinator for Govee device state management.

    Features:
    - Parallel state fetching for all devices
    - MQTT integration for real-time updates
    - Scene caching
    - Optimistic state updates
    - Group device handling

    Implements IStateProvider protocol for entities.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api_client: GoveeApiClient,
        iot_credentials: GoveeIotCredentials | None,
        poll_interval: int,
        enable_groups: bool = False,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: Home Assistant instance.
            config_entry: Config entry for this integration.
            api_client: Govee REST API client.
            iot_credentials: Optional IoT credentials for MQTT.
            poll_interval: Polling interval in seconds.
            enable_groups: Whether to include group devices.
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
            # _async_update_data mutates and returns the same self._states dict
            # every poll. With always_update=False, HA's refresh gate compares
            # previous_data != self.data by identity (same object) and never
            # fires listeners after the first poll, so poll-only devices (BLE
            # thermometers like H5109 with no MQTT push) freeze until reload.
            # MQTT devices are masked via async_set_updated_data. (fixes #93)
            always_update=True,
        )

        self._config_entry = config_entry
        self._api_client = api_client
        self._iot_credentials = iot_credentials
        self._enable_groups = enable_groups
        # Opt-in: route power/brightness/color over MQTT instead of REST.
        self._enable_mqtt_control = config_entry.options.get(
            CONF_ENABLE_MQTT_CONTROL, DEFAULT_ENABLE_MQTT_CONTROL
        )

        # Device registry
        self._devices: dict[str, GoveeDevice] = {}

        # State cache
        self._states: dict[str, GoveeDeviceState] = {}

        # Per-device transport health: extracted to TransportHealthTracker
        # (audit H1). Drives optional connectivity binary_sensors + diagnostic
        # transport attributes.
        self._transport = TransportHealthTracker()

        # Per-device asyncio.Lock for serializing segment commands. Govee
        # rate-limits parallel segment dispatch with empty-body 200s; the
        # lock keeps one in flight at a time, paced by
        # SEGMENT_COMMAND_PACING_SECONDS to stay under the 100/min cap.
        self._segment_locks: dict[str, asyncio.Lock] = {}

        # Scene cache manager
        self._scene_cache = SceneCacheManager(api_client)

        # Observers for state changes

        # MQTT client for real-time updates
        self._mqtt_client: GoveeAwsIotClient | None = None

        # Device-specific MQTT topics from undocumented API
        # Maps device_id -> MQTT topic for publishing commands
        self._device_topics: dict[str, str] = {}

        # BLE passthrough manager for MQTT-based commands
        self._ble_manager = BlePassthroughManager(
            get_mqtt_client=lambda: self._mqtt_client,
            device_topics=self._device_topics,
            ensure_device_topic=self._ensure_device_topic,
        )

        # BLE direct transport — per-device GoveeBLEDevice instances
        # populated dynamically from Bluetooth advertisements.
        self._ble_devices: dict[str, GoveeBLEDevice] = {} if HAS_BLUETOOTH else {}

        # SKUs for which we've already logged "advertised but not on the
        # BLE command allowlist" — avoid spamming the log on every advert.
        self._ble_ignored_skus_logged: set[str] = set()

        # BLE advertisement subscription + correlation handler (audit H1).
        self._ble_handler = BleAdvertisementHandler(self)

        # Track in-flight power-off commands so segment entities can
        # avoid racing with a concurrent device power-off (issue #16).
        self._pending_power_off: set[str] = set()

        # Track rate limit state to avoid spamming repair issues
        self._rate_limited: bool = False

        # Store original poll interval for restoring after rate limit backoff
        self._original_update_interval = timedelta(seconds=poll_interval)

        # Developer-API thermometers whose live reading we also pull from the
        # BFF device list (e.g. H5110/H5075 via H5151, H5179). The BFF call
        # tickles Govee's cloud into refreshing + carries the live value (#83).
        self._thermo_bff_devices: set[str] = set()
        # Set once a reload has been scheduled to pick up newly-added BFF leak
        # sensors, so the periodic poll doesn't queue a reload every tick while
        # the reload is pending (issue #101).
        self._bff_reload_scheduled = False
        # Leak sensor subsystem
        self._leak_sensors: dict[str, GoveeLeakSensor] = {}
        self._leak_states: dict[str, GoveeLeakSensorState] = {}
        # BFF-discovered thermo-hygrometers (H5301, issue #86): synthesized into
        # self._devices but absent from the Developer API, so their state is
        # owned by the BFF poll, not /device/state. Tracked here so
        # _fetch_device_state skips the (futile) developer poll for them.
        self._bff_thermometer_ids: set[str] = set()
        # Gateway hubs (e.g. H5044) bridging BFF thermo-hygrometers, keyed by
        # hub_device_id -> {"sku"}. Registered as HA devices so via_device on
        # the thermo entities resolves (#86).
        self._bff_thermo_hubs: dict[str, dict[str, str]] = {}
        # When a device's temp/humidity reading last *changed* — backs the
        # "Last Reading" diagnostic timestamp sensor (#83). Cloud batches
        # BLE-bridged sensors every 15-60 min; this exposes when a new value
        # actually landed.
        self._sensor_reading_changed_at: dict[str, datetime] = {}
        self._leak_hubs: dict[str, dict[str, Any]] = {}
        self._sno_to_sensor_id: dict[tuple[str, int], str] = {}
        # Last time the account device list was re-checked for newly added
        # devices (#101). Seeded to "now" so the first re-check waits a full
        # interval rather than firing right after setup discovery.
        self._last_rediscovery_check: float = time.monotonic()
        # Per-device queued button presses (supports multiple presses per tick)
        self._pending_button_presses: dict[str, int] = {}
        self._bff_poll_unsub: CALLBACK_TYPE | None = None
        self._bff_poll_task: asyncio.Task[None] | None = None
        # Standalone water-detector (H5054) leak polling (issue #62).
        self._wd_poll_unsub: CALLBACK_TYPE | None = None
        # Last seen lastTime per detector — warnMessage is only called when the
        # device has freshly reported (or is currently wet), keeping the account
        # API request count low.
        self._water_leak_last_time: dict[str, int] = {}
        # PII-free census of the last BFF device-list response (#87 diagnostics):
        # which SKUs the BFF returned and whether they carry leak-discovery
        # fields. Empty until the first _discover_leak_sensors() call.
        self._bff_device_census: list[dict[str, Any]] = []
        # PII-free structural skeleton of the raw BFF response — distinguishes
        # "leak sensors absent" from "present under an unexpected shape" (#87).
        self._bff_response_skeleton: Any = None
        # Redacted per-device BFF scalar values (deviceSettings + lastDeviceData)
        # — reveals which fields the BFF carries per device (battery, signal,
        # temp/humidity, air-quality) for SKUs beyond leak+thermo sensors (#114).
        self._bff_device_values: list[dict[str, Any]] = []

        # LAN (UDP) transport (issue #57). Auto-enabled when a device answers
        # the discovery scan AND its scan MAC correlates to a known device_id;
        # there is no enable toggle. ``_lan_client`` stays None when LAN is
        # disabled, degraded, or nothing correlated — every LAN path keys off it.
        self._lan_client: GoveeLanClient | None = None
        # Correlated LAN devices keyed by coordinator device_id.
        self._lan_devices: dict[str, LanDeviceInfo] = {}
        # Scan records that matched no device_id (counted for diagnostics).
        self._lan_unmatched: list[Mapping[str, Any]] = []
        # Consecutive solicited-read misses per device, for demotion (LAN-011).
        self._lan_read_misses: dict[str, int] = {}
        # Consecutive LAN write-confirm failures (value mismatch / no reply) per
        # device. Drives WRITE suppression only — never transport health, which
        # is read-driven — so a device that reports a write late never flaps the
        # lan_connectivity sensor off (#57, the value_mismatch flap).
        self._lan_write_misses: dict[str, int] = {}
        # Monotonic deadline until which LAN write *attempts* are skipped for a
        # device whose recent writes did not confirm. Reads keep using LAN (the
        # sensor stays Connected); writes fall straight to MQTT/REST without
        # paying the verify-by-read confirm timeout each time (#57).
        self._lan_write_suppressed_until: dict[str, float] = {}
        # Monotonic timestamp of the last LAN rescan, for throttling (LAN-011).
        self._last_lan_rescan: float = 0.0

    @property
    def devices(self) -> dict[str, GoveeDevice]:
        """Get all discovered devices."""
        return self._devices

    @property
    def api_rate_limit_remaining(self) -> int:
        """Return API rate limit remaining."""
        return self._api_client.rate_limit_remaining

    @property
    def api_rate_limit_total(self) -> int:
        """Return API rate limit total."""
        return self._api_client.rate_limit_total

    @property
    def api_rate_limit_reset(self) -> int:
        """Return API rate limit reset time."""
        return self._api_client.rate_limit_reset

    @property
    def mqtt_client(self) -> GoveeAwsIotClient | None:
        """Return MQTT client instance."""
        return self._mqtt_client

    @property
    def api_client(self) -> GoveeApiClient:
        """Return the REST API client (diagnostics reads its raw captures)."""
        return self._api_client

    @property
    def scene_cache_count(self) -> int:
        """Return number of devices with cached scenes."""
        return self._scene_cache.scene_cache_count

    @property
    def diy_scene_cache_count(self) -> int:
        """Return number of devices with cached DIY scenes."""
        return self._scene_cache.diy_scene_cache_count

    @property
    def mqtt_connected(self) -> bool:
        """Return True if MQTT client is connected."""
        return self._mqtt_client is not None and self._mqtt_client.connected

    @property
    def mqtt_last_message_ts(self) -> datetime | None:
        """UTC timestamp of the last inbound MQTT state message, or None."""
        if self._mqtt_client is None:
            return None
        return self._mqtt_client.last_message_ts

    def mqtt_last_receive_for(self, device_id: str) -> datetime | None:
        """UTC timestamp of the last inbound MQTT message for a device, or None."""
        if self._mqtt_client is None:
            return None
        return self._mqtt_client.last_message_ts_for(device_id)

    def is_ble_available(self, device_id: str) -> bool:
        """Return True if a BLE transport is active for this device."""
        return device_id in self._ble_devices

    # ------------------------------------------------------------------ #
    # Transport health (per-device connectivity tracking)
    # ------------------------------------------------------------------ #

    def _ensure_transport_health(self, device_id: str) -> None:
        """Initialize transport-health entries for a device if missing."""
        self._transport.ensure(device_id)

    def get_transport_health(
        self,
        device_id: str,
        transport: TransportKind,
    ) -> TransportHealth | None:
        """Return health for (device, transport), or None if untracked."""
        return self._transport.get(device_id, transport)

    def _record_transport_success(
        self,
        device_id: str,
        transport: TransportKind,
    ) -> None:
        """Stamp a successful inbound transport use (data received)."""
        self._transport.record_success(device_id, transport)

    def _record_transport_send(
        self,
        device_id: str,
        transport: TransportKind,
    ) -> None:
        """Stamp a successful outbound transport use (command sent)."""
        self._transport.record_send(device_id, transport)

    def _record_transport_failure(
        self,
        device_id: str,
        transport: TransportKind,
        reason: str,
    ) -> None:
        """Stamp a failed transport use."""
        self._transport.record_failure(device_id, transport, reason)

    def _note_lan_write_miss(self, device_id: str) -> None:
        """Account a LAN write-confirm failure; suppress writes if persistent (#57).

        A verify-by-read failure — the device answered with a different value, or
        did not answer within ``LAN_WRITE_CONFIRM_TIMEOUT`` — does NOT mean LAN is
        down. The UDP datagram was sent; on a value mismatch a ``devStatus`` even
        came back, which is direct proof the transport is alive. It almost always
        means a freshly-powered controller is reporting its pre-command state
        late. So this NEVER touches transport health (health is read-driven: the
        solicited poll read and unsolicited pushes own it). Flipping the ``lan``
        transport unavailable on confirm misses is exactly the flap users
        reported — the lan_connectivity sensor dropping to Disconnected the
        instant a device was turned on.

        Instead it counts consecutive write-confirm failures and, after
        ``LAN_WRITE_SUPPRESS_THRESHOLD`` of them, suppresses LAN write *attempts*
        for ``LAN_WRITE_SUPPRESS_SECONDS`` so the device's writes fall straight to
        MQTT/REST without each paying the confirm timeout — while LAN reads keep
        working and the sensor stays Connected. A confirmed write
        (:meth:`_clear_lan_write_misses`) or the cooldown expiry re-arms LAN
        writes, so a device that begins confirming again is picked back up. (A
        ``send_failed`` OSError remains a hard, immediate transport failure — that
        path is genuine LAN unreachability, not a confirm race.)
        """
        misses = self._lan_write_misses.get(device_id, 0) + 1
        self._lan_write_misses[device_id] = misses
        if misses >= LAN_WRITE_SUPPRESS_THRESHOLD:
            self._lan_write_suppressed_until[device_id] = (
                time.monotonic() + LAN_WRITE_SUPPRESS_SECONDS
            )
            _LOGGER.debug(
                "Govee LAN: suppressing LAN write attempts for %s after %d "
                "unconfirmed writes — writes use MQTT/REST for %ds; LAN reads "
                "and the connectivity sensor are unaffected",
                device_id,
                misses,
                LAN_WRITE_SUPPRESS_SECONDS,
            )

    def _clear_lan_write_misses(self, device_id: str) -> None:
        """Re-arm LAN writes for a device (confirmed write): reset streak + cooldown."""
        self._lan_write_misses.pop(device_id, None)
        self._lan_write_suppressed_until.pop(device_id, None)

    def _lan_writes_suppressed(self, device_id: str) -> bool:
        """Whether LAN write *attempts* are currently in cooldown for a device (#57).

        Returns ``True`` while the device is inside its post-failure cooldown, so
        :meth:`_try_lan_command` skips the LAN write (and its confirm-read wait)
        and falls straight to MQTT/REST. When the cooldown has elapsed it re-arms
        the device (clearing the streak) and returns ``False`` so the next command
        probes the LAN write path again. Reads are never affected.
        """
        deadline = self._lan_write_suppressed_until.get(device_id)
        if deadline is None:
            return False
        if time.monotonic() >= deadline:
            self._clear_lan_write_misses(device_id)
            return False
        return True

    def lan_write_suppressed(self, device_id: str) -> bool:
        """Read-only view of LAN write suppression for diagnostics (issue #57).

        Unlike :meth:`_lan_writes_suppressed` this never re-arms an expired
        cooldown, so a diagnostics download cannot mutate runtime state. ``True``
        means LAN reads are healthy but the device's writes are currently routed
        to MQTT/REST because recent LAN writes did not confirm.
        """
        deadline = self._lan_write_suppressed_until.get(device_id)
        return deadline is not None and time.monotonic() < deadline

    def _refresh_mqtt_health(self) -> None:
        """Propagate MQTT client connection state to per-device health."""
        self._transport.refresh_mqtt_for_devices(
            self._devices,
            connected=self.mqtt_connected,
            client_configured=self._mqtt_client is not None,
        )

    def _refresh_ble_staleness(self) -> None:
        """Mark BLE unavailable for devices whose last advertisement is stale."""
        self._transport.refresh_ble_staleness(
            self._devices, set(self._ble_devices.keys())
        )

    def _refresh_lan_staleness(self) -> None:
        """Mark LAN unavailable for non-LAN or stale-read devices (issue #57).

        LAN-active = currently correlated in ``self._lan_devices`` (answered the
        scan and matched a device_id). Everything else gets ``no_lan_presence``;
        a correlated device whose last applied read is stale gets ``stale_lan``.
        Runs AFTER :meth:`_refresh_lan_reads` so a read applied this cycle has
        already stamped success and the device is not falsely marked stale.
        """
        self._transport.refresh_lan_staleness(self._devices, set(self._lan_devices))

    @property
    def states(self) -> dict[str, GoveeDeviceState]:
        """Get current states for all devices."""
        return self._states

    @property
    def leak_sensors(self) -> dict[str, GoveeLeakSensor]:
        """Get all discovered leak sensors."""
        return self._leak_sensors

    @property
    def leak_states(self) -> dict[str, GoveeLeakSensorState]:
        """Get current leak states (device_id -> state)."""
        return self._leak_states

    @property
    def bff_device_census(self) -> list[dict[str, Any]]:
        """PII-free census of the last BFF device-list response (diagnostics)."""
        return self._bff_device_census

    @property
    def bff_response_skeleton(self) -> Any:
        """PII-free structural skeleton of the last raw BFF response."""
        return self._bff_response_skeleton

    @property
    def bff_device_values(self) -> list[dict[str, Any]]:
        """Redacted per-device BFF scalar values for diagnostics (#114)."""
        return self._bff_device_values

    def _note_sensor_reading_change(
        self,
        device_id: str,
        new_state: GoveeDeviceState,
        existing_state: GoveeDeviceState,
    ) -> None:
        """Stamp the reading-change time when temp/humidity actually changes.

        The Cloud API does not expose the device-side reading time, so the
        semantic is last *change*, not last confirmation. First reading stamps
        too (device not yet in the map).
        """
        if new_state.sensor_temperature is None and new_state.sensor_humidity is None:
            return
        reading_changed = (
            new_state.sensor_temperature != existing_state.sensor_temperature
            or new_state.sensor_humidity != existing_state.sensor_humidity
        )
        if reading_changed or device_id not in self._sensor_reading_changed_at:
            self._sensor_reading_changed_at[device_id] = dt_util.utcnow()

    def sensor_reading_changed_at(self, device_id: str) -> datetime | None:
        """When the device's temp/humidity reading last changed (#83).

        Semantic is last *change*, not last poll — the Cloud API does not
        expose the device-side reading time. Returns None until a reading
        has been seen.
        """
        return self._sensor_reading_changed_at.get(device_id)

    def device_data_last_updated(self, device_id: str) -> datetime | None:
        """Most recent time any transport delivered data for this device.

        Max of ``last_success_ts`` across cloud_api / mqtt / ble — surfaces
        the device's overall data freshness ("All Data Last Updated") as a
        diagnostic TIMESTAMP. Returns None until a transport has succeeded.
        """
        latest: datetime | None = None
        for kind in TRANSPORT_KINDS:
            health = self._transport.get(device_id, kind)
            if health is None or health.last_success_ts is None:
                continue
            if latest is None or health.last_success_ts > latest:
                latest = health.last_success_ts
        return latest

    def device_last_command_sent(self, device_id: str) -> datetime | None:
        """Most recent time a command was sent to this device, any transport.

        Max of ``last_send_ts`` across cloud_api / mqtt / ble — the outbound
        counterpart to ``device_data_last_updated``. Returns None until a
        command has been sent.
        """
        latest: datetime | None = None
        for kind in TRANSPORT_KINDS:
            health = self._transport.get(device_id, kind)
            if health is None or health.last_send_ts is None:
                continue
            if latest is None or health.last_send_ts > latest:
                latest = health.last_send_ts
        return latest

    @property
    def has_iot_credentials(self) -> bool:
        """Whether AWS IoT credentials are configured (MQTT path enabled)."""
        return self._iot_credentials is not None

    @property
    def device_topic_count(self) -> int:
        """Number of devices with a resolved MQTT publish topic."""
        return len(self._device_topics)

    def consume_button_press(self, device_id: str) -> bool:
        """Consume one pending button press for device_id. Returns True if consumed."""
        count = self._pending_button_presses.get(device_id, 0)
        if count > 0:
            if count == 1:
                del self._pending_button_presses[device_id]
            else:
                self._pending_button_presses[device_id] = count - 1
            return True
        return False

    def get_device(self, device_id: str) -> GoveeDevice | None:
        """Get device by ID."""
        return self._devices.get(device_id)

    def get_state(self, device_id: str) -> GoveeDeviceState | None:
        """Get current state for a device."""
        return self._states.get(device_id)

    def is_bff_thermometer(self, device_id: str) -> bool:
        """Return True if this device is a BFF-discovered thermo-hygrometer.

        These battery/gateway-bridged sensors report ``online`` as an
        unreliable liveness flag that flaps false between infrequent uploads,
        so entity availability must not gate on it (issue #97).
        """
        return device_id in self._bff_thermometer_ids

    def is_power_off_pending(self, device_id: str) -> bool:
        """Return True if a power-off command is in flight for this device.

        Segment entities use this to avoid racing with a concurrent power-off.
        """
        return device_id in self._pending_power_off

    # ------------------------------------------------------------------ #
    # BLE direct transport
    # ------------------------------------------------------------------ #

    def setup_ble_subscriptions(self) -> list[Any]:
        """Subscribe to BLE advertisements via the BleAdvertisementHandler.

        Returns the list of unsubscribe callables to pass to
        ``entry.async_on_unload``. No-op when HAS_BLUETOOTH is False.
        """
        return self._ble_handler.setup_subscriptions()

    @callback
    def _handle_ble_advertisement(self, service_info: Any) -> None:
        """Compatibility delegate — tests still call this directly."""
        self._ble_handler.handle_advertisement(service_info)

    async def _async_setup(self) -> None:
        """Set up the coordinator - discover devices and start MQTT.

        Called automatically by async_config_entry_first_refresh().
        """
        # Discover devices
        await self._discover_devices()

        # Start MQTT client if credentials available
        if self._iot_credentials:
            await self._start_mqtt()
            # Fetch device-specific MQTT topics for publishing commands
            await self._fetch_device_topics()

        # Discover leak sensors via BFF API (requires email/password)
        await self._discover_leak_sensors()

        # Discover BFF-only thermo-hygrometers (H5301) that the Developer API
        # omits (issue #86). Also requires email/password.
        await self._discover_bff_thermometers()

        # Standalone water detectors (H5054) deliver their trip only via the
        # account warnMessage history — start the dedicated leak poll (issue #62).
        if self._water_detectors and self._iot_credentials:
            await self._poll_water_detectors()
            self._schedule_water_detector_poll()

        # LAN (UDP) transport is opened LAST, after every fallible discovery /
        # login step above, so a partially-built setup can never strand a bound
        # :4002 socket — if one of those steps raises ConfigEntryNotReady the LAN
        # client is never opened, and any failure inside _async_setup_lan after
        # the socket opens stops the client before propagating (#57, blocking #5).
        await self._async_setup_lan()

    async def _async_setup_lan(self) -> None:
        """Set up the LAN (UDP) transport — the LAST step of coordinator setup.

        Auto-enables the local UDP transport (issue #57) when at least one
        device answers the LAN discovery scan AND its scan MAC correlates to a
        known ``device_id``. There is no enable toggle: correlation IS the gate.
        The only LAN option is ``CONF_LAN_TARGETS`` (extra cross-VLAN unicast
        targets); setting it to the literal ``"off"`` (case-insensitive) is the
        rollback escape hatch that skips LAN setup entirely, leaving
        ``self._lan_client`` ``None`` so every LAN path is bypassed.

        Opened LAST in :meth:`_async_setup` (after every fallible discovery /
        login step) and, once the client's socket is bound, wrapped so any
        subsequent failure stops the client before the exception propagates — a
        partially-built setup must never strand a bound socket (blocking #5).
        The discovery scan and ``async_start`` degrade cleanly (never raise into
        setup) when port :4002 is held by another local-control app.

        LAN-011 fills in the runtime read/rescan/health wiring; this method only
        owns the lifecycle (open LAST, correlate, leak-proof teardown).
        """
        raw_targets = self._config_entry.options.get(CONF_LAN_TARGETS, "")
        # Rollback escape hatch: an explicit "off" disables LAN with no socket
        # work, so a user can opt out of the auto-enabled transport entirely.
        if isinstance(raw_targets, str) and raw_targets.strip().lower() == "off":
            _LOGGER.info("Govee LAN transport disabled via %s='off'", CONF_LAN_TARGETS)
            return

        # Tolerate a stale / invalid targets option: a bad cross-VLAN entry must
        # not break LAN setup, let alone the integration. Fall back to no extras.
        try:
            extra_targets = expand_lan_targets(
                raw_targets if isinstance(raw_targets, str) else ""
            )
        except LanTargetError as err:
            _LOGGER.warning(
                "Ignoring invalid %s option (%r): %s",
                CONF_LAN_TARGETS,
                raw_targets,
                err,
            )
            extra_targets = []

        interface_ips = await async_get_lan_interface_ips(self.hass)
        # Auto-derived per-subnet broadcast(s): reach newer devices that ignore
        # the multicast scan but answer a directed broadcast (issue #57).
        broadcast_targets = await async_get_lan_broadcast_addresses(self.hass)

        # The discovery scan must never raise into setup: an OSError (port :4002
        # held by another local-control app without SO_REUSEPORT) means "no LAN
        # data", so degrade cleanly to MQTT/REST.
        try:
            scan = await async_scan_lan_devices(
                interface_ips=interface_ips,
                extra_targets=extra_targets,
                broadcast_targets=broadcast_targets,
            )
        except OSError as err:
            _LOGGER.info(
                "Govee LAN discovery scan could not bind, LAN disabled: %s", err
            )
            return

        client = GoveeLanClient(self._on_lan_dev_status)
        await client.async_start(interface_ips)
        if not client.available:
            # Clean degrade: the :4002 bind failed (held by a non-sharing app).
            # Release whatever it grabbed and stay LAN-disabled (client None).
            await client.async_stop()
            return

        # From here the client owns a bound socket. ANY failure must stop it
        # before propagating so a partially-built setup never strands the bind
        # (blocking #5) — even though the steps below are not expected to raise.
        try:
            matched, unmatched = correlate_scan(
                scan, set(self._devices), time.monotonic()
            )
            if not matched:
                # Auto-enable gate not met: the scan answered but nothing
                # correlated to a device_id. Don't hold sockets for nothing —
                # stop the client and stay disabled.
                _LOGGER.debug(
                    "Govee LAN: scan answered but no device correlated "
                    "(%d unmatched) — LAN disabled",
                    len(unmatched),
                )
                await client.async_stop()
                return
            self._lan_devices = matched
            self._lan_unmatched = list(unmatched)
            # Stamp the rescan throttle at setup so the first periodic rescan
            # waits a full LAN_RESCAN_INTERVAL from here — independent of host
            # uptime — rather than firing a redundant rescan on the next poll
            # (which a 0.0 sentinel would on a long-uptime host).
            self._last_lan_rescan = time.monotonic()
            # Promote the client only after correlation succeeds.
            self._lan_client = client
        except Exception:
            await client.async_stop()
            raise

        _LOGGER.info(
            "Govee LAN transport enabled for %d device(s) (%d unmatched scan "
            "record(s))",
            len(self._lan_devices),
            len(self._lan_unmatched),
        )

    @callback
    def _on_lan_dev_status(self, src_ip: str, status: LanDevStatus) -> None:
        """Apply an unsolicited LAN ``devStatus`` push, guarded by source IP.

        Injected into :class:`GoveeLanClient` as its ``on_dev_status`` callback.
        A ``devStatus`` reply carries no device identity, so the only key is the
        datagram source IP — which a DHCP reassignment can silently re-point at a
        different device (critic blocking #3). The push is applied ONLY when the
        source IP reverse-resolves to exactly one correlated device whose
        correlation is still fresh (within ``LAN_CORRELATION_TTL_SECONDS``); any
        ambiguity (unknown IP, stale correlation, or two devices sharing one IP)
        skips the push and requests a re-correlation so the next poll fixes the
        map, rather than clobbering a guessed device. The mode-aware overlay and
        the optimistic-grace guard live in :meth:`_apply_lan_read`, shared with
        the solicited poll path.

        Args:
            src_ip: The datagram's source IP address.
            status: The parsed LAN ``devStatus`` snapshot.
        """
        device_id = self._lan_device_id_for_ip(src_ip)
        if device_id is None:
            # Unknown or ambiguous source IP: a DHCP reassignment may have left
            # the IP->device map stale. Re-correlate before trusting it again and
            # do NOT apply to a guessed device — fall back to cloud (blocking #3).
            self._request_lan_rescan()
            return
        info = self._lan_devices[device_id]
        if (time.monotonic() - info.last_correlated_ts) >= LAN_CORRELATION_TTL_SECONDS:
            # Correlation too old to trust this IP mapping; re-correlate first
            # and skip — a stale map could route this reply to the wrong device.
            self._request_lan_rescan()
            return
        # Unsolicited push: notify HA only when a value actually changed.
        self._apply_lan_read(device_id, status)

    def _lan_device_id_for_ip(self, src_ip: str) -> str | None:
        """Reverse-resolve a LAN source IP to its correlated ``device_id``.

        Returns the single correlated ``device_id`` currently mapped to
        ``src_ip``, or ``None`` when no device maps to it (unknown IP) or more
        than one does (ambiguous, e.g. a botched DHCP lease) — the only safe
        answer is to skip LAN for that datagram (blocking #3).
        """
        matches = [
            device_id
            for device_id, info in self._lan_devices.items()
            if info.ip == src_ip
        ]
        return matches[0] if len(matches) == 1 else None

    @callback
    def _request_lan_rescan(self) -> None:
        """Force the next LAN rescan to bypass its throttle (blocking #3).

        Called from the unsolicited-push path when a ``devStatus`` arrives from an
        IP that is unknown or whose correlation has gone stale: a DHCP
        reassignment may have invalidated the IP<->device map, so the next poll
        must re-correlate immediately rather than wait out ``LAN_RESCAN_INTERVAL``.
        Idempotent (it only clears the throttle timestamp), so a burst of
        unknown-IP pushes can never trigger a rescan flood.

        Uses ``-inf`` rather than ``0.0`` so ``now - self._last_lan_rescan``
        always exceeds ``LAN_RESCAN_INTERVAL`` regardless of host uptime:
        ``time.monotonic()`` counts seconds since boot, so on a freshly-booted
        Home Assistant host (monotonic < ``LAN_RESCAN_INTERVAL``) a ``0.0``
        sentinel still reads as throttled and would silently swallow the forced
        rescan — the DHCP re-correlation of blocking #3 would never fire.
        """
        self._last_lan_rescan = float("-inf")

    def _in_optimistic_grace(self, existing_state: GoveeDeviceState) -> bool:
        """Return ``True`` while a device is inside its optimistic grace window.

        A control command that just fired marks the state ``source`` as
        ``optimistic`` and stamps ``last_optimistic_update``; for a short window
        (two poll intervals, capped at ``OPTIMISTIC_GRACE_CAP_SECONDS``) a slower
        authoritative read — the cloud poll or a solicited LAN read — must not
        overwrite the in-flight power/brightness and flip-flop the UI. Extracted
        so the cloud poll and the LAN overlay share one definition (issue #57).
        """
        if existing_state.source != "optimistic":
            return False
        optimistic_ts = existing_state.last_optimistic_update
        if optimistic_ts is None:
            return False
        poll_seconds = (
            self.update_interval.total_seconds()
            if self.update_interval is not None
            else 60.0
        )
        grace_window = min(2 * poll_seconds, OPTIMISTIC_GRACE_CAP_SECONDS)
        return (time.monotonic() - optimistic_ts) < grace_window

    def _apply_lan_read(
        self, device_id: str, status: LanDevStatus, *, notify: bool = True
    ) -> None:
        """Overlay a parsed LAN ``devStatus`` onto a device's existing state.

        Additive and abortable: a device with no existing state returns without
        raising, so a stray reply can never strand or corrupt state. Brightness is
        rescaled from the LAN 0-100 domain into the device's native range BEFORE
        the overlay (``state.brightness`` is device-native), and the overlay
        honours the optimistic grace window so a solicited read cannot clobber an
        in-flight command. The mode-aware / {0,0,0}-sentinel / colour-vs-CT
        mutual-exclusion guards live in
        :meth:`GoveeDeviceState.update_from_lan`.

        Args:
            device_id: The correlated device to overlay.
            status: The parsed LAN ``devStatus`` reply.
            notify: When ``True`` (the unsolicited-push path) call
                ``async_set_updated_data`` only if a value actually changed (BFF
                churn-avoidance). The solicited poll path passes ``False`` and
                mutates in place, letting ``_async_update_data`` return
                ``self._states`` so HA fires listeners without a re-entrant
                ``async_set_updated_data`` (critic advisory).
        """
        existing = self._states.get(device_id)
        if existing is None:
            return

        device = self._devices.get(device_id)
        brightness_range = device.brightness_range if device is not None else (0, 100)
        native_brightness = (
            lan_brightness_to_device(status.brightness_0_100, brightness_range)
            if status.brightness_0_100 is not None
            else None
        )
        overlay = _LanReadOverlay(
            on=status.on,
            brightness=native_brightness,
            color=status.color,
            color_temp_kelvin=status.color_temp_kelvin,
        )

        before = (
            existing.power_state,
            existing.brightness,
            existing.color,
            existing.color_temp_kelvin,
            existing.online,
        )
        existing.update_from_lan(
            overlay, skip_power_brightness=self._in_optimistic_grace(existing)
        )
        # A devStatus reply reaching here is a confirmed inbound read — proof of
        # life — so stamp LAN read success (read => success). This is the read
        # half of the LAN recording asymmetry (see refresh_lan_staleness): the
        # write half lives in the LAN control tier. Recording here keeps the
        # device LAN-available so the very next _refresh_lan_staleness pass
        # leaves it active instead of demoting it to stale_lan.
        self._record_transport_success(device_id, "lan")
        # NOTE: a confirmed inbound READ proves the transport is alive (recorded
        # above) but says nothing about whether WRITES land, so it must NOT reset
        # the write-miss streak — only a confirmed write
        # (:meth:`_clear_lan_write_misses`) or the suppression cooldown does that
        # (#57). Resetting here would let a polling device whose writes never
        # confirm pay the verify-by-read timeout on every single command forever.
        after = (
            existing.power_state,
            existing.brightness,
            existing.color,
            existing.color_temp_kelvin,
            existing.online,
        )
        if notify and before != after:
            self.async_set_updated_data(self._states)

    async def _refresh_lan_reads(self) -> None:
        """Solicit a ``devStatus`` from each correlated LAN device and overlay it.

        Sends one batched query to every correlated device's current IP and
        overlays each reply onto the FRESH cloud state in place — no re-entrant
        ``async_set_updated_data``: ``_async_update_data`` returns ``self._states``
        so HA fires listeners. A device that answers resets its miss counter; a
        device silent for ``LAN_READ_MISS_DEMOTE_THRESHOLD`` consecutive polls is
        demoted out of the LAN map so reads AND writes fall back to MQTT/REST
        until a rescan re-promotes it.
        """
        if self._lan_client is None or not self._lan_devices:
            return

        ip_to_device = {
            info.ip: device_id
            for device_id, info in self._lan_devices.items()
            if info.ip
        }
        if not ip_to_device:
            return

        replies = await self._lan_client.async_read_batch(list(ip_to_device))

        demoted: list[str] = []
        for ip, device_id in ip_to_device.items():
            status = replies.get(ip)
            if status is not None:
                # Mutate in place (notify=False) — see _apply_lan_read.
                self._apply_lan_read(device_id, status, notify=False)
                self._lan_read_misses[device_id] = 0
            else:
                misses = self._lan_read_misses.get(device_id, 0) + 1
                self._lan_read_misses[device_id] = misses
                if misses >= LAN_READ_MISS_DEMOTE_THRESHOLD:
                    demoted.append(device_id)

        for device_id in demoted:
            self._lan_devices.pop(device_id, None)
            self._lan_read_misses.pop(device_id, None)
            self._lan_write_misses.pop(device_id, None)
            self._lan_write_suppressed_until.pop(device_id, None)
            _LOGGER.debug(
                "Govee LAN: demoting %s after %d consecutive read misses — reads "
                "and writes fall back to MQTT/REST until a rescan re-promotes it",
                device_id,
                LAN_READ_MISS_DEMOTE_THRESHOLD,
            )

    async def _async_maybe_rescan_lan(self) -> None:
        """Re-scan + re-correlate LAN devices on a throttled cadence (issue #57).

        Mirrors :meth:`_async_maybe_rediscover_devices`' monotonic throttle. A
        rescan re-promotes devices demoted by read-misses, picks up newly added
        devices, and — critically — re-correlates IP<->device_id mappings so a
        DHCP IP reassignment cannot leave the read path pointed at the wrong
        device (blocking #3). Never raises: a scan hiccup must not fail the poll.
        :meth:`_request_lan_rescan` can clear the throttle so an unknown-IP push
        forces the next call to run.
        """
        if self._lan_client is None:
            return
        now = time.monotonic()
        if now - self._last_lan_rescan < LAN_RESCAN_INTERVAL:
            return
        self._last_lan_rescan = now

        raw_targets = self._config_entry.options.get(CONF_LAN_TARGETS, "")
        try:
            extra_targets = expand_lan_targets(
                raw_targets if isinstance(raw_targets, str) else ""
            )
        except LanTargetError:
            extra_targets = []

        try:
            interface_ips = await async_get_lan_interface_ips(self.hass)
            broadcast_targets = await async_get_lan_broadcast_addresses(self.hass)
            scan = await async_scan_lan_devices(
                interface_ips=interface_ips,
                extra_targets=extra_targets,
                broadcast_targets=broadcast_targets,
            )
        except OSError as err:
            _LOGGER.debug("Govee LAN rescan could not bind/scan: %s", err)
            return

        matched, unmatched = correlate_scan(scan, set(self._devices), now)
        self._merge_lan_correlation(matched, unmatched)

    def _merge_lan_correlation(
        self,
        matched: dict[str, LanDeviceInfo],
        unmatched: list[Mapping[str, Any]],
    ) -> None:
        """Merge a fresh LAN correlation into the live device map (blocking #3).

        Fresh matches are authoritative for IP<->device_id and reset the
        read-miss counter (re-promoting demoted devices). A previously-correlated
        device that did NOT answer this scan is kept (a transient scan miss;
        read-miss demotion handles a real drop) UNLESS its old IP is now claimed
        by a DIFFERENT device — that stale mapping is invalidated and dropped so a
        DHCP reassignment can never misroute a future read (blocking #3 (b)).
        """
        claimed_ips = {info.ip for info in matched.values() if info.ip}
        rebuilt: dict[str, LanDeviceInfo] = {}
        for device_id, info in self._lan_devices.items():
            if device_id in matched:
                continue  # superseded by the fresh correlation below
            if info.ip and info.ip in claimed_ips:
                # Old IP now belongs to a different device — drop the stale map.
                self._lan_read_misses.pop(device_id, None)
                self._lan_write_misses.pop(device_id, None)
                self._lan_write_suppressed_until.pop(device_id, None)
                continue
            rebuilt[device_id] = info
        for device_id, info in matched.items():
            rebuilt[device_id] = info
            self._lan_read_misses[device_id] = 0
        self._lan_devices = rebuilt
        self._lan_unmatched = list(unmatched)

    async def _discover_devices(self) -> None:
        """Discover all devices from Govee API."""
        try:
            devices = await self._api_client.get_devices()

            _LOGGER.info(
                "API returned %d devices (enable_groups=%s)",
                len(devices),
                self._enable_groups,
            )

            for device in devices:
                _LOGGER.debug(
                    "Device: %s (%s) type=%s is_group=%s",
                    device.name,
                    device.device_id,
                    device.device_type,
                    device.is_group,
                )
                # Log capabilities for debugging segment issues
                for cap in device.capabilities:
                    _LOGGER.debug(
                        "  Capability: type=%s instance=%s params=%s",
                        cap.type,
                        cap.instance,
                        cap.parameters,
                    )

                # Filter group devices unless enabled
                if device.is_group and not self._enable_groups:
                    _LOGGER.info(
                        "Skipping group device: %s (device_id=%s) because enable_groups=False",
                        device.name,
                        device.device_id,
                    )
                    continue

                _LOGGER.debug("Adding device to coordinator: %s", device.device_id)
                self._devices[device.device_id] = device
                # Create empty state for each device
                self._states[device.device_id] = GoveeDeviceState.create_empty(
                    device.device_id
                )
                self._ensure_transport_health(device.device_id)

            _LOGGER.info(
                "Discovered %d Govee devices (enable_groups=%s)",
                len(self._devices),
                self._enable_groups,
            )

            # Clean up scene caches for devices no longer discovered
            self._scene_cache.cleanup_stale(set(self._devices))

            # Scene cache is populated lazily via async_get_scenes() / async_get_diy_scenes()
            # during entity setup, avoiding rate limit pressure at startup

            # Clear any auth issues on success
            await async_delete_auth_issue(self.hass, self._config_entry)

        except GoveeAuthError as err:
            # Create repair issue for auth failure
            await async_create_auth_issue(self.hass, self._config_entry)
            raise ConfigEntryAuthFailed("Invalid API key") from err
        except GoveeApiError as err:
            raise UpdateFailed(f"Failed to discover devices: {err}") from err

    async def _async_maybe_rediscover_devices(self) -> None:
        """Re-check the account device list and reload when new devices appear.

        Device discovery is otherwise one-shot at setup, so a device added to
        the Govee account after Home Assistant started stays invisible until a
        manual reload (issue #101). Here we re-fetch the device list on a slow
        cadence (``DEVICE_REDISCOVERY_INTERVAL``, to respect the API rate
        limits); if any genuinely new device id appears, we schedule a config
        entry reload, which re-runs the existing, tested discovery + platform
        setup so the new entities are created.

        Only additions trigger a reload — removals stay tied to reload/restart
        and the existing orphan-cleanup pass, to keep entity churn minimal. The
        method never raises: a discovery hiccup must not fail the state poll.
        """
        now = time.monotonic()
        if now - self._last_rediscovery_check < DEVICE_REDISCOVERY_INTERVAL:
            return
        self._last_rediscovery_check = now

        try:
            devices = await self._api_client.get_devices()
        except Exception as err:
            # Non-fatal: the regular state poll continues on the known devices.
            _LOGGER.debug("Periodic device re-discovery failed: %s", err)
            return

        # Apply the same group filter discovery uses, so a group device left
        # disabled doesn't look "new" on every pass and reload-loop.
        candidate_ids = {
            device.device_id
            for device in devices
            if not (device.is_group and not self._enable_groups)
        }
        new_ids = candidate_ids - set(self._devices)
        if not new_ids:
            return

        _LOGGER.info(
            "Detected %d new Govee device(s) since startup (%s) — reloading the "
            "integration to add them (#101)",
            len(new_ids),
            ", ".join(sorted(new_ids)),
        )
        self.hass.config_entries.async_schedule_reload(self._config_entry.entry_id)

    async def _start_mqtt(self) -> None:
        """Start MQTT client for real-time updates."""
        if not self._iot_credentials:
            return

        self._mqtt_client = GoveeAwsIotClient(
            credentials=self._iot_credentials,
            on_state_update=self._on_mqtt_state_update,
            on_give_up=self._on_mqtt_give_up,
        )

        if self._mqtt_client.available:
            try:
                await self._mqtt_client.async_start()
                _LOGGER.info("MQTT client started for real-time updates")
                # Clear any MQTT issues on success
                await async_delete_mqtt_issue(self.hass, self._config_entry)
            except Exception as err:
                _LOGGER.warning("MQTT client failed to start: %s", err)
                await async_create_mqtt_issue(
                    self.hass,
                    self._config_entry,
                    str(err),
                )
        else:
            _LOGGER.warning("MQTT library not available")

    async def _fetch_device_topics(self) -> None:
        """Fetch device-specific MQTT topics from undocumented Govee API.

        These topics are required for publishing commands (ptReal, etc).
        Device targeting via payload alone doesn't work - AWS IoT requires
        publishing to the device's specific topic.
        """
        if not self._iot_credentials:
            return

        try:
            async with GoveeAuthClient(hass=self.hass) as auth_client:
                self._device_topics = await auth_client.fetch_device_topics(
                    self._iot_credentials.token
                )
                _LOGGER.info(
                    "Fetched MQTT topics for %d devices",
                    len(self._device_topics),
                )
        except GoveeApiError as err:
            _LOGGER.warning("Failed to fetch device topics: %s", err)
            # Continue without device topics - ptReal commands won't work
            # but the integration can still function with polling
        except Exception as err:
            _LOGGER.warning("Unexpected error fetching device topics: %s", err)

    async def _discover_leak_sensors(self) -> None:
        """Discover leak sensor sub-devices via BFF API.

        Reuses the app2 IoT token (already obtained for MQTT) to call the
        BFF device list endpoint. No additional login required.
        """
        if not self._iot_credentials:
            return

        try:
            # Create a short-lived auth client per call, consistent with
            # _fetch_device_topics(). The hass= param shares HA's managed
            # aiohttp.ClientSession, so no new TCP connections are created.
            async with GoveeAuthClient(hass=self.hass) as auth_client:
                (
                    sensor_data,
                    hub_data,
                    thermo_readings,
                ) = await auth_client.fetch_bff_leak_sensors(
                    self._iot_credentials.token
                )
                # Capture the PII-free census + skeleton before the client
                # closes (#87).
                self._bff_device_census = auth_client.bff_device_census()
                self._bff_response_skeleton = auth_client.bff_response_skeleton()
                self._bff_device_values = auth_client.bff_device_values()

            self._leak_hubs = hub_data
            for sensor in sensor_data:
                leak_sensor = GoveeLeakSensor(
                    device_id=sensor["device_id"],
                    name=sensor["name"],
                    sku=sensor["sku"],
                    hub_device_id=sensor["hub_device_id"],
                    sno=sensor["sno"],
                    hw_version=sensor.get("hw_version", ""),
                    sw_version=sensor.get("sw_version", ""),
                )
                self._leak_sensors[leak_sensor.device_id] = leak_sensor

                # Initialize state from BFF data
                state = GoveeLeakSensorState()
                state.battery = sensor.get("battery")
                state.online = sensor.get("online", True)
                state.gateway_online = sensor.get("gateway_online", True)
                state.last_wet_time = sensor.get("last_wet_time")
                state.read = sensor.get("read", True)
                self._leak_states[leak_sensor.device_id] = state

                self._sno_to_sensor_id[(leak_sensor.hub_device_id, leak_sensor.sno)] = (
                    leak_sensor.device_id
                )

            if self._leak_sensors:
                _LOGGER.info(
                    "Discovered %d leak sensors across %d hubs",
                    len(self._leak_sensors),
                    len({s.hub_device_id for s in self._leak_sensors.values()}),
                )

            # Track which Developer-API thermometers (e.g. H5110 via H5151/H5044)
            # the BFF list carries a reading for. We DON'T apply the BFF value:
            # the BFF `tem`/`hum` scale varies by gateway/firmware (tenths vs
            # hundredths) and a fixed divisor mis-scaled humidity 10x (#102).
            # The BFF call is kept purely as a *tickle* — it nudges Govee's cloud
            # to refresh the Developer `/device/state` reading, which the regular
            # poll then picks up correctly (and with the right °C/°F handling).
            # This set only gates whether the 5-min tickle poll runs (#83).
            self._thermo_bff_devices = set(thermo_readings) & set(self._devices)
            if self._thermo_bff_devices:
                _LOGGER.info(
                    "BFF tickle enabled for %d thermometer device(s); readings "
                    "come from the Developer API poll",
                    len(self._thermo_bff_devices),
                )

            # Battery is the one BFF field we DO apply for these thermometers:
            # the Developer API never exposes it, and unlike tem/hum it's a plain
            # 0-100 int with no gateway-dependent scale (#83 follow-up).
            self._apply_bff_thermo_battery(thermo_readings)

            # Start the 5-min BFF poll if we have leak sensors OR thermometers
            # whose readings we refresh via the BFF tickle (#83).
            if self._leak_sensors or self._thermo_bff_devices:
                self._schedule_bff_poll()

        except Exception as err:
            _LOGGER.warning("Failed to discover leak sensors: %s", err)
            # Non-fatal: integration continues without leak sensors

    def _apply_bff_thermo_battery(
        self, thermo_readings: dict[str, dict[str, Any]]
    ) -> None:
        """Apply BFF-reported scalars (battery, water-tank-full) to devices.

        The Developer API doesn't expose battery for BLE-bridged sensors (e.g.
        H5110 via an H5151/H5044 gateway), nor the dehumidifier water-tank-full
        value, but the BFF ``deviceSettings`` carries both. Unlike the BFF
        ``tem``/``hum`` (scale varies by gateway — used only as a tickle, #102),
        battery is a plain 0-100 int and waterFull a plain 0/1 flag, so they're
        safe to store. Devices whose BFF entry omits a value are left untouched,
        so no entity shows a stale reading (issues #83, #118).

        Battery is skipped for mains-powered devices — both by device_type and
        by an explicit SKU list (the H5106 air-quality monitor reports a bogus
        ``battery: 100`` while plugged in but doesn't carry a mains device_type)
        — so they don't surface a misleading 100% battery sensor (issues #125,
        #114).
        """
        for dev_id in set(thermo_readings) & set(self._devices):
            state = self._states.get(dev_id)
            if state is None:
                continue
            device = self._devices.get(dev_id)
            reading = thermo_readings[dev_id]

            battery = reading.get("battery")
            if battery is not None and (
                device is None
                or (
                    device.device_type not in MAINS_POWERED_DEVICE_TYPES
                    and device.sku.upper() not in MAINS_POWERED_BATTERY_SKUS
                )
            ):
                try:
                    state.battery = int(battery)
                except (TypeError, ValueError):
                    pass

            water_full = reading.get("water_full")
            if water_full is not None:
                try:
                    state.water_full = bool(int(water_full))
                except (TypeError, ValueError):
                    pass

    async def _discover_bff_thermometers(self) -> None:
        """Discover thermo-hygrometers (H5301) via the BFF device list (issue #86).

        These battery WiFi sensors are absent from the Developer API
        ``/user/devices`` list, so capability-based discovery never sees them and
        they "don't show up". The account-login BFF list returns them; we
        synthesize a thermometer ``GoveeDevice`` for each and inject it into
        ``self._devices`` (before platform setup) so the existing temperature /
        humidity sensor entities attach. Reuses the app2 IoT token, like
        ``_discover_leak_sensors``. Non-fatal on error.
        """
        if not self._iot_credentials:
            return

        try:
            async with GoveeAuthClient(hass=self.hass) as auth_client:
                sensors = await auth_client.fetch_bff_thermo_hygrometers(
                    self._iot_credentials.token
                )
                # Refresh the PII-free census so a diagnostics download shows the
                # thermo-hygro SKU flags even when no leak sensors are present.
                self._bff_device_census = auth_client.bff_device_census()
                self._bff_response_skeleton = auth_client.bff_response_skeleton()
                self._bff_device_values = auth_client.bff_device_values()

            for sensor in sensors:
                device_id = sensor["device_id"]
                if not device_id:
                    continue
                hub_device_id = sensor.get("hub_device_id", "")
                device = GoveeDevice.synthetic_thermometer(
                    device_id=device_id,
                    sku=sensor["sku"],
                    name=sensor["name"],
                    hub_device_id=hub_device_id,
                )
                self._devices[device_id] = device
                self._bff_thermometer_ids.add(device_id)
                if hub_device_id:
                    self._bff_thermo_hubs[hub_device_id] = {
                        "sku": sensor.get("hub_sku", ""),
                    }

                state = GoveeDeviceState.create_empty(device_id)
                state.online = sensor.get("online", True)
                state.sensor_temperature = sensor.get("temperature")
                state.sensor_humidity = sensor.get("humidity")
                state.battery = sensor.get("battery")
                self._states[device_id] = state
                self._ensure_transport_health(device_id)
                if (
                    state.sensor_temperature is not None
                    or state.sensor_humidity is not None
                ):
                    self._sensor_reading_changed_at[device_id] = dt_util.utcnow()

            if self._bff_thermometer_ids:
                _LOGGER.info(
                    "Discovered %d BFF thermo-hygrometers (issue #86)",
                    len(self._bff_thermometer_ids),
                )
                # Reuse the 5-min BFF poll loop to refresh readings.
                self._schedule_bff_poll()

        except Exception as err:
            _LOGGER.warning("Failed to discover BFF thermo-hygrometers: %s", err)
            # Non-fatal: integration continues without these sensors.

    async def _refresh_bff_thermometers(self) -> None:
        """Refresh temp/humidity readings for BFF thermo-hygrometers (issue #86)."""
        if not self._bff_thermometer_ids or not self._iot_credentials:
            return

        try:
            async with GoveeAuthClient(hass=self.hass) as auth_client:
                sensors = await auth_client.fetch_bff_thermo_hygrometers(
                    self._iot_credentials.token
                )
        except Exception as err:
            _LOGGER.debug("BFF thermo-hygrometer refresh failed: %s", err)
            return

        changed = False
        for sensor in sensors:
            device_id = sensor["device_id"]
            existing = self._states.get(device_id)
            if existing is None:
                continue
            new_state = GoveeDeviceState.create_empty(device_id)
            new_state.online = sensor.get("online", True)
            # Preserve last good reading when the BFF omits it this cycle —
            # battery WiFi sensors upload infrequently.
            new_state.sensor_temperature = (
                sensor.get("temperature")
                if sensor.get("temperature") is not None
                else existing.sensor_temperature
            )
            new_state.sensor_humidity = (
                sensor.get("humidity")
                if sensor.get("humidity") is not None
                else existing.sensor_humidity
            )
            new_state.battery = (
                sensor.get("battery")
                if sensor.get("battery") is not None
                else existing.battery
            )
            self._note_sensor_reading_change(device_id, new_state, existing)
            self._states[device_id] = new_state
            self._record_transport_success(device_id, "cloud_api")
            # Only a value change warrants pushing an HA update — skip the churn
            # when a 5-min poll returns the same reading (#86). `online` is
            # ignored here: the availability mixin doesn't gate on it.
            if (
                new_state.sensor_temperature != existing.sensor_temperature
                or new_state.sensor_humidity != existing.sensor_humidity
                or new_state.battery != existing.battery
            ):
                changed = True

        if changed:
            self.async_set_updated_data(self._states)

    def register_leak_hubs(self) -> None:
        """Register each leak sensor's hub as a device.

        Leak sensors set ``via_device=(DOMAIN, hub_device_id)``; HA requires
        the referenced device to exist before the child entity is added,
        otherwise HA logs a deprecation warning (will error in 2025.12).

        Called from leak-sensor platforms ``async_setup_entry`` so hubs are
        registered AFTER the integration's orphaned-device cleanup pass
        (which would otherwise remove a hub that has no entities yet).
        Idempotent: ``async_get_or_create`` returns the existing entry.
        """
        if not self._leak_sensors:
            return
        device_reg = dr.async_get(self.hass)
        # Group sensors by hub so we can infer hub model from children.
        sensors_by_hub: dict[str, list[GoveeLeakSensor]] = {}
        for sensor in self._leak_sensors.values():
            if sensor.hub_device_id:
                sensors_by_hub.setdefault(sensor.hub_device_id, []).append(sensor)

        for hub_id, hub_sensors in sensors_by_hub.items():
            hub_meta = self._leak_hubs.get(hub_id, {})
            # Prefer SKU from BFF if present; otherwise infer from child SKUs.
            # H5058 leak sensors are paired with the H5043 Wi-Fi hub.
            child_skus = {s.sku for s in hub_sensors}
            if hub_meta.get("sku"):
                model = hub_meta["sku"]
            elif "H5058" in child_skus:
                model = "H5043"
            else:
                model = None
            name = hub_meta.get("name") or "Govee Leak Sensor Hub"
            device_reg.async_get_or_create(
                config_entry_id=self._config_entry.entry_id,
                identifiers={(DOMAIN, hub_id)},
                manufacturer="Govee",
                model=model,
                name=name,
            )

    def register_thermo_hubs(self) -> None:
        """Register gateway hubs bridging BFF thermo-hygrometers (#86).

        Thermo entities set ``via_device=(DOMAIN, hub_device_id)`` (e.g. H5310
        via H5044); HA requires the referenced hub device to exist first.
        Called from the sensor platform ``async_setup_entry`` after orphan
        cleanup. Idempotent via ``async_get_or_create``.
        """
        if not self._bff_thermo_hubs:
            return
        device_reg = dr.async_get(self.hass)
        for hub_id, meta in self._bff_thermo_hubs.items():
            if not hub_id:
                continue
            device_reg.async_get_or_create(
                config_entry_id=self._config_entry.entry_id,
                identifiers={(DOMAIN, hub_id)},
                manufacturer="Govee",
                model=meta.get("sku") or None,
                name=meta.get("sku") or "Govee Gateway",
            )

    def _schedule_bff_poll(self) -> None:
        """Schedule the next BFF poll in 5 minutes."""
        if self._bff_poll_unsub:
            self._bff_poll_unsub()
        self._bff_poll_unsub = async_call_later(
            self.hass, BFF_POLL_INTERVAL, self._bff_poll_callback
        )

    async def _bff_poll_callback(self, _now: Any = None) -> None:
        """Callback for periodic BFF polling."""
        await self._poll_bff_leak_state()
        # Refresh thermo-hygrometer readings on the same cadence (issue #86).
        await self._refresh_bff_thermometers()
        # Re-schedule next poll
        self._schedule_bff_poll()

    async def _poll_bff_leak_state(self) -> None:
        """Poll BFF API for updated leak sensor + thermometer state.

        Called every 5 minutes and after MQTT events. Reuses the app2 IoT token
        from initial login. The BFF device-list call also tickles Govee's cloud
        into refreshing the Developer-API readings for BLE-bridged thermometers
        and carries their live ``lastDeviceData`` value (#83).
        """
        if not self._iot_credentials:
            return
        if not self._leak_sensors and not self._thermo_bff_devices:
            return

        try:
            # Short-lived auth client, consistent with _fetch_device_topics()
            # and _discover_leak_sensors(). hass= reuses HA's aiohttp session.
            async with GoveeAuthClient(hass=self.hass) as auth_client:
                (
                    sensor_data,
                    _hub_data,
                    thermo_readings,
                ) = await auth_client.fetch_bff_leak_sensors(
                    self._iot_credentials.token
                )
        except Exception as err:
            _LOGGER.debug("BFF poll failed: %s", err)
            return

        # Pick up leak sensors added to a hub after startup (issue #101). These
        # are BFF-only sub-devices, so they never appear in the Developer-API
        # /user/devices list that _async_maybe_rediscover_devices watches — the
        # only way to notice them is here, in the BFF poll. A genuinely new
        # sensor id schedules a reload, which re-runs the tested discovery +
        # platform setup so its entities are created without a manual reload.
        if not self._bff_reload_scheduled:
            new_leak_ids = {
                s["device_id"] for s in sensor_data if s.get("device_id")
            } - set(self._leak_sensors)
            if new_leak_ids:
                self._bff_reload_scheduled = True
                _LOGGER.info(
                    "Detected %d new leak sensor(s) on a hub since startup (%s) "
                    "— reloading the integration to add them (#101)",
                    len(new_leak_ids),
                    ", ".join(sorted(new_leak_ids)),
                )
                self.hass.config_entries.async_schedule_reload(
                    self._config_entry.entry_id
                )
                return

        # Refresh BLE-bridged thermometer battery from the BFF data (#83). The
        # tem/hum are still discarded (scale varies, #102) — battery only.
        self._apply_bff_thermo_battery(thermo_readings)

        # Update state for each known sensor
        now_s = time.time()
        now_ms = int(now_s * 1000)
        for sensor in sensor_data:
            device_id = sensor["device_id"]
            state = self._leak_states.get(device_id)
            if state is None:
                continue

            state.battery = sensor.get("battery")
            state.online = sensor.get("online", True)
            state.gateway_online = sensor.get("gateway_online", True)
            bff_wet_time = sensor.get("last_wet_time")
            if bff_wet_time and (
                state.last_wet_time is None or bff_wet_time > state.last_wet_time
            ):
                state.last_wet_time = bff_wet_time
            state.read = sensor.get("read", True)

            # Fallback wet detection: if BFF shows a recent leak event
            # within the poll window but MQTT never reported wet during that
            # period, force it on. This covers MQTT disconnects or lost packets.
            last_wet = sensor.get("last_wet_time") or 0
            age_ms = now_ms - last_wet if last_wet > 0 else float("inf")
            poll_window_ms = BFF_POLL_INTERVAL * 1000
            mqtt_wet_age = now_s - state.last_mqtt_wet_at

            if age_ms < poll_window_ms and mqtt_wet_age > BFF_POLL_INTERVAL:
                sensor_obj = self._leak_sensors.get(device_id)
                sensor_name = sensor_obj.name if sensor_obj else device_id
                _LOGGER.warning(
                    "BFF fallback: '%s' has unread leak from %ds ago "
                    "but MQTT didn't report wet in the last %ds — forcing wet",
                    sensor_name,
                    int(age_ms / 1000),
                    BFF_POLL_INTERVAL,
                )
                state.is_wet = True

        # The BFF device-list call above already tickled Govee's cloud into
        # refreshing the Developer `/device/state` reading for these BLE-bridged
        # thermometers; the regular Developer poll picks up the fresh, correctly
        # scaled value. We deliberately do NOT apply the BFF `tem`/`hum` here —
        # its scale varies by gateway and a fixed divisor over-scaled humidity
        # 10x on every BFF cycle (#102). Only battery is applied (above), since
        # it's plain 0-100 and absent from the Developer API (#83).

        # Notify leak sensor entities only (avoids churning unrelated
        # light / switch entities that also subscribe to the coordinator).
        async_dispatcher_send(self.hass, f"{DOMAIN}_leak_update")

    @property
    def _water_detectors(self) -> list[GoveeDevice]:
        """Developer-API devices that expose a standalone water-leak event.

        These (H5054) appear in the regular device list but have no MQTT topic
        and no pollable event state — their trip is fetched separately via the
        account warnMessage history (issue #62).
        """
        return [
            d
            for d in self._devices.values()
            if not d.is_group and d.supports_water_leak_event
        ]

    def _schedule_water_detector_poll(self) -> None:
        """Schedule the next standalone water-detector leak poll."""
        if self._wd_poll_unsub:
            self._wd_poll_unsub()
        self._wd_poll_unsub = async_call_later(
            self.hass,
            WATER_DETECTOR_POLL_INTERVAL,
            self._water_detector_poll_callback,
        )

    async def _water_detector_poll_callback(self, _now: Any = None) -> None:
        """Periodic callback: poll water detectors, then re-arm the timer."""
        await self._poll_water_detectors()
        self._schedule_water_detector_poll()

    async def _poll_water_detectors(self) -> None:
        """Poll standalone water detectors (H5054) for online + leak state.

        One BFF ``device/list`` GET per tick yields online/gateway/battery/
        lastTime. ``warnMessage`` (one POST per detector) is only called when a
        detector has freshly reported (``last_time`` advanced) or is currently
        wet (to detect the user clearing the alert) — keeping the account-API
        request count near one call per tick in steady state (issue #62).
        """
        detectors = self._water_detectors
        if not detectors or not self._iot_credentials:
            return

        token = self._iot_credentials.token
        device_ids = {d.device_id for d in detectors}
        sku_by_id = {d.device_id: d.sku for d in detectors}

        try:
            async with GoveeAuthClient(hass=self.hass) as auth_client:
                states = await auth_client.fetch_water_detector_states(
                    token, device_ids
                )
                changed = False
                for device_id, info in states.items():
                    state = self._states.get(device_id)
                    if state is None:
                        continue

                    online = bool(info.get("online", True)) and bool(
                        info.get("gateway_online", True)
                    )
                    last_time = info.get("last_time") or 0
                    prev_time = self._water_leak_last_time.get(device_id, 0)

                    # Only hit warnMessage when there's something new to learn:
                    # a fresh report, or a currently-wet sensor that may have
                    # been cleared in the app.
                    if last_time > prev_time or state.water_leak:
                        try:
                            is_wet = await auth_client.fetch_leak_warning(
                                token, device_id, sku_by_id[device_id]
                            )
                        except Exception as err:  # noqa: BLE001
                            _LOGGER.debug(
                                "warnMessage poll failed for %s: %s", device_id, err
                            )
                            is_wet = bool(state.water_leak)
                        if state.water_leak != is_wet:
                            state.water_leak = is_wet
                            changed = True
                    if last_time:
                        self._water_leak_last_time[device_id] = last_time

                    if state.online != online:
                        state.online = online
                        changed = True
                    state.source = "api"
                    _LOGGER.debug(
                        "Water-detector %s: online=%s water_leak=%s last_time=%s",
                        device_id,
                        online,
                        state.water_leak,
                        last_time,
                    )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Water-detector poll failed: %s", err)
            return

        if changed:
            self.async_update_listeners()

    @callback
    def _handle_leak_event(self, state_data: dict[str, Any]) -> None:
        """Handle a decoded leak event from MQTT multiSync message."""
        hub_id = state_data["hub_device_id"]
        sno = state_data["sensor_slot"]
        is_wet = state_data["is_wet"]

        sensor_id = self._sno_to_sensor_id.get((hub_id, sno))
        if not sensor_id:
            _LOGGER.debug(
                "Leak event for unknown sensor: hub=%s slot=%d wet=%s",
                hub_id,
                sno,
                is_wet,
            )
            return

        state = self._leak_states.get(sensor_id)
        if state is None:
            return

        prev_wet = state.is_wet
        state.is_wet = is_wet

        if is_wet:
            state.last_mqtt_wet_at = time.time()
            state.last_wet_time = int(time.time() * 1000)

        if prev_wet != is_wet:
            sensor = self._leak_sensors.get(sensor_id)
            sensor_name = sensor.name if sensor else sensor_id
            _LOGGER.info(
                "Leak sensor '%s' changed: %s -> %s",
                sensor_name,
                "wet" if prev_wet else "dry",
                "wet" if is_wet else "dry",
            )

        async_dispatcher_send(self.hass, f"{DOMAIN}_leak_update")
        self._bff_poll_task = self.hass.async_create_task(self._poll_bff_leak_state())

    @callback
    def _handle_button_press(self, state_data: dict[str, Any]) -> None:
        """Handle a button press event from MQTT multiSync message."""
        sensor_id = state_data["device_id"]

        if sensor_id not in self._leak_sensors:
            _LOGGER.debug("Button press for unknown sensor: %s", sensor_id)
            return

        sensor = self._leak_sensors[sensor_id]
        _LOGGER.info("Button pressed on leak sensor '%s'", sensor.name)

        self._pending_button_presses[sensor_id] = (
            self._pending_button_presses.get(sensor_id, 0) + 1
        )
        async_dispatcher_send(self.hass, f"{DOMAIN}_leak_update")
        self._bff_poll_task = self.hass.async_create_task(self._poll_bff_leak_state())

    @callback
    def _on_mqtt_state_update(self, device_id: str, state_data: dict[str, Any]) -> None:
        """Handle state update from MQTT.

        Called from aiomqtt's async message loop, which runs on the HA event
        loop. Safe to call async_set_updated_data() directly. The @callback
        decorator documents this event-loop-only contract.
        """
        # Handle leak sensor events (from multiSync messages)
        if state_data.get("_leak_event"):
            self._handle_leak_event(state_data)
            return

        # Handle button press events
        if state_data.get("_button_press"):
            self._handle_button_press(state_data)
            return

        if device_id not in self._devices:
            _LOGGER.debug("MQTT update for unknown device: %s", device_id)
            return

        state = self._states.get(device_id)
        if state is None:
            state = GoveeDeviceState.create_empty(device_id)
            self._states[device_id] = state

        was_offline = not state.online

        # Update state from MQTT data (also flips online back True — issue #68)
        state.update_from_mqtt(state_data)

        if was_offline:
            device = self._devices.get(device_id)
            _LOGGER.info(
                "MQTT push restored online status for %s (was offline per cloud)",
                device.name if device else device_id,
            )

        # Confirmed push — record transport health and end the optimistic
        # grace window for this device (state.update_from_mqtt also calls
        # clear_optimistic_window, but recording MQTT health is our job).
        self._record_transport_success(device_id, "mqtt")

        # Update coordinator data and notify HA
        self.async_set_updated_data(self._states)

        _LOGGER.debug(
            "MQTT state applied for %s: power=%s",
            device_id,
            state.power_state,
        )

    def _on_mqtt_give_up(self, attempts: int, last_error: str) -> None:
        """Called by MQTT client when reconnect loop exhausts MAX_RECONNECT_ATTEMPTS.

        Creates a repair issue so the user is prompted to reload the
        integration. Without this, the MQTT loop exits silently and the
        integration falls back to polling-only with no user-visible signal.
        """
        _LOGGER.warning(
            "MQTT gave up after %d attempts (last error: %s) — surfacing repair",
            attempts,
            last_error,
        )
        self._config_entry.async_create_background_task(
            self.hass,
            async_create_mqtt_issue(
                self.hass,
                self._config_entry,
                f"connection lost after {attempts} reconnect attempts: {last_error}",
            ),
            name="govee_mqtt_give_up_issue",
        )

    async def _async_update_data(self) -> dict[str, GoveeDeviceState]:
        """Fetch state for all devices (parallel).

        Called by DataUpdateCoordinator on poll interval.
        """
        # Pick up devices added to the account since setup (#101). Throttled and
        # failure-isolated inside the method so it never disrupts the state poll.
        await self._async_maybe_rediscover_devices()

        if not self._devices:
            return self._states

        # Create tasks for parallel fetching
        tasks = [
            self._fetch_device_state(device_id, device)
            for device_id, device in self._devices.items()
        ]

        # Scale timeout based on device count (2s per device, min 30s, max 120s)
        timeout = min(max(STATE_FETCH_TIMEOUT, len(self._devices) * 2), 120)

        # Wait for all with timeout
        try:
            async with asyncio.timeout(timeout):
                results = await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            _LOGGER.warning("State fetch timed out after %ds", timeout)
            return self._states

        # Process results
        successful_updates = 0
        for device_id, result in zip(self._devices.keys(), results):
            if isinstance(result, GoveeDeviceState):
                self._states[device_id] = result
                successful_updates += 1
            elif isinstance(result, GoveeAuthError):
                await async_create_auth_issue(self.hass, self._config_entry)
                raise ConfigEntryAuthFailed("Invalid API key") from result
            elif isinstance(result, Exception):
                _LOGGER.debug(
                    "Failed to fetch state for %s: %s",
                    device_id,
                    result,
                )
                # Keep previous state on error

        # Clear rate limit issue and restore poll interval if we got successful updates
        if successful_updates > 0 and self._rate_limited:
            self._rate_limited = False
            self.update_interval = self._original_update_interval
            _LOGGER.info(
                "Rate limit cleared, restoring poll interval to %s",
                self._original_update_interval,
            )
            await async_delete_rate_limit_issue(self.hass, self._config_entry)

        # Refresh transport-health snapshots tied to coordinator cadence.
        self._refresh_mqtt_health()
        self._refresh_ble_staleness()

        # LAN overlay (issue #57): re-correlate (throttled) then overlay the
        # fresh cloud state with the latest solicited devStatus reads. Runs AFTER
        # the cloud fan-in so LAN overlays FRESH cloud objects, never the reverse,
        # and mutates in place so the return below fires HA listeners without a
        # re-entrant async_set_updated_data. Failure-isolated: a LAN hiccup must
        # never fail the state poll.
        try:
            await self._async_maybe_rescan_lan()
            await self._refresh_lan_reads()
            self._refresh_lan_staleness()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Govee LAN read refresh failed: %s", err)

        return self._states

    async def _fetch_device_state(
        self,
        device_id: str,
        device: GoveeDevice,
    ) -> GoveeDeviceState | Exception:
        """Fetch state for a single device.

        Args:
            device_id: Device identifier.
            device: Device instance.

        Returns:
            GoveeDeviceState or Exception on error.
        """
        # Skip API call for group devices - state fetch always fails with 400
        if device.is_group:
            existing = self._states.get(device_id)
            if existing:
                # Group devices are always "available"; return a new instance
                # so listeners see a fresh object on identity comparison.
                refreshed = dataclasses.replace(existing, online=True)
                self._states[device_id] = refreshed
                return refreshed
            return GoveeDeviceState.create_empty(device_id)

        # Standalone water detectors (H5054) carry no useful developer-API state
        # — the poll only ever returns online=false and never the leak event.
        # Their online/leak state is owned by the BFF warnMessage poll
        # (_poll_water_detectors); a developer poll here would clobber it every
        # cycle. Preserve the BFF-managed state and skip the call (issue #62).
        if device.supports_water_leak_event:
            return self._states.get(device_id) or GoveeDeviceState.create_empty(
                device_id
            )

        # BFF-discovered thermo-hygrometers (H5301) are absent from the Developer
        # API, so /device/state would 400/return empty and clobber the
        # BFF-managed reading. State is owned by _refresh_bff_thermometers (#86).
        if device_id in self._bff_thermometer_ids:
            return self._states.get(device_id) or GoveeDeviceState.create_empty(
                device_id
            )

        try:
            state = await self._api_client.get_device_state(device_id, device.sku)
            self._record_transport_success(device_id, "cloud_api")

            # Preserve optimistic state fields that API doesn't reliably return.
            # Clear them when device is turned off (no longer active).
            existing_state = self._states.get(device_id)
            if existing_state:
                # Optimistic grace period (issue #60): if a control command
                # just fired, the device may not yet be visible to the cloud
                # (e.g. BLE-out-of-range or slow AWS propagation). Preserve
                # the optimistic power/brightness for a short window instead
                # of flipflopping the UI. MQTT pushes clear the window early.
                # The grace test is shared with the LAN read path (#57).
                if (
                    self._in_optimistic_grace(existing_state)
                    and existing_state.power_state != state.power_state
                ):
                    _LOGGER.debug(
                        "Preserving optimistic power for %s during grace "
                        "(API=%s optimistic=%s)",
                        device_id,
                        state.power_state,
                        existing_state.power_state,
                    )
                    state.power_state = existing_state.power_state
                    state.brightness = existing_state.brightness
                    state.source = "optimistic"
                    state.last_optimistic_update = existing_state.last_optimistic_update

                # Log state transitions from API for debugging stale-state issues
                if existing_state.power_state != state.power_state:
                    _LOGGER.debug(
                        "API state change for %s: power %s -> %s (was source=%s)",
                        device_id,
                        existing_state.power_state,
                        state.power_state,
                        existing_state.source,
                    )
                if existing_state.brightness != state.brightness:
                    _LOGGER.debug(
                        "API state change for %s: brightness %s -> %s",
                        device_id,
                        existing_state.brightness,
                        state.brightness,
                    )
                # Scenes persist on device across power cycles — always preserve
                if existing_state.active_scene:
                    state.active_scene = existing_state.active_scene
                if existing_state.active_scene_name:
                    state.active_scene_name = existing_state.active_scene_name
                # DIY scenes also persist across power cycles
                if existing_state.active_diy_scene:
                    state.active_diy_scene = existing_state.active_diy_scene

                # Govee API returns colorRgb=0 when the device is in certain
                # modes (scenes, color_temp, music, etc.).  This sentinel value
                # is not meaningful — preserve the last known real color so the
                # HA UI doesn't flash black after every poll cycle.
                if (
                    state.color is not None
                    and state.color.as_packed_int == 0
                    and existing_state.color is not None
                    and existing_state.color.as_packed_int != 0
                ):
                    state.color = existing_state.color
                # Preserve restore-target fields across API polls.
                # These are "memory" fields — always preserved regardless of power state.
                if existing_state.last_color is not None:
                    state.last_color = existing_state.last_color
                if existing_state.last_color_temp_kelvin is not None:
                    state.last_color_temp_kelvin = existing_state.last_color_temp_kelvin
                if existing_state.last_scene_id is not None:
                    state.last_scene_id = existing_state.last_scene_id
                if existing_state.last_scene_name is not None:
                    state.last_scene_name = existing_state.last_scene_name

                # Heater state: preserve across polls (API doesn't reliably return these)
                if existing_state.heater_temperature is not None:
                    state.heater_temperature = existing_state.heater_temperature
                if existing_state.heater_auto_stop is not None:
                    state.heater_auto_stop = existing_state.heater_auto_stop
                if (
                    existing_state.heater_temperature_unit is not None
                    and state.heater_temperature_unit is None
                ):
                    state.heater_temperature_unit = (
                        existing_state.heater_temperature_unit
                    )

                # Stand-alone thermometer/hygrometer readings (H5179, H5109,
                # H5110, HS5108, HS5106): battery-powered sensors push to the
                # cloud infrequently, and the /device/state response may omit
                # the value or return null between device-side updates. Without
                # preservation the entity flips to "unknown" after the first
                # poll. (#78 follow-up: temperature stops updating until restart.)
                if (
                    existing_state.sensor_temperature is not None
                    and state.sensor_temperature is None
                ):
                    state.sensor_temperature = existing_state.sensor_temperature
                if (
                    existing_state.sensor_humidity is not None
                    and state.sensor_humidity is None
                ):
                    state.sensor_humidity = existing_state.sensor_humidity

                # Battery and water-tank-full are owned by the BFF poll — the
                # Developer /device/state response carries neither, so the fresh
                # state has them as None. Preserve the BFF-applied values across
                # the developer poll that runs ~5x between BFF cycles, or they
                # would flicker to "unknown" each poll (issues #83, #118).
                if existing_state.battery is not None and state.battery is None:
                    state.battery = existing_state.battery
                if existing_state.water_full is not None and state.water_full is None:
                    state.water_full = existing_state.water_full
                # Occupancy (H5127) is a momentary push event; the developer
                # /device/state poll returns only `online` for it (never the
                # bodyAppearedEvent value), so the fresh state has presence=None.
                # Preserve the last pushed value or the occupancy sensor would
                # reset to "unknown" on every poll (issue #124).
                if existing_state.presence is not None and state.presence is None:
                    state.presence = existing_state.presence

                # Stamp when the reading last changed (after preservation, so a
                # preserved-unchanged value does not count as a change).
                self._note_sensor_reading_change(device_id, state, existing_state)

                self._preserve_optimistic_field(
                    existing_state, state, device_id, "dreamview_enabled", "DreamView"
                )
                # Music mode has extra fields to preserve alongside the flag
                if existing_state.music_mode_enabled:
                    if state.power_state:
                        state.music_mode_enabled = existing_state.music_mode_enabled
                        state.music_mode_value = existing_state.music_mode_value
                        state.music_mode_name = existing_state.music_mode_name
                        state.music_sensitivity = existing_state.music_sensitivity
                    else:
                        _LOGGER.debug(
                            "Clearing music mode for %s (device turned off)",
                            device_id,
                        )

            return state

        except GoveeDeviceNotFoundError:
            # Expected for group devices - use existing/optimistic state
            _LOGGER.debug(
                "State query failed for group device %s [expected]", device_id
            )
            existing = self._states.get(device_id)
            if existing:
                existing.online = True  # Group devices are always "available"
                return existing
            return GoveeDeviceState.create_empty(device_id)

        except GoveeRateLimitError as err:
            _LOGGER.warning("Rate limit hit, keeping previous state")
            # Create rate limit repair issue and back off (only once)
            if not self._rate_limited:
                self._rate_limited = True
                reset_time = "unknown"
                # Back off: increase poll interval to retry_after or 120s
                backoff_seconds = int(err.retry_after) if err.retry_after else 120
                self.update_interval = timedelta(seconds=backoff_seconds)
                _LOGGER.warning(
                    "Rate limited, increasing poll interval to %ds",
                    backoff_seconds,
                )
                if err.retry_after:
                    reset_time = f"{int(err.retry_after)} seconds"
                self._config_entry.async_create_background_task(
                    self.hass,
                    async_create_rate_limit_issue(
                        self.hass,
                        self._config_entry,
                        reset_time,
                    ),
                    name="govee_rate_limit_issue",
                )
            existing = self._states.get(device_id)
            return existing if existing else GoveeDeviceState.create_empty(device_id)

        except Exception as err:
            self._record_transport_failure(device_id, "cloud_api", str(err))
            return err

    async def async_control_device(
        self,
        device_id: str,
        command: DeviceCommand,
    ) -> bool:
        """Send control command to device with optimistic update.

        Args:
            device_id: Device identifier.
            command: Command to execute.

        Returns:
            True if command succeeded.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device: %s", device_id)
            return False

        # Track power-off commands so segment entities can detect them
        # before the first await, ensuring concurrent coroutines see the flag.
        is_power_off = isinstance(command, PowerCommand) and not command.power_on
        if is_power_off:
            self._pending_power_off.add(device_id)

        try:
            # BLE-first dispatch: if a BLE transport is available for this
            # device, try it before the cloud REST API. BLE is ~10x faster
            # (~50ms local vs ~500ms cloud) and works when internet is down.
            if HAS_BLUETOOTH and device_id in self._ble_devices:
                if await self._try_ble_command(device_id, command):
                    self._apply_optimistic_update(device_id, command)
                    self.async_set_updated_data(self._states)
                    return True
                # BLE failed — fall through to LAN/MQTT/REST

            # LAN (UDP) control tier — sits between BLE and MQTT so precedence
            # is BLE > LAN > MQTT > REST. Unlike the BLE/MQTT blocks above,
            # _try_lan_command owns its optimistic update + transport recording
            # internally: a LAN write is unacked, so it applies optimistic state
            # immediately for snappy UI, then confirms by reading the device
            # back. A confirmed write returns True here; an unreachable /
            # unconfirmed / value-mismatched write returns False and falls
            # through to MQTT -> REST, so a device is never stranded.
            if await self._try_lan_command(device_id, device, command):
                return True
            # LAN unavailable / unconfirmed — fall through to MQTT/REST.

            # MQTT-native control tier: when enabled and connected, push
            # power/brightness/color over the AWS IoT channel (~50ms) instead
            # of the REST cloud API (~500ms). Group devices and non-capable
            # commands (color temp, scenes, segments) fall through to REST.
            if (
                self._enable_mqtt_control
                and self.mqtt_connected
                and not device.is_group
            ):
                if await self._try_mqtt_command(device_id, device.sku, command):
                    self._record_transport_send(device_id, "mqtt")
                    self._apply_optimistic_update(device_id, command)
                    self.async_set_updated_data(self._states)
                    return True
                # MQTT not applicable / publish failed — fall through to REST

            # Serialize segment commands per device. Govee silently drops
            # parallel segment requests (issue #53); sequential dispatch
            # with a small gap respects the 100/min rate limit. Optimistic
            # state is applied before entering the lock so UI feedback is
            # immediate; the actual REST write trails by <~1.5s for a
            # full 14-segment burst on RGBIC strips.
            if isinstance(command, SegmentColorCommand):
                self._apply_optimistic_update(device_id, command)
                self.async_set_updated_data(self._states)
                success = await self._dispatch_segment_command(
                    device_id, device.sku, command
                )
                return success

            success = await self._api_client.control_device(
                device_id,
                device.sku,
                command,
            )

            if success:
                # A REST control call sends AND gets a response back, so stamp
                # both directions; MQTT publishes are fire-and-forget (send only).
                self._record_transport_send(device_id, "cloud_api")
                self._record_transport_success(device_id, "cloud_api")
                # Apply optimistic update
                self._apply_optimistic_update(device_id, command)
                self.async_set_updated_data(self._states)
            else:
                self._record_transport_failure(
                    device_id, "cloud_api", "control_returned_false"
                )

            return success

        except GoveeAuthError as err:
            self._record_transport_failure(device_id, "cloud_api", "auth_failed")
            raise ConfigEntryAuthFailed("Invalid API key") from err
        except GoveeApiError as err:
            _LOGGER.error("Control command failed: %s", err)
            self._record_transport_failure(device_id, "cloud_api", str(err))
            return False
        finally:
            if is_power_off:
                self._pending_power_off.discard(device_id)

    async def _dispatch_segment_command(
        self,
        device_id: str,
        sku: str,
        command: SegmentColorCommand,
    ) -> bool:
        """Send a single segment command under a per-device lock.

        Pacing: after each REST call we hold the lock for a short delay so
        the next segment command has to wait — this bounds the effective
        dispatch rate for a multi-segment burst to stay within Govee's
        rate limit without adding a separate queue.
        """
        lock = self._segment_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            try:
                success = await self._api_client.control_device(device_id, sku, command)
            except GoveeAuthError:
                self._record_transport_failure(device_id, "cloud_api", "auth_failed")
                raise
            except GoveeApiError as err:
                _LOGGER.error("Segment command failed for %s: %s", device_id, err)
                self._record_transport_failure(device_id, "cloud_api", str(err))
                return False

            if success:
                # REST send + response — stamp both directions (see control path).
                self._record_transport_send(device_id, "cloud_api")
                self._record_transport_success(device_id, "cloud_api")
            else:
                self._record_transport_failure(
                    device_id, "cloud_api", "segment_returned_false"
                )

            # Pace the next acquire so bursts don't trip silent rate limiting.
            await asyncio.sleep(SEGMENT_COMMAND_PACING_SECONDS)
            return success

    async def _try_lan_command(
        self,
        device_id: str,
        device: GoveeDevice,
        command: DeviceCommand,
    ) -> bool:
        """Attempt a LAN (UDP) control write, verified by reading the device back.

        The LAN control tier sits between BLE and MQTT (BLE > LAN > MQTT >
        REST). A LAN write is fire-and-forget and unacknowledged, so this
        method confirms it by reading the device's reported state back within
        ``LAN_WRITE_CONFIRM_TIMEOUT`` and requiring an exact (power) /
        within-tolerance (brightness) match. Anything that cannot be confirmed —
        LAN disabled, a group / numeric id, an uncorrelated device, an
        unavailable LAN read-health, a write-suppression cooldown, a non-LAN
        command, a failed send, no reply, or a value mismatch — returns ``False``
        so the caller falls through to MQTT/REST, which re-applies the optimistic
        state and actually delivers the write. A device is therefore never
        stranded on a LAN write the firmware ignored.

        Transport health vs. write viability are kept separate (issue #57): a
        confirm miss (no reply / value mismatch) NEVER marks the ``lan`` transport
        unavailable — that produced a flap synced to control activity. Health is
        read-driven (the poll read + unsolicited pushes); a run of confirm misses
        only suppresses LAN *write attempts* (:meth:`_note_lan_write_miss`) so the
        device's writes route to MQTT/REST without paying the confirm timeout,
        while its LAN reads — and the connectivity sensor — keep working.

        On a confirmed write the LAN send + success are recorded and the
        device's real reported values are locked in via :meth:`_apply_lan_read`;
        an optimistic update is applied immediately (before the blocking confirm
        read) so the UI responds in ~0ms.

        Args:
            device_id: The device to control.
            device: The device's :class:`GoveeDevice` (sku / group / range).
            command: The domain command to send.

        Returns:
            ``True`` only when the write was confirmed by a matching readback.
        """
        # [1] LAN disabled or degraded — the client is the master switch.
        if self._lan_client is None:
            return False

        # [2] Group / numeric ids have no LAN unicast identity.
        if device.is_group or device_id.isdigit():
            return False

        # [3] Not a correlated LAN device — no IP to write to.
        info = self._lan_devices.get(device_id)
        if info is None:
            return False

        # [4] Write-health gate: never write into a black hole. A device whose
        #     reads have gone stale — or which has never had a successful LAN
        #     read yet (TransportHealth defaults is_available=False, so the
        #     FIRST control after startup falls through HERE until the first LAN
        #     read marks it available) — must fall back to MQTT/REST.
        health = self._transport.get(device_id, "lan")
        if health is None or not health.is_available:
            return False

        # [4.5] Write-suppression cooldown (issue #57): this device's recent LAN
        #     writes did not confirm, so skip the LAN write attempt AND its
        #     confirm-read wait and fall straight to MQTT/REST. LAN READS keep
        #     running and the lan_connectivity sensor stays Connected — only the
        #     futile (and latency-adding) write attempt is paused. The cooldown
        #     re-arms itself so a device that starts confirming again is retried.
        if self._lan_writes_suppressed(device_id):
            return False

        # [5] Only power + brightness map to LAN; colour / colour-temp / scenes /
        #     segments / music / DIY / work-modes / toggles (and the H5080/H5083
        #     power quirk) return None so they keep using REST, where the write
        #     is acknowledged and no readback confirmation is needed.
        mapped = command_to_lan(command, device.sku, device.brightness_range)
        if mapped is None:
            return False
        cmd, data = mapped

        # [6] Fire-and-forget unicast write. A sendto OSError (surfaced as a
        #     False return) means the device is unreachable on LAN right now —
        #     record the failure and fall through.
        ip = info.ip
        sent = await self._lan_client.async_send_command(ip, cmd, data)
        if not sent:
            self._record_transport_failure(device_id, "lan", "send_failed")
            return False

        # [7] Apply optimistic state immediately for ~0ms UI feedback, before the
        #     confirm read blocks for up to LAN_WRITE_CONFIRM_TIMEOUT.
        self._apply_optimistic_update(device_id, command)
        self.async_set_updated_data(self._states)

        # [8] Verify-by-read: a LAN write is unacked, so read the device back and
        #     require the reported value to match what we sent.
        reply = await self._lan_client.async_read_one(ip, LAN_WRITE_CONFIRM_TIMEOUT)
        if reply is None:
            # No reply in the confirm window — ambiguous. Count it toward write
            # suppression (so we stop paying the confirm timeout on a device that
            # won't confirm) but DO NOT mark LAN down: the periodic poll read is
            # the authority on transport liveness. The write already fell through
            # to MQTT/REST, which re-applies the optimistic update and delivers
            # it. A transient power-on miss must never flap LAN off (#57).
            self._note_lan_write_miss(device_id)
            return False

        if not self._lan_write_confirmed(device, command, reply):
            # The device ANSWERED — so LAN is alive — but reports a value other
            # than the one we sent. This is almost always power-on reporting lag
            # (the controller briefly echoes its pre-command state), NOT a
            # transport fault. Record only LAN read SUCCESS — keeps the transport
            # available and the sensor Connected and refreshes the staleness
            # clock — but DO NOT overlay this reply onto state: it is by
            # definition stale (a mismatch), and we are inside the optimistic
            # grace window, so overlaying its color/color-temp would briefly
            # revert an in-flight optimistic colour write. Count a write-miss
            # toward suppression and fall through so the write still lands via
            # MQTT/REST. Transport health is NEVER flipped on a content mismatch —
            # that flap, synced to control activity, was the reported bug (#57).
            self._record_transport_success(device_id, "lan")
            self._note_lan_write_miss(device_id)
            return False

        # Confirmed: stamp the LAN write (send + success — the verify-by-read
        # recording asymmetry), re-arm LAN writes (reset the miss streak), and
        # lock in the real device-reported values.
        self._record_transport_send(device_id, "lan")
        self._record_transport_success(device_id, "lan")
        self._clear_lan_write_misses(device_id)
        self._apply_lan_read(device_id, reply)
        return True

    def _lan_write_confirmed(
        self,
        device: GoveeDevice,
        command: DeviceCommand,
        reply: LanDevStatus,
    ) -> bool:
        """Return ``True`` when a LAN readback confirms the just-sent write.

        Only power and brightness reach LAN (``command_to_lan`` returns ``None``
        for everything else), so only those two are confirmable:

        * :class:`PowerCommand` — require an exact ``reply.on == command.power_on``.
          A plug whose ``devStatus`` lacks ``onOff`` reports ``reply.on is None``
          and so never matches, falling through instead of being stranded on a
          write the device ignored.
        * :class:`BrightnessCommand` — scale the LAN 0-100 readback into the
          device-native range and require it within
          ``LAN_BRIGHTNESS_CONFIRM_TOLERANCE`` of the requested value, absorbing
          0-100<->native rounding.

        Any other command type is treated as unconfirmable (returns ``False``).
        Such commands should never reach LAN, so this is a defensive backstop.

        Args:
            device: The device under control (for its brightness range).
            command: The command that was sent.
            reply: The parsed LAN ``devStatus`` readback.

        Returns:
            ``True`` only when the readback matches the sent value.
        """
        if isinstance(command, PowerCommand):
            return reply.on == command.power_on
        if isinstance(command, BrightnessCommand):
            if reply.brightness_0_100 is None:
                return False
            native = lan_brightness_to_device(
                reply.brightness_0_100, device.brightness_range
            )
            return abs(native - command.brightness) <= LAN_BRIGHTNESS_CONFIRM_TOLERANCE
        return False

    async def _try_mqtt_command(
        self, device_id: str, sku: str, command: DeviceCommand
    ) -> bool:
        """Attempt to send a command via native MQTT. Returns True on success.

        Only power, brightness, and RGB color map to native MQTT commands;
        all others return False so the caller falls back to REST. A failure
        is recorded only when a publish was actually attempted and failed
        (not when the command simply has no MQTT representation).
        """
        mapped = command_to_mqtt(command, sku)
        if mapped is None:
            return False  # Not MQTT-capable — silent fall-through to REST.

        if self._mqtt_client is None:
            return False

        topic = await self._ensure_device_topic(device_id)
        if not topic:
            return False

        cmd, data, cmd_version = mapped
        success = await self._mqtt_client.async_publish_command(
            topic, cmd, data, cmd_version=cmd_version
        )
        if not success:
            self._record_transport_failure(device_id, "mqtt", "publish_failed")
        return success

    async def _try_ble_command(self, device_id: str, command: DeviceCommand) -> bool:
        """Attempt to send a command via BLE. Returns True on success.

        Only power, brightness, and RGB color commands are BLE-capable.
        Scenes, color_temp, work modes, etc. fall through to REST.
        """
        ble_device = self._ble_devices.get(device_id)
        if ble_device is None:
            return False

        try:
            if isinstance(command, PowerCommand):
                if command.power_on:
                    await ble_device.turn_on()
                else:
                    await ble_device.turn_off()
            elif isinstance(command, BrightnessCommand):
                await ble_device.set_brightness(command.brightness)
            elif isinstance(command, ColorCommand):
                await ble_device.set_rgb(
                    command.color.r,
                    command.color.g,
                    command.color.b,
                )
            else:
                # Not BLE-capable (scenes, color_temp, work modes, etc.)
                return False
        except Exception as err:
            _LOGGER.debug(
                "BLE command failed for %s, falling back to REST",
                device_id,
                exc_info=True,
            )
            self._record_transport_failure(device_id, "ble", str(err))
            return False
        else:
            _LOGGER.debug(
                "BLE command succeeded for %s: %s", device_id, type(command).__name__
            )
            self._record_transport_success(device_id, "ble")
            # A successful BLE write reaches the device directly — flip
            # `online` back True if a stale `online: false` from the cloud
            # is masking a recovered device (issue #68).
            ble_state = self._states.get(device_id)
            if ble_state is not None and not ble_state.online:
                ble_state.online = True
            return True

    async def _ensure_device_topic(self, device_id: str) -> str | None:
        """Get device MQTT topic, refreshing if needed.

        If the topic is missing for this device but we have credentials,
        attempt a single refresh from the API.
        """
        topic = self._device_topics.get(device_id)
        if topic is not None:
            return topic

        # Topic missing - try one refresh
        if self._iot_credentials:
            _LOGGER.debug("Device topic missing for %s, refreshing from API", device_id)
            await self._fetch_device_topics()
            topic = self._device_topics.get(device_id)
            if topic:
                _LOGGER.debug("Got device topic for %s after refresh", device_id)

        return topic

    async def async_send_music_mode(
        self,
        device_id: str,
        enabled: bool,
        sensitivity: int = 50,
        music_mode: int = 1,
        last_scene_id: str | None = None,
        last_scene_name: str | None = None,
    ) -> bool:
        """Send music mode command via REST API first, with BLE fallback.

        Tries REST API for devices with STRUCT music mode capability,
        then falls back to BLE passthrough via MQTT.

        Args:
            device_id: Device identifier.
            enabled: True to enable music mode, False to disable.
            sensitivity: Microphone sensitivity 0-100 (default 50).
            music_mode: Music mode value (default 1 = Rhythm).
            last_scene_id: Last active scene ID (for restoring on disable).
            last_scene_name: Last active scene name (for restoring on disable).

        Returns:
            True if command was sent successfully.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for music mode: %s", device_id)
            return False

        # Try REST API first for devices with STRUCT music mode capability
        if device.has_struct_music_mode:
            if enabled:
                try:
                    command = MusicModeCommand(
                        music_mode=music_mode,
                        sensitivity=sensitivity,
                        auto_color=1,
                    )
                    success = await self.async_control_device(device_id, command)
                    if success:
                        _LOGGER.debug(
                            "Sent music mode ON to %s via REST API", device.name
                        )
                        return True
                except ConfigEntryAuthFailed:
                    raise
                except Exception as err:
                    _LOGGER.debug(
                        "REST music mode ON failed for %s: %s, trying BLE",
                        device.name,
                        err,
                    )
            else:
                # Disable music mode via REST: restore last scene or send brightness
                success = await self._rest_disable_music_mode(
                    device_id, last_scene_id, last_scene_name
                )
                if success:
                    return True

        # Fall back to BLE passthrough via MQTT
        if not self._ble_manager.available:
            _LOGGER.warning(
                "Cannot send music mode for %s: MQTT not connected",
                device_id,
            )
            return False

        success = await self._ble_manager.async_send_music_mode(
            device_id, device.sku, enabled, sensitivity
        )

        if success:
            # Apply optimistic update to state
            state = self._states.get(device_id)
            if state:
                state.apply_optimistic_music_mode(enabled)
            _LOGGER.debug(
                "Sent music mode %s (sensitivity=%d) to %s via BLE",
                "ON" if enabled else "OFF",
                sensitivity,
                device.name,
            )

        return success

    async def _rest_disable_music_mode(
        self,
        device_id: str,
        last_scene_id: str | None = None,
        last_scene_name: str | None = None,
    ) -> bool:
        """Disable music mode via REST API.

        Tries to restore the last active scene, then falls back to a
        brightness command to cleanly exit music mode.

        Args:
            device_id: Device identifier.
            last_scene_id: Scene ID to restore.
            last_scene_name: Scene name to restore.

        Returns:
            True if successfully disabled via REST.
        """
        device = self._devices.get(device_id)
        success = False

        # Try restoring last active scene
        if last_scene_id and last_scene_name:
            command = SceneCommand(
                scene_id=int(last_scene_id),
                scene_name=last_scene_name,
            )
            success = await self.async_control_device(device_id, command)
            if success:
                _LOGGER.debug(
                    "Restored scene '%s' on %s after music mode off",
                    last_scene_name,
                    device.name if device else device_id,
                )

        if not success:
            # No last scene or scene restore failed - send brightness command
            # to cleanly exit music mode via REST (avoids visible power cycle)
            state = self._states.get(device_id)
            brightness = state.brightness if state and state.brightness else 100
            success = await self.async_control_device(
                device_id, BrightnessCommand(brightness=brightness)
            )
            _LOGGER.debug(
                "Sent brightness command to %s to exit music mode (brightness=%d)",
                device.name if device else device_id,
                brightness,
            )

        if success:
            self.clear_music_mode(device_id)

        return success

    async def async_send_dreamview(self, device_id: str, enabled: bool) -> bool:
        """Send DreamView command via REST API, with BLE fallback.

        Args:
            device_id: Device identifier.
            enabled: True to enable DreamView, False to disable.

        Returns:
            True if command was sent successfully.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for DreamView: %s", device_id)
            return False

        # Try REST API first (works for HTTP-capable devices like H6097)
        try:
            success = await self.async_control_device(
                device_id, create_dreamview_command(enabled)
            )
            if success:
                _LOGGER.debug(
                    "Sent DreamView %s to %s via REST API",
                    "ON" if enabled else "OFF",
                    device.name,
                )
                return True
        except ConfigEntryAuthFailed:
            # Let authentication errors propagate so Home Assistant can handle reauth
            raise
        except Exception as err:
            _LOGGER.debug("REST DreamView failed for %s: %s", device.name, err)

        # Fall back to BLE passthrough for devices that need it
        if not self._ble_manager.available:
            _LOGGER.warning(
                "Cannot send DreamView for %s: MQTT not connected",
                device_id,
            )
            return False

        success = await self._ble_manager.async_send_dreamview(
            device_id, device.sku, enabled
        )

        if success:
            state = self._states.get(device_id)
            if state:
                state.apply_optimistic_dreamview(enabled)
            _LOGGER.debug(
                "Sent DreamView %s to %s via BLE passthrough",
                "ON" if enabled else "OFF",
                device.name,
            )

        return success

    async def async_send_diy_scene(
        self,
        device_id: str,
        scene_id: int,
        scene_name: str = "",
    ) -> bool:
        """Send DIY scene command via REST API, with BLE fallback.

        Args:
            device_id: Device identifier.
            scene_id: DIY scene ID from the API.
            scene_name: DIY scene name for logging/state.

        Returns:
            True if command was sent successfully.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for DIY scene: %s", device_id)
            return False

        # Try REST API first
        try:
            command = DIYSceneCommand(scene_id=scene_id, scene_name=scene_name)
            success = await self.async_control_device(device_id, command)
            if success:
                _LOGGER.debug(
                    "Activated DIY scene '%s' on %s via REST API",
                    scene_name,
                    device.name,
                )
                return True
            _LOGGER.debug(
                "REST DIY scene returned failure for %s, trying BLE passthrough",
                device.name,
            )
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            _LOGGER.debug("REST DIY scene failed for %s: %s", device.name, err)

        # Fall back to BLE passthrough
        if not self._ble_manager.available:
            _LOGGER.warning(
                "Cannot send DIY scene for %s: MQTT not connected",
                device_id,
            )
            return False

        success = await self._ble_manager.async_send_diy_scene(
            device_id, device.sku, scene_id
        )

        if success:
            state = self._states.get(device_id)
            if state:
                state.apply_optimistic_diy_scene(str(scene_id))
            _LOGGER.debug(
                "Activated DIY scene '%s' on %s via BLE passthrough",
                scene_name,
                device.name,
            )

        return success

    async def async_send_diy_style(
        self, device_id: str, style: str, speed: int = 50
    ) -> bool:
        """Send DIY style command via BLE passthrough.

        Note: DIY style changes require complex multi-packet BLE sequences.
        This is a placeholder that applies optimistic state only.
        Full BLE packet implementation is not yet available.

        Args:
            device_id: Device identifier.
            style: DIY style name (Fade, Jumping, Flicker, Marquee, Music).
            speed: Animation speed 0-100 (default 50).

        Returns:
            True if optimistic state was applied.
        """
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Unknown device for DIY style: %s", device_id)
            return False

        style_value = DIY_STYLE_NAMES.get(style)
        if style_value is None:
            _LOGGER.warning("Unknown DIY style: %s", style)
            return False

        _LOGGER.debug(
            "DIY style command for %s is optimistic only - no device command sent. "
            "Full BLE packet implementation is not yet available",
            device.name,
        )

        # Apply optimistic state update
        state = self._states.get(device_id)
        if state:
            state.apply_optimistic_diy_style(style, style_value)

        return False

    @staticmethod
    def _preserve_optimistic_field(
        existing: GoveeDeviceState,
        new: GoveeDeviceState,
        device_id: str,
        field: str,
        label: str,
    ) -> None:
        """Preserve an optimistic state field across API polls.

        If the existing state has a truthy value for the field, preserve it
        on the new state when the device is on. Clear it when the device is off.
        """
        if getattr(existing, field):
            if new.power_state:
                setattr(new, field, getattr(existing, field))
            else:
                _LOGGER.debug(
                    "Clearing %s for %s (device turned off)", label, device_id
                )

    def _apply_optimistic_update(
        self,
        device_id: str,
        command: DeviceCommand,
    ) -> None:
        """Apply optimistic state update based on command."""
        state = self._states.get(device_id)
        if not state:
            return

        if isinstance(command, PowerCommand):
            state.apply_optimistic_power(command.power_on)
        elif isinstance(command, BrightnessCommand):
            state.apply_optimistic_brightness(command.brightness)
        elif isinstance(command, ColorCommand):
            state.apply_optimistic_color(command.color)
        elif isinstance(command, ColorTempCommand):
            state.apply_optimistic_color_temp(command.kelvin)
        elif isinstance(command, SceneCommand):
            state.apply_optimistic_scene(str(command.scene_id), command.scene_name)
        elif isinstance(command, DIYSceneCommand):
            state.apply_optimistic_diy_scene(str(command.scene_id))
        elif isinstance(command, ModeCommand):
            if command.mode_instance == INSTANCE_HDMI_SOURCE:
                state.apply_optimistic_hdmi_source(command.value)
        elif isinstance(command, TemperatureSettingCommand):
            state.heater_temperature = command.temperature
            state.heater_auto_stop = command.auto_stop
        elif isinstance(command, WorkModeCommand):
            state.apply_optimistic_work_mode(command.work_mode, command.mode_value)
        elif isinstance(command, MusicModeCommand):
            # Look up mode name from device capabilities for display
            device = self._devices.get(device_id)
            mode_name = None
            if device:
                for opt in device.get_music_mode_options():
                    if opt.get("value") == command.music_mode:
                        mode_name = opt.get("name")
                        break
            state.apply_optimistic_music_mode_struct(
                command.music_mode,
                command.sensitivity,
                mode_name,
            )
        elif isinstance(command, ToggleCommand):
            # Handle toggle commands (DreamView, night light, thermostat, etc)
            if command.toggle_instance == INSTANCE_DREAMVIEW:
                state.apply_optimistic_dreamview(command.enabled)
            elif command.toggle_instance == INSTANCE_THERMOSTAT_TOGGLE:
                state.heater_auto_stop = 1 if command.enabled else 0

    async def async_get_scenes(
        self,
        device_id: str,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Get available scenes for a device.

        Args:
            device_id: Device identifier.
            refresh: Force refresh from API.

        Returns:
            List of scene definitions.
        """
        device = self._devices.get(device_id)
        return await self._scene_cache.async_get_scenes(device_id, device, refresh)

    async def async_get_diy_scenes(
        self,
        device_id: str,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Get available DIY scenes for a device.

        Args:
            device_id: Device identifier.
            refresh: Force refresh from API.

        Returns:
            List of DIY scene definitions.
        """
        device = self._devices.get(device_id)
        return await self._scene_cache.async_get_diy_scenes(device_id, device, refresh)

    async def async_clear_scene(self, device_id: str) -> None:
        """Clear active scene by sending a color/color_temp command to exit it on the device.

        Brightness commands don't exit scenes, so we must send a color or color_temp
        command. Restores the last known color/color_temp when available.
        """
        state = self._states.get(device_id)
        device = self._devices.get(device_id)
        if not state or not device:
            return

        # Nothing to clear if no scene is active
        if not state.active_scene and not state.active_diy_scene:
            self.clear_scene(device_id)
            self.clear_diy_scene(device_id)
            return

        # HDMI sync boxes (e.g., H6604 AI Sync Box) have no meaningful "static
        # color" to restore — their default mode is video sync of the live HDMI
        # feed. Sending ColorCommand(white) here would lock the device into
        # manual color mode and lose the sync (issue #48: "Setting DIY scene
        # to none just leaves a flat white image"). Re-selecting the HDMI
        # source forces the device back into Video Sync.
        if device.supports_hdmi_source:
            source = state.hdmi_source
            if source is None:
                options = device.get_hdmi_source_options()
                source = int(options[0]["value"]) if options else 1
            success = await self.async_control_device(
                device_id,
                ModeCommand(mode_instance=INSTANCE_HDMI_SOURCE, value=int(source)),
            )
            if success:
                self.clear_scene(device_id)
                self.clear_diy_scene(device_id)
            return

        # Resolve the color to restore. Skip RGBColor(0,0,0) — the API returns
        # colorRgb=0 when a scene is running, which is not a meaningful restore target.
        color = state.color or state.last_color
        if color and color.as_packed_int == 0:
            color = state.last_color
        # Final guard: never restore black even if last_color is somehow (0,0,0)
        if color and color.as_packed_int == 0:
            color = None
        color_temp = state.color_temp_kelvin or state.last_color_temp_kelvin

        success = False
        if color and device.supports_rgb:
            success = await self.async_control_device(
                device_id, ColorCommand(color=color)
            )
        elif color_temp and device.supports_color_temp:
            success = await self.async_control_device(
                device_id, ColorTempCommand(kelvin=color_temp)
            )
        elif device.supports_rgb:
            # Prefer RGB white — Govee API reflects this value reliably
            # (colorRgb=16777215), unlike color_temp which may return 0.
            success = await self.async_control_device(
                device_id, ColorCommand(color=RGBColor(255, 255, 255))
            )
        elif device.supports_color_temp:
            # Fallback for color-temp-only devices
            ct_range = device.color_temp_range
            if ct_range:
                midpoint = (ct_range.min_kelvin + ct_range.max_kelvin) // 2
            else:
                midpoint = 4000
            success = await self.async_control_device(
                device_id, ColorTempCommand(kelvin=midpoint)
            )

        if success:
            # ColorCommand/ColorTempCommand already clear active_scene via optimistic handlers,
            # but we also need to clear active_diy_scene explicitly.
            self.clear_scene(device_id)
            self.clear_diy_scene(device_id)

    def clear_scene(self, device_id: str) -> None:
        """Clear active scene for a device."""
        state = self._states.get(device_id)
        if state:
            state.active_scene = None
            state.active_scene_name = None
            state.source = "optimistic"

    def clear_diy_scene(self, device_id: str) -> None:
        """Clear active DIY scene for a device."""
        state = self._states.get(device_id)
        if state:
            state.active_diy_scene = None
            state.source = "optimistic"

    def clear_music_mode(self, device_id: str) -> None:
        """Clear music mode state for a device."""
        state = self._states.get(device_id)
        if state:
            state.music_mode_enabled = False
            state.source = "optimistic"

    def restore_group_state(
        self, device_id: str, power: bool, brightness: int | None = None
    ) -> None:
        """Restore state for a group device from HA state machine."""
        state = self._states.get(device_id)
        if state:
            state.power_state = power
            if brightness is not None:
                state.brightness = brightness
            state.source = "optimistic"

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and cleanup resources."""
        # Cancel BFF polling
        if self._bff_poll_unsub:
            self._bff_poll_unsub()
            self._bff_poll_unsub = None
        if self._bff_poll_task and not self._bff_poll_task.done():
            self._bff_poll_task.cancel()
            self._bff_poll_task = None
        # Cancel standalone water-detector polling (issue #62)
        if self._wd_poll_unsub:
            self._wd_poll_unsub()
            self._wd_poll_unsub = None

        # Disconnect all BLE devices
        for ble_device in self._ble_devices.values():
            await ble_device.stop()
        self._ble_devices.clear()

        if self._mqtt_client:
            await self._mqtt_client.async_stop()
            self._mqtt_client = None

        # Drop the multicast group and release the :4002 bind so a reload /
        # unload never leaks the LAN socket (issue #57, blocking #5).
        if self._lan_client is not None:
            await self._lan_client.async_stop()
            self._lan_client = None

        await self._api_client.close()
