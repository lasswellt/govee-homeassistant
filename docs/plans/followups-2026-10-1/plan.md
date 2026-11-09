# Plan: follow-ups after v2026.10.1

## Architecture

Three release days. The nightly 03:00 UTC run releases whatever is on `main`, so each day's work is pushed before then and the next day's starts after the release.

- **Day A (T-001 to T-011):** device-scoped fixes plus #268. They are independent but share `coordinator.py`, so they run one after another.
- **Day B (T-012 to T-014):** PR #267 alone. It touches BLE eligibility for every model, so per the broad-behaviour rule it gets its own day. The CI fixes and the H6104-only scoping go into the merge commit, so `main` stays green. The lifecycle bugs follow as separate commits.
- **Day C (T-015):** #269 alone. It changes what every light's scene select shows, reversing #18, so it lands only after a hands-on try.
- **Revised 2026-10-28 (user: "everything tonight, finish all issues").** Day A shipped as v2026.10.2. Days B and C are merged into the 2026-10-29 03:00 UTC release, along with the sweep items. The order on `main` is:
  1. T-012 to T-014: #267, first, so its own-day check still holds.
  2. T-016: #261 H1A42.
  3. T-017: PR #270.
  4. T-018: PR #273.
  5. T-020: PR #271, which replaces T-015 and T-019. Both stay blocked as superseded, because `tasks.sh` can't remove a task or edit its checks.
  6. T-021: replies.
  7. T-022: gates.

  The trade-off accepted: if tonight's release regresses lights, the release that caused it carries #267, #271 and the scoped fixes together, so the hotfix has to bisect.

