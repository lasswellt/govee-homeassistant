## 2026-10-23T00:00:00Z plan created
Ruling: class=architectural tasks=27; 17 fork PR merges in conflict order, 9 issue fixes, py3.14 test fixes, reply queue, final gates; #254 isolated to its own release day at the user's request.

- Baseline: `.cc-sessions/venv312` (Python 3.12.14, HA 2025.1 lane) 3,598 passed. `.cc-sessions/venv314` (Python 3.14.9, HA 2026.10.2) 3,596 passed, 2 failed (test-only, T-025).
- PR heads fetched to `refs/pr/N` for #229–#234, #238, #247, #248, #250–#257, #259 at the SHAs in research-prs.md.

## 2026-10-23T13:30:00Z plan v2 after validation + security round
Ruling: class=architectural tasks=32; four independent reviews (plan validation: 2 blocker / 10 major / 14 minor, all applied; CI security: 1 critical / 3 high on main's release workflow; integration PRs: 0 high, fixes folded into T-005/T-007/T-028; kettle + ported code: #234 F-01/F-02 and #245 F-03 folded into T-012/T-022).
- User decisions: harden the release job first (T-001..T-003); hold #251/#252/#256 until the #249 report is read; resolve #254 ourselves, last, on its own release day; add the LAN scan trust fix (T-030).
- New issue #261 (H1A42) -> data request in T-025.
- Five checks pass today by design (invariants): T-004/T-010 issue OPEN, T-010 #235 not merged, T-018 _allow_lan_availability kept, T-025 no #249 entry.
- security-ci.md stays out of docs/ until T-001 and T-003 land (describes an unfixed attack path on main).

## 2026-10-23T13:24:01Z build T-001 start (attempt 1)
## 2026-10-23T13:28:07Z review T-001 ok=false failed="main-thread review" tail="Write(path) rules never match in Claude Code (docs: use Edit(path)); notes.md would never be written, and the release breaks after the bump push"
## 2026-10-23T13:29:18Z verify T-001 ok=true (fix round 1: Edit(//) rule, notes before bump, dedupe/cap/PR-close; actionlint clean)
- Note: dry_run stops before the Claude steps, so the Edit(//...) rule is first exercised on a real release night; a failure there now stops before the bump.
## 2026-10-23T13:29:35Z build T-002 start (attempt 1); scope+ .github/workflows/*.yml|yaml (pins, badge permissions, delete rebase_dependencies.yml) per its notes
## 2026-10-23T13:31:05Z verify T-002 ok=true (all action SHAs cross-checked upstream; actionlint clean; no tracked .claude/settings)
## 2026-10-23T13:38:30Z T-003 settings applied via gh api (user-approved): tag ruleset 25725868 on refs/tags/v* (update+deletion, no bypass), immutable releases on, Dependabot alerts on, fork-PR approval all_external_contributors. secret_scanning_non_provider_patterns stayed disabled (plan-gated); push protection already on. Remaining: maintainer rotates RELEASE_TOKEN.
## 2026-10-24T11:40:46Z verify T-003 ok=true (RELEASE_TOKEN rotated by maintainer, updated_at 2026-10-24T11:40:11Z)
## 2026-10-24T11:44:27Z build T-004 start (attempt 1); main thread (two small PRs, merges only)
## 2026-10-24T11:56:48Z verify T-004 ok=true (#238, #259 merged; #238 body Fixes->Refs #237 so #237 stays open; CI green on 8f6b7ea)
## 2026-10-24T11:57:14Z build T-005 start (attempt 1); main thread; flap fix lives in coordinator.py (scope+coordinator.py, tests/test_leak_mqtt_stamps.py)
## 2026-10-24T12:02:36Z verify T-005 ok=true (#253, #257 merged; flap fix requires dry > max(mqtt wet, cloud last_wet); #253 isfinite guard; CI green on fbeab40)
## 2026-10-24T12:03:03Z build T-006 start (attempt 1); main thread; fork PR gated locally
## 2026-10-24T12:08:16Z verify T-006 ok=true (#248 merged; raw HomeAssistantError -> segment_passthrough_failed translation key; CI green)
## 2026-10-24T12:08:33Z build T-007 start (attempt 1); main thread; LOW fixes land in api/client.py and __init__.py (scope+api/client.py)
## 2026-10-24T12:13:49Z verify T-007 ok=true (#250 merged; Retry-After finite+capped 3600 s, future buckets dropped, Store load guarded; CI green)
## 2026-10-24T12:14:02Z build T-008 start (attempt 1); main thread
## 2026-10-24T12:20:22Z verify T-008 ok=true (#255 merged; conflict with #250 kept both: commit in _fetch_device_state_bounded, count only non-cached; config_flow 100%; CLAUDE.md orphan note updated by the PR; CI green)
- T-001 gap (found by peer session 0f945fd3, memory claude-allowlist-git-log-output-write): Bash(git log:*)/Bash(git show:*) allow --output=<path>, so the Claude steps can overwrite files a later RELEASE_TOKEN step runs. Fix before the 03:00 UTC run: Claude steps in their own job, pre-rendered inputs, Read + Edit(out/**) only. Asked the peer session whether it owns the fix.
## 2026-10-24T12:28:30Z build T-009 start (attempt 1); main thread; fork PR gated locally
## 2026-10-24T12:33:52Z T-001 rework: 56504cb splits daily-release.yml into gate/draft/preview/publish (Claude in its own job, no Bash/web, /proc denied; publish on a fresh runner takes only notes.md + replies.json after check-drafts); secret-shape rejects; dry run 40094032298 started
## 2026-10-24T12:33:52Z verify T-009 ok=true (#247 merged; refusal guard covers the 60 s H5086 tick, tick skips excluded SKUs (tests); naive restored datetimes dropped; CI green)
## 2026-10-24T12:34:30Z build T-010 start (attempt 1); main thread; #229 body Fixes->Refs #228
## 2026-10-24T12:40:15Z dry run 40094032298 ok: gate/draft/preview success, publish skipped; Claude (no Bash) wrote notes.md (10 KB) + replies.json (8 drafts), check-drafts passed, preview would comment on #259 #237 #238 #250 #248 #257 #253 #255. The new Edit(//) rule is now exercised by a dry run.
## 2026-10-24T12:40:15Z verify T-010 ok=true (#229-#231 merged as one stack; #229 body Fixes->Refs #228, #228 open; async_read_device double commit removed after #255; kettle status queries respect re-query off and #259 refusal; #235 not merged)
- Ruling: defer; the H7175 doc stays in docs/_research/ (the repo's established folder, 40 docs), not moved to docs/research/ as T-010 notes said (T-010)
## 2026-10-24T12:40:25Z build T-011 start (attempt 1); main thread
## 2026-10-24T12:47:42Z verify T-011 ok=true (#232/#233 merged; options save keeps slot labels + segment modes; frame kinds capped at 32; config_flow 100%; CI green)
## 2026-10-24T12:47:55Z build T-012 start (attempt 1); main thread
## 2026-10-24T13:01:41Z verify T-012 ok=true (#234 merged; request_status admin-only; one throttled, lock-serialised path with in-flight marker held for the spacing window; REQUEST_STATUS_MIN_INTERVAL per device; mirrors sweep targets + #259 refusal; rebased over peer commit ecaa078 (App-token publish), merge edits restored from 905efed; CI green)
## 2026-10-24T13:10:15Z verify T-013 ok=true (registry.py get_registry_device + device_config_entry_ids; 2026.10 also deprecates DeviceEntry.config_entries; split test skipped on 2026.10+; 3.14 suite 4037 passed 1 skipped; CI green)
## 2026-10-24T13:19:16Z verify T-014 ok=true (looks_like_group_id: explicit group type, or all-digit and <= 11 chars; LAN scan + write gate use it; CLAUDE.md Device ID Detection updated). Note: the first push (8c? see git log) went out with a failing test because `pytest | tail && git commit` masked the exit status; CI red ~5 min, fixed by the follow-up commit; chains now use pipefail
## 2026-10-24T13:25:14Z verify T-015 ok=true (H5111 in THERMO_HYGRO_BFF_READ_SKUS; BFF parse -20.9 C / 60 %; #151 guard tested; CI green)
## 2026-10-24T13:26:47Z pause (user asked): 15/32 done. T-016 in progress on branch wip/issue-sweep-late-oct-T-016 (watchdog, repair helpers, strings; still needs the clear-on-inbound hook in _on_mqtt_state_update and tests/test_issue_222.py). Resume: git merge or cherry-pick that branch onto main, finish, verify.
## 2026-10-24T13:30:48Z build T-016 resume (attempt 1); WIP from wip/issue-sweep-late-oct-T-016 cherry-picked uncommitted
## 2026-10-24T13:38:27Z verify T-016 ok=true (mqtt_silent watchdog: 30 min after connect, >=3 queries and 0 inbound -> one forced reconnect -> non-fixable repair; clears on first inbound; never armed by refused/off sweeps; WIP branch merged and deleted; CI green)
## 2026-10-24T13:44:25Z verify T-017 ok=true (GoveeDeviceOfflineError from body/HTTP 400 offline msg; coordinator marks online=False + notifies; entity device_offline key; CI green)
## 2026-10-24T13:51:30Z verify T-018 ok=true (device_reachable = online or LAN/BLE healthy or MQTT msg <= 10 min; entity available = healthy and (group or reachable); light.py bypass untouched; mock coordinators get tests/coordinator_mocks.follow_online_flag; CI green)
## 2026-10-24T13:58:14Z verify T-019 ok=true (bff_last_measurement_at from lastTime ms/s; GoveeLastMeasurementSensor + GoveeReportedOnlineBinarySensor for BFF thermometers; CI green)
## 2026-10-24T14:06:04Z verify T-020 ok=true (H1252 zones: 33 30 toggle path with REST fallback, aa 30 decode (20-byte + checksum), async_set_ring over fixed PTREAL_RING_MASK, kelvin on main panel clamped in builder; release-replies hunk dropped; Co-authored-by holger-dunst; CI green)
## 2026-10-24T14:12:24Z verify T-021 ok=true (GoveeRingLight for H1252, panel COLOR_TEMP 2700-6500 K; reporter test hunk for test_cov_light reviewed and applied; CI green)
## 2026-10-24T14:35:59Z verify T-022..T-024 ok=true (CommandConfirmer module; wired behind confirm_commands (default off), MQTT/REST sends only, resend over REST never reconfirmed; diagnostics command_confirm hashed; README section; CI green)
## 2026-10-24T14:36:59Z verify T-025 ok=true (12 queue entries; #222/#228/#229-#234/#238 covered by commit subjects, so no separate #222 interim entry: the (#222) commit reply ships with the repair and its Reconfigure advice; F-14 respected, no frames in briefs)

## 2026-11-15T01:24:39Z superseded
Ruling: superseded; remaining open tasks carried into docs/plans/nov-sweep (user 2026-11-14)
