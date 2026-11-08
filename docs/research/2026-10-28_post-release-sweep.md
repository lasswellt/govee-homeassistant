---
date: 2026-10-28
type: Feature Investigation
topic: Sweep of threads after v2026.10.2, and what goes into tonight's release
plan: docs/plans/followups-2026-10-1
---
# Sweep after v2026.10.2 (2026-10-28)

## Summary

v2026.10.2 went out on 2026-10-27 at 03:12 UTC with all of Day A. The bot posted 11 replies and emptied the queue (`f40773a`). The 2026-10-28 run released nothing because nothing had changed. Nothing under `custom_components/` is unreleased.

Since the plan was written (2026-10-27T01:33Z) these have arrived:
- 3 new contributor PRs: #270, #271 and #273.
- 3 dependabot PRs: #275, #276 and #277.
- 1 new issue: #274.
- New data from reporters on #261, #239, #266, #258, #137 and #272.

Three of the code items are scoped and ready: #261, #270 and #273. Two need a release day of their own: PR #267, the planned Day B, and PR #271, which replaces T-015. Because #267 must ship alone, tonight can carry either #267 or the scoped batch, not both.

## Research questions

1. Which new threads need code, and how big and how risky is each change?
2. Which can share a release, and which need a day to themselves?
3. Do the new PRs conflict with `main` or with the open plan tasks (T-012 to T-015)?
4. Which threads only need a reply? Does each reply go out on its own through a commit subject, or does it need a queued brief?

## Findings

### Code: scoped, safe to ship together

- **#261 H1A42 segment colour.** The reporter confirmed on 2026-10-27 that `govee.send_raw_ptreal` colours one segment with mask `01 00 00`, and all 15 with `FF 7F 00`. The Cloud API `segmentedColorRgb` returns 200 but does nothing.
  - `build_segment_color_ptreal` (`api/ble_packet.py:196`) already produces those exact bytes. Masks above segment 0 are encoded the same way, so it should cover them too.
  - The fix is adding `"H1A42"` to `PTREAL_SEGMENT_SKUS` (`const.py:220`).
  - Without the account login, the command still goes to REST unchanged, as it does today: no regression, but it still doesn't work.
  - The H1A42 needs no `PTREAL_MAIN_PANEL_BIT` entry, and no segment-count override (it has 15).
  - Size: 1 line of code plus about 40 lines of test. Reaches the H1A42 only.
  - The plan's conditional `gradientToggle` follow-up isn't needed. (lights-analyst §1)
- **PR #270: H5105 and H5108 report in °F.**
  - The evidence holds: a fridge reading of 36.86 can only be °F.
  - The "pending a reading" comment near `const.py:455` is about the H5074, not the H5108.
  - An account °C hint still overrides the SKU list. The BFF and BLE paths use the same resolver.
  - `tests/test_thermometer.py`: 99 passed.
  - One comment hunk in `const.py` conflicts with `MQTT_COLOR_SKUS`; resolving it keeps both. Reaches only those two SKUs. (prs-analyst §1)
- **PR #273: the Bluetooth discovery card is closed when the integration is set up by hand.**
  - `_async_in_progress()` excludes the current flow, so the card's own flow is never aborted. The APIs it uses exist in HA 2025.1 and 2026.10.
  - With the full suite, `config_flow.py` stays at 100%.
  - No conflicts. Config flow only. (prs-analyst §2)

### Code: each needs its own day

- **PR #267 (H6104 BLE), plan T-012 to T-014.** Unchanged since 2026-10-25 (head `2f412ef`). `git merge-tree` reports one conflict, `custom_components/govee/switch.py`, against our T-006/T-007 heater switches. Everything else in the T-012 notes still applies.
- **PR #271 (magicmn): DIY scenes as light effects, scene selects read None while off, and colour ends a DIY scene.**
  - Clean merge onto `main`, +536/-32 across 6 files.
  - Locally: 4306 tests passed. mypy strict, black and flake8 are clean. Coverage is 100% on the files it touches and 99.85% overall.
  - The fork PR's CI is waiting for approval.
  - Its "None while off" matches option A, which the user chose. That reverses #18 for every light, so it needs its own day and a hands-on try first.
  - Colour clearing a DIY scene (`models/state.py:944`, `:955`) is a real bug fix.
  - Minor points, none blocking:
    - the literal `"DIY: "` prefix at `light.py:82`
    - DIY scenes that share an id collapse in the reverse map
    - automations keyed on the select's state now fire on power changes
  - It supersedes our own T-015 implementation; T-015's checks (`_scene_hidden_while_off`, `tests/test_issue_269.py`) need rewriting. (lights-analyst §3)

