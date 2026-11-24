# Plan: late-nov-sweep

## Architecture

Three groups, in this order.

**Before the 02:00Z release (T-001 – T-011).** Small, independent fixes that follow patterns already in the code:

- CI calls `tox -e py312` / `tox -e py313` directly. Rejected: pinning `tox` or `tox-gh-actions`. A pin still leaves a plugin that imports tox internals in the release gate.
- #305 goes through a helper in `registry.py`, which already feature-detects two 2026.10 device-registry changes. New HA gets `via_device_id` (the hub's registry id); older HA keeps `via_device`. Rejected: deleting the link outright. It is the smallest diff but drops the "connected via" relationship on every HA version.
- #308, #307 and #261 are one constant each in `const.py` plus one branch, mirroring `POWER_QUIRK_SKUS`, `FAHRENHEIT_REPORTING_SKUS` and `SKU_SEGMENT_OVERRIDES`. No other SKU's behaviour changes.
- #309 adds an optional `param_id` to `SceneCommand`. When the scene list has no paramId the payload is exactly today's, so the change is additive per scene. The active scene keeps its id string and gains a separate `active_scene_param_id`. Rejected: encoding `"id:paramId"` into `active_scene`, because every reader of that field and every restored state would need to parse it.
- PR #303 merges early (T-002) so later tasks that touch `const.py`, `coordinator.py` and `models/commands.py` build on it instead of conflicting with it.

Tasks that edit `const.py` or `coordinator.py` are chained (T-002 → T-005 → T-006 → T-007, T-006 → T-009) so they never run in parallel on the same file. T-003/T-004 and T-010 are independent of that chain.

**After the release exists (T-012).** The dependabot bumps to `daily-release.yml`, then a `dry_run`. Held back on purpose: the publish job is not exercised by PR checks or by a dry run, and the release workflow is the thing that has just been failing.

**Then (T-013).** A failure job in `daily-release.yml` that opens or updates an issue, using the default token with `issues: write` on that job only.

## File map

| Path | Change |
|---|---|
| `.github/workflows/tox.yaml` | install `tox` only; run `tox -e py3xx` from the matrix |
| `tox.ini` | remove `[gh-actions]` |
| `custom_components/govee/registry.py` | `hub_link_info()` |
| `custom_components/govee/models/device.py` | leak sensor device info without `via_device`; (from #303) toggle regex, `music_mode_requires_rgb` |
| `custom_components/govee/entity.py` | bridged-device link through the helper |
| `custom_components/govee/const.py` | `LAN_POWER_OFF_EXCLUDED_SKUS`, `BRIGHTNESS_REPORT_SCALE_SKUS`, `"H1A42": 5`; (from #303) `SUFFIX_*` |
| `custom_components/govee/api/lan_control.py` | no LAN power-off for excluded SKUs |
| `custom_components/govee/brightness.py` | `normalize_reported_brightness()` |
| `custom_components/govee/coordinator.py` | apply the brightness normaliser on REST and MQTT ingest; pass paramId when restoring a scene; (from #303) rgb-required flag |
| `custom_components/govee/models/commands.py` | `SceneCommand.param_id`; (from #303) `MusicModeCommand.rgb_required` |
| `custom_components/govee/select.py`, `light.py` | scene maps carry paramId; reverse maps no longer collapse variants |
| `custom_components/govee/models/state.py` | `active_scene_param_id` |
| `custom_components/govee/switch.py`, `strings.json`, `icons.json`, `translations/*.json` | from #303: three H2800 switches |
| `tests/…` | `test_registry.py`, `test_lan_control.py`, `test_issue_261.py`, `test_h2800_monitor_light_bar.py`, and the `test_cov_*` files named in each task |
| `docs/release-replies.md` | entries for #306, #241; rewritten #285, #299 |
| `.github/workflows/daily-release.yml` | T-012 action bumps; T-013 failure job |

## Coverage

| Outcome | Const / model | Logic | Entity / platform | CI / workflow | Tests | Reply |
|---|---|---|---|---|---|---|
| Gate without plugin | n/a | n/a | n/a | T-001 | T-001 (CI run) | n/a |
| H2800 (#303) | T-002 | T-002 | T-002 | n/a | T-002 | auto |
| Hub link (#305) | T-004 | T-003 | T-004 | n/a | T-003, T-004 | auto + traceback ask (T-010) |
| H60A6 off (#308) | T-005 | T-005 | n/a | n/a | T-005 | auto |
| H6054 brightness (#307) | T-006 | T-006 | n/a | n/a | T-006 | auto |
| H1A42 segments (#261) | T-007 | n/a | n/a (count-driven) | n/a | T-007 | auto |
| Scene paramId (#309) | T-008, T-009 | T-009 | T-008 | n/a | T-008, T-009 | auto; #285 via T-010 |
| Reply-only threads | n/a | n/a | n/a | n/a | n/a | T-010 |
| Release goes out | n/a | n/a | n/a | T-011 | T-011 | n/a |
| Bumps + alerting | n/a | n/a | n/a | T-012, T-013 | dry run | n/a |

No gaps.

## Risks

- **Deadline.** The release fires at 2026-11-25 02:00Z with whatever is on `main`. A task that is half-merged at that time ships half-merged. Merge each task only when its own verify passes, and stop starting new ones when T-011 cannot finish in time; unfinished tasks wait for the next night.
- **#309 is broad and unverified on our side.** It changes the scene payload for every device whose scene list carries a paramId. Evidence is one reporter on two SKUs plus our captured scene list. If scenes break after release, that is the "broken release" case in CLAUDE.md: hotfix by reverting T-008/T-009.
- **#305 on new HA is untested in CI.** Both lanes resolve HA 2025.1.4. A wrong feature check either drops the hub link or passes a key old HA rejects. T-003 must read HA 2026.11.2's `DeviceInfo` before choosing the check, and tests fake both branches.
- **#261 removes ten entities** per H1A42. They never worked, but automations referencing them will error. Release notes must say so.
- **#307 rests on one unit.** A unit reporting 1–100 would be mapped down. The clamp limits the damage to wrong readback, not wrong control.
- **#303 music-mode flag** could change payloads for another SKU whose capability marks `rgb` required. T-002 searches the fixtures for one.
- **Research gaps carried in:** upload-artifact v7 with download-artifact v8 across jobs and claude-code-action 1.0.262 → 1.0.276 were not confirmed from release notes; T-012's dry run is the check. The H6099 video-mode frame in the #241 reply is inferred from one sample; T-010 falls back to asking for a second diagnostics if it cannot be derived.
- **T-004 touches more than three files** because the same `via_device` assertion lives in five test files. It is one behaviour, not a per-file slice.

## Solutions consulted

`docs/plans/BACKLOG.md` and `docs/solutions/` do not exist. Carried over from `docs/plans/post-11-0-sweep`: T-013 there (merge #291/#292/#294 after a release, then dry-run) becomes T-012 here, with #302 replacing the superseded #294.

## Research

- `docs/research/2026-11-24_late-nov-issue-pr-sweep.md` (ingested with `--from-research`; no agents spawned by this skill)
- Maintainer decision recorded in this session: #309 ships tonight.
