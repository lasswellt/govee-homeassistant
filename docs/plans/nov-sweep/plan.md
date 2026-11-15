# nov-sweep plan

## Architecture
One release night, done as serial merges onto `main`. Each merge or fix is one commit (or one merge commit), and it's pushed only after its verify[] passes. CI must be green on the full SHA before the next push. Fork PRs merge locally with `git merge --no-ff refs/pr/N`, and conflicts and fixups are resolved inside that merge commit. Because the PR head becomes an ancestor of `main`, GitHub marks the PR merged and the nightly bot replies on it.

Merge order follows the conflict matrix (2026-11-14, `git merge-tree`):
- `#251 × main`: `coordinator.py`. Resolve it first, before other work lands in the same region (#282 fix T-013 follows it).
- `#252 × #267`: `api/ble.py`. Both add an idle BLE disconnect. #252's generic `DISCONNECT_DELAY` timer wins; #267's flag/property sits on top of it (T-007).
- `#264 × #277`: setup-python/checkout lines, resolved in T-017.
- #271 shares `state.py` colour functions with #286 (T-014). T-015 keeps both sets of clears.
- Everything else merges clean pairwise.

Chosen tradeoff (user): everything tonight, except #254. Rejected: one broad PR per release day (about 6 nights), and pairing the low-risk PRs (about 5 nights). Cost accepted: a regression report after tonight may be harder to trace to a single change. The release notes have to name each behaviour change: #271 None-while-off and DIY effects, #286 music switch, #252 idle BLE release, #251 login retry.

Workflow-only merges (T-016..T-019) never trigger a release by themselves, because the gate is `git diff <tag>..HEAD -- custom_components/`.

## File map
- `custom_components/govee/const.py`: +H5105/H5108 (PR #270); +H1A42 in `PTREAL_SEGMENT_SKUS` (T-009)
- `custom_components/govee/config_flow.py`: PR #273 bluetooth-flow abort
- `custom_components/govee/api/auth.py`, `__init__.py`, `coordinator.py`, `mqtt.py`, `openapi_events.py`: PR #251, plus the re-login latch and `is_connection_error` fix
- `custom_components/govee/api/ble.py`, `ble_advertisement.py`, `api/lan_client.py`, `api/lan.py`: PR #252
- `custom_components/govee/coordinator.py`: BLE back-off (T-005); H6104 background readback and poll order (T-007); `FRAME_CLIMATE_DEHUMIDIFIER_SKUS` gate (T-011); idempotent silent-repair clear (T-013)
- `custom_components/govee/api/lan.py`: scan source-address trust and response cap (T-006)
- `custom_components/govee/h6104.py`, `number.py`, `select.py`, `switch.py`, `strings.json`, `translations/en.json`, `icons.json`: PR #267
- `custom_components/govee/models/device.py`: +H7170 in `KETTLE_FRAME_SKUS` (T-010); `FRAME_CLIMATE_DEHUMIDIFIER_SKUS` (T-011)
- `custom_components/govee/water_heater.py`: `native_*` behind an HA ≥ 2026.11 gate (T-012)
- `custom_components/govee/models/state.py`, `switch.py`: music fields cleared on power-off/colour/CT (T-014)
- `custom_components/govee/light.py`, `select.py`, `models/state.py`: PR #271
- `.github/workflows/daily-release.yml`, `scripts/release_checks.sh`, `CLAUDE.md`: PR #256; pins from #264/#275/#276
- `.github/workflows/tox.yaml`, `style.yml`, `install-stats`, `type-check`, `uptime`: #277, #263 (`files:`), #264
- `.github/dependabot.yml`: pytest semver-major ignore (T-019)
- `docs/release-replies.md`: 19 queued threads (T-020)
- tests: `test_issue_251_relogin.py`, `test_issue_246.py`, `test_lan_scan_trust.py`, `test_h6104_setup_entry.py`, `test_issue_261.py` (new); `test_kettle_stale_temperature.py`, `test_h7152_temperature_humidity.py`, `test_kettle_water_heater.py`, `test_issue_222.py`, `test_cov_switch.py` (extended)

## Coverage
| Outcome | Merge / code | Tests | Non-test check | Reply |
|---|---|---|---|---|
| Contributor PRs | T-001, T-002, T-007 | T-001/2/7 suites, T-008 lifecycle | ancestry + grep | automatic (merge subject #N) |
| BrawnyBravo PRs + follow-ups | T-003, T-004, T-016, T-005, T-006 | relogin, BLE/LAN suites, release workflow test | ancestry, `DISCONNECT_DELAY`, `exclude-drafts`, `LAN_SCAN_MAX_RESPONSES` | automatic; #249 via T-020 |
| SKU-scoped fixes | T-009..T-012 | per-issue tests | SKU-set greps, `native_*` greps | automatic (commit subject #N) |
| Repair / music state | T-013, T-014 | test_issue_222, test_cov_switch | absence of the flag guard; state.py clears | automatic |
| #271 | T-015 | light/select/models suites | hands-on line, workflow file absent | automatic |
| Action pins | T-017, T-018, T-019 | CI | tag-SHA equality via `gh api`; #280 CLOSED | n/a |
| Replies | T-020 | n/a | 19 `## #N` headings | queue |
| Gates / #254 | T-021, T-022 | full suites | everything pushed; hands-on line | automatic |

No gaps: each outcome has at least one task with a non-test check.

## Risks
- **T-002 is blocked but satisfied (T-023).** `tasks.sh next` never offers its dependents (T-009, T-010, T-011, T-014, T-016, T-019, T-020, then T-021/T-022). A fresh session must dispatch them by id: `/blitz:build <ID>`.
- **Timing:** the cron runs at 03:00 UTC, and the plan was written at about 01:30 UTC. Twenty merges with CI waits will probably run past 03:00. If they finish before 05:00 UTC (still 2026-11-14 Eastern), ask the maintainer before dispatching `daily-release` with `dry_run=false`. Otherwise it ships at the next scheduled run. #256 moves the cron to 02:00 UTC from tomorrow on.
- **#251 conflict resolution is ours.** The author's tests cover their own head, so after resolving, rerun `test_issue_222.py` (main's silent-session detection) and the PR's suites together.
- **#252 × #267 idle-timer merge:** two designs of the same feature. The verify pins one timer (`DISCONNECT_DELAY`, no `_idle_disconnect_handle`), and T-008 asserts that other BLE models are unchanged.
- **#284 isn't run against HA 2026.11**, because the local lane is HA 2025.1.4. Both branches are tested by patching the version gate, and CI's newer lane (if any) is the second check.
- **#271 changes behaviour on every light,** and the own-day rule is waived. It's gated on the maintainer's hands-on try (T-015 verify). The build stops there until the progress.md line exists.
- **#264/#275/#276 pins:** the tag-SHA equality is checked through `gh api`, which needs the sandbox disabled.
- **Fork PR CI never ran on the current heads.** Local gates stand in for it, and CI runs on `main` after each push.
- **T-021 lists `tests/test_issue_261.py` as its file**, only because `tasks.sh` needs a file. It's a gate-only task.

## Solutions consulted
- No `docs/solutions/` or `docs/plans/BACKLOG.md` in the repo.
- Superseded open tasks absorbed:
  - `followups-2026-10-1`: T-012, T-013, T-014 → T-007/T-008; T-016 → T-009; T-017 → T-001; T-018 → T-002; T-020 → T-015; T-021 → T-020; T-022 → T-021.
  - `issue-sweep-late-oct`: T-026 → T-016; T-027 → T-004; T-028 → T-003; T-029 → T-005; T-030 → T-006; T-031 → T-021; T-032 → T-022.

## Research
- `docs/research/2026-11-14_nov-issue-pr-sweep.md` (this plan's `--from-research` input)
- `docs/research/2026-10-28_post-release-sweep.md`, `docs/research/2026-10-23_issue-sweep-late-oct-security-prs.md` (#251/#252 security review), `docs/research/2026-10-24_secure-ai-release-pipeline.md` (release-job threat model, for T-016/T-018)
