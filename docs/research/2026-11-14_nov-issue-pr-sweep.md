---
title: November issue and PR sweep — fix plan and merge readiness
date: 2026-11-14
type: Feature Investigation
topic_slug: nov-issue-pr-sweep
status: complete
agents: pr-contrib-validator, pr-maint-validator, issues-new-triage, issues-backlog-triage (4/4 succeeded)
supersedes_open_tasks:
  - docs/plans/followups-2026-10-1 (T-012..T-022, never built after the 2026-10-28 revision)
  - docs/plans/issue-sweep-late-oct (T-026..T-032, held on #249 / own-day rule)
---

# November issue and PR sweep

## Summary

Nothing under `custom_components/` has merged since v2026.10.2 (2026-10-27): `git log v2026.10.2..origin/main` shows three docs/chore commits, and the daily-release job has run green and skipped every night since. Meanwhile 34 issues and 14 PRs are open. Two plans still have open tasks: `followups-2026-10-1` (T-012..T-022) and `issue-sweep-late-oct` (T-026..T-032). Both are 17+ days stale.

Main baseline (origin/main `7216551`, py3.12 venv, HA 2025.1.4 lane): **4277 passed**, flake8 clean, black clean, CI green.

Findings:
- **Ready to merge now:** PR #270 and PR #273 (gates green locally, clean merge, SKU- or flow-scoped). Workflow-only PRs #256, #277 and #263 (with a one-line fixup). #264, #275 and #276 can follow once their action SHAs are verified.
- **Ready, but each on its own release day:** #267 (H6104 BLE, needs fixups), #252 (BLE/LAN), #271 (needs the author to drop a fork workflow first), and #254 (all-device brightness).
- **Blocked on the author:** #251 (coordinator.py conflict plus two open security items).
- **Close:** #280 (pytest 9 cannot resolve against `pytest-homeassistant-custom-component`'s `pytest==8.3.4` pin).
- **Code fixes we can make now (8):** #261, #278, #282 (also covers #222), #283, #284, #286, #246 (small form), and #287 once diagnostics arrive.
- **Replies or closes owed:** about 20 threads. Several reporters confirmed fixes 2–18 days ago and nobody answered them.

## Research questions

1. Which open PRs can merge as-is, and which need fixups, a rebase, the author, or their own release day? → §PR validation
2. Which new issues (#278–#289) have a code fix we can make today, and where? → §New issues
3. In the older backlog, who owes the next move on each issue? → §Backlog
4. Are the stale plan tasks still valid, and what supersedes them? → §Stale plans
5. Which external API changes (HA 2026.11, GitHub Action majors, pytest 9) force work? → §External changes
6. What blocks merging, and what can only the maintainer decide? → §Decisions needed

## PR validation

Method: each PR was fetched as `refs/pr/N`, merged onto origin/main in a scratch worktree, and run through the four gates (pytest, flake8, mypy strict, black). Pairwise checks used `git merge-tree --write-tree`. Fork PRs have no CI on their current heads because workflow approval is pending.

| PR | Author | Scope | Merge onto main | Gates (PR+main) | Verdict |
|---|---|---|---|---|---|
| #270 | bdashrad | +H5105/H5108 in `FAHRENHEIT_REPORTING_SKUS` | clean | 4283 pass, 99.85 %, lint/mypy/black clean | **MERGE-AS-IS** |
| #273 | snoack | config_flow aborts in-progress bluetooth flows on manual setup | clean | 4278 pass, config_flow 100 % | **MERGE-AS-IS** |
| #267 | krozgrov | H6104 opt-in BLE control/readback, +1847/-37, 25 files | clean | 4413 pass, 99.71 %, h6104.py 100 % | **MERGE-WITH-FIXUPS**, own day |
| #271 | magicmn | DIY scenes as light effects; scene selects show None while off | clean | 4306 pass, 99.85 % | **NEEDS-AUTHOR**, then own day |
| #252 | BrawnyBravo | BLE enrol by MAC, idle BLE release, LAN client hardening | clean (108 behind) | 4309 pass, clean | **MERGE-AS-IS**, own day; held by #249 decision |
| #254 | BrawnyBravo | exact brightness round-trip, segment brightness, scene refresh | clean (17 behind) | 4318 pass, clean | **HOLD-OWN-DAY** (all-device), hands-on first |
| #251 | BrawnyBravo | login/MQTT recovery, auth.py rewrite | **conflict: coordinator.py** | not run | **NEEDS-AUTHOR** |
| #256 | BrawnyBravo | daily-release.yml + scripts/release_checks.sh gate fixes | clean; main hasn't changed either file since PR base | n/a (workflow) | **MERGE-WITH-FIXUPS**; held by #249 decision |
| #277 | dependabot | setup-python 5.6.0→7.1.0 | mergeable | CI green | **MERGE-AS-IS** |
| #263 | dependabot | codecov-action 4.6.0→7.1.1 | mergeable | CI green | **MERGE-WITH-FIXUPS** (`file:`→`files:`) |
| #264 | dependabot | checkout 4.4.0→7.0.1 (11 uses, incl. daily-release.yml) | mergeable | CI green | **MERGE** after #256, after SHA check |
| #275 | dependabot | claude-code-action 1.0.259→1.0.262 (release drafting job) | mergeable | CI green | **MERGE** after SHA/compare check |
| #276 | dependabot | create-github-app-token 3.2.0→3.3.0 (release App key) | mergeable | CI green | **MERGE** after SHA/changelog check |
| #280 | dependabot | pytest >=9.2.0 | blocked | install fails | **CLOSE** + dependabot ignore |

### Details that change what we do

- **#271 includes `.github/workflows/sync-upstream.yml` (+68).** It is the contributor's fork-sync cron. It has `contents: write` and runs `gh release create` every 6 h. It must not land upstream. The PR is pushed from the fork's `main`, so the author has to rebase it out. Otherwise we cherry-pick the five feature commits without that file. The feature itself changes behaviour on every light. `select.py:70-87` `_light_is_off()` makes the scene and DIY selects read None whenever power is off, which reverses #18. `light.py` adds `EffectFeature` and "DIY: <name>" effects to every light with DIY scenes. The `models/state.py:944-955` change that clears `active_diy_scene` on colour is a safe bug fix and could ship on its own.
- **#267 open items** (most of the 10-28 review is resolved: CI red, README, DreamView guard, S1/S3/S4/S6/S7):
  1. S2: `coordinator.py` ~5500 awaits `_async_refresh_h6104_ble_status(force=True)` inline after every BLE command. That is up to 3 × 0.5 s under the device lock. Move it to `entry.async_create_background_task`.
  2. The poll (~4359) gathers H6104 readbacks before the cloud read.
  3. The T-014 MockConfigEntry lifecycle test and the "other BLE models unchanged" assertion are still missing.
  4. `number.py`/`select.py` read `coordinator._config_entry`. Use a coordinator property instead.
  5. Head `5e6eeea` hasn't been retested on hardware.

  The behaviour is opt-in (default False), but the BLE eligibility code it touches is shared with other models.
- **#251:** No equivalent fix landed on main, so it isn't superseded. Two items from the 10-23 security review are still open:
  - MEDIUM: `_async_refresh_iot_credentials` re-logs in every 15 min on GoveeAuthError/2FA without latching. That can mean about 96 logins a day, which trips Govee's 2FA hardening.
  - LOW: `is_connection_error` treats `ClientResponseError`/`ContentTypeError` as network errors.

  The conflict is in `coordinator.py`. Main's #222 silent-session detection (`fc4dbe6`, `d7b50b6`) touched the same region.
- **#256 is still needed.** Main has no equivalent for any of these:
  - The `--exclude-drafts` guard. Today a draft release makes `date -d ""` evaluate to today, which silently skips every night.
  - The "Check style formatting" required check.
  - The tag-exists refusal.
  - Re-run-safe bumps.
  - The per-name check query (pagination).
  - The cron move from 03:00 to 02:00 UTC.

  Before merging, confirm that the ruleset actually requires "Check style formatting".
- **#280:** the failing log says `pytest-homeassistant-custom-component 0.13.205 depends on pytest==8.3.4` → ResolutionImpossible. Add `ignore: pytest` (semver-major) to `.github/dependabot.yml` and close the PR.
- **#263:** the v7.1.1 `action.yml` has no `file` input, only `files`. As written, `tox.yaml` passes `file: ./coverage.xml`, which v7.1.1 ignores, so the upload falls back to auto-discovery. Edit that line on the PR branch.

## New issues (#278–#289)

| # | Category | Root cause (file:line) | Fix | Blast radius | Conf. |
|---|---|---|---|---|---|
| 278 | sku-support | `models/device.py:47` `KETTLE_FRAME_SKUS = {"H7175"}` | add H7170; flip `tests/test_kettle_stale_temperature.py:83` param; docs | H7170 only | high (reporter verified on hardware) |
| 282 | bug | `coordinator.py:4000` deletes the repair only `if self._mqtt_silent_reported`; flag is per-instance (`:407`), so after reload/reauth the old non-persistent issue is never cleared | delete unconditionally (idempotent); also clear when the silence check is armed for a new session (`_arm_mqtt_silence_check` ~3987) | MQTT accounts; repairs only | high |
| 283 | sku-support | frame decoder `models/state.py:791-834` is gated on `PUMP_DEHUMIDIFIER_SKUS={"H7152"}` (`device.py:41`, used at `coordinator.py:3906`, `device.py:722/736`) | new `FRAME_CLIMATE_DEHUMIDIFIER_SKUS={"H7152","H7151"}` for the climate gates; keep the pump set unchanged (pump fault/hose bytes) | H7151 only | high |
| 284 | deprecation | `water_heater.py:93/122/128` implement `temperature_unit`/`current_temperature`/`target_temperature` | rename to `native_*` behind a HA-version gate (hacs.json minimum is `2024.11.0`); keep `min_temp`/`max_temp`/step (not renamed per HA blog) | kettle SKUs | high on cause; check `async_set_temperature` unit semantics against HA 2026.11 source |
| 286 | bug | `switch.py:663-668` falls back to a stale `self._is_on` when `music_mode_enabled` is None; `state.py:924` `apply_optimistic_power(False)` and the colour/CT optimistic paths never clear the music flags | clear the music fields on optimistic power-off/colour/CT and on MQTT power-off; `is_on` reads `bool(state.music_mode_enabled)` | music-mode switch display on every music-capable light; no command change | high; overlaps #271 in the same `state.py` colour functions |
| 287 | bug / needs-info | H5107 isn't in `FAHRENHEIT_REPORTING_SKUS` (`const.py:115-132`), or the BFF `tem` path assumes Celsius | (a) same one-line fix as #270, or (b) fahOpen handling in the BFF path. Diagnostics decide which | H5107 only for (a) | med |
| 281 | needs-info / API limit | Platform API returns `[]` DIYs for H70B6 (`scene_cache.py:243-262` → `api/client.py:804-854`); select only created when non-empty (`select.py:143-154`) | none until diagnostics; app "DIY" may be account-side `/appsku/v1/diys/groups-diys` (not implemented) | — | med |
| 285 | needs-info | possible: `SceneCommand.get_value` (`models/commands.py:190-194`) sends `{id,name}` and drops `paramId`; two scenes sharing id 136 collide in the reverse map `select.py:297,307` | reply asking for version + diagnostics; the paramId fix is all-device → hands-on first | all scenes if done | low |
| 288 | feature | no Tap-to-Run execute API known (`docs/govee-protocol-reference.md:1105` lists read-only `/bff-app/v1/exec-plat/home`) | park; reply asking for an app capture | — | low |
| 289 | needs-info | `api/lan.py:465-480` already sets `IP_MULTICAST_IF` per enabled source IP; the 60 s multicast the reporter captured doesn't match our 300 s rescan (`const.py:496`) | reply: upgrade from 2026.10.0, send the diagnostics `lan_discovery` block, answer their four questions | — | med-low |

## Backlog (#200–#274)

| Owes | Issues |
|---|---|
| **MAINTAINER-FIX** | #261: `const.py:220` add H1A42 to `PTREAL_SEGMENT_SKUS`. The reporter ran both raw frames successfully on 10-27; this is plan T-016. #246: H1270 BLE flapping, 34 days and never answered. The small fix: `_ble_write_eligible` returns False for LAN-capable commands while LAN is healthy (BLE-capable SKUs are only H6199/H1270, `api/ble.py:101`). The full back-off (T-029) depends on #252. #222: subsumed by the #282 fix, plus a reply to Araknus13's question. |
| **MAINTAINER-REPLY / close** | #223 (retest OK 11-08), #266 (confirmed on 10.2), #258 (confirmed on 17 devices), #207 (verified fixed: `api/client.py:526,587,731,801,856`), #245 (shipped in 10.1 as an option), #249 (PVR enabled, `SECURITY.md` links the advisory form, no advisory filed in 22 days), #239 (four questions, from T-021), #244 (probe-contact test), #209 + #274 (duplicates; close #274) |
| **WAITING-REPORTER, >45 days → stale close** | #201 (54 d), #215 (50 d), #208 (47 d) |
| **WAITING-REPORTER, keep** | #200 (OP okayed close; close and invite them to reopen with data), #213, #236, #237, #241, #242 (verified fixed via `entity.py:73-85`), #265 (all 18–20 d) |
| **DEFER / help-wanted** | #211 (H60B0, waiting on a contributor PR), #269 (closes via #271) |

## Stale plans

- `followups-2026-10-1`: T-012..T-014 (#267), T-016 (#261), T-017 (#270), T-018 (#273), T-020 (#271), T-021 (replies) and T-022 (gates) are still valid in scope. The facts underneath them changed: #267 got a new head, #271 kept the sync workflow, #270 was rebased. T-020's assumption that "#271 merges cleanly as-is" is now false.
- `issue-sweep-late-oct`: T-026..T-028 are held on #249, T-029 (#246) depends on #252, and T-032 is #254 on its own day. T-030 (LAN datagram source trust) is still open.
- Recommendation: close both plans as superseded and carry their remaining tasks into one new plan, so `/blitz:next` stops reading two sets of stale state.

## External changes

- **HA 2026.11 water heater:** `current_temperature`/`target_temperature`/`target_temperature_high`/`target_temperature_low`/`temperature_unit` → `native_*`. "Implementing them, setting the `_attr_` attributes or reading them logs a warning", and support is removed in 2027.11. `min_temp`/`max_temp`/`target_temperature_step` are not renamed and stay in the native unit. The blog gives no back-compat guidance for older cores. ([HA blog 2026-10-08](https://contributors.home-assistant.io/blog/2026/10/08/water-heater-native-temperature), [2026.11 changelog](https://www.home-assistant.io/changelogs/core-2026.11/)). Climate got the same rename ([climate blog](https://developers.home-assistant.io/blog/2026/10/08/climate-native-temperature)). `fan.py`/`humidifier.py` aren't affected; the warning names only the kettle.
- **actions/setup-python v6/v7:** Node 24; v7 removed the `pip-install` input, which we don't use ([releases](https://github.com/actions/setup-python/releases)).
- **actions/checkout v5–v7:** Node 24; v7 blocks fork checkout under `pull_request_target`/`workflow_run`, neither of which the repo uses ([releases](https://github.com/actions/checkout/releases)).
- **codecov-action v7.1.1:** the `files` input only ([action.yml](https://raw.githubusercontent.com/codecov/codecov-action/v7.1.1/action.yml)).
- **pytest 9:** blocked by `pytest-homeassistant-custom-component`'s exact pin, `pytest==8.3.4` (CI run 41806401065 log).

## Dissent / contradictory evidence

- **#252 risk.** pr-maint rates it "MERGE-AS-IS, own day not required". The 10-23 sweep and the maintainer's ruling put it behind #249, and its BLE idle disconnect and LAN retry loop touch every BLE/LAN user. This doc treats it as a separate release day. The gates are clean either way.
- **#286 reach.** It touches every music-capable light's switch state. That reads like the "all-device" rule, but no command or device behaviour changes; only a stale display state gets cleared. This doc puts it on a normal day and calls it out in the release note. If you apply the own-day rule strictly, move it.
- **#246 small fix vs T-029.** The small fix (prefer LAN when healthy) changes H6199's transport order too. T-029 (back-off) is more targeted but waits on #252. Recommend the small fix only if #252 stays held.
- **#284 back-compat.** The HA blog gives no dual-version pattern. Two options: define both names (old ones log a warning on ≥2026.11), or gate on `homeassistant.const.MAJOR_VERSION/MINOR_VERSION` as `registry.py` does for 2026.10. The gate avoids the warning, so it's preferred. Not verified against HA 2026.11 source in this sweep, because the local test lane is HA 2025.1.4.

## Decisions needed (maintainer only)

1. **Lift the #249 hold?** 22 days have passed and no advisory has been filed (`gh api .../security-advisories` returns empty). The owner replied "enabled" on 10-23. Recommendation: lift the hold for #252 and #256 (the 10-23 security review rated #252 clean and #256 is workflow-only). Keep #251 with its author, and send #249 a final note.
2. **Own-day rule.** On 10-28 you waived it ("everything tonight"). Re-apply it for #267, #252, #271 and #254, or waive it again? Recommendation: re-apply. Four broad changes have queued up, and bundling them would make regressions untraceable.
3. **#271's fork workflow.** Ask magicmn to rebase it out, or cherry-pick the feature commits without it ourselves (with a `Co-authored-by` trailer)?
4. **#271 hands-on** and **#254 hands-on** on your own lights before each merges.

## Recommendation

### Decision

Close `followups-2026-10-1` and `issue-sweep-late-oct` as superseded. Create one plan, `nov-sweep`, that runs over release days. No release day carries more than one broad change.

- **Day 1 (tonight, normal release):**
  - Merge #270 and #273.
  - SKU-scoped fixes: #261 (H1A42 → `PTREAL_SEGMENT_SKUS`), #278 (H7170 → `KETTLE_FRAME_SKUS`), #283 (new `FRAME_CLIMATE_DEHUMIDIFIER_SKUS`), #284 (kettle `native_*`, version-gated).
  - Repairs fix #282/#222 (idempotent delete, clear on new session).
  - Music-mode state fix #286.
  - Workflow-only merges, which don't trigger a release by themselves: #277, #263 with the `files:` fixup, #256 (if decision 1 = lift) then #264, and #275/#276 after verifying their tag SHAs. Close #280 and add a dependabot `ignore` for pytest majors.
  - Queue `docs/release-replies.md` entries for the threads no commit references:
    - Closes: #223, #266, #258, #207, #245, #249, #201, #215, #208, #200, #274 (as a duplicate of #209).
    - Asks: #239, #244, #246 (ack), #281, #285, #287, #288, #289, #209.
- **Day 2:** #267 with fixups S2, the poll ordering, and the T-014 lifecycle test plus the "other BLE models unchanged" test. Ask krozgrov to confirm head `5e6eeea` on hardware.
- **Day 3:** #252 (if decision 1 = lift), then the T-029 BLE back-off for #246.
- **Day 4:** #271 without `sync-upstream.yml`, after a hands-on try. The #286 state.py edits from Day 1 will need a rebase here.
- **Day 5:** #254 after a hands-on try; CI must run on its head first.
- **Later:** #251 once the author rebases and fixes the two security items. #287 once diagnostics arrive (likely one line, like #270).

### Rationale

- Six of the eight code fixes are one-SKU or repairs-only. They're backed by reporter evidence (on-hardware verification for #278/#261, frames for #283, warning text for #284), so they fit a normal release. The HACS rule says ship unverified-but-scoped fixes rather than parking them.
- The four broad PRs (#267, #252, #271, #254) each change shared transport or entity semantics. The maintainer's own-day rule exists so a regression can be traced to one change, and two of them need a hands-on try first.
- The workflow-only merges never trigger a release (the gate is `git diff <tag>..HEAD -- custom_components/`). #256 also fixes a latent bug where a draft release silently skips every night.
- Twenty threads with confirmed fixes or open asks have gone unanswered for 2–34 days. Queued replies go out with tonight's release, per the reply-after-release rule.

### Implementation sketch

| Task | Files | Tests | Verify |
|---|---|---|---|
| Merge #270 | (PR) | tests/test_thermometer.py | `pytest tests/test_thermometer.py` |
| Merge #273 | (PR) config_flow.py | tests/test_config_flow*.py | config_flow 100 % |
| #261 H1A42 ptReal | const.py:212-220 | new tests/test_issue_261.py (mask 0x01 / 0x7FFF frames) | `pytest tests/test_issue_261.py` |
| #278 H7170 kettle | models/device.py:44-47, water_heater.py:1-4, README | tests/test_kettle_stale_temperature.py:83 | grep H7170 in set |
| #283 H7151 climate frames | models/device.py:41+, :722, :736; coordinator.py:3906 | tests/test_cov_models.py, tests/test_cov_coordinator_events.py (frame `aa10810359...` → 21.9 °C/39.9 %) | targeted pytest |
| #284 kettle native_* | water_heater.py:88-190 | tests/test_water_heater*.py | no `temperature_unit` override on ≥2026.11 path |
| #282 repair clears | coordinator.py:3987-4053 | tests/test_issue_222.py, tests/test_cov_coordinator_setup.py | reload path deletes issue |
| #286 music state | models/state.py:924-950, switch.py:663-740, MQTT power path | tests/test_models.py, tests/test_cov_switch.py | is_on False after power-off/colour |
| Workflow merges | .github/workflows/*, dependabot.yml | CI | `gh run list --commit $(git rev-parse HEAD)` all green; `workflow_dispatch` dry_run after #256 |
| Replies queue | docs/release-replies.md | — | one `## #N` per thread |
| Final gates | — | full suite | coverage ≥95 %, per-module ≥96 %, config_flow 100 %, flake8, mypy, black |

## Sources

- Local: `gh issue list`/`gh pr list` on 2026-11-14; per-thread dumps; `git merge-tree`; scratch-worktree gates; `gh api .../private-vulnerability-reporting` → `{"enabled":true}`; `gh api .../security-advisories` → empty.
- Prior docs: `docs/research/2026-10-28_post-release-sweep.md`, `docs/research/2026-10-23_issue-sweep-late-oct-security-prs.md`, `docs/plans/followups-2026-10-1/`, `docs/plans/issue-sweep-late-oct/`.
- https://contributors.home-assistant.io/blog/2026/10/08/water-heater-native-temperature
- https://developers.home-assistant.io/blog/2026/10/08/climate-native-temperature
- https://www.home-assistant.io/changelogs/core-2026.11/
- https://github.com/actions/setup-python/releases
- https://github.com/actions/checkout/releases
- https://raw.githubusercontent.com/codecov/codecov-action/v7.1.1/action.yml
