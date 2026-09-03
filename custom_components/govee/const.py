"""Constants for Govee integration."""

from typing import Final

DOMAIN: Final = "govee"

# Config entry keys
CONF_API_KEY: Final = "api_key"
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"

# Options keys
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_ENABLE_GROUPS: Final = "enable_groups"
CONF_ENABLE_SCENES: Final = "enable_scenes"
CONF_ENABLE_DIY_SCENES: Final = "enable_diy_scenes"
CONF_EXPOSE_TRANSPORT_ENTITIES: Final = "expose_transport_entities"
CONF_ENABLE_MQTT_CONTROL: Final = "enable_mqtt_control"

# Standalone water-detector (H5054) leak-poll interval (seconds). These RF-only
# sensors deliver their trip only via the account warnMessage history (issue
# #62); a leak surfaces with up to this much latency. Configurable because the
# account API's rate limit is unverified (homebridge issue #543) — users with
# many detectors may want to back off, while a single detector can poll faster.
CONF_WATER_DETECTOR_POLL_INTERVAL: Final = "water_detector_poll_interval"

# Extra LAN discovery targets for devices the local multicast scan can't reach —
# e.g. Govee devices on a different VLAN/subnet than Home Assistant (issue #57).
# Free-text list (comma / newline / space separated) of device IPs, broadcast
# addresses, and CIDR subnets (≤ /24, unicast-swept since inter-VLAN firewalls
# usually drop directed broadcast). Empty = local multicast scan only.
#
# Also accepts ``device_id=ip`` (skip discovery, bind that device straight to
# an IP) and ``device_id=ip!`` (same, plus mark it write-only: skip the
# read-health gate and write-confirm readback for firmware that accepts LAN
# writes but never answers a scan or devStatus read — issue #164). See
# ``api.lan.parse_lan_device_overrides``.
CONF_LAN_TARGETS: Final = "lan_targets"

# Some Govee thermometer/hygrometer SKUs report temperatures in Fahrenheit via
# the Cloud API without unit metadata, while the native sensor unit is tagged
# Celsius — so a 101°F reading surfaces as 213.5°F in HA (issues #72, #78, #96).
# This option controls normalization:
#   "auto"       -> convert only for SKUs in FAHRENHEIT_REPORTING_SKUS (default)
#   "fahrenheit" -> always convert (treat API value as °F)
#   "celsius"    -> never convert (trust API value as °C)
CONF_API_TEMPERATURE_UNIT: Final = "api_temperature_unit"

# SKUs observed reporting sensorTemperature in Fahrenheit regardless of account
# locale. Used by the "auto" mode to convert out-of-the-box. Compared
# case-insensitively against GoveeDevice.sku.
#   H717A (smart kettle): reports its temperature in °F under the °C-tagged
#     native unit, so 187°F surfaced as 187°C — impossible for a kettle (water
#     boils at 100°C); real value ~86°C (issue #115).
#   H5106 / H5140 (air-quality / CO₂ monitors): report °F as a plain float
#     (e.g. 73.9°F ≈ 23.3°C), surfaced under the °C unit as a "wrong large
#     value". Confirmed by reporter diagnostics (issue #116) — they are NOT
#     centi-encoded, just Fahrenheit; same class as #72/#78/#96.
#   H5075 / H5100 (thermo-hygrometers): report °F as a plain float under the
#     °C-tagged unit (screenshots show 80.8°C / 70.7°C at normal room
#     temperature — i.e. the raw °F reading). Same class as #72/#78/#96
#     (issue #128).
#   H5220 (gateway-bridged thermo-hygrometer): same class — a captured BFF
#     sample shows the device itself configured for °F (`"fahOpen": true`),
#     and its sensorTemperature comes via the Developer API, same path as
#     H5075/H5100 (issue #128 follow-up).
#   H5111 (fridge/freezer thermometer, temperature-only): reports °F under the
#     °C-tagged unit — a ~6.1°F freezer reading surfaced as 43.3°F in HA. Same
#     BLE-bridged read path as H5110 (issue #83 findings: API returns °F with
#     no unit field).
#   H5310 (pool thermometer, gateway-bridged): a pool at 88°F surfaced as ~191°F
#     — the Developer API had returned 88.34 already in °F (issue #157). This
#     entry is only the fallback: when the account's own `fahOpen` preference is
#     known (BFF device list), that hint wins and a °C account is left alone.
#   H5053 (WiFi thermometer): reports sensorTemperature in °F under the
#     °C-tagged unit — six units on one account read ~162°F for a real ~72°F
#     (72.2 × 9/5 + 32 = 162.0), every one reversing to a plausible value for
#     its location. Developer-API path; the SKU is not in the BFF thermo sets,
#     so the account's fahOpen preference is never recorded for it and this
#     entry is the only signal (issue #173).
FAHRENHEIT_REPORTING_SKUS: Final = frozenset(
    {
        "H5179",
        "H5310",
        "H5109",
        "H5110",
        "H5111",
        "H5053",
        "HS5108",
        "HS5106",
        "H717A",
        "H5106",
        "H5140",
        "H5075",
        "H5100",
        "H5220",
    }
)


