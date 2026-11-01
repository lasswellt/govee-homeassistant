# PR #267 review: opt-in H6104 BLE control and status readback

- PR: lasswellt/govee-homeassistant#267 by krozgrov, head `2f412ef` (10 commits), +1791/-36, 24 files
- Base: merge-base == main HEAD `27f0581` (branched from current main)
- Hosted CI: none run yet (`statusCheckRollup` empty; fork PR waits for approval)
- Review was read-only. The tests ran in a throwaway worktree (since removed) using `.cc-sessions/venv312` (the py312 CI lane).

## 1. Merge conflicts

`git merge-tree --write-tree --name-only main refs/pr/267` gives a clean tree (`c2bf886`) with **no conflicting files**.
The PR body says it overlaps #252 (BLE enrollment/lifecycle). If #252 lands first, expect conflicts in `api/ble.py`, `ble_advertisement.py` and `coordinator.py`.

## 2. Local gate results on PR head (py3.12 venv)

| Gate | Result |
|---|---|
| pytest (`-n auto --timeout 30`) | 4324 passed, **2 errors**: `tests/test_api_ble.py::TestLegacyUartTransport::test_h6104_subscribes_and_writes_plaintext_to_ffe1[False/True]` fail with `Lingering timer ... GoveeBLEDevice._begin_idle_disconnect(3)`. The test never calls `device.stop()`, and pytest-homeassistant-custom-component fails the test on the leftover timer. The contributor's 3.13 run did not show this; CI's py312 lane will. |
| coverage | 99.69% total. **`h6104.py` 94.83%** (lines 51, 77, 79) breaks the every-module-above-96% rule. `api/ble.py` 96.25% and `ble_advertisement.py` 96.61% only just clear it. `config_flow.py` stays at 100%. |
| black | clean |
| flake8 | clean |
| mypy strict | **1 error**: `ble_advertisement.py:264: Cannot infer type of lambda [misc]` (the `lambda report, did=matched_id: ...` report callback) |

CI will fail as submitted: 2 test errors plus mypy.

## 3. Findings

### Blocking

**B1. CI red.** Covered in section 2: the lingering idle-disconnect timer in `test_api_ble.py:712` (add `await device.stop()`), the mypy lambda error at `ble_advertisement.py:264` (use a typed `functools.partial` or a nested def), and `h6104.py` coverage below 96%.

**B2. README is written for the fork, not upstream** (`README.md` +143). It says "this fork", "download the test component package supplied with the change", and "The fork's default branch and upstream HACS release do not include this draft change". Merged as-is it would mislead every HACS user. It needs a short rewrite: what the option does and its limits; drop install/rollback and the tester narrative.

**B3. The DreamView guard raises an untranslated `HomeAssistantError` for every H6104, opted in or not.**
- Where: `coordinator.py:5347-5361` (PR head) and `async_send_dreamview` `coordinator.py:6339-6340`.
- What changed: H6104 now always routes to `async_control_device`. If BLE is not eligible, it raises `HomeAssistantError(f"H6104 TV mode requires Bluetooth; ... ({reason})")`.
- Rule broken: no `translation_key` or `exceptions` entry, and the message embeds the raw `last_failure_reason`, which can be a BleakError string or a whole GATT service dump (see S6).
- Not gated on the option. A non-opted-in H6104 whose cloud capabilities include `dreamViewToggle` used to reach the REST path and then the AWS IoT ptReal passthrough (`_ble_manager.async_send_dreamview`). Now it always errors.
- Our documented H6104 (`docs/govee-protocol-reference.md:2170`) lists no `dreamViewToggle`, so the impact is probably small. It is still a behaviour change outside the opt-in.
- Fix: gate both branches on `CONF_ENABLE_H6104_BLE`, and raise with `translation_domain`/`translation_key`.

### Should-fix: correctness

