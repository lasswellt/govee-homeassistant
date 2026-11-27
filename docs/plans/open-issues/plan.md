# Plan: open-issues

## Architecture

The plan has four independent work streams. They share files in only two places:
- T-002 and T-003 both edit `const.py`, so they run in sequence.
- T-005 and T-006 describe what T-002 to T-004 shipped, so they come after.

- **Release workflow (T-001):** merge order is #311, #312 and #313 first, then #292 and #291 back to back. That keeps upload-artifact v7 and download-artifact v8 on one artifact-library generation. PR CI never runs `daily-release.yml`, so a `workflow_dispatch` dry run is the acceptance test.
- **#310 (T-002):** extend the existing ptReal segment tier (`PTREAL_SEGMENT_SKUS`, `coordinator.py:5885`) with a fourth mask byte. Byte 15 of the colour frame is already the `0x00` pad, so existing SKUs keep identical packets.
  - Rejected: a per-SKU mask width. It adds a branch with nothing to gain.
  - Rejected: a separate H7038 command path. It would duplicate the tier.
- **#241 (T-003):** reuse the H66A0 machinery: frame-derived state, ptReal ON and colour-restore OFF. A new `FRAME_DREAMVIEW_SKUS` capability fallback is needed because the H6099 advertises no `dreamViewToggle`.
  - Rejected: a read-only binary sensor. The maintainer chose control.
  - Rejected: a new video-mode select. There is no evidence of the sub-mode bytes.
- **DIY (T-004):** observability only. Store the last fetch outcome (count or error class) per device in the scene cache and surface it in diagnostics. No change to when the select exists.
  - Rejected for now: lazy entity creation after a refresh. It needs a dynamic platform add and isn't justified until diagnostics show the cause.