### CI only: no release, no customer impact

- **#277** (setup-python 5.6.0 → 7.1.0, 5 workflows):
  - The workflows pass only `python-version`.
  - `daily-release.yml` doesn't use setup-python.
  - It supersedes #262, which is closed.
- **#275** (claude-code-action 1.0.259 → 1.0.262) and **#276** (create-github-app-token 3.2.0 → 3.3.0) both edit `daily-release.yml`. #276 changes how the token action handles permission inputs, which the publish step relies on.
- All three are SHA-pinned, and their checks are green. (prs-analyst §3)

### Needs data or a reply only

- **#239 H7045 White Light.**
  - White Light is an ordinary `lightScene` entry (id 23449).
  - The diagnostics show the White Light write and then the Sunset write 8 s later. Both got HTTP 200 and `success`, and HA's state shows Sunset.
  - The failure is on the device or cloud side. The diagnostics don't contain the scene-list entry, so `paramId` can be neither confirmed nor ruled out.
  - Changing the payload would affect every scene on every light, so don't change it now.
  - Ask the reporter:
    - Does the app go straight from White Light to Sunset?
    - After White Light from HA, does a colour or brightness write also stay white?
    - Does turning the light off and on in HA unstick it?
    - Is the light physically white after the failed switch?
  - (lights-analyst §2)
- **#274 H7105 Tower Fan 2.**
  - Diagnostics show an empty `raw_api_devices`: the Developer API returned no devices for that key (`api/client.py:522`).
  - Only leak sensors, thermo-hygrometers and hubs are built from BFF data alone.
  - Same problem as #209. Reply: link #209, ask them to confirm the key is from the same account, and ask for a fresh diagnostics download from the UI. (prs-analyst §5)
- **#137 (closed PR): H5901 behind an H5044.**
  - The `ee34` frames reach the leak path at `api/mqtt.py:1085`. That is deliberate, so an unknown leak SKU is never silenced.
  - The slot doesn't belong to any leak sensor, so the coordinator logs at debug level and returns. Log noise only.
  - A scoped divert needs the H5901's subtype byte. Reply: ask for the diagnostics download's `recent_multisync` data; thank them for the relay findings.
  - Valve control is not feasible yet: the open/close payload hasn't been captured. (prs-analyst §4)
- **#266 H5194.** The reporter confirmed on v2026.10.2 that all four probes update. Close with thanks.
- **#258 H5179.** The reporter confirmed on 17 devices that "Last measurement" and "Reported online" solve the problem. Their battery note is a Govee-side limitation (battery syncs only over app BLE). Close with thanks.
- **#272.** The reporter closed it: deleting and reinstalling fixed the 401. No reply needed.
- **#236, #244, #213, #222, #240, #265.** Nothing new since the bot's 2026-10-27 replies. Waiting on the reporters.
- **issue-sweep-late-oct T-026 to T-028.** Still held on #249, which is still open. PRs #251, #254 and #256 had force-pushes on 2026-10-26; #251 now conflicts with `main`.

## Dissent / contradictory evidence

- The prs-analyst found that running only the `config_flow` test files leaves `config_flow.py` at 92.49%. Main shows the same gaps, and the full suite reaches 100%. The 100% rule is checked against the full suite, as CI runs it.
- The plan's Risks section said T-009 reaches every DreamView model. The user limited it to the H66A0 on 2026-10-26, so that risk no longer applies.

## Recommendation

### Decision