**S1. A late disconnect callback after an idle release clears `_idle_disconnected`, so H6104 can't reconnect.**
- Code: `_disconnect_client` sets `self._client = None` and `_disconnect_if_idle` then sets `_idle_disconnected = True` (`ble.py:503-513`).
- If bleak or the ESPHome proxy delivers `disconnected_callback` after `disconnect()` returns, `_on_disconnected` (`ble.py:471`) passes its guard: `self._client is not None and ...` is False when `_client` is None. It then sets `_idle_disconnected = False`.
- After that, `_h6104_idle_reconnect_eligible` is False. Once adverts age out, `_ble_write_eligible` is False, so commands go to the cloud and TV mode raises B3's error until a fresh advert arrives.
- The PR itself says some proxies don't re-advertise promptly, which is exactly this case.
- Fix: return early in `_on_disconnected` when `self._client is None`, or compare against the released client.

**S2. The readback in the command path adds up to about 1.5 s to every BLE command.**
- `coordinator.py:5343` awaits `_async_refresh_h6104_ble_status(force=True)` after each successful BLE write. That is 3 queries with a 0.5 s timeout each (`ble.py:379+`), all under the device lock.
- On firmware that does not answer AA04/AA05, every command (slider drags included) waits 1 s or more and serialises behind the readback.
- Fix: run it as a tracked background task, or debounce it.

**S3. The poll does a duplicate readback whenever a reconnect takes 2 s or more.**
- `_async_poll_all` gathers readbacks for all H6104 (`coordinator.py:4204`). `_fetch_device_state` then calls it again (`:4764`).
- The throttle stamps `_h6104_last_query` *before* the read (`:5983`). Any read that takes ≥2 s, which is normal for a proxy reconnect, therefore triggers a second full query burst.
- The gather also blocks the whole poll by up to 5 s before any cloud read starts.
- Fix: drop the `_fetch_device_state` call, or stamp after the read completes.

**S4. A best-effort read failure disables BLE commands, and records an empty reason.**
- `coordinator.py:5985-5988`: when the `asyncio.timeout(5)` around the read fires, which is likely during a proxy reconnect with `_MAX_CONNECT_ATTEMPTS=4`, it records `record_failure(ble, str(TimeoutError()))`, i.e. `""`.
- That makes BLE unavailable, and `""` is not in `(None, "stale_advertisement")`, so the idle-reconnect path is also off until the next advert.
- Result: a failed read, not a failed command, pushes commands to the cloud.
- Fix: record `type(err).__name__`, and don't mark the transport failed on a read timeout.

**S5. Stale BLE fields override a newer cloud-path command for up to 120 s.**
- `_h6104_last_write` is only stamped after a BLE success (`coordinator.py:5340`).
- Scenario: a BLE read at t=0 says power on; BLE then becomes ineligible; at t=10 the user turns the light off over REST; at t=60 the poll merges the BLE field (`_merge_h6104_ble_fields`, `:5954`). `power_state=True` is restored and the UI flips back to on.
- Fix: stamp `_h6104_last_write` for every successful H6104 command, whatever the transport.

**S6. Every BLE write now raises a long `BleakError` containing the full GATT dump** when the write UUID is missing (`ble.py:604-620`, modern SKUs too).
- It only exposes structure and is functionally equivalent to the old failure. But the text flows into `last_failure_reason`, diagnostics and B3's user-facing message.
- Fix: keep the dump at debug level, and use a short reason.

**S7. Untracked fire-and-forget task.**
- `ble.py:501` `asyncio.create_task(self._disconnect_if_idle(...))` keeps no reference, so the task can be garbage-collected mid-run.
- It also isn't tied to entry unload. `stop()` bumps the generation, so a queued task exits, but HA warns on pending tasks at shutdown.
- Fix: keep a reference, or pass a scheduler (`hass.async_create_background_task`).

### Should-fix: scope (changes that reach beyond H6104)

**S8. A live GATT link now overrides advert staleness for all BLE SKUs** (H6199, H1270).
- `transport_health.py:114` (`live_gatt_ids`), `coordinator.py:966-971` and `_ble_write_eligible` `:6020-6023` apply to any device with `client.is_connected`.
- Modern devices hold their connection indefinitely, so after this change they never age to `stale_advertisement`. That changes the #198 fallback behaviour and `device_reachable` (#242) for non-H6104 users.
- The new test `TestLiveGattStaleness` uses a generic `dev1`, so the reach is intentional.
- Fix: gate it to `supports_readback`/H6104, or split it into its own PR and release day (memory: broad behaviour change gets its own release day).

