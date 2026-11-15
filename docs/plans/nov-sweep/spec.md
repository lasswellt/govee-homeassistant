---
status: active
priority: P1
created: 2026-11-14
ship: manual
---
# November sweep: merge the open PRs and fix the actionable issues

## Goal
Nothing under `custom_components/` has shipped since v2026.10.2 (2026-10-27), while 14 PRs and 34 issues piled up. Two older plans (`followups-2026-10-1`, `issue-sweep-late-oct`) stalled. This plan merges every PR that is ready (or that we make ready), fixes the issues that have enough evidence, queues replies for every thread that is owed one, and ships it all in tonight's release, except PR #254, which gets its own day after a hands-on try.

## Outcomes
- Contributor PRs #270, #273 merged unchanged; #267 merged with the BLE idle-timer conflict against #252 resolved and its lifecycle fixups → T-001, T-002, T-007, T-008
- BrawnyBravo PRs #251 (with re-login latch + HTTP-error classification), #252, #256 merged; #246 BLE back-off and LAN scan trust built on #252 → T-003, T-004, T-005, T-006, T-016
- SKU-scoped fixes ship: H1A42 segment colours (#261), H7170 kettle (#278), H7151 climate frames (#283), kettle native temperature properties (#284); MQTT silent repair clears (#282/#222); Music Mode switch state (#286) → T-009..T-014
- PR #271 merged without its fork workflow, after the maintainer's hands-on try → T-015
- Action pins current and tag-verified (#277, #263, #264, #275, #276); dependabot stops proposing pytest majors; #280 closed → T-017, T-018, T-019
- Every thread owed a reply that no commit references is queued in `docs/release-replies.md` → T-020
- Tonight's tree passes all gates (coverage 95 % / 96 % per module / config_flow 100 %, flake8, mypy, black, 3.14 suite) → T-021; PR #254 merges on a later day of its own → T-022

## Out of scope
- Code for #281 (H70B6 DIY via BFF), #285 (scene paramId), #287 (H5107 unit), #288 (Tap-to-Run), #289 (multihomed LAN): replies asking for data only (T-020).
- #211 H60B0 (waits on its contributor's PR), #213/#236/#237/#241/#242/#265 (waiting on reporters, <30 days).
- Cutting the release or bumping `manifest.json` (the daily-release workflow does it).

## Assumptions
- User 2026-11-14: lift the #249 hold for #251, #252 and #256; no advisory was filed in 22 days. We resolve #251's conflict and its two security items ourselves.
- User 2026-11-14: own-day rule waived for tonight (#267, #252, #251, #271 all ship tonight); #254 still waits for its own day.
- User 2026-11-14: #271's `.github/workflows/sync-upstream.yml` is removed inside our merge commit; the build pauses for the maintainer's hands-on try before that merge.
- Reporters install through HACS, so SKU-scoped fixes that aren't verified on hardware still ship tonight, and the replies ask the reporters to confirm.
- The local test lane is HA 2025.1.4 (py3.12) plus a py3.14 suite. #284's HA 2026.11 branch is exercised by patching the version gate, not by running HA 2026.11.

## Verification
- `bash /home/lasswellt/.claude/plugins/cache/blitz/blitz/3.16.0/scripts/tasks.sh verify nov-sweep <id>` per task; `/blitz:check --scope plan nov-sweep` before the final push.
- After every push: `gh run list --commit "$(git rev-parse HEAD)"` until all required checks pass (full SHA).
- Manual: maintainer writes `PR #271 tried on own lights: <result>` (T-015) and, on a later day, `PR #254 tried on own lights: <result>` (T-022) in progress.md.
- After T-016: run `daily-release` via `workflow_dispatch` with `dry_run` (default) and confirm the gate logic.