Key decisions:
- **#222 evidence.** A gateway thermometer's BFF reading with a device-side `lastTime` newer than the session start counts as a push Govee sent that the session never delivered. That is a prompt with no answer, the same thing the 3-query rule measures. Rejected:
  - querying the H5310 or the hub topic (risks #195 drops)
  - lowering the query minimum or using time alone (false repairs on quiet accounts)
  - using Developer API value changes (no device timestamp)
- **#265 Auto Stop.** Applies to the H7130 only. Its capability labels the `targetTemperature.autoStop` field "Auto Stop", and `thermostatToggle` is never reported. Other heaters keep the #29 order. The thermostat becomes its own optimistic switch.
- **#240.** Colour is SKU-scoped and gated on IoT credentials, mirroring `synthetic_thermometer`.
- **#269.** Presentation only (`current_option`). Rejected:
  - clearing and restoring the scene (more state paths, no gain)
  - an opt-in option (two behaviours to keep forever, and an options-flow field)
- **#267.** Merged with our fixes (user decision). Rejected: asking the contributor for changes first.

## File map

| Path | Change |
|---|---|
| `custom_components/govee/light.py` | #268: brightness after a scene (T-001) |
| `custom_components/govee/const.py` | `PROBE_PACKET_SPACING`, `PROBE_LIMITS_READ_EVERY`, `MQTT_SILENT_MIN_MISSED_PUSHES`, `HEATER_AUTO_STOP_STRUCT_SKUS`, `MQTT_COLOR_SKUS` (T-002, T-003, T-007, T-008) |
| `custom_components/govee/coordinator.py` | paced, tracked probe sweep; missed-push evidence and the split silent handler; heater power-on optimism; MQTT colour routing; DreamView frame decode wiring; #267 merge and lifecycle (T-002, T-003, T-005, T-008, T-009, T-012, T-013) |
| `custom_components/govee/diagnostics.py` | `has_mqtt_topic` / `has_gateway_route`, missed-push counter (T-004) |
| `custom_components/govee/strings.json`, `translations/en.json`, `icons.json` | evidence-neutral `mqtt_silent` text; heater oscillation and thermostat keys; #267 keys and error (T-004, T-006, T-007, T-012) |
| `custom_components/govee/number.py` | `round()` heater setpoint (T-005) |
| `custom_components/govee/switch.py` | `GoveeHeaterOscillationSwitchEntity`, `GoveeHeaterThermostatSwitchEntity`, H7130 Auto Stop via the struct (T-006, T-007) |
| `custom_components/govee/models/state.py` | `heater_thermostat`; `update_dreamview_from_frames` (T-007, T-009) |
| `custom_components/govee/models/device.py` | synthesised `colorRgb` for `MQTT_COLOR_SKUS` (T-008) |
| `custom_components/govee/api/ble.py` and the other #267 files | merge, scoping, lifecycle fixes (T-012, T-013) |
| `custom_components/govee/select.py` | `_scene_hidden_while_off` (T-015) |
| `README.md` | #267 section rewritten for HACS users (T-012) |
| `docs/release-replies.md` | briefs for #236, #261, #239, #213, #265, #269 and #243 (T-010) |
| `tests/test_issue_{222,240,265,266,269}.py`, `tests/test_dreamview_frames.py`, `tests/test_h6104_lifecycle.py`, `tests/test_h6104_setup_entry.py` | new tests |

## Coverage

| Outcome | Const | Coordinator | Entity/UI | Strings/icons | Diagnostics | Tests | Reply |
|---|---|---|---|---|---|---|---|
| #268 effect + brightness | n/a | n/a | ✓ T-001 | n/a | n/a | ✓ T-001 | auto (subject) |
| #266 probe pacing | ✓ T-002 | ✓ T-002 | n/a | n/a | n/a | ✓ T-002 | auto |
| #222 silent detection | ✓ T-003 | ✓ T-003 | n/a | ✓ T-004 | ✓ T-004 | ✓ T-003 | auto |
| #265 heater | ✓ T-007 | ✓ T-005 | ✓ T-005..T-007 | ✓ T-006, T-007 | n/a | ✓ T-005..T-007 | ✓ T-010 |
| #240 H7073 colour | ✓ T-008 | ✓ T-008 | ✓ (light modes from caps) T-008 | n/a | n/a | ✓ T-008 | auto |
| #213 DreamView state | n/a | ✓ T-009 | n/a | n/a | n/a | ✓ T-009 | ✓ T-010 |
| #236, #261, #239, #243 | n/a | n/a | n/a | n/a | n/a | n/a | ✓ T-010 |
| Day A gates | | | | | | ✓ T-011 | |
| #267 H6104 | ✓ T-012 | ✓ T-012, T-013 | ✓ T-012 | ✓ T-012 | ✓ T-012 (build stamp dropped) | ✓ T-013, T-014 | auto (merge subject) |
| #269 scene select | n/a | n/a | ✓ T-015 | n/a | n/a | ✓ T-015 | auto + interim brief T-010 |

## Risks

- **#222:** the design rests on BFF `lastTime` advancing while MQTT is silent. If the H5044 also stopped reaching Govee's cloud, the evidence never accrues and nothing changes for this reporter. The T-004 diagnostics flags will show whose topic the account has.
- **#265 Auto Stop:** the direction is inferred from capability labels and is unverified on hardware. It is scoped to the H7130, and the T-010 reply asks which toggle acts.
- **#240:** the H7073 may ignore `colorwc` over AWS IoT. The reply asks; a ptReal fallback is a follow-up.
- **T-009** changes DreamView state for every device that reports `aa 05` frames (H66A0, H605B, H6199 era). Its tests must cover the existing DreamView devices.
- **#267:**
  - It conflicts with #252 (`issue-sweep-late-oct` T-027) in `api/ble.py`, `ble_advertisement.py` and `coordinator.py`, whichever lands second.
  - The merge commit is large (about 300 lines of our edits), so review the scoping carefully against research-pr267.md §4.
  - Poll readback churns BLE proxy slots: one connect per poll.
- **Release-day discipline:** the own-day checks only look backwards. If anything merges after T-012 or T-015 on the same Eastern day, the rule is broken without any check failing.
- **Coordinator conflicts:** five Day A tasks edit `coordinator.py`, so they are chained (T-002 → T-003 → T-005 → T-008 → T-009) and none run in parallel.

## Solutions consulted

- `docs/plans/BACKLOG.md`: absent.
- `docs/solutions/`: absent.
- Memories applied: broad behaviour change gets its own release day (T-012, T-015); HACS reporters can't test branches, so SKU-scoped unverified fixes ship in the daily release (T-007, T-008); issue replies go out after the release, through the queue; ask for the diagnostics UI download, never logger YAML; gate chains need pipefail; the CI wait needs the full SHA.

## Research

- `docs/plans/followups-2026-10-1/research/research-coordinator.md`: #222, #265, #266
- `docs/plans/followups-2026-10-1/research/research-lights.md`: #261, #240, #213, #239, #269
- `docs/plans/followups-2026-10-1/research/research-gateway.md`: #236
- `docs/plans/followups-2026-10-1/research/research-pr267.md`: PR #267 review
- `docs/research/2026-10-28_post-release-sweep.md`: sweep after v2026.10.2 (#261, #270, #271, #273, #239, #274, #137, dependabot)
- Diagnostics attachments (untrusted, not committed): session scratchpad `diag-1025/`