# SKU-specific segment count overrides.
# Some Govee devices report a higher segment count via the API than
# the physical sections on the device. This dict pins the real count
# per SKU. Add a new entry when the API is observed to misreport.
#
# This is also the escape hatch for the size.max clamp in
# GoveeDevice.segment_count. That clamp reads fields[].size.max as a ceiling on
# the segment count, but the field is documented as the max array *length*
# accepted in one command. On every capture we hold the two agree
# (size.max == elementRange.max + 1), so the clamp only ever fires on the
# inconsistency that signals the bug. If a device ever reports a genuine
# per-command batch limit below its real segment count, the clamp would drop
# working entities — pin the true count here to override it.
SKU_SEGMENT_OVERRIDES: Final = {
    "H7075": 3,  # API reports 15 (elementRange.max=14), device has 3 physical sections
    # H7076 Outdoor Up/Down Wall Light: API reports 15 and size.max is 15 too,
    # so the clamp can't catch it. Indices 0-3 are the only ones that move the
    # light (0=top, 1=bottom, 2=part of the left side, 3=everything else);
    # 4-14 are accepted with HTTP 200 "success" and do nothing (issue #160).
    "H7076": 4,
}


def resolve_fahrenheit_conversion(
    sku: str, api_unit: str, device_unit_hint: str | None = None
) -> bool:
    """Whether a Developer-API ``sensor_temperature`` should be treated as °F.

    Shared by the sensor entity (which converts °F→°C for display) and the
    coordinator's BFF reading path (which must store the value in the SAME
    unit the entity expects, so a true-°C BFF reading round-trips correctly
    instead of being double-converted) — issues #96, #83.

    ``device_unit_hint`` is explicit unit metadata reported by the device
    itself — heaters (e.g. H713B) carry a ``unit`` field in their
    temperature_setting STRUCT state and report ``sensorTemperature`` in that
    same unit. In "auto" mode the device's own metadata beats the static SKU
    allowlist, so Fahrenheit-configured heaters normalize out-of-the-box
    without a per-SKU entry (issue #129).
    """
    if api_unit == "auto":
        if device_unit_hint is not None:
            return device_unit_hint.lower() == "fahrenheit"
        return sku.upper() in FAHRENHEIT_REPORTING_SKUS
    return api_unit == "fahrenheit"


# Defaults
DEFAULT_POLL_INTERVAL: Final = 60  # seconds
DEFAULT_ENABLE_GROUPS: Final = False
DEFAULT_ENABLE_SCENES: Final = True
DEFAULT_ENABLE_DIY_SCENES: Final = True
DEFAULT_SEGMENT_MODE: Final = "individual"  # "disabled", "grouped", or "individual"
DEFAULT_EXPOSE_TRANSPORT_ENTITIES: Final = False
DEFAULT_ENABLE_MQTT_CONTROL: Final = False
DEFAULT_API_TEMPERATURE_UNIT: Final = "auto"
DEFAULT_LAN_TARGETS: Final = ""
DEFAULT_WATER_DETECTOR_POLL_INTERVAL: Final = 120  # seconds (2 minutes)

# Bounds for the configurable water-detector poll interval (seconds). The lower
# bound keeps the unverified account-API rate limit at arm's length; the upper
# bound (1 hour) is the slowest that still makes a leak alert useful.
MIN_WATER_DETECTOR_POLL_INTERVAL: Final = 60
MAX_WATER_DETECTOR_POLL_INTERVAL: Final = 3600

