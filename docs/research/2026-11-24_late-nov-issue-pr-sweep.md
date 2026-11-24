---
title: Late-November sweep — stalled release, new issues #305–#309, PR merge readiness
date: 2026-11-24
type: Feature Investigation
topic_slug: late-nov-issue-pr-sweep
status: complete
agents: library-docs (partial), web-researcher (partial), codebase-analyst (complete); gaps closed on the main thread
follows: docs/research/2026-11-16_post-2026-11-0-sweep.md (plan docs/plans/post-11-0-sweep; T-013 still open, T-005/T-011 blocked)
citations:
  - url: "https://pypi.org/project/tox-gh-actions/"
    title: "tox-gh-actions on PyPI"
    pub_date: "2026-11"
    fetched_ts: "2026-11-24T03:50:00Z"
    claimed_span: "Latest 3.5.1 uploaded 2026-11-18; previous 3.5.0 uploaded 2025-10-22; requires tox<5,>=4"
    status: LIVE
  - url: "https://pypi.org/project/tox/"
    title: "tox on PyPI"
    pub_date: "2026-11"
    fetched_ts: "2026-11-24T03:50:00Z"
    claimed_span: "4.67.14 and 4.67.15 uploaded 2026-11-17; 4.67.16 uploaded 2026-11-23"
    status: LIVE
  - url: "https://github.com/ymyzk/tox-gh-actions/issues/243"
    title: "with tox 4.67.15: ImportError: cannot import name '_PLACE_HOLDER' from 'tox.config.of_type'"
    pub_date: "2026-11"
    fetched_ts: "2026-11-24T03:50:00Z"
    claimed_span: "Issue #243 opened 2026-11-18, closed; #244 'Fix compatibility with newer tox releases' and #246 'Remove tox pin from CI now that tox-gh-actions 3.5.1 is released' closed the same day"
    status: LIVE
  - url: "https://github.com/ymyzk/tox-gh-actions/blob/master/src/tox_gh_actions/plugin.py"
    title: "tox-gh-actions plugin.py (master)"
    pub_date: "2026-11"
    fetched_ts: "2026-11-24T03:50:00Z"
    claimed_span: "# tox < 4.58.0 (_PLACE_HOLDER was removed in tox 4.67.14)"
    status: LIVE
  - url: "https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/helpers/device_registry.py"
    title: "Home Assistant core 2026.11.2 device_registry.py"
    pub_date: "2026-11"
    fetched_ts: "2026-11-24T03:55:00Z"
    claimed_span: "\"via_device\": (\"2027.8.0\", \"via_device_id\") ... Resolve the deprecated via_device to a device id. The identifier is not unique across config entries ... This ambiguity is why via_device is deprecated."
    status: LIVE
  - url: "https://contributors.home-assistant.io/blog/2026/08/24/device-registry-follow-up-changes"
    title: "Device registry follow-up changes (HA developer blog)"
    pub_date: "2026-08"
    fetched_ts: "2026-11-24T03:40:00Z"
    claimed_span: "paraphrase: DeviceInfo via_device and async_get_or_create(via_device=) deprecated for via_device_id; custom integrations warn until 2027.8; helper async_get_device_id_by_identifier raises ValueError when the parent is not registered"
    status: LIVE
  - url: "https://github.com/wez/govee2mqtt/issues/747"
    title: "H60A6 LAN power-off not acknowledged; light re-powers"
    pub_date: "2026"
    fetched_ts: "2026-11-24T03:40:00Z"
    claimed_span: "paraphrase: about nine commenters on H60A6 firmware 1.00.48; units on 1.00.41 work; disabling LAN control or removing the LAN IP works around it"
    status: LIVE
  - url: "https://github.com/Galorhallen/govee-local-api/issues/410"
    title: "H60A6 LAN turn-off reboots lamp"
    pub_date: "2026"
    fetched_ts: "2026-11-24T03:40:00Z"
    claimed_span: "paraphrase: two H60A6 on FW 1.00.48 / HW 1.04.03 reboot for ~30 s on LAN turn value 0; cloud off works; H60A1, H61E6, H6022, H70C4 on the same network unaffected; brightness 0 does not work as a substitute"
    status: LIVE
  - url: "https://github.com/actions/upload-artifact/releases"
    title: "actions/upload-artifact releases"
    pub_date: "2026-02"
    fetched_ts: "2026-11-24T03:40:00Z"
    claimed_span: "paraphrase: v6 Node 24 runtime, minimum runner 2.327.1; v7 adds archive: false direct uploads and moves to ESM"
    status: LIVE
  - url: "https://github.com/actions/download-artifact/releases"
    title: "actions/download-artifact releases"
    pub_date: "2026-02"
    fetched_ts: "2026-11-24T03:40:00Z"
    claimed_span: "paraphrase: v5 changes the extract path only for download by artifact ID; v7 Node 24; v8 fails on digest mismatch by default and skips unzipping non-zip content"
    status: LIVE
