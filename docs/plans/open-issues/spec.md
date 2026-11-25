---
status: active
priority: P1
created: 2026-11-25
ship: manual
---
# Open issues and Dependabot PRs after v2026.11.1

## Goal

Act on everything the 2026-11-25 triage found needing us. Merge the five green Dependabot PRs and prove the release workflow still works with them. Fix the two issues with hardware evidence: #310 (H7038 segments 16–30) and #241 (H6099 video mode). Make the DIY-scene reports (#314, #296, #298, #281) diagnosable. Document the #304 protocol findings. Queue the replies the release bot posts tonight. The fixes ship in tonight's 02:00Z release (v2026.11.2).

## Outcomes

- Dependabot #311, #312, #313, #292 and #291 are merged, and a `daily-release.yml` dry run on the merged `main` succeeds before 02:00Z → T-001
- `govee.set_segment_color` on an H7038 with account login reaches segments 16–30. It does so with one AWS IoT frame carrying a 4-byte mask. Frames for H1232/H1252/H1A42 are byte-identical to before. A segment index of 32 or above fails the command instead of being dropped → T-002
- An H6099 gets a DreamView (video mode) switch:
  - its state follows the light's own `aa 05` report;
  - ON sends the DreamView frame over AWS IoT;
  - OFF restores the last colour → T-003
- A diagnostics download shows, per device, how many DIY scenes the last fetch returned or which error it hit. The log says when a DIY-capable device returned none → T-004
- The protocol reference documents the 4-byte mask, H6099 video mode, and the H607C Music DreamView `33 60 02` frames → T-005
- Replies for #310, #208, #241, #314, #308 and #304 are queued in `docs/release-replies.md` → T-006
- Full gates pass locally and the four CI workflows are green on `main` HEAD before 02:00Z → T-007

## Out of scope

- H705E and H7026 on the ptReal segment path. H7026 waits for the #208 reporter to test the frame.
- The REST fallback for H7038 without account login. It still ignores indices 15–29.
- Reading or activating app-side DIY scenes through `/appsku/v1/diys/groups-diys`. Creating the DIY select lazily after a refresh.
- Implementing #304 Music DreamView control.
- The home-assistant/core#190454 proposal (#295).
- late-nov-sweep T-013 (release failure alert). It stays in that plan.

## Assumptions

- The maintainer chose H6099 control (ON frame `33 05 00 01 00 64`) over read-only state, knowing the ON frame is unverified on that model (2026-11-25).
- #313 replaces #302 in late-nov-sweep T-012, which is now blocked as superseded.
- Every reporter-facing change is SKU-scoped, so it ships in the daily release without a test branch: HACS reporters can't test branches.

## Verification

- `bash /home/lasswellt/.claude/plugins/cache/blitz/blitz/3.16.0/scripts/tasks.sh verify open-issues <id>` per task. Run `/blitz:check --scope plan open-issues` before the release.
- After the release, reporters confirm on hardware:
  - #310: all 30 bulbs.
  - #241: ON enters video mode, app Video→Color flips the switch off, and OFF restores colour.
  - No `verify[]` can capture these.
