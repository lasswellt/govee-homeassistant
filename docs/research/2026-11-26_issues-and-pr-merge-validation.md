---
date: 2026-11-26
topic: issues-and-pr-merge-validation
type: Feature Investigation
status: complete
---

# Open issues and PR merge validation, 2026-11-26

## Summary

This is a delta on `docs/research/2026-11-25_open-issues-and-prs.md`. Its plan, `docs/plans/open-issues/` (T-001 to T-007), is still entirely open: `progress.md` has only the plan-created entry. The 2026-11-27T02:12Z daily-release run gated and skipped (`gh run view 45926994029`: gate success, draft/publish skipped), because nothing under `custom_components/` changed since v2026.11.1 (`git diff --stat v2026.11.1..origin/main -- custom_components/` is empty). The next release window is 2026-11-28T02:00Z.

New since yesterday's research (commented on or opened after 2026-11-25T15:05Z):
- **Three community fork PRs: #319 (H60A6 zone readback), #320 (H60B0 light-mode select) and #321 (H60B0 ripple-flow slider).** All three are mergeable on the code. CI never ran on them: each fork run is `action_required` (`gh run list --event pull_request`). Local gates pass on each PR and on all three merged together.
- **Two new code fixes are supported by evidence:**
  - **#316:** a false `mqtt_silent` repair when every MQTT device on the account is offline.
  - **#317:** the H5074 reads °F, but the integration converts that reading as if it were °C.
- **Reply-only threads:** #315 (H6022), #318 (duplicate of #295), #298 (H7087 DIY), #299 (DreamView scenes), #288 (Tap-to-Run) and #308 (firmware 1.00.49 confirmed).
- **Dependabot:** #311, #312, #313, #292 and #291 are unchanged and still `CLEAN`, with all checks green. Yesterday's T-001 verdict stands.

## Research questions

1. Can #319, #320 and #321 merge? In what order, and with what conflict handling?
2. Is the reporter's #316 fix correct, and does it keep the #242 recovery path?
3. Does #317 justify adding H5074 to `FAHRENHEIT_REPORTING_SKUS`?
4. What do #315, #318, #298, #299, #288 and #308 need?
5. Are the open-issues plan cites (T-002..T-006) still valid on main?

## Findings

### Q1 Community PRs

Main baseline: 4,741 tests pass (py3.12 venv, HA 2025.1.4 lane, `pytest -q --no-cov`).

| PR | Scope | Local gates (black / flake8 / mypy / pytest / cov) | Verdict |
|---|---|---|---|
| #319 AdamEszes | H60A6: decode the `aa 30 <main> <ring>` push into the main/background switches | clean / clean / clean / 4,746 passed / 99.74% (switch.py, state.py 100%) | MERGE |
| #320 m4ary | H60B0: `EXCLUSIVE_LIGHT_TOGGLE_MODES`, optimistic cross-off, **Light mode** select | clean / clean / clean / 4,765 passed / 99.74% (new lines 100%) | MERGE |
| #321 m4ary | H60B0: `build_ripple_flow_ptreal` `33 05 2c 01 03 <0-100>`, **Ripple flow** number | clean / clean / clean / 4,760 passed / 99.74% (number.py, ble_packet.py 100%) | MERGE |
| #319+#321+#320 combined | merged in that order in a scratch worktree, conflicts resolved by keeping both sides | clean / clean / clean / **4,789 passed** / 99.74% | all three together are green |