**S9. `apply_optimistic_color` / `apply_optimistic_color_temp` now clear `dreamview_enabled` and `music_mode_enabled` for every device** (`models/state.py:908, 919`, plus a third site at `:951`).
- Plausibly correct, but it is a cross-SKU UI change that was only verified on an H6104.
- Fix: gate it to H6104, or call it out in release notes and ship it separately.

**S10. Non-opted-in H6104s change too.**
- `GoveeMusicModeSelectEntity.current_option` (`select.py:701-708`) now returns `None` instead of the first option.
- `GoveeMusicSensitivityNumber.native_value` (`number.py:201+`) behaves differently.
- `device_reachable` (`coordinator.py:856`) has an H6104 branch.
- All are low-risk, but they are not behind the option.

### Should-fix: project rules

**R1. Missing icons** for the new `govee_tv_sensitivity` (number) and `govee_tv_area` (select) translation keys in `icons.json`.

**R2. The TV area select uses raw English strings as options.**
- `h6104.py:16` has `TV_AREAS = ("Part of screen", "Whole screen")` with no `state` translations.
- These strings are persisted in restored state, so switching to keys (`part`/`whole`) plus a `state` block in strings.json/en.json has to happen *before* release.

**R3. Plain `ValueError` instead of `ServiceValidationError` with a translation key** at `h6104.py:51, 77`. Both are effectively dead code: HA already range-checks numbers and options. Delete them; that also restores coverage.

**R4. Leftover tester build stamp.**
- `const.py` has `H6104_BLE_BUILD = "2026.10.25.10"`, reported in diagnostics `_runtime_diag`.
- It will go stale on the first upstream release. Remove it.

**R5. Option read inconsistently.** `number.py`, `select.py` and `switch.py` read `entry.options.get(CONF_ENABLE_H6104_BLE, False)` instead of `DEFAULT_ENABLE_H6104_BLE`.
- The `data_description` in strings.json also reads like changelog text ("Power and TV mode have been field-tested...").
- Nit-level; trim both.

**R6. Info-level log on enrollment** at `ble_advertisement.py:266` ("experimental status readback enabled"). CLAUDE.md says info only for things the user must act on; make it debug.

### Nits

- **N1. Magic number.** H6104 brightness is clamped to `max(20, ...)` raw (`coordinator.py:6049`), undocumented. A 1% command reads back as about 8%, so the slider jumps.
- **N2. No change check.** `_apply_h6104_ble_report` calls `async_update_listeners` on every report, even an unchanged one (there is no "only when changed" check, unlike the other push paths).
- **N3. No `asyncio.Lock` dict cleanup.** `_h6104_tv_settings_locks` is per device and never cleaned up (bounded by device count, so fine).
- **N4. Import order.** `from .api.ble_status import H6104Report` is placed before `.api.auth` in `coordinator.py`.
- **N5. Missing docstring.** `supports_readback` has no docstring.

## 4. Opt-in and scoping verdict

- **Enrollment is truly opt-in.** `BleAdvertisementHandler._supports_commands` requires `options[enable_h6104_ble] is True`, and H6104 is not added to `BLE_COMMAND_SUPPORTED_MODELS`.
- **Entities are opt-in.** The TV sensitivity number, TV area select and DreamView switch for H6104 are created only with the option on.
- **Not scoped:**
  - S8 changes BLE staleness, write eligibility and reachability for all BLE devices.
  - S9 changes optimistic color handling for all devices.
  - B3 and S10 change H6104 behaviour without the option.
- **Contained to H6104 via `sku == "H6104"` checks:** all other coordinator additions (readback, merge, TV settings, music routing).

## 5. Security

- **Parsing is bounded.**
  - `decode_h6104_status` requires exactly 20 bytes and a zero XOR checksum, and indexes at most `frame[6]`.
  - `_on_uart_notification` drops frames from superseded clients, non-20-byte frames and unmatched AA replies.
  - Late replies after a timeout are dropped because `_pending_status` is None.
- **Buffers are bounded.**
  - `deque(maxlen=12)` ×2, a 3-key result dict and a fixed field map.
  - The callback list grows once per enrollment and `stop()` clears it.