# Optimistic state handling
# Grace window (seconds) during which API polls do NOT overwrite optimistic
# power/brightness. Masks out-of-range BLE devices and slow cloud responses
# without producing the "UI flipflop" that longer windows would. MQTT push
# confirmations clear the window early.
OPTIMISTIC_GRACE_CAP_SECONDS: Final = 15

# How often (seconds) the coordinator re-checks the Govee account device list to
# pick up devices added after startup (issue #101). Throttled well above the
# poll interval to respect the 100/min, 10k/day API rate limits — a new device
# appears within this window without a manual reload.
DEVICE_REDISCOVERY_INTERVAL: Final = 300

# LAN transport constants (issue #57)
# Runtime tuning for the local UDP (:4002/:4003) transport. These are NOT
# user-facing options — the only LAN option is CONF_LAN_TARGETS (above), which
# feeds extra cross-VLAN unicast scan targets. LAN auto-enables when a device
# answers the discovery scan and its scan MAC correlates to a coordinator
# device_id; there is no enable toggle.
#
# Wall-clock window (seconds) for one shared solicited read-batch collection.
# Bounds how long async_read_batch waits to gather devStatus replies.
LAN_READ_WINDOW: Final = 1.0
# Timeout (seconds) for the verify-by-read confirm after a LAN write. A reply
# within this window confirms the write; otherwise the command falls through
# to MQTT/REST so a device is never stranded.
LAN_WRITE_CONFIRM_TIMEOUT: Final = 0.5
# How often (seconds) to re-run the LAN discovery scan and re-correlate scan
# MACs to device_ids. Re-promotes demoted devices and picks up new ones / IP
# reassignments. Mirrors DEVICE_REDISCOVERY_INTERVAL throttling.
LAN_RESCAN_INTERVAL: Final = 300
# Consecutive solicited-read misses before a device is demoted out of the LAN
# device map so both reads and writes fall back to MQTT/REST.
LAN_READ_MISS_DEMOTE_THRESHOLD: Final = 3
# Consecutive LAN write-confirm failures (verify-by-read timeout OR value
# mismatch) before LAN write *attempts* are suppressed for a device. Issue #57:
# LAN transport health is READ-driven — a write whose readback does not confirm
# (a freshly-powered controller reporting its pre-command state late, or simply
# answering the confirm read after LAN_WRITE_CONFIRM_TIMEOUT) must NOT flip the
# lan_connectivity sensor off, which produced a flap synced to control activity
# (the reported symptom). Instead, after this many consecutive unconfirmed
# writes the device's writes skip the LAN attempt (and its confirm-read wait)
# and go straight to MQTT/REST for LAN_WRITE_SUPPRESS_SECONDS, while LAN reads
# keep working and the sensor stays Connected. A confirmed write or the cooldown
# expiry re-arms LAN writes.
LAN_WRITE_SUPPRESS_THRESHOLD: Final = 3
# Cooldown (seconds) after LAN writes are suppressed for a device before the next
# command probes the LAN write path again. Aligned with LAN_RESCAN_INTERVAL so a
# device that started confirming after a rescan/firmware change is re-tried at
# roughly the same cadence it would be re-correlated.
LAN_WRITE_SUPPRESS_SECONDS: Final = 300
# Age (seconds) past which a device's last successful LAN exchange is treated as
# stale, marking its 'lan' transport health unavailable. Set just above the 60s
# default poll so a single missed poll is tolerated.
LAN_STALE_SECONDS: Final = 90
# Time-to-live (seconds) for a scan MAC↔IP correlation. A devStatus read is only
# applied for an IP whose correlation is fresher than this, guarding against
# DHCP IP reassignment mis-routing a read to the wrong device. Kept at or above
# LAN_RESCAN_INTERVAL so a correlation stays valid across a full rescan cycle.
LAN_CORRELATION_TTL_SECONDS: Final = 600

# SKUs whose two light zones (a central downlight panel and an RGBIC ring)
# cannot be separated by the obvious capabilities: ``mainLightToggle`` /
# ``backgroundLightToggle`` are inert on this hardware, and ``powerSwitch`` is
# whole-fixture — it kills the ring too, and leaves the firmware in a state
# where any later light command silently wakes the main panel back up. For
# these SKUs the integration adds a dedicated main-panel light entity that
# switches the panel via the whole-device colour channel instead. See
# ``GoveeMainLightEntity`` in ``light.py`` for the mechanism (issue #131).
#
# Deliberately narrow: only the H1270 has been verified against real hardware,
# though H1250/H60A6 (the other SKUs reported with inert light toggles) are
# plausibly the same fixture design.
MAIN_LIGHT_TOGGLE_SKUS: Final = frozenset({"H1270"})

