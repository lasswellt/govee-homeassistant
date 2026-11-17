# Plan: post-11-0-sweep

## Architecture

Three independent tracks, all finishing before tonight's 02:00Z daily-release run:

1. **#295 (outward, no code):** T-001 posts the workaround and pins the issue. T-002 drafts the real fix for home-assistant/core in a scratch clone and exports the patch and PR text to `docs/upstream/`. Nothing goes upstream without the maintainer.
   - Chosen design: `async_pre_install` reads the on-disk manifest. Our manifest's `documentation`/`issue_tracker` URLs and `version` identify the owning repo, so a stale record can be cleared safely.
   - Rejected: an integration-side repair. Blocked users can't install new code, and it would edit another integration's storage.
   - Rejected: a domain rename. It breaks every config entry.
   - Rejected: removing LaggAt from hacs/default. It doesn't touch local flags.
2. **Workflow/dependency queue:** T-003 merges #290. T-004 closes #293 and widens the pytest ignore. T-013 merges the three `daily-release.yml` bumps **after tonight's release tag exists**, then runs a `dry_run`. T-005 (merge before tonight) was superseded: `dry_run` skips the publish job, which uses `download-artifact` (~:321), so the first real test of the new pins would have been tonight's release. The research recommended waiting, and the maintainer had only decided the code bundling. T-012 (which replaces T-011) asserts the v4 pin is still in place at gate time.
3. **Integration code, shipping with #254 tonight:**
   - T-006 merges #300.
   - T-007/T-008 add a `bff` segment-count source for an allow-listed H3A53. Allow-listing follows the `FAHRENHEIT_REPORTING_SKUS` / `SKU_SEGMENT_OVERRIDES` pattern. A global BFF source was rejected because the BFF count is unproven on other SKUs (cf. H7075/H7026 overrides).
   - T-009 edits the README. T-010 queues replies.
   - T-012 gates everything (T-011 superseded).

Tradeoff accepted by the maintainer: #254 (all-device brightness) no longer ships alone. #300 (debug service only) and #301 (one SKU) are scoped, which limits how much they muddy regression attribution.

## File map
- `docs/upstream/core-190454-marketplace-stale-owner.md`: new, upstream PR title/body/design/tests run (T-002)
- `docs/upstream/core-190454.patch`: new, core diff incl. `tests/components/marketplace/` (T-002)
- `.github/workflows/hacs-hass.yaml`: hassfest SHA (T-003)
- `.github/dependabot.yml`: pytest ignore without `update-types` (T-004)
- `.github/workflows/daily-release.yml`: upload/download-artifact, claude-code-action pins (T-013, after tonight)
- `custom_components/govee/services.py`, `coordinator.py`, `ble_passthrough.py`, `strings.json`, `translations/en.json`: PR #300 (T-006)
- `custom_components/govee/models/device.py`: `bff_segment_count` field and the `bff` resolution source (T-007)
- `custom_components/govee/const.py`: `BFF_SEGMENT_COUNT_SKUS = {"H3A53"}` (T-007)
- `custom_components/govee/api/auth.py`: parse `deviceSettings.segmentCount` (T-008)
- `custom_components/govee/coordinator.py`: apply the BFF count before platforms are forwarded (T-008)
- `README.md`: H7105 removed at :84/:136/:180/:449 (T-009)
- `docs/release-replies.md`: #284, #286, #285, #296–#299 (T-010)

## Coverage

| Outcome | Upstream/GitHub | Workflow | Model/const | API/coordinator | Docs | Test/gate |
|---|---|---|---|---|---|---|
| #295 users unblocked | T-001 | n/a | n/a | n/a | n/a | T-001 verify |
| Core fix ready | T-002 | n/a | n/a | n/a | T-002 | T-002 (core tests) |
| Dependabot queue empty | T-003, T-004, T-013 | T-003, T-013 | n/a | n/a | n/a | T-013 dry run |
| #300 multi-frame | T-006 | n/a | n/a | T-006 | n/a | T-006, T-012 |
| H3A53 segment count | auto reply via commit | n/a | T-007 | T-008 | release notes | T-007, T-008, T-012 |
| H7105 off README | auto reply via commit | n/a | n/a | n/a | T-009 | T-009 |
| Threads answered | T-010 | n/a | n/a | n/a | T-010 | T-010 |
| Green before 02:00Z | n/a | n/a | n/a | n/a | n/a | T-012 |

No gaps.

## Risks
- **Deadline.** Everything must be on origin/main with CI green before 2026-11-18T02:00Z. If T-007/T-008 slip, hold them rather than ship them half-done. The other tasks don't depend on them except T-012, which waits on T-008 and record a ruling.
- **T-013:** PR CI never runs `daily-release.yml` and `dry_run` skips publish; the 2026-11-19 run is the first real publish with the new pins (and it skips unless `custom_components/` changed). Revert on any failure.
- **T-008 timing:** segment entities are created at platform setup. If the BFF fetch fails (2FA, outage), the H3A53 falls back to the API count; a length change needs a reload.
- **T-002:** a core dev env is heavy (py3.14 + `requirements_test.txt`). If only the marketplace tests can be run, say so in `## Tests run`. Core may prefer a different design (for example a "stop tracking" action); the PR text should offer that alternative.
- **#254 bundling:** a brightness regression reported after tonight is now attributable to #254 with high likelihood, but not certainty.
- **Blocked #295 users don't receive tonight's release** until they apply the workaround.

## Solutions consulted
- `docs/solutions/`: absent. `docs/plans/BACKLOG.md`: absent.
- Memory: issue-replies-after-release (the #295 reply cites no version, so it's allowed now), broad-behaviour-change-own-release-day (waived by maintainer for tonight), hacs-reporters-cannot-test-branches (ship scoped #301 unverified), gh-needs-sandbox-disabled.

## Research
- `docs/research/2026-11-16_post-2026-11-0-sweep.md`
- Prior: `docs/research/2026-11-14_nov-issue-pr-sweep.md`, `docs/plans/nov-sweep/progress.md`