- **2026-11-26 extension:**
  - **T-008 (community PRs):** merge in the order #319 → #321 → #320. Run it before T-002, T-003 and T-005, which edit the same `const.py` blocks and protocol-reference sections.
    - Conflict resolution: keep both sides (proven green: 4,789 tests passed).
    - Rejected: asking the authors to rebase. It costs a day for an adjacency conflict.
  - **T-009 (#316):** one counting helper gated on `state.online`. The query is still sent, which keeps the #242 recovery.
    - Rejected: dropping offline devices from the poll targets. That breaks #242.
    - Rejected: `device_reachable()`. A device reachable only over LAN or BLE doesn't answer over MQTT.
  - **T-010 (#317):** a one-line SKU addition, following the H5102/H5107 precedent.
  - **T-011 / T-012:** new replies, then the final gate.

## File map

| Path | Change | Task |
|---|---|---|
| `.github/workflows/daily-release.yml` | Dependabot bumps: app-token 3.4.0, claude-code-action 1.0.286, upload-artifact v7.0.2, download-artifact v8.0.2 | T-001 |
| `.github/workflows/hacs-hass.yaml` | hassfest pinned to `9e9565e` | T-001 |
| `custom_components/govee/const.py` | H7038 in `PTREAL_SEGMENT_SKUS` (T-002); `FRAME_DREAMVIEW_SKUS`, plus H6099 in the two DreamView sets (T-003) | T-002, T-003 |
| `custom_components/govee/api/ble_packet.py` | fourth mask byte in the colour and colour-temperature builders; docstrings say 4-byte | T-002 |
| `custom_components/govee/coordinator.py` | segment index ≥ 32 → command fails | T-002 |
| `custom_components/govee/models/device.py` | `supports_dreamview` fallback for `FRAME_DREAMVIEW_SKUS` | T-003 |
| `custom_components/govee/scene_cache.py` | last DIY fetch outcome per device | T-004 |
| `custom_components/govee/diagnostics.py` | per-device `diy_scenes` entry | T-004 |
| `custom_components/govee/select.py` | debug log "no DIY scenes" | T-004 |
| `tests/test_ble_packet.py`, `tests/test_cov_coordinator_control.py` | 30-bulb golden frames; H7038 tier case; updated mask and length tests | T-002 |
| `tests/test_dreamview_frames.py`, `tests/test_cov_switch.py` | H6099 frame and switch tests | T-003 |
| `tests/test_diagnostics.py`, `tests/test_scene_cache.py` | DIY outcome tests | T-004 |
| `docs/govee-protocol-reference.md` | mask wording; §9.8 rows; H6099 section; Music DreamView subsection | T-005 |
| `docs/release-replies.md` | six queued replies | T-006 |
| `custom_components/govee/const.py`, `models/device.py`, `docs/govee-protocol-reference.md` | conflict resolution while merging PRs #319, #321 and #320 (their own files come with the merges) | T-008 |
| `custom_components/govee/coordinator.py`, `tests/test_issue_222.py` | `_count_mqtt_status_query` used at both increment sites; offline and mixed tests | T-009 |
| `custom_components/govee/const.py`, `tests/test_zone_light_toggles.py` | H5074 in `FAHRENHEIT_REPORTING_SKUS`; comment; parametrize | T-010 |
| `docs/release-replies.md` | #315, #318, #298, #299, #288, #211; one #308 entry | T-011 |

## Coverage

| Outcome | Constants | Frame builder | Coordinator / model | Diagnostics | Tests | Docs | Reply |
|---|---|---|---|---|---|---|---|
| Release workflow on new actions | n/a | n/a | n/a | n/a | dry run T-001 | n/a | n/a |
| H7038 segments 16–30 | ✓ T-002 | ✓ T-002 | ✓ T-002 | n/a | ✓ T-002 | ✓ T-005 | ✓ T-006 |
| H6099 video mode | ✓ T-003 | existing | ✓ T-003 | n/a | ✓ T-003 | ✓ T-005 | ✓ T-006 |
| DIY cause visible | n/a | n/a | ✓ T-004 | ✓ T-004 | ✓ T-004 | n/a | ✓ T-006 |
| #304 frames recorded | n/a | n/a | n/a | n/a | n/a | ✓ T-005 | ✓ T-006 |
| #308 second user, #208 test request | n/a | n/a | n/a | n/a | n/a | n/a | ✓ T-006 |
| Ships tonight | n/a | n/a | n/a | n/a | ✓ T-007 | n/a | n/a |
| H60A6 zones / H60B0 mode + flow (PRs) | ✓ T-008 | ✓ T-008 (#321) | ✓ T-008 | n/a | ✓ PR tests | ✓ PR docs | ✓ T-011 (#211); the bot replies on PRs |
| No false mqtt_silent | n/a | n/a | ✓ T-009 | n/a | ✓ T-009 | n/a | automatic (#316 in the commit subject) |
| H5074 °F | ✓ T-010 | n/a | n/a | n/a | ✓ T-010 | n/a | automatic (#317 in the commit subject) |
| New threads answered | n/a | n/a | n/a | n/a | n/a | n/a | ✓ T-011 |
| Ships 2026-11-28 | n/a | n/a | n/a | n/a | ✓ T-012 | n/a | n/a |

## Risks

- **#310:** the 4-byte layout rests on one reporter's hardware test. No community source confirms or contradicts it (research §Dissent). Cloud-only H7038 installs keep the REST no-op for indices 15–29.
- **#241:**
  - The ON frame may not enter camera video mode on the H6099.
  - OFF detection is inferred: the video-off capture had no `aa 05` frame.
  - If ON fails, the switch reports the device's real state, so the failure shows; the reply asks for a capture.
- **T-001:**
  - download-artifact v8 fails on a digest mismatch.
  - claude-code-action 1.0.286 has no release notes.
  - If the dry run fails, revert the merges before 02:00Z, or tonight's release is skipped and the fixes slip a day.
- **Scheduling:** T-007 must pass before 02:00Z for the fixes to ship tonight. Otherwise they ship the next night.

- **T-008:**
  - Fork CI must be approved run by run. A check the local lane doesn't cover (the py3.13 test job, HACS, hassfest) may fail after approval. That PR then waits a day; the others don't.
  - #320's side-light assumption is unconfirmed beyond the author's lamp.
- **T-009:** detection can lag by one sweep after a device powers on, because the cloud `online` flag lags. This is harmless against the 1,800 s check.
- **T-010:** an account that really reports H5074 in °C would now be converted wrongly. There is no report of one, and the option overrides it.
- **Ordering:** `tasks.sh next` picks by id, so it would offer T-002 before T-008. Build T-008 first by hand; T-002, T-003 and T-005 carry notes saying so.

## Solutions consulted

- `docs/solutions/`: none exists.
- `docs/plans/BACKLOG.md`: none exists.
- late-nov-sweep T-012 is absorbed into T-001 and blocked there as superseded. late-nov-sweep T-013 is not absorbed.

## Research

- `docs/research/2026-11-26_issues-and-pr-merge-validation.md` (`--from-research`, 2026-11-26 extension: T-008..T-012).
- `docs/research/2026-11-25_open-issues-and-prs.md` (`--from-research`). Agent findings are in the session scratchpad under `research/`: `codebase-ptreal.md`, `codebase-diy.md`, `h6099.md`, `library-docs.md`, `web-researcher.md`.