# BLE constants
# Govee AWS/BLE advert manufacturer ID. Verified against
# Bluetooth-Devices/govee-ble (used by H5127 and related). Additional IDs
# remain unverified and are omitted until observed in the wild.
GOVEE_BLE_MANUFACTURER_IDS: Final = (0x8803,)  # 34819

# Segment mode options
SEGMENT_MODE_DISABLED: Final = "disabled"
SEGMENT_MODE_GROUPED: Final = "grouped"
SEGMENT_MODE_INDIVIDUAL: Final = "individual"
# Both the 12 individual segment entities AND one grouped entity that
# controls all of them together — the individual entities stay the source of
# truth per-segment; the grouped entity is a convenience "all segments" light
# on top, not a replacement for either the individual entities or a native HA
# Light Group helper (which needs no integration support at all).
SEGMENT_MODE_BOTH: Final = "both"

# Config entry schema version. Bumped to 2 in sprint-4 when IoT credentials
# moved from hass.data[DOMAIN] to entry.data (see async_migrate_entry).
CONFIG_VERSION: Final = 2

# Keys for storing cached data in hass.data[DOMAIN]
# Minimum gap between account re-login attempts after the BFF rejects the
# stored token (issue #132). Repeated logins are what trips Govee's own 2FA
# hardening, so a persistently failing account must back off rather than retry
# on every 5-minute poll.
IOT_RELOGIN_MIN_INTERVAL: Final = 900  # 15 minutes

KEY_IOT_CREDENTIALS: Final = "iot_credentials"
KEY_IOT_LOGIN_FAILED: Final = "iot_login_failed"

# Entity unique_id suffixes
# Used in entity creation and orphan cleanup to keep patterns consistent
SUFFIX_SEGMENT: Final = "_segment_"
SUFFIX_GROUPED_SEGMENT: Final = "_grouped_segments"
SUFFIX_SCENE_SELECT: Final = "_scene_select"
SUFFIX_SNAPSHOT_SELECT: Final = "_snapshot_select"
SUFFIX_DIY_SCENE_SELECT: Final = "_diy_scene_select"
SUFFIX_DIY_STYLE_SELECT: Final = "_diy_style_select"
SUFFIX_HDMI_SOURCE_SELECT: Final = "_hdmi_source_select"
SUFFIX_MUSIC_MODE_SELECT: Final = "_music_mode_select"
SUFFIX_REFRESH_SCENES: Final = "_refresh_scenes"
SUFFIX_NIGHT_LIGHT: Final = "_night_light"
SUFFIX_LIGHT_ZONE: Final = "_light_zone_"
SUFFIX_SOCKET: Final = "_socket_"
SUFFIX_MAIN_LIGHT: Final = "_main_light"
# Distinct from SUFFIX_MAIN_LIGHT (the switch backed by the cloud
# ``mainLightToggle`` capability) — this is the dedicated main-panel light
# entity added for MAIN_LIGHT_TOGGLE_SKUS (issue #131).
SUFFIX_MAIN_LIGHT_TOGGLE: Final = "_main_light_toggle"
SUFFIX_BACKGROUND_LIGHT: Final = "_background_light"
SUFFIX_NEBULA_LIGHT: Final = "_nebula_light"
SUFFIX_SIDE_LIGHT: Final = "_side_light"
SUFFIX_BOTTOM_LIGHT: Final = "_bottom_light"
SUFFIX_MUSIC_MODE: Final = "_music_mode"
SUFFIX_MUSIC_SENSITIVITY: Final = "_music_sensitivity"
SUFFIX_DREAMVIEW: Final = "_dreamview"
SUFFIX_HEATER_TEMPERATURE: Final = "_heater_temperature"
SUFFIX_HEATER_FAN_SPEED: Final = "_heater_fan_speed"
SUFFIX_HEATER_AUTO_STOP: Final = "_heater_auto_stop"
SUFFIX_PURIFIER_MODE_SELECT: Final = "_purifier_mode_select"
SUFFIX_PRESET_SCENE_SELECT: Final = "_preset_scene_select"
SUFFIX_NIGHTLIGHT_SCENE_SELECT: Final = "_nightlight_scene_select"