**User decision (2026-10-28), overriding the split below: everything ships tonight.** That means #267, PR #271 (instead of T-015), #261, PR #270, PR #273 and the replies. The plan follows that order in `docs/plans/followups-2026-10-1/plan.md` (Revised 2026-10-28). The split proposed before that decision is kept here for the record:

Tonight (2026-10-29 03:00 UTC) ship the scoped batch: #261, #270 and #273, plus the reply queue. Move PR #267 (T-012 to T-014) to tomorrow, alone. Replace T-015 with merging PR #271, alone, on the day after that, once the maintainer has tried it. Merge #277 and #275 any time, since they don't touch `custom_components/`. Merge #276 after a release, then do a dry run.

Release days:

| Night (03:00 UTC) | Contents | Rule |
|---|---|---|
| 10-29 | #261 H1A42, PR #270, PR #273, queued replies (#239, #274, #137, #266, #258) | scoped batch |
| 10-30 | PR #267 with our fixes (T-012 to T-014) | alone |
| 10-31 or later | PR #271 (#269), after the maintainer's try | alone |

### Rationale

- The batch fixes visible wrong values today. The H5105 reads 166 °F and the H5108 98 °F. The H1A42 segment action does nothing.
- Each fix reaches one SKU or the config flow. Together they are under 100 lines.
- #267 is an opt-in feature that one contributor is waiting on. A day's delay costs nobody a working device.
- Doing #267 tonight instead pushes all three fixes back a day, and #271 a day further.
- Either way, each own-day change still gets its day.

## Implementation sketch

- **T-016 (#261):**
  - `const.py:220` becomes `frozenset({"H1232", "H1252", "H1A42"})`, and the comment above it names #261.
  - New `tests/test_issue_261.py`: the frame bytes for segment 0 and for all 15 segments, and the REST fallback without IoT credentials.
  - Subject: `fix: H1A42 segment colours go over AWS IoT (#261)`.
- **T-017 (#270):**
  - Merge with `const.py` resolved, keeping `MQTT_COLOR_SKUS`.
  - Subject: `Merge PR #270: H5105 and H5108 report in °F`.
- **T-018 (#273):**
  - Merge as is.
  - Subject: `Merge PR #273: close the Bluetooth discovery card on a manual set-up`.
- **T-019:** queue replies in `docs/release-replies.md` for #239, #274, #137, #266 (close: yes) and #258 (close: yes). The commit subject must name no `#N`.
- **T-020:** gates: 95 % overall, 96 % per module, `config_flow.py` 100 %, flake8, mypy, black, 3.14 suite.
- **Shift dates:** T-012's first check already enforces the own-day rule; it passes as long as the 10-29 release goes out first.
- **Rewrite T-015** as "merge PR #271 after the maintainer's try". Its checks change to: `Merge PR #271` in the log, the `#269 tried on own lights:` line in progress.md, and the own-day check.
- **CI:**
  - #277 and #275 merge now with CI green.
  - #276 merges after the 10-29 release, followed by a dry run of `daily-release.yml`.
  - They are not in the plan, since they change nothing under `custom_components/`.

## Open questions

- #239: is `paramId` needed in scene payloads? Wait for the reporter's answers. An additive diagnostics change that includes the raw scene-list entry would answer it after a release (about 25 lines; not planned).
- #137: which subtype byte does the H5901 use in `ee34` frames?

## Sources

- GitHub threads: #137, #239, #258, #261, #266, #267, #270, #271, #272, #273, #274, #275, #276 and #277, read 2026-10-28 with `gh api` (comments since 2026-10-25).
- Diagnostics (untrusted, not committed): the session `sweep-1028/attach/{239,261}/diag.json`.
- Agent findings (session scratchpad): `research/lights-analyst.md` and `research/prs-analyst.md`.
- Earlier research: `docs/plans/followups-2026-10-1/research/research-lights.md` and `research-pr267.md`.
- Citation critic skipped: the document cites no external URLs, only GitHub thread numbers and file:line references, which were checked with `sed` and `grep` this session.