---

# Late-November sweep (2026-11-24)

## Summary

**Nothing has shipped since v2026.11.0 (2026-11-15).** The daily release pushed the bump commit `64b9038` ("Bump version to 2026.11.1") on 2026-11-18 02:21Z, the `Tests` workflow failed on it, and the release was never created. Every nightly run since (11-19 through 11-24, six in a row) stops at the gate with `Required checks failed on 64b9038…: test (3.12); test (3.13)`. The test failure is not in our code: `tox-gh-actions` 3.5.0 imported a private tox name that tox 4.67.14 (2026-11-17) removed. `tox-gh-actions` 3.5.1 (2026-11-18) fixes it. Unreleased on `main`: #254 (brightness/scene refresh), #300 and the H3A53 work (#301), and the README change (#209); seven queued replies in `docs/release-replies.md` are waiting on that release.

Since the 11-16 sweep: 5 new issues (#305–#309), 1 contributor PR (#303), 1 new dependabot PR (#302, supersedes #294), follow-ups on #241, #261, #295, #299.

Recommendation in one line: unblock the release first (re-run, then remove the plugin from CI), then fix #305 (now a real breakage on HA 2026.11.2), #308, #307, #261 and merge #303 for tonight's release; ship #309 (scene `paramId`, all devices) on its own release day; reply-only for #306, #241, #299.

## Research Questions

### Q1: Why has the daily release failed since 11-18, and what fixes it?
`pip install tox tox-gh-actions` in `.github/workflows/tox.yaml` is unpinned. tox 4.67.14 removed `tox.config.of_type._PLACE_HOLDER` (stated in the plugin's own source comment); tox-gh-actions 3.5.0 imported it at module load, so `tox` died before running anything: `ImportError: cannot import name '_PLACE_HOLDER' from 'tox.config.of_type'` (job log, run 44222645498, `test (3.13)`; `test (3.12)` was cancelled by fail-fast). The plugin's issue #243 reports exactly this and was closed by 3.5.1 the same day. A re-run of the failed jobs during this research (2026-11-24 ~04:00Z) installed 3.5.1 and passed on both lanes, so `64b9038` now has all six required checks green. The durable fix is to stop depending on the plugin.

### Q2: #305 — is `via_device` only a deprecation warning?
No. On HA 2026.11.2 a second user (jeffcaradona) reports all four H5058 leak sensors behind an H5043 unavailable with a `RuntimeError` during entity registration, fixed by deleting the `via_device=` line. HA 2026.11.2's `device_registry.py` maps `via_device` to removal in 2027.8.0 and resolves the tuple by searching the same config entry, then the same domain, then any entry. The source I read logs rather than raises for custom integrations on that path, so the exact line that raises on his install is **not identified**; his traceback is needed to say more. Either way the fix is the same: stop passing `via_device`.

### Q3: #309 — must `lightScene` carry `paramId`?
Evidence says yes. The scene list returns `{"name": …, "value": {"paramId": …, "id": …}}` (our own `docs/govee-protocol-reference.md:557-563` shows that shape), and `SceneCommand.get_value()` (`models/commands.py:177-197`) sends `{"id", "name"}`. The reporter verified by eye on an H70B1 and an H61A0 that the lights change only when `{"paramId", "id"}` is sent. It also explains #285 (Lightning-A/B share id 136). No external source confirmed or contradicted this; the web agent found none either way.

### Q4: #308 — is the H60A6 LAN power-off reboot real and how narrow is it?
Confirmed outside this project by two fetched upstream issues (govee2mqtt #747, govee-local-api #410): firmware 1.00.48 reboots on LAN `turn` value 0 and comes back on; 1.00.41 units are fine; cloud power-off works; sending brightness 0 instead does not work. Three users on our tracker.

### Q5: #307, #261, #306, #241, #299 — code or reply?
- #307 (H6054 reports 20–254): code, SKU-scoped. One reporter, one unit, nine measured points; no outside source documents the offset.
- #261 (H1A42): code, one-line override. Only indices 0–4 move LEDs; the integration creates 15.
- #306 (H60B2): reply. The reporter's diagnostics show 15 devices in the account list and 2 from the Developer API. It is not specific to the H60B2.
- #241 (H6099): reply now, feature later. The Platform API exposes no DreamView/video capability for it.
- #299 (groups on/off only): reply. The API reports only `powerSwitch` for the group.

### Q6: Are the open PRs mergeable?
- #303 (H2800): yes after formatting. Applies cleanly; three lines in the new test file exceed 119 columns, so `black --check` would fail. CI has not run on it (fork PR, runs are `action_required`).
- #302, #292, #291 (all `daily-release.yml`): safe on paper, but their green checks predate the tox break and do not exercise the release workflow. Merge after a release has gone out, then run a `dry_run`.

## Findings

### Release pipeline
- Bump commit on `origin/main` since 2026-11-18; local `main` is one commit behind (`git status -sb`: `behind 1`).
- Gate logic handles the leftover bump: version is computed from the latest tag (v2026.11.0 → 2026.11.1), the tag does not exist, and the bump step skips the commit when an earlier night already pushed it (CLAUDE.md, Release Process). Once CI is green on HEAD, the next 02:00Z run releases without manual steps.
- `tox.ini` already has `py312`/`py313` envs, so the matrix can call `tox -e py312` / `tox -e py313` directly and `[gh-actions]` plus the plugin become dead weight.
- Six consecutive failed nightly runs produced no alert that reached the maintainer. That is a monitoring gap worth a task.

### #305 via_device (codebase-analyst)
- Two production uses: `custom_components/govee/models/device.py:1606` (`leak_sensor_device_info`, used by sensor, binary_sensor, event) and `custom_components/govee/entity.py:69` (gateway-bridged thermometers, #86).
- Hubs are registered before children: `sensor.py:226-227`, `binary_sensor.py:135`, `event.py:40`, via `coordinator.py:3102` (`register_leak_hubs`) and `:3143` (`register_thermo_hubs`).
- `registry.py` already feature-detects two 2026.10 registry changes with `getattr`; it has no `via_device` helper yet.
- Tests asserting `via_device`: `tests/test_cov_binary_sensor_event.py:363,398,516`, `tests/test_cov_sensor.py:635,658,702`, `tests/test_thermometer.py:592-598`, `tests/test_cov_models.py:313`, `tests/test_cov_button_entity.py:179,183`.
- `hacs.json` declares minimum HA `2024.11.0`, where `DeviceInfo` has no `via_device_id` key, so the new key must be feature-detected.

### #309 scene paramId (codebase-analyst)
- `SceneCommand` build sites: `select.py:425`, `light.py:424`, `coordinator.py:6735` (`_rest_disable_music_mode`, rebuilt from the stored scene id alone).
- Option maps are keyed by name (`select.py:382-385`, `light.py:430-455`), but the reverse maps `_scene_id_to_option` (`select.py:325`) and `_scene_id_to_effect` (`light.py:452`) are keyed by `str(scene_id)`, so -A/-B variants sharing an id collapse to the last one for display.
- `active_scene` is the id string only (`models/state.py:207-208, 981-982`; preserved on poll at `coordinator.py:5191`).
- LAN and MQTT mappings return `None` for scenes (`api/lan_control.py:115`, `api/mqtt_control.py:103`): REST only, no change there. DIY scenes send a plain int; snapshots are sent verbatim.

### #308 H60A6 (codebase-analyst + web-researcher)
- Transport order `coordinator.py:5788-5800`: BLE, LAN, MQTT, REST. `command_to_lan` (`api/lan_control.py:109-165`) already returns `None` for `PowerCommand` on `POWER_QUIRK_SKUS`, which makes the command fall through to MQTT/REST. Same mechanism fits here.
- No LAN exclusion table exists in `const.py`.

### #307 H6054 (codebase-analyst)
- Reported brightness enters at `models/state.py:435-437` (REST) and `:629-632` (MQTT). Neither has the SKU; the coordinator call sites do.
- Pattern to mirror: `FAHRENHEIT_REPORTING_SKUS` (`const.py:125`, helper at `:289`).

### #261 H1A42 (codebase-analyst + reporter)
- Count resolution: `BFF_SEGMENT_COUNT_SKUS` > `SKU_SEGMENT_OVERRIDES` (`const.py:202`) > `fields[].size.max` > `elementRange.max + 1`. H1A42 has no override; it is only in `PTREAL_SEGMENT_SKUS` (`const.py:241`).
- Reporter's map on v2026.11.0: index 0 → LEDs 1–18, 1 → 19–30, 2 → 31–42, 3 → 43–54, 4 → 55–72, 5–14 → nothing. The app shows 12 groups of 6, so five uneven areas is coarser than the app; finer control needs a capture of the app's own frames.

### #306 H60B2 (diagnostics read on the main thread)
- `raw_api_devices`: 2 (H619A, H6871). `bff_device_values`: 15 (adds H607C, H7094, H5104, 2×H5129, 2×H7075, H3351, H8057, H6076, H60B2, 2×H6022). Several of the missing SKUs are ordinary Platform API lights elsewhere, so this points at the API key belonging to a different Govee account than the email login, or at shared devices; not confirmed.
- `lan_discovery.error`: `[Errno 98] Address in use` — the reporter runs govee2mqtt on the same host, so LAN is unavailable to us too.
- No code path builds a light from the account list alone (`coordinator.py:2560-2580` does it for leak sensors only); doing so is a new discovery path, not a bounded fix.

### #241 H6099 (diagnostics read on the main thread)
- Capabilities: power, gradientToggle, brightness, segments, colour, colour temperature, empty lightScene/diyScene/snapshot, musicMode. No DreamView toggle or movie mode. `dreamview_enabled: None`.
- Taken with Video Mode on, the MQTT report has `"mode": 0` and the op frame `aa0500000832000232…`. That looks like the device's own video-mode report, which would make state readable and a raw-frame switch possible. This reading of the frame is my inference from one sample, not a decoded spec.

### PR #303 (codebase-analyst, line lengths re-checked on the main thread)
- `git apply --check` passes against HEAD.
- Regex widened to `[a-z][A-Za-z]*LightToggle` with `fullmatch`; unmapped instances are skipped by `NAMED_LIGHT_TOGGLE_SPECS.get(instance)` in `switch.py`.
- `MusicModeCommand.rgb_required` defaults to `False`; it is set only when the device's `musicMode` capability marks `rgb` as required, so other payloads are unchanged unless their capability also says required.
- Defects: `tests/test_h2800_monitor_light_bar.py` has three lines of 120–122 columns; `switch.py` import order is not alphabetical (cosmetic). Translations (strings, en, ca, es) and icons are complete.
- Author says they could not run the full suite; nobody has yet.

### Dependabot PRs
- `daily-release.yml` uploads and downloads artifacts by `name` (`release-inputs`, `release-drafts`) at lines 145, 167, 257, 282, 321. The download-artifact v5 path change applies only to download by ID, and v7 `archive: false` is opt-in, so neither affects this workflow. v8's digest check is on by default and is a benefit here. Node 24 needs runner ≥ 2.327.1; GitHub-hosted `ubuntu-latest` qualifies.
- upload v7 with download v8 across jobs: not stated in the release notes; verify with a `dry_run`.
- #302 (claude-code-action 1.0.262 → 1.0.276): changelog not researched beyond the PR body, which lists git-wrapper and CI-read changes. Verify with the same `dry_run`.

## Compatibility Analysis

### Stack Compatibility
| Change | HA min (2024.11) | HA 2026.11.2 | Notes |
|---|---|---|---|
| Drop `via_device`, set `via_device_id` when supported | falls back to no link or the old key | no warning, no error | feature-detect, as `registry.py` already does |
| `SceneCommand.param_id` | n/a | n/a | REST payload only |
| `tox -e py3xx` without plugin | n/a | n/a | removes a third-party plugin from the release gate |

### Integration Complexity
| Item | Prod files | Tests | Size |
|---|---|---|---|
| CI: drop tox-gh-actions | `tox.yaml`, `tox.ini` | none | S |
| #305 | `models/device.py`, `entity.py`, `registry.py` | 5 files | S–M |
| #308 | `const.py`, `api/lan_control.py` | 1 | S |
| #307 | `const.py`, `brightness.py`, `coordinator.py` | 1–2 | S–M |
| #261 | `const.py` | `tests/test_issue_261.py` | S |
| #303 | merge + `black` | included | S |
| #309 | `models/commands.py`, `select.py`, `light.py`, `models/state.py`, `coordinator.py` | scene tests in select, light, coordinator | M |

## Dissent / Contradictory Evidence

- **#305 RuntimeError.** The HA blog post says core integrations raise and custom integrations only warn until 2027.8, and the 2026.11.2 source I read logs on the unresolved-parent path. That contradicts a `RuntimeError` on a custom integration. One user reports it; the traceback is missing. The fix does not depend on resolving this, but the reply should ask for the log.
- **#307 offset of 20.** No outside source supports a 20–254 scale; community snippets (unfetched, leads only) mention 0–254 on some older models. The evidence is one unit. A SKU-scoped table keeps the blast radius to the H6054, and a clamp protects against a unit that reports 1–100.
- **#309 paramId.** No external confirmation; one Govee forum lead (unfetched) shows the API rejecting a missing `id` with a 400, which shows the endpoint validates `id` and says nothing about `paramId`. The evidence is the reporter's two devices plus our own captured scene list. Because it changes every scene command on every device, it gets its own release day (standing rule from PR #254).
- **#308 scope.** Upstream reports tie the fault to firmware 1.00.48; 1.00.41 units work over LAN. A SKU-wide exclusion costs 1.00.41 owners one cloud call per power-off and nothing else. We do not have firmware at command time on every path, so SKU-wide is the simpler choice.
- **#306.** The "different account" reading is an inference from counts. The web agent found only general, unverified causes for devices missing from the API list.
- Agent coverage: library-docs and web-researcher both returned partial. Search-snippet URLs they did not fetch are excluded from the citations above.

## Recommendation

### Decision
Run one plan, `late-nov-sweep`, in this order:

1. **Harden the release gate (first, today).** The re-run of `Tests` on `64b9038` is green, so the 02:00Z run on 2026-11-25 will release v2026.11.1 from whatever is on `main`. Change `.github/workflows/tox.yaml` to `pip install tox` and `tox -e py${{ matrix.python-version }}` with the dot removed (`py312`, `py313`), and delete the `[gh-actions]` block from `tox.ini`. Fast-forward local `main` first.
2. **#305**: stop passing `via_device`. Add a helper in `registry.py` that resolves the hub's registry id and returns it only when HA's `DeviceInfo` accepts `via_device_id`; use it at `models/device.py:1606` and `entity.py:69`. Update the five test files and the stale comments.
3. **#308**: `LAN_POWER_OFF_EXCLUDED_SKUS = frozenset({"H60A6"})` in `const.py`; in `command_to_lan`, return `None` for `PowerCommand(power_on=False)` on those SKUs.
4. **#307**: `BRIGHTNESS_REPORT_SCALE_SKUS = {"H6054": (20, 254)}` and a pure helper in `brightness.py`, applied where the coordinator ingests REST and MQTT brightness; clamp to the capability range.
5. **#261**: `"H1A42": 5` in `SKU_SEGMENT_OVERRIDES`; update `tests/test_issue_261.py`; release note that segment entities 6–15 go away.
6. **PR #303**: gate locally in a worktree (pytest, flake8, mypy, black), fix the three long lines on the contributor's branch (`maintainerCanModify` is true), merge.
7. **Next release day, alone: #309.** Carry `param_id` from the scene list through `SceneCommand`, key the reverse maps and `active_scene` by id plus paramId, and cover `_rest_disable_music_mode`. Maintainer tries it on real hardware before merge.
8. **After a release has gone out: #302, #292, #291**, then a `dry_run` of `daily-release.yml` (closes T-013 of the previous plan).
9. **Replies** (queue in `docs/release-replies.md`): #306, #241, #299, #261, #305, #308, #307, and update the #285 entry to say the paramId fix is coming rather than asking for diagnostics.
10. **Monitoring**: make a failed `Daily release` run visible (open or update an issue from the workflow on failure).

### Rationale
- Every other fix is stuck behind item 1; nine days of merged work and seven queued replies are waiting.
- #305 is the only report where devices stop working on a current HA release, so it leads the code work.
- Items 3–5 are SKU-scoped and cannot affect other models, which is the bar for merging without the reporter testing a branch (HACS users cannot test branches).
- #309 is the highest-value fix (scenes silently doing nothing) and the broadest change, hence the separate day.
- Dependabot bumps to the release workflow should not land while that workflow is the thing being repaired.

## Implementation Sketch

### Step 1: CI
```yaml
# .github/workflows/tox.yaml
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install tox
      - name: Run tox
        run: tox -e "py$(echo '${{ matrix.python-version }}' | tr -d .)"
```
Remove `[gh-actions]` from `tox.ini`. Verify: `gh run list --commit "$(git rev-parse HEAD)"` shows `Tests` green on both lanes.

### Step 2: #305
```python
# registry.py
def via_device_kwargs(hass, hub_identifier: tuple[str, str]) -> dict[str, Any]:
    """Link to the hub by registry id where HA supports it; otherwise no link."""
    if "via_device_id" not in getattr(DeviceInfo, "__annotations__", {}):
        return {"via_device": hub_identifier}      # older HA: unchanged behaviour
    hub = get_registry_device(hass, hub_identifier)
    return {"via_device_id": hub.id} if hub else {}
```
The annotation check must be confirmed against HA 2026.11's `DeviceInfo` class before use; `leak_sensor_device_info` currently has no `hass`, so its signature or call sites change.

### Step 3: #308, #307, #261
One constant each in `const.py`, mirrored on `POWER_QUIRK_SKUS`, `FAHRENHEIT_REPORTING_SKUS`, `SKU_SEGMENT_OVERRIDES`. H6054 mapping: `round(1 + (v - 20) * 99 / 234)` clamped to the capability range; values already inside 1–19 pass through untouched only if we decide a unit can report both scales (open question).

### Step 4: #309
`SceneCommand(scene_id, scene_name, param_id=None)`; `get_value()` returns `{"paramId", "id"}` when `param_id` is set, else today's payload. Scene maps become name → (id, param_id, name). Store the active scene as `"id:paramId"` when a paramId exists, and accept a bare id when restoring state saved by older versions.

### Configuration Changes
None for users. `tox.ini` loses `[gh-actions]`.

## Risks

- **A release now fires automatically.** CI is green on `64b9038`, so the 02:00Z run on 2026-11-25 ships v2026.11.1 with whatever is on `main` then. Anything not meant for that release (#309) must not be merged before it.
- **#305 fix across HA versions.** A wrong feature check either drops the hub link on new HA or passes an unknown key on old HA. Both lanes in CI resolve an old HA (2025.1.x per the local-env notes), so the new-HA branch needs a test that fakes the newer `DeviceInfo`.
- **#261 removes entities.** Ten segment entities disappear for H1A42 owners; automations that reference them break. They never did anything, but the release note has to say so.
- **#307 on one data point.** If another H6054 firmware reports 1–100, the mapping would turn 20–100 into 1–35. Mitigation: ask the reporter and any second owner for diagnostics; consider applying the mapping only when a reported value exceeds 100 has been seen for that device.
- **#303 music-mode change.** Any other SKU whose capability marks `rgb` required will start receiving `rgb: 0xFFFFFF` with auto-colour on. No such SKU was found in this pass; the search was not exhaustive.
- **#309 state migration.** Stored `active_scene` values are bare ids; restoring must keep working or effects show as unknown after the update.

### Open Questions
- What raised the `RuntimeError` in #305? Needs jeffcaradona's log.
- #306: is the API key from the same Govee account as the email login?
- #241: does the `aa0500…` frame toggle video mode when sent back as a `33 05 00 …` command? Needs a reporter test with `govee.send_raw_ptreal`.
- Maintainer decision: #309 tonight with the rest, or on its own day as recommended.

## References

Fetched this session: the ten URLs in the frontmatter. Repository evidence: `gh run view 45310719197 --log-failed` (gate), run 44222645498 (`Tests` on `64b9038`), check runs on `64b9038`, issue threads #241, #261, #295, #299, #305–#309, PR #303 diff, diagnostics attachments on #306, #241, #308, `docs/govee-protocol-reference.md:557-563`.

## Citation Health

research-critic verdict: **PASS** (probed 2026-11-24; 10 of 10 URLs LIVE, none dead or hallucinated).

| URL | Status |
|---|---|
| pypi.org/project/tox-gh-actions | LIVE |
| pypi.org/project/tox | LIVE |
| github.com/ymyzk/tox-gh-actions/issues/243 | LIVE |
| github.com/ymyzk/tox-gh-actions plugin.py | LIVE |
| github.com/home-assistant/core device_registry.py @2026.11.2 | LIVE |
| contributors.home-assistant.io device-registry-follow-up-changes | LIVE |
| github.com/wez/govee2mqtt/issues/747 | LIVE |
| github.com/Galorhallen/govee-local-api/issues/410 | LIVE |
| github.com/actions/upload-artifact/releases | LIVE |
| github.com/actions/download-artifact/releases | LIVE |

Grounding notes from the critic (advisory): the `tox<5,>=4` requirement and the #244/#246 titles come from the PyPI JSON API and the GitHub issues API, not the cited HTML pages; the critic's fetch of `device_registry.py` was truncated, so those quoted lines rest on the raw file read on the main thread; the commenter count and the 1.00.41 detail for govee2mqtt #747 come from the web agent's read and were not re-confirmed.