- **Tasks are bounded** (one idle task per generation), but see S7.
- **No credentials are logged.**
  - Device id (MAC) at debug level, consistent with the rest of the code.
  - The device name at info level.
  - Diagnostics contain only status-frame hex (power, brightness, mode, RGB) and no identifiers.
- **Low-risk exposure:** the GATT service dump in error text (S6).
- **Untrusted input handled:** a malicious peer would have to be the connected GATT client. Decoded values are clamped (brightness), whitelisted (music effect 0-3, sensitivity ≤100) or turned into a mode enum, and the RGB bytes are used directly, which is safe.

## 6. BLE connection lifecycle

- **Idle release:** works in the happy path. Commands and reads cancel the timer and bump the generation, and `_disconnect_if_idle` rechecks the generation under the lock.
- **Gaps:**
  - S1: the late callback clears the idle flag.
  - S7: the untracked task.
  - Poll readback reconnects every poll interval (≥30 s). Each cycle holds a proxy slot for connect time + about 1.5 s of queries + 5 s idle. On 3-slot ESPHome proxies that means steady churn. Consider polling over BLE only when the link is already up, or at most every few minutes.
- **Unload:** `coordinator` shutdown calls `stop()` for every BLE device, which cancels the timer, disconnects under the lock and clears callbacks. That is adequate. Shutdown can wait up to 5 s behind an in-flight readback.

## 7. Test quality

- **Decoder tests are good.** They are table-driven and cover echo, checksum and length rejection plus late-reply filtering.
- **Coordinator tests build `GoveeCoordinator` around `MagicMock` hass and entry objects.** They patch `async_update_listeners`, `_write` and `async_read_status`, so every timing and lifecycle bug above (S1-S5) is invisible.
- **Fake GATT clients are `MagicMock`s,** so `services.get_characteristic` is always non-None unless overridden. The "UUID absent" and subscription paths are only partly exercised (`ble.py` lines 344-345, 353, 367, 386, 407, 476, 510, 514-515, 663 are uncovered).
- **No `MockConfigEntry` test** drives option on → reload → advert enrollment → entity creation → unload, which is the style CLAUDE.md prefers for setup and registry work.
- **No cross-SKU regression test** asserts that an H6199 or H1270 keeps the old staleness behaviour; the new test asserts the opposite for a generic device (S8).

## 8. Recommendation

**Request changes.** The PR is opt-in, well-researched and close to mergeable, but CI is red and the README is fork-specific. Then merge it on its own release day with our follow-up commits, because it touches all-device BLE behaviour.

**Ask the contributor for** (or we do as maintainer follow-ups):
1. B1: fix CI (stop the device in the test, type the callback, bring `h6104.py` above 96% by deleting the dead `ValueError` paths).
2. B2: rewrite the README section for upstream users.
3. B3 + R3: gate the DreamView guard on the option and give its exception a translation key (`strings.json` `exceptions` plus the en.json mirror).
4. S8 and S9: gate the live-GATT staleness change and the optimistic DreamView/music clearing to H6104, or split them into a separate PR.
5. R1 + R2: icons for both new keys; select options as translation keys with `state` strings.
6. R4 + R6: remove `H6104_BLE_BUILD`; make the enrollment log debug.

**Our follow-up commits after merge (if the contributor stops at items 1-6):**
- S1: `_on_disconnected` returns early when `_client is None`.
- S2: command-path readback becomes a tracked background task.
- S3: drop the second readback in `_fetch_device_state`, or stamp after completion.
- S4: don't mark BLE failed on a read timeout; record a non-empty reason.
- S5: stamp `_h6104_last_write` on any successful H6104 command.
- S6: shorten the GATT-missing error text.
- S7: keep a reference to the idle task.
- A `MockConfigEntry` lifecycle test for option, enrollment and unload.

**Estimated size:**
- Contributor-side items 1-6: about 120-180 changed lines.
- Our S1-S7 follow-ups plus one `MockConfigEntry` test: about 150-250 lines across `api/ble.py`, `coordinator.py` and `tests/` (half a day, including the py312 lane run).
- The README rewrite is about 40 lines, replacing the current 143.
