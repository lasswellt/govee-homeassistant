---
date: 2026-11-25
topic: open-issues-and-prs
type: Feature Investigation
status: complete
---

# Open issues and Dependabot PRs after v2026.11.1

## Summary

v2026.11.1 shipped at 02:24Z on 2026-11-25, and the release bot replied on 19 threads. After that, 39 issues and 5 PRs are open (`gh issue list --state open`).

- **33 issues** are waiting on the reporter: our reply is the last comment (`gh api repos/.../issues/<n>/comments`, last author per issue). They need no work today.
- **6 issues** have an unanswered last comment: #314, #310, #308, #304, #295, #241.
- **5 Dependabot PRs** (#313, #312, #311, #292, #291) are all `MERGEABLE CLEAN` with all six CI checks green. None of the breaking changes in their release notes affects how this repository calls the actions.

Two of the six issues have a code fix the evidence supports today:

- **#310 (H7038):** segments 16–30 are reached over AWS IoT with a 4-byte mask. The reporter verified this on hardware.
- **#241 (H6099):** video-mode state can be read from the `aa 05 00` status frame, which is identical in two users' captures.

The other four need replies or docs only, plus one small diagnostics improvement that would explain the DIY-scene reports.

## Research questions

1. Which open threads need action from us, and which are waiting on a reporter?
2. Is the #310 fix (H7038 in `PTREAL_SEGMENT_SKUS`, 4-byte mask) safe for the SKUs already on the ptReal path?
3. Does the #241 diagnostics pair show how to read and switch H6099 video mode?
4. Why do DIY scenes go missing (#314, and the related #296/#298/#281)? Is it one cause, and is it ours?
5. Can each Dependabot PR merge as is, and which must merge together?
6. What do #308, #304 and #295 need?

## Findings

### Q1 Thread triage

| Last reply | Issues (waiting on reporter) |
|---|---|
| 2026-11-25 (release bot) | #309 #307 #306 #305 #301 #299 #298 #297 #296 #285 #261 #209 |
| 2026-11-15 | #289 #288 #287 #282 #281 #278 #246 #244 #239 #222 #215 #208 #207 #201 #200 |
| 2026-10-27 | #265 #236 #213 |
| 2026-10-25 | #242 #237 #211 |

Needs action: #314 (new, empty template), #310 (reporter supplied a verified fix), #308 (a second user asks how to get firmware 1.00.49), #304 (protocol findings, no reply yet), #295 (a user posted a Marketplace workaround), #241 (the requested video-off diagnostics arrived).

### Q2 #310 H7038 4-byte ptReal mask

- **Reporter evidence (issue #310, 2026-11-25 comment):**
  - Frames `33 05 15 01 RR GG BB 00 00 00 00 00 m0 m1 m2 m3` with masks `FF 7F 00 00`, `00 80 FF 00` and `00 00 00 3F` switched bulbs 1–15, 16–24 and 25–30 respectively.
  - Their local patch (H7038 in `PTREAL_SEGMENT_SKUS`, plus a `(mask >> 24) & 0xFF` byte) drove all 30 bulbs through `govee.set_segment_color`.
  - The Platform API answers 200 for indices 15–29 and does nothing.
- **Byte-identical for current SKUs:**
  - `build_packet` pads data to 19 bytes with `0x00` before the XOR checksum (`custom_components/govee/api/ble_packet.py:64`).
  - The colour frame is 15 data bytes, with the mask at indices 12–14 (`ble_packet.py:196`). Byte 15 is therefore already `0x00`.
  - A fourth mask byte that is 0 for masks below 2^24 (H1232/H1252/H1A42) leaves those 20-byte packets and checksums unchanged. The golden frames in `tests/test_ble_packet.py:696-723` stay valid.
- **Mask computation:** `coordinator.py:5885-5890` ORs `1 << index` with no cap, and each byte is `& 0xFF`. Indices 24–29 are therefore silently dropped today.
- **Builders:**
  - Segment brightness never goes over ptReal: `coordinator.py:5921-5923` sends it over REST, so `build_segment_brightness_ptreal` (mask at data bytes 5–7, `ble_packet.py:226`) can stay as is.
  - The colour-temperature builder (`ble_packet.py:250`, zone path only, `coordinator.py:5582`) takes the same fourth byte without collision.
- **Tests that must change:**
  - `tests/test_ble_packet.py:733-736` (24-bit masking), `:743-746` (colour length 15 becomes 16) and `:748-751` (brightness length, only if that builder changes).
- **Fallback without account login:**
  - `coordinator.py:5897-5901` falls through to REST when every index is below `api_count`.
  - For H7038, `api_count` is 30, so a cloud-only install keeps the silent no-op for indices 15–29.
- **H7026 (#208):**
  - The reporter suggests the same 4-byte layout, but `SKU_SEGMENT_OVERRIDES` pins it to 16 (`const.py:227-233`, asserted at `tests/test_models.py:1543-1547`).
  - Lifting that override would reopen the REST whole-string recolour for indices 16–29 on installs without MQTT. Not in scope until the #208 reporter confirms the frame with `govee.send_raw_ptreal`.
- **Community sources:** no community project or documentation confirms or contradicts the 4-byte layout (web search, 2026-11-25). The only evidence is the reporter's hardware test.

### Q3 #241 H6099 video mode

Data: three diagnostics uploads in #241. User A's has video on (2026-11-19). User B's has video on (2026-11-24). User A's second has video off and the lamp in colour mode, set to purple in the app (2026-11-25). Parsed offline; no identifiers copied.

- **Video on:**
  - Both ON captures carry the same status frame, `aa050000083200023200000000000000000000a5` (XOR valid), and `last_mqtt_message.state.mode = 0`. This holds for two users with different last colours.
  - The OFF capture's push carries only `color` (139,0,255), with no `mode` and no `_op_frames`. "Off = `aa 05` byte 2 ≠ 0x00" is therefore inferred, not captured.
- **Existing frame-based handling:**
  - The integration already reads DreamView from that exact frame for the H66A0: `DREAMVIEW_STATE_FROM_FRAMES_SKUS` (`const.py:285`), and `models/state.py:706-733` sets `dreamview_enabled = raw[2] == 0x00`.
  - For `PTREAL_DREAMVIEW_SKUS` (`const.py:601`) it sends ON as `33 05 00 01 00 64` (`api/ble_packet.py:311-336`) and OFF as a colour restore (`coordinator.py:6845-6935`).
- **Blocker:**
  - The H6099 advertises no `dreamViewToggle`. Its capabilities are powerSwitch, gradientToggle, brightness, segmentedBrightness, segmentedColorRgb, colorRgb, colorTemperatureK, lightScene, musicMode, diyScene and snapshot.
  - So `GoveeDevice.supports_dreamview` is false (`models/device.py:912-925`). No switch is created (`switch.py:179`), and the frame-state gate is skipped (`coordinator.py:4133`).
  - Adding the SKU to the two sets is not enough. It needs a capability fallback like the H2A41 movie-mode one (`models/device.py:920`).
- **Unverified ON frame:**
  - The report's trailing bytes (`08 32 00 02 32`) do not map onto the ON command's fields.
  - Whether the H6099's camera-based video mode can be entered with `33 05 00 01 00 64` is unverified. The H66A0 ON path is itself marked unverified in `docs/govee-protocol-reference.md` §9.8.

### Q4 DIY scenes (#314, #296, #298, #281)

- **Fetch:**
  - `POST /device/diy-scenes` (`api/client.py:820-844`) is cached for 24 h, an empty list included (`scene_cache.py:273`).
  - A fetch error is logged at ERROR and returns `[]` without caching (`scene_cache.py:284-293`).
- **Entity creation:**
  - The DIY select is created only when the device has `dynamic_scene/diyScene` and the fetch at setup returns at least one option (`select.py:175-186`).
  - The Refresh scenes button only refreshes existing selects (`select.py:313-356`). It cannot create a missing one, so only an entry reload can.
- **Earlier replies:** the 2026-11-25 replies on #296 (H60B3) and #281 (H70B6) say the Platform API returns an empty DIY list, which is an upstream limit. #298 (H7087) was asked for diagnostics.
- **#314 (H61D3):**
  - It has no data: empty template, no version, no diagnostics. The reporter says other strip models list their DIY scenes and that reload and refresh did not help, which points to an empty upstream list for this model.
  - A missing capability, the option being off, or a setup-time fetch error are not ruled out.
- **Diagnostics gap:** diagnostics record only `diy_scene_cache_count` (`diagnostics.py:373`), not the per-device DIY count or the fetch error, so they cannot tell these causes apart today.
- **Alternate source:** the app endpoint `GET /appsku/v1/diys/groups-diys` is documented (`docs/govee-protocol-reference.md:1129`) but not implemented, and app-side DIY activation has no captured path.

### Q5 Dependabot PRs

| PR | Change | Release notes | Our usage | Verdict |
|---|---|---|---|---|
| #311 | create-github-app-token 3.3.0 → 3.4.0 | Dependency bumps only (setup-node 7, stale 11), published 2026-11-18 (GitHub API `published_at`) | release job token | merge |
| #312 | hassfest SHA → `9e9565e` | "Update dependency github/gh-actions-lock to v0.5.1 (#161)", committed 2026-11-23 (GitHub API) | CI validation | merge |
| #313 | claude-code-action 1.0.262 → 1.0.286 (replaces #302) | Patch releases. The 1.0.286 notes contain only a compare link; a search of the action's issues found no reported regression in 1.0.262–1.0.286 (web-researcher, 2026-11-25), an absence claim | release `draft` job only | merge, then dry run |
| #292 | upload-artifact 4.6.2 → 7.0.2 | v5 adds Node 24 support; v6 runs on Node 24 and requires runner ≥ 2.327.1 (v6.0.0 notes). v7 is ESM plus an opt-in `archive: false`. v7.0.2 adds 429 retry | `daily-release.yml:145,257`: `name`, `path`, `if-no-files-found`, `retention-days`, all unchanged | merge with #291 |
| #291 | download-artifact 4.3.0 → 8.0.2 | v5 changes the path only for downloads **by ID**. v7 moves to the Node 24 runtime. v8 makes a digest mismatch an error by default (`digest-mismatch` input) | `daily-release.yml:167,282,321`: by `name` + `path` | merge with #292 |

All five PRs: `mergeable: MERGEABLE`, `mergeStateStatus: CLEAN`, and green on style, HACS, hassfest, mypy, test 3.12 and test 3.13.

PR CI does not run `daily-release.yml`. Only a `workflow_dispatch` dry run exercises the artifact hand-off (draft → preview) and the Claude drafting step. That run is plan task T-012, whose list named #302, now superseded by #313.

### Q6 #308, #304, #295

- **#308:**
  - A second user is still on firmware 1.00.48 and is not offered 1.00.49. No source documents Govee's rollout or a manual update path (web search, 2026-11-25).
  - v2026.11.1 already routes H60A6 power-off over the cloud (`LAN_POWER_OFF_EXCLUDED_SKUS`), which is the fix for users still on 1.00.48.
  - Reply: update the integration; the firmware arrives on Govee's schedule.
- **#304:** H607C "Music DreamView" sync-centre frames `33 60 02 GG II 00 VV SS …` (12 Lighting Show and 10 Carnival effects, 36 combinations) and 8-colour frames, captured from the app and replayed with `govee.send_raw_ptreal`. This is a feature request with complete protocol data. It is not implemented, and the protocol reference has no section on it.
- **#295:**
  - The user posted a HAOS `marketplace.repositories` edit procedure. No code action on our side.
  - home-assistant/core#190454 is still open, with no assignee or linked PR (fetched 2026-11-25). Our pinned reply promised a fix proposal there, and that is still outstanding.

## Dissent / contradictory evidence

- **#310 layout:** the 4-byte mask rests on one reporter's hardware tests. No second source confirms or contradicts it. Their frames are consistent with the 3-byte layout already shipped for H1232/H1A42, and their full-strip test exercised all 30 bulbs.
- **download-artifact v8:** the digest check now fails a run where v4 only warned. This is the most plausible new failure mode for the bump (web-researcher). We upload and download inside one workflow run, so a mismatch would mean real corruption. A failure there is the correct outcome.
- **#241 OFF state:** the video-off capture holds no frame, so the OFF detection is inferred. The colour-restore OFF path and the ON frame are untested on the H6099. Shipping control rather than read-only state risks a switch that does nothing; the frame-based state limits that, because a no-op ON would visibly stay off.
- **#296/#281 replies:** the codebase analyst notes those replies inferred "no options" from capability parameters. The `/device` list's `diyScene` capability may carry no options even when `/device/diy-scenes` returns some. The diagnostics change below makes that verifiable instead of inferred.

## Recommendation

### Decision

1. **Merge the Dependabot PRs:** #311, #312, #313, then #292 and #291 together. Then run `daily-release.yml` by hand in dry-run mode (T-012) before tonight's 02:00Z run.
2. **#310: fix now.**
   - Add `H7038` to `PTREAL_SEGMENT_SKUS`.
   - Emit a fourth mask byte in `build_segment_color_ptreal` and `build_segment_color_temp_ptreal`, and reject segment indices ≥ 32.
   - Update the three mask/length tests and add 30-bulb golden frames.
   - Update the reference's mask wording and the §9.8 row.
   - Leave the brightness builder, H705E and H7026 out. Queue a #208 reply asking the reporter to try the 4-byte frame with `govee.send_raw_ptreal`.
3. **#241: frame-based H6099 video mode.**
   - Add a capability fallback so the H6099 gets the DreamView switch without `dreamViewToggle`.
   - Add the H6099 to `DREAMVIEW_STATE_FROM_FRAMES_SKUS` and `PTREAL_DREAMVIEW_SKUS`.
   - Test with the captured `aa 05 00` frame.
   - Queue a reply asking reporters to confirm ON, OFF and state sync after the release.
4. **DIY scenes: diagnostics only, plus a #314 reply.**
   - Record the per-device DIY fetch result (count, or error class) in diagnostics, and log one debug line when a DIY-capable device returns an empty list.
   - Queue the #314 reply asking for version, diagnostics, whether the scenes are DIY or Snapshot in the app, and any "DIY scenes" log lines.
   - No behaviour change.
5. **Replies and docs only:**
   - #308: queue a reply.
   - #304: add the `33 60 02` Music DreamView frames to the protocol reference, queue a thank-you reply, and keep the issue open as a feature request.
   - #295: no reply. The core#190454 proposal stays a separate maintainer task.

### Rationale

- **Dependabot PRs:**
  - Each PR is CI-green. None of the documented breaking changes touches an input we use: download by name, not ID; `name`/`path`/`if-no-files-found`/`retention-days` unchanged; GitHub-hosted runners already exceed 2.327.1.
  - The artifact pair crosses the v4→v7/v8 boundary, so merging them together keeps upload and download on the same artifact library generation.
  - The dry run is the only test of the release path, and it must happen before the scheduled run uses the new versions.
- **#310:** the fix changes no byte for existing SKUs (padding argument above). It is SKU-scoped and reporter-verified, which fits the rule to merge SKU-scoped fixes into the daily release (reporters on HACS can't test branches). Rejecting indices ≥ 32 replaces a silent drop with an error.
- **#241:** the state signal is identical in two independent captures and reuses shipped H66A0 code. Control is lower confidence, but frame-reported state makes a failed ON visible rather than silent.
- **DIY scenes:** four reports share a symptom the code cannot currently explain from diagnostics. Recording the fetch result is cheap and decides between an upstream gap and a fetch error on the next upload, without changing behaviour.
- **#304:** documenting the frames keeps the reporter's work and costs nothing. A Music DreamView select is a larger feature with one requester.

### Implementation sketch

| Item | Files | Tests |
|---|---|---|
| Dependabot | merge #311, #312, #313, #292, #291 (`gh pr merge --squash`); `gh workflow run daily-release.yml -f dry_run=true` | dry-run jobs gate/draft/preview green |
| #310 | `custom_components/govee/const.py` (`PTREAL_SEGMENT_SKUS`, comment), `api/ble_packet.py` (colour + colour-temp builders, docstrings), `coordinator.py:~5887` (index ≥ 32 → `False`), `docs/govee-protocol-reference.md` (0x15 row, §9.8) | `tests/test_ble_packet.py` (update 3, add golden `… FF FF FF 3F` and bit-29-only), coordinator H7038 indices (0, 29) → mask `01 00 00 20`, H1232/H1A42 frames unchanged |
| #241 | `const.py` (new `FRAME_DREAMVIEW_SKUS = {"H6099"}`; add to the two DreamView sets), `models/device.py:912-925` (`supports_dreamview` fallback), docs catalog/reference rows | `tests/test_dreamview_frames.py` (H6099 `aa05 00…a5` → True, `aa05 15…` → False, bad XOR ignored), switch created without cap, ON sends `33 05 00 01 00 64`, OFF restores colour |
| DIY diagnostics | `scene_cache.py` (keep last fetch result per device), `diagnostics.py` (per-device `diy_scenes: {count, error}`), `select.py:178` debug log | `tests/test_diagnostics*.py`, `tests/test_scene_cache*.py` |
| Replies | `docs/release-replies.md`: #310, #208, #241, #314, #308, #304 | n/a |

## Risks

- **#310:** the 4-byte mask is verified only by one reporter. Installs without account login still hit the REST no-op for indices 15–29.
- **#241:** the ON frame may not enter camera video mode on the H6099, and OFF detection is inferred.
- **Dependabot:** a v8 digest mismatch now fails the run. The dry run must pass before 02:00Z or the night's release is skipped.
- **Release scope:** #310 and #241 change `custom_components/`, so tonight's run will release v2026.11.2. Both are SKU-scoped.

## Sources

All primary sources are on github.com because the subject is this repository's GitHub issues and the GitHub Actions it pins. The release pages are the vendors' own notes; no second domain publishes them.


- https://github.com/lasswellt/govee-homeassistant/issues/310 (reporter frames and patch)
- https://github.com/lasswellt/govee-homeassistant/issues/241 (three diagnostics uploads)
- https://github.com/lasswellt/govee-homeassistant/issues/314
- https://github.com/lasswellt/govee-homeassistant/issues/308
- https://github.com/lasswellt/govee-homeassistant/issues/304
- https://github.com/lasswellt/govee-homeassistant/issues/295
- https://github.com/home-assistant/core/issues/190454
- https://github.com/actions/create-github-app-token/releases/tag/v3.4.0
- https://github.com/anthropics/claude-code-action/releases/tag/v1.0.286
- https://github.com/actions/upload-artifact/releases/tag/v5.0.0
- https://github.com/actions/upload-artifact/releases/tag/v6.0.0
- https://github.com/actions/upload-artifact/releases/tag/v7.0.0
- https://github.com/actions/upload-artifact/releases/tag/v7.0.2
- https://github.com/actions/download-artifact/releases/tag/v5.0.0
- https://github.com/actions/download-artifact/releases/tag/v7.0.0
- https://github.com/actions/download-artifact/releases/tag/v8.0.0
- https://github.com/actions/download-artifact/releases/tag/v8.0.2
- https://github.com/home-assistant/actions/commit/9e9565e68f5a70d874202871f4c4064d23814235
