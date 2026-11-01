---
status: active
priority: P1
created: 2026-10-26
ship: manual
---
# Follow-ups after v2026.10.1

## Goal
v2026.10.1 shipped on 2026-10-25 and the threads answered within a day. Three follow-ups need code: #222's silent-session check never arms on accounts with no status-poll target, the H5194 drops its later probe reads (#266), and the H7130 heater's controls misbehave (#265). New diagnostics show why the H7073 has no colour control (#240) and why the H66A0's DreamView switch shows ON while sync isn't running (#213). Two PRs arrived: #268, a small brightness fix, and #267, a large opt-in H6104 Bluetooth feature. #269 asks for the scene select to show None while the light is off. The work was planned over three release days. Day A shipped as v2026.10.2 on 2026-10-27. On 2026-10-28 the user put everything left into that night's release ("everything tonight, finish all issues"): #267, PR #271 (which replaces our own #269 work), and three scoped items from the 2026-10-28 sweep: #261 H1A42 segments, PR #270 and PR #273 (`docs/research/2026-10-28_post-release-sweep.md`).

## Outcomes
- `light.turn_on` with an effect and a brightness applies both (#268) → T-001
- Every H5194 probe updates at the polled interval: reads are paced 1 s apart, temperatures before limits, and limits every 6th tick (#266) → T-002
- A connected but silent AWS IoT session is detected on a hub-only account, by gateway readings that reached Govee's cloud but not the session. A quiet account with no prompts still never gets the repair (#222) → T-003, T-004
- An H7130 heat-level or setpoint change shows the heater as on, and setpoints round instead of truncating. The heater gets an oscillation switch, and Auto Stop drives the device's own Auto Stop field (#265) → T-005, T-006, T-007
- An H7073 with the account login gets a colour picker whose writes go over AWS IoT (#240) → T-008
- The H66A0's DreamView switch follows the device's own mode report, not a stale optimistic ON (#213); other DreamView models keep today's behaviour until a capture confirms theirs → T-009
- Every thread that needs a specific reply has a queued brief, and no thread gets two replies → T-010
- Day A passes every gate: 95 % overall, 96 % per module, config_flow.py 100 %, lint and types, and the 3.14 suite → T-011
- PR #267 is the first change merged after v2026.10.2, CI green. Its behaviour changes are limited to an opted-in H6104, it has translated errors and icons, and its README is written for upstream users. Its BLE connection bugs are fixed, and a config-entry test covers it → T-012, T-013, T-014
- `govee.set_segment_color` and the segment lights colour individual H1A42 segments, over the AWS IoT ptReal route the reporter confirmed (#261) → T-016
- H5105 and H5108 readings convert from °F in Auto mode (PR #270) → T-017
- A Bluetooth discovery card closes when the integration is set up by hand (PR #273) → T-018
- Scene and DIY selects show None while the light is off, DIY scenes are light effects, and a colour change ends a DIY scene (#269, by merging PR #271; replaces T-015) → T-020
- #239, #274, #137, #266 and #258 get queued replies; #266 and #258 close → T-021
- Tonight passes every gate: 95 % overall, 96 % per module, config_flow.py 100 %, lint and types, and the 3.14 suite → T-022

## Out of scope
- #236 H5111 behind an H5151: Govee's account data is stale (last reading Oct 4, while other sensors are fresh), and no public client reads anything else. Reply only.
- #261 H1A42 segments and #239 H7045 White scene: no code fault in the integration; the reporters' data comes first. Conditional follow-ups: H1A42 in `PTREAL_SEGMENT_SKUS`, a `gradientToggle` switch, and the `paramId` in scene payloads.
- The H66A0 DreamView ON frame (`DREAMVIEW_VIDEO_PAYLOAD_BY_SKU`) waits for the reporter's `send_raw_ptreal` test.
- #244 H5054 (the reporter is testing more sensors), #209 H7105, and #211 H60B0.
- The `issue-sweep-late-oct` tasks T-026 to T-032 (#249-gated PRs, #246, LAN trust, #254) stay in that plan.

## Assumptions
- #222: the H5310 is not made a status-poll target, and the H5044 hub topic is never queried. Both risk the #195 session drops on an account whose only push channel is that hub.
- #265: the Auto Stop change applies to the H7130 only (`HEATER_AUTO_STOP_STRUCT_SKUS`), so other heaters keep the #29 behaviour. None of the fixes sends raw frames (F-14).
- #240: colour is offered only when the entry has IoT credentials, so an API-key-only user never gets a picker that can only fail.
- Replies: a thread named in a commit subject gets the bot's automatic reply. Threads that need specific asks (#265, #213) are named only in commit bodies (`Refs #N`) and get a queued brief instead.
- #267: "merge with our fixes" (user, 2026-10-26). The contributor is not asked for changes first.
- #269: option A, presentation only (user, 2026-10-26). The stored scene is untouched, and the snapshot select is unchanged. On 2026-10-28 the user chose to merge PR #271, which implements the same option, instead of our own T-015.
- Release days (user, 2026-10-28): everything above ships in the 2026-10-29 03:00 UTC release. That waives the own-day rule for #267 and #271 and the hands-on try before #271, for this night only. #267 still merges first, so its own-day check stays meaningful. The maintainer may still try #271 before 23:00 EDT and revert it if it misbehaves.
- The dependabot bumps (#275, #276, #277) change no file under `custom_components/` and are not plan tasks. #277 and #275 merge with CI green; #276 changes the release token step and merges after tonight's release, followed by a dry run.

## Verification
- `bash /home/lasswellt/.claude/plugins/cache/blitz/blitz/3.16.0/scripts/tasks.sh verify followups-2026-10-1 <id>` per task; `/blitz:check --scope plan followups-2026-10-1` before each release day.
- Own-day rule: T-012 checks that nothing under `custom_components/` is unreleased on main before the #267 merge. For 2026-10-28 the user waived "nothing else after it" (see Assumptions).
- Manual (optional, user waived it as a gate): the maintainer tries #271 on their own lights before 23:00 EDT.
- After each release, read the bot's replies and the reporters' answers (#240 colour, #265 which toggle acts, #213 frame test).
