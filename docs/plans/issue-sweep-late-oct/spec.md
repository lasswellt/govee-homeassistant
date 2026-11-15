---
status: superseded
priority: P0
created: 2026-10-23
ship: manual
---
# Issue sweep: late October 2026

## Goal
Clear the three-week backlog since v2026.10.0 (2026-10-02). Nothing has merged since then; 19 issues wait on a maintainer reply and 19 fork PRs have never been reviewed. The release pipeline is hardened first. A security review found that the nightly release job hands an admin token and push/close tools to a model that reads text anyone on GitHub can write, so no merge happens until that is fixed. Then 17 PRs are merged in an order that keeps conflicts small, with the security fixes the review asked for, and #235 is declined. #251, #252 and #256 wait for the private #249 report. The fixable open issues get fixed: #243, #236, #222, #242, #258, #223 (H1252), #245, #246, LAN scan trust, and the HA 2026.10 registry deprecation. Every thread no commit subject names gets a queued reply. PR #254 changes brightness for every light, so it ships alone, last.

## Outcomes
- The release job's model can't push, comment, close or read a write token. Shell posts replies only to validated thread numbers, and notes with foreign URLs or shell blocks are refused → T-001
- The repo has SECURITY.md, CODEOWNERS on pipeline files, SHA-pinned actions, least-privilege badge workflows, and no dead force-push workflow → T-002
- RELEASE_TOKEN is rotated, `v*` tags are protected, releases are immutable, Dependabot alerts are on, and outside contributors' CI needs approval → T-003 (maintainer)
- H5108 accounts no longer churn MQTT for ~35 min after restart (#237), and account-wide status refusals stop every sweep including the H5086 tick → T-004, T-009
- Heater targets restore in Celsius, and a leak sensor never shows dry while wet, even after a lost MQTT message → T-005
- The H8057 4th segment works; 429 back-off and request counts survive reloads and can't be disabled by a corrupt Store or a hostile Retry-After; segment modes survive options saves → T-006, T-007, T-008
- H5086 energy resets are handled (#200) → T-009
- The H7175 kettle gets fresh temperature, a water heater, brew mode, slot labels and an on-base sensor. `govee.request_status` is admin-only, rate-limited and skips excluded or quarantined models (#228) → T-010, T-011, T-012
- No production code calls the deprecated `device_registry.async_get_device`, and the suite passes on Python 3.14 / HA 2026.10 and on the 3.12 CI lane → T-013, T-031
- An all-digit H5054 is a normal sensor (#243), and an H5111 behind an H5151 shows its account-data reading (#236) → T-014, T-015
- A connected MQTT session whose status queries go unanswered raises `mqtt_silent`; a quiet session with the sweep off does not (#222) → T-016
- Cloud-only devices are available when the cloud says online or a local or push transport proved them alive in the last 10 min. Govee's "Device is offline" reply gives a translated error. #226 still holds (#242) → T-017, T-018
- BFF thermometers show Last measurement and the cloud's reported online flag (#258) → T-019
- H1252 panel and ring switch independently, the ring is its own light, and the panel takes colour temperature (#223) → T-020, T-021
- Optional confirm-and-resend for light commands only (`confirm_commands`, off by default, budgeted, one resend, appliances never resent), visible in diagnostics (#245) → T-022, T-023, T-024
- Every thread without a commit-subject reference has a queued reply → T-025
- After the #249 report is read: #256's release gates, #252's BLE enrolment fix and #251's login recovery land, with the re-login loop stopped (#246 partial) → T-026, T-027, T-028
- A BLE device that fails to connect is backed off, and LAN-capable commands skip BLE while LAN is healthy (#246) → T-029
- A LAN host can't redirect another device's commands by lying in a scan reply, and the scan collector is bounded → T-030
- Coverage ≥ 95 % overall, ≥ 96 % per module, config_flow.py 100 %; flake8, mypy, black and the 3.14 suite are clean → T-031
- PR #254 ships alone after a hands-on try on the maintainer's lights, with its conflicts resolved here → T-032

## Out of scope
- #209 / #260 H7105 Tower Fan 2: not in the Developer API, even on the same account
- #211 H60B0 zones: waiting on the reporter's PR; third-party frames are not used unverified
- #239 H7045, #240 H7073, #241 H6099, #213 H66A0, #244 H5054 (API-key only), #261 H1A42: diagnostics needed first (T-025 asks for them)
- #200, #201, #207, #208, #215: no reporter activity since 2026-10-01
- #235 experimental kettle keep-warm frames (declined)
- #249 itself: it is handled through private vulnerability reporting; this plan only gates #251, #252 and #256 on it
- A Python 3.14 CI lane, environment-approval-gated release publication, and a GitHub App token in place of the PAT. These are recommended in security-ci.md; T-003 lists the App token as the better option

## Assumptions
- Merge tasks need T-003, which only the maintainer can complete, so the loop stops after T-002 until the repo settings are changed
- The three HELD merges need a maintainer-written `#249 report reviewed: <outcome>` line in progress.md. Everything not held proceeds and ships nightly
- Fork PRs are gated locally in `.cc-sessions/venv312` (the CI lane) with pytest, flake8, mypy and black before merging
- Every merge is `--no-ff` with subject `Merge PR #N: <title> (#issue)`, and every fix commit carries `(#issue)`. After T-001, the nightly bot reads all commit subjects, not only those touching custom_components/
- PR bodies that auto-close issues (#229 → #228, #238 → #237) are edited to "refs", or the issues are reopened, until the reporter validates
- The H1252 work ports holger-dunst's patch by hand under a security checklist, credited with Co-authored-by
- #245 is built here (user choice) and limited to light commands (security review F-03)
- #242 rule: `available = coordinator healthy and (group or device_reachable)`; light.py's LAN bypass is unchanged (#226)
- #254: own release day, conflicts resolved here (user choice)

## Verification
- `bash /home/lasswellt/.claude/plugins/cache/blitz/blitz/3.16.0/scripts/tasks.sh verify issue-sweep-late-oct <id>` per task; `/blitz:check --scope plan issue-sweep-late-oct` before ship
- After T-001 merges, run `daily-release.yml` by hand with `dry_run` and record the outcome in progress.md
- Manual preconditions written to progress.md by the maintainer: `#249 report reviewed: …` (T-026–T-028) and `PR #254 tried on own lights: …` (T-032)
- Five checks pass today on purpose, as invariants: #237 and #228 stay open (T-004, T-010), #235 stays unmerged (T-010), light.py keeps `_allow_lan_availability` (T-018), and no #249 queue entry (T-025)
- H1252 end-to-end behaviour can only be confirmed on holger-dunst's hardware; golden frames come from their captures
