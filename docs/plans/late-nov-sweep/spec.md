---
status: active
priority: P0
created: 2026-11-24
ship: manual
---
# Late-November sweep: unstick the release, fix #305–#309, merge #303

## Goal
Nothing has shipped since v2026.11.0 (2026-11-15) because a third-party tox plugin broke the CI gate on the bump commit. CI is green again after a re-run, so v2026.11.1 goes out automatically at 02:00Z on 2026-11-25. Before then: remove the plugin from the gate, fix the one report where devices stop working on current Home Assistant (#305), fix the three device-specific bugs (#308, #307, #261), fix scenes that report success without changing the light (#309), and merge the H2800 contribution (#303). Afterwards: land the held dependabot bumps and make a failed nightly release visible.

## Outcomes
- `Tests` on `main` runs without `tox-gh-actions` and is green on both Python lanes → T-001
- H2800 owners get three light switches and a working music mode → T-002
- On HA 2026.11.2, leak sensors and gateway-bridged thermometers register without a `via_device` warning or error and still show their hub; on the CI lane's older HA nothing changes → T-003, T-004
- Turning an H60A6 off from Home Assistant sends no LAN power-off; the light stays off → T-005
- An H6054 set to HA brightness 128 reads back about 128, not 255 → T-006
- An H1A42 shows 5 segment lights, each of which moves LEDs → T-007
- Selecting a scene sends `{"paramId", "id"}` when the scene list has a paramId; "-A"/"-B" variants are distinct; the scene restored after music mode carries its paramId; scenes without a paramId and state saved before the update still work → T-008, T-009
- Threads no commit references have a queued reply → T-010
- Local gates and the six required checks are green on `main` before 02:00Z → T-011
- After the release: #302, #292, #291 merged, `dry_run` green, and a failed nightly run opens or updates an issue → T-012, T-013

## Out of scope
- #306: creating lights from the account list when the Developer API omits them (new discovery path; reply only).
- #241: a video-mode entity for the H6099 (needs a reporter test of a raw frame first; reply only).
- #299: brightness or colour for Govee app groups (the API reports on/off only).
- Finer than 5-area control on the H1A42 (needs a capture of the app's frames).
- Bumping `manifest.json` or cutting the release by hand; the workflow does both.

## Assumptions
- #309 ships tonight with the other fixes. Maintainer decision on 2026-11-24, overriding the usual "broad change gets its own release day" rule.
- The H6054 mapping (20–254 → 1–100) rests on one reporter's nine measurements and stays scoped to that SKU.
- The H60A6 exclusion is SKU-wide, not firmware-gated: units on firmware 1.00.41 lose LAN power-off and nothing else.
- What raises the `RuntimeError` in #305 is unknown; removing `via_device` is the fix either way, and the reporter is asked for the traceback.

## Verification
- `bash /home/lasswellt/.claude/plugins/cache/blitz/blitz/3.16.0/scripts/tasks.sh verify late-nov-sweep <id>` per task; `/blitz:check --scope plan late-nov-sweep` before the release window.
- Not captured by any `verify[]`: #305 only reproduces on HA 2026.11.x, and both CI lanes resolve HA 2025.1.4. Run the registry and leak-sensor tests once in an HA 2026.11 venv (T-011 notes) or confirm on a real install.
- Not captured: #309, #308, #307 and #261 are confirmed only when the reporters test v2026.11.1 on hardware. Leave those issues open until they do.
- After 02:00Z on 2026-11-25: `gh release list --limit 1` shows v2026.11.1 and the queue in `docs/release-replies.md` is empty.