**#319, H60A6 zone readback:**
- **Gate:** only the SKU gate widens. `LIGHT_ZONE_FRAME_SKUS = PTREAL_LIGHT_ZONE_SKUS | {"H60A6"}` (PR `const.py:287`) is used at the push decode (`coordinator.py:4130`), and the decoder at `models/state.py:678-704` is unchanged.
- **Writes:** the write paths that must stay H1252-only still use `PTREAL_LIGHT_ZONE_SKUS` (`coordinator.py:5558`, `:5605`, `light.py:125`).
- **Power (#308):** no conflict. The decode writes only `state.toggles` (`state.py:702-703`), never `power_state`, and `LAN_POWER_OFF_EXCLUDED_SKUS` (`const.py:133`) only reroutes power-off.
- **Polls:** a poll returning `""` skips the toggle (`state.py:461-466`), and `setdefault` keeps the frame value (`coordinator.py:5322-5323`).
- **Tests:** they drive a real `MockConfigEntry` with captured pushes, and 4 of the 5 fail without the change.
- **Nit:** stale comments mention H60A6 at `const.py:257` and `:592`. Non-blocking.

**#320, H60B0 light-mode select:**
- **Command path:** the cross-off runs only in `_apply_optimistic_update` (PR `coordinator.py:7224-7232`), which every control path calls after a successful send. A rejected command therefore changes no state.
- **Select behaviour:**
  - It returns `None` when only the side light is on. This is valid for an HA select.
  - A restored value is checked against the modes.
  - An unknown option raises `ServiceValidationError`.
  - A failed command goes through `_async_send_command`.
- **Scope:** groups and other SKUs get `{}`, and the H60B3 is untouched.
- **Translations and icons:**
  - `strings.json`, `en.json`, `es.json` and `ca.json` all carry the new keys. These are the repo's only three locales.
  - Both new icon names (`mdi:floor-lamp-torchiere-variant` and `mdi:waves-arrow-right` from #321) exist in the MDI index (`Templarian/MaterialDesign-SVG` `meta.json`, checked 2026-11-26). Every `mdi:` name in the combined `icons.json` resolves.

**#321, H60B0 ripple-flow slider:**
- **Frame:** `[0x33,0x05,0x2C,0x01,0x03,speed]`, clamped to 0–100, padded and XOR-checksummed by `build_packet` (`api/ble_packet.py:64-87`).
- **Gating:** `supports_ripple_flow` requires a non-group device on that SKU that advertises `rippleLightToggle`.
- **No transport:** without LAN or AWS IoT, `async_send_raw_ptreal` returns `False` (`coordinator.py:7078-7083`), and the number raises the translated `_command_failed()`.
- **Restore:** `RestoreNumber` restores the value without sending it.

**Conflicts** (`git merge-tree`, then a real merge):
- **#319 × #321:** `const.py` only. Both insert a block after `PTREAL_LIGHT_ZONE_SKUS` (`const.py:281`); keep both.
- **#320 × #321:** `models/device.py` only. Both add a property after `named_light_toggle_instances`. Keep both, with a `@property` before #320's `light_toggle_modes`. The protocol-reference overlap auto-merged in the real merge.
- **#319 × #320:** clean.
- **Edit rights:** `maintainerCanModify: true` on all three, so the maintainer can push the resolution to each fork branch.

### Q2 #316 false `mqtt_silent`

All of the reporter's claims check out against main:
- `_mqtt_status_poll_targets` (`coordinator.py:3763-3782`) does not filter on `state.online`.
- The two increment sites, `_poll_mqtt_status` (`:3891`) and `async_request_status` (`:4542`), are the only ones. The counter is reset at `:4234`.
- `MQTT_SILENT_MIN_QUERIES = 3` (`const.py:355`) and `MQTT_SILENT_CHECK_DELAY = 1800` (`:354`).
- `_async_check_mqtt_silence` (`:4256-4281`) leads to `_async_handle_silent_session` (`:4283`). Its first call reconnects (`:4294-4304`); its second, 30 minutes after the re-arm, raises the repair (`:4306-4309`).

The proposed fix:
- **Change:** count a query only when `self._states[device_id].online` is true. The query is still published.
- **#242 is preserved:** a live device that the cloud wrongly reports as `online: false` still answers, `update_from_mqtt` sets `online = True` (`models/state.py:~620`), inbound becomes > 0, and silence detection never starts.
- **Gate choice:** use `state.online`, not `device_reachable()` (`:896`). The latter also counts LAN and BLE reachability, and a device reachable only that way would not answer over MQTT.
- **Shape:** use one helper at both sites rather than the two inline conditions in the issue's diff.

Relation to #282:
- The repair is already cleared when inbound resumes: `_note_mqtt_inbound` (`:4250-4254`), tested at `tests/test_issue_222.py:113`, `:128` and `:140`.
- #316 recurs only because inbound never resumes while the hardware is off.

Test impact:
- The existing silence tests (`tests/test_issue_222.py:92`, fixture `_silent_coord` at `:51`) must give their devices `online=True`, or the new gate stops them counting.
- New cases are needed for all-offline (no reconnect, no repair) and mixed online/offline (only online devices count).

### Q3 #317 H5074 °F

- **Status:** the reporter closed the issue on 2026-11-25T22:30Z after working around it. The code gap stays, and the commit that fixes it, by citing #317, gets the automatic release reply on the closed thread.
- **The arithmetic:** 63.5 °F treated as °C gives 63.5 × 1.8 + 32 = 146.3, which is exactly what HA showed.
- **Current set:** H5074 is not in `FAHRENHEIT_REPORTING_SKUS` (`const.py:141-163`), but its sibling H5075 is.
- **Pending note in const.py:** `const.py:497-500` says the H5074 stays out "until a reading shows which unit it reports in". This issue is that reading.
- **The reporter's "config" fix:** this is almost certainly the account-wide **API temperature unit** option (`CONF_API_TEMPERATURE_UNIT`, `const.py:64`).
- **Precedent:** commit `cc7e949` added H5102 and H5107 on the same kind of evidence.
- **Outside sources:** no report found of H5074 cloud-unit behaviour elsewhere (web-researcher, searched 2026-11-26). The Govee docs don't say whether `sensorTemperature` follows the app's unit setting.

### Q4 Reply-only threads

- **#315 (H6022):**
  - This model is already supported: `MQTT_MUSIC_MODE_SKUS` (`const.py:193`), `number.py:248`, `switch.py:704`, `docs/device-catalog.md:616`.
  - The one-line report is most likely "not discovered", the same class as #306 and #209.
  - Ask for versions, whether the lamp is on the same account as the API key, and diagnostics.
- **#318:** a duplicate of #295. core#190454 is open with no assignee or linked PR (direct `gh issue view -R home-assistant/core`, last update 2026-11-16). The canonical workaround is already posted on #295.
- **#298 (H7087):**
  - The reporter confirmed v2026.11.1 with DIY scenes enabled, and only this model is affected.
  - There is no H7087-specific code.
  - T-004's per-device DIY fetch result in diagnostics is the prerequisite. Ask for diagnostics after it ships.
- **#299:** there is still no SKU, so ask for it again. Per-SKU ptReal DreamView work (#304, #241) is the only path, because the Platform API exposes DreamView as on/off only.
- **#288 (Tap-to-Run):**
  - The commenter's govee2mqtt claim is confirmed. `src/undoc_api.rs` at commit `6f5a8449` calls `GET app2.govee.com/bff-app/v1/exec-plat/home` with a community-api token (web-researcher, fetched 2026-11-26).
  - Nothing comparable exists here: grep finds no `exec-plat` or `oneClick`, and `api/mqtt.py` has no raw publish (`async_publish_command` at `:1149` builds its own envelope).
  - It is unknown whether the endpoint accepts this integration's app2 token (`api/auth.py:1658`). Without that, a second login and token store would be needed.
  - Estimated at about 8 files, 300 lines and 25 tests, plus a captured response for fixtures.
- **#308:** a second H60A6 owner (2026-11-26) reports that firmware 1.00.49 fixes the reboot on the device side. v2026.11.1's cloud power-off covers 1.00.48. Leave it open until the original reporter confirms.

### Q5 Plan cite check

T-002..T-006 cites still match main:
- `PTREAL_SEGMENT_SKUS` is at `const.py:263`. The mask bytes at `ble_packet.py:220-222` are still 3-byte.
- `DREAMVIEW_STATE_FROM_FRAMES_SKUS` (`const.py:285`) and `PTREAL_DREAMVIEW_SKUS` (`:601`) are still `{H66A0}`.
- `diy_scene_cache_count` is at `diagnostics.py:373`.
- The protocol reference still has "3-byte little-endian mask" at `:4737`, and `33 60 02` is absent.

Two merge interactions with the open plan:
- **T-002 / #321:** #321 adds a function to `api/ble_packet.py` but doesn't touch the segment builders T-002 edits.
- **T-005 / #319, #320, #321:** all three touch the protocol reference. T-005 should be built after the PRs merge.

## Dissent / Contradictory Evidence

- **#320 "side runs with either":**
  - #211's frame table says "Only two sections can be on at once" (`33 30 <sec>`). That fits #320's model (side plus one of bottom or ripple), but it does not prove side never drops.
  - The PR author's hardware test is the only evidence that side stays on. If the lamp drops side instead, the mirror would show side on while it is off, until the next user action. This is low impact, because only H60B0 entities are affected.
- **#320/#321, single tester:** the author verified both on one H60B0, and no public source documents the ripple-flow frame or mutual exclusivity (web-researcher: not found). This is the same evidence class as other SKU-scoped merges, per memory "HACS reporters can't test branches".
- **#319 synthetic steps:** two of the frames in the tests (main off, background off) are synthesised from the captured frame list. The hardware table in the PR body covers the same transitions.
- **#316 detection delay:** gating on `state.online` lags the cloud flag (comment at `models/state.py:618`). A device that just powered on may not count for one sweep, which delays detection by at most one poll interval (default 60 s) against the 1,800 s check. The reporter's diff and the analyst agree.
- **#317 for every account:** an account whose API really returns °C for H5074 would now be converted wrongly. No such report exists, the sibling H5075 is already °F, and the option remains the override.

## Recommendation

### Decision

Extend `docs/plans/open-issues/` rather than starting a new plan. Its T-001..T-007 are unbuilt and still valid. Ship everything in the 2026-11-28T02:00Z release.

1. **T-001 (unchanged): Dependabot.** Merge #311, #312 and #313, then #292 with #291, and do the `daily-release.yml` dry run.
2. **Community PRs:**
   - Approve the pending fork workflow runs: `gh api -X POST repos/lasswellt/govee-homeassistant/actions/runs/<id>/approve` for each `action_required` run.
   - Merge in this order: **#319 → #321 → #320**.
   - After each merge, update the next PR's branch from main with the conflict resolved by keeping both blocks (`maintainerCanModify` is true), and wait for its CI to go green before merging it (`gh run list --commit <full sha>`).
   - The resolution is the one proven green in this research: `const.py`, keep `LIGHT_ZONE_FRAME_SKUS` then `RIPPLE_FLOW_SKUS`; `models/device.py`, keep `supports_ripple_flow` then `@property light_toggle_modes` and `light_toggles_turned_off_by`.
   - Optionally, refresh the stale H60A6 comments at `const.py:257` and `:592` in a follow-up commit.
3. **New T-008, #316:**
   - Add a `_count_mqtt_status_query(device_id)` helper that increments `_mqtt_queries_since_connect` only when `state.online`, and use it at `coordinator.py:3891` and `:4542`.
   - Give the online-device fixtures in `tests/test_issue_222.py` `online=True`.
   - Add the all-offline and mixed cases.
4. **New T-009, #317:**
   - Add `"H5074"` to `FAHRENHEIT_REPORTING_SKUS` (`const.py:141-163`).
   - Replace the `const.py:497-500` note with the #317 evidence.
   - Add H5074 to the parametrised °F SKU test next to H5102 and H5107.
5. **T-002, T-003, T-004 (unchanged).** Build T-005 (protocol reference) after the three PRs merge, to avoid more conflicts in `docs/govee-protocol-reference.md`.
6. **T-006, extended reply queue** (`docs/release-replies.md`). #316 and #317 are cited by their commit subjects and get the automatic reply, so they need no entry.

   | Thread | close | Reply |
   |---|---|---|
   | #315 | no | H6022 is supported; ask for versions, same-account check and diagnostics |
   | #318 | duplicate | Point to #295's workaround and core#190454 |
   | #298 | no | Ask for diagnostics on the release that adds the DIY fetch result |
   | #299 | no | Ask for the SKU again |
   | #288 | no | Ask a user with Tap-to-Runs to test `exec-plat/home` with the app2 token; the feature is planned only once that works |
   | #308 | no | Thank the second user for confirming firmware 1.00.49; the original reporter to confirm |

   This is in addition to the existing #310, #208, #241, #314, #308 and #304 entries; merge the two #308 briefs into one.
7. **Defer #288** (Tap-to-Run) to its own plan once the token question has an answer.
8. **T-007 (unchanged):** full gates and green main CI before 02:00Z.

### Rationale

- **The three PRs:** each passes every local gate, and all three merged together pass too (4,789 tests, 99.74%). They are SKU-scoped (H60A6, H60B0), hardware-tested by their authors, and follow CLAUDE.md conventions: translation keys, icons in `icons.json`, translated errors, and RestoreEntity for unreported state. The conflicts are adjacency only, with a resolution shown to be green.
- **Merge order:** #319 first, because it is the smallest and is independent of #320. #321 next, then #320, so each conflict is resolved once against main.
- **Approving the runs:** the "Protect main" ruleset needs green required checks, and fork CI waits on approval. Approving the runs is the only way to get them.
- **#316:** the fix is two call sites behind one helper. It keeps the #242 recovery (the query is still published) and removes a repair that tells users to reconfigure, which cannot help while their hardware is off.
- **#317:** a one-line SKU addition with exact numeric evidence and a sibling precedent. The repo comment at `const.py:497-500` already named the missing evidence.
- **The rest:** none of the other threads has evidence for a code change today. Replies collect it.

### Implementation sketch

| Task | Files | Verify |
|---|---|---|
| PRs | `gh api .../actions/runs/<id>/approve`; `gh pr merge 319 --squash`; update #321 and #320 branches (`const.py`, `models/device.py` resolutions above) | CI green per PR; on main, `pytest` ≥ 4,789 passed |
| T-008 #316 | `custom_components/govee/coordinator.py` (helper, `:3891`, `:4542`), `tests/test_issue_222.py` | `pytest tests/test_issue_222.py`; coordinator.py coverage unchanged or higher |
| T-009 #317 | `custom_components/govee/const.py` (`:141-163`, `:497-500`), the °F SKU parametrised test (grep `H5107` in `tests/`) | `pytest -k "fahrenheit or H5074"` |
| T-006 | `docs/release-replies.md` | entries for #315, #318, #298, #299, #288, #308 |

## Risks

- **Release size:** the 2026-11-28 release would carry #310, #241, DIY diagnostics, #316, #317 and three community features.
  - All are SKU-scoped except #316, which is narrow: it changes only when a query counts as silence evidence.
  - No broad behaviour change, so the "own release day" rule does not apply.
- **Fork CI:** if a PR's CI fails after approval on a check the local lane doesn't cover (py3.13 job, HACS, hassfest), that PR waits for the next day rather than blocking the others.
- **#320:** the side-light assumption may be wrong (see Dissent).
- **Dependabot dry run:** still the only test of the release path (T-001).

## Sources

The primary sources for this repository's own threads and pins are on github.com. The H6022's support status comes from this repo's own code and device catalog. The one other domain is the icon index.

- https://github.com/lasswellt/govee-homeassistant/pull/319
- https://github.com/lasswellt/govee-homeassistant/pull/320
- https://github.com/lasswellt/govee-homeassistant/pull/321
- https://github.com/lasswellt/govee-homeassistant/issues/211 (H60B0 frame table, "Only two sections can be on at once")
- https://github.com/lasswellt/govee-homeassistant/issues/316
- https://github.com/lasswellt/govee-homeassistant/issues/317
- https://github.com/lasswellt/govee-homeassistant/issues/315
- https://github.com/lasswellt/govee-homeassistant/issues/318
- https://github.com/lasswellt/govee-homeassistant/issues/308
- https://github.com/lasswellt/govee-homeassistant/issues/288
- https://github.com/home-assistant/core/issues/190454
- https://github.com/wez/govee2mqtt/blob/6f5a8449ca63da8bdc7cc696288a993681ff2a69/src/undoc_api.rs
- https://raw.githubusercontent.com/Templarian/MaterialDesign-SVG/master/meta.json (MDI icon index)
