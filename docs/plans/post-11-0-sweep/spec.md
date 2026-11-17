---
status: active
priority: P0
created: 2026-11-16
ship: manual
---
# Post-v2026.11.0 sweep

## Goal

Unblock the users that HA 2026.11's Marketplace stops from updating (#295), and push the underlying fix upstream. Clear the open PR queue (#300, #290–#294) and ship the scoped fixes (#301 H3A53 segment count, #209 README) together with #254 in tonight's release (2026-11-18 02:00Z). Answer every thread that's owed a reply.

## Outcomes
- #295 reporters have the cause, the workaround and the upstream link in a pinned comment → T-001
- A reviewed-ready core patch makes the Marketplace clear a stale same-domain record instead of refusing the update → T-002
- Dependabot queue is empty: #290/#291/#292/#294 merged, #293 closed, and pytest is no longer proposed; the release workflow passes a dry run on the new action pins → T-003, T-004, T-013 (after tonight's release)
- `govee.send_raw_ptreal` accepts several frames in one message and uses LAN when healthy (#300) → T-006
- An H3A53 with account login gets its segment entities from the installed length (BFF `segmentCount`), not a fixed count → T-007, T-008
- The README no longer lists the H7105 → T-009
- Confirmed threads close, and asks/parks go out with the release → T-010
- Everything is on origin/main with CI green before 02:00Z → T-012

## Out of scope
- #285 paramId fix: waits on the reporter's diagnostics.
- #299 DreamView mode select: waits on the SKU and diagnostics.
- Account-side DIY (`/appsku/v1/diys/groups-diys`) for #281/#296/#298, playlists (#297), Tap-to-Run (#288): no captured API.
- Opening the upstream core PR: T-002 drafts it only; the maintainer opens it.
- Any integration-side Marketplace workaround. Blocked users can't install new code, and editing another integration's storage is out of bounds.

## Assumptions
- The maintainer chose to bundle #300/#301/#209 with #254 tonight, overriding the own-day recommendation (2026-11-16 answer "Bundle tonight").
- #300 merges on local gates; fork CI is not approved first (maintainer answer).
- H7105 is removed from the README, not caveated (maintainer answer). The code support (`MQTT_OSCILLATION_SKUS`) stays.
- The #295 reply is posted now and the issue pinned (maintainer answer "1, do both").
- The daily-release workflow bumps the version; no task touches `manifest.json`'s version.
- The H3A53 is allow-listed alone in `BFF_SEGMENT_COUNT_SKUS`; other SKUs keep their current resolution.

## Verification
- `bash /home/lasswellt/.claude/plugins/cache/blitz/blitz/3.16.0/scripts/tasks.sh verify post-11-0-sweep <id>` per task; `/blitz:check --scope plan post-11-0-sweep` before 02:00Z.
- Manual: after the release, check that the bot replied on #209/#301 and that the queued entries were posted and cleared.
- Manual: the maintainer reviews `docs/upstream/core-190454*` before anything goes upstream.
