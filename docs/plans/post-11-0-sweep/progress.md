## 2026-11-17T04:05:00Z plan created
Ruling: class=architectural tasks=11; multi-track sweep (upstream core fix, workflow queue, scoped code), deadline = 2026-11-18T02:00Z daily release; maintainer answers: #295 reply+pin now and draft core PR, bundle #300/#301/#209 with #254 tonight, merge #300 on local gates, remove H7105 from README
Ruling: spec-defect; side-agent review: T-005 merged the daily-release.yml bumps (#291/#292/#294) before tonight's release, reversing the research recommendation without a maintainer decision; dry_run skips the publish job that uses download-artifact. T-005 -> T-013 (after tonight's release tag exists), T-011 -> T-012 (gate without T-005, asserts the v4 download-artifact pin is still in place). tasks.sh cannot edit depends_on, so both old tasks are blocked hard_spec as superseded (T-005, T-011, round 0)
## 2026-11-17T04:07:24Z build T-001 verify ok (reply 6937949785, #295 pinned); T-002/T-007 dispatched to dev worktrees
## 2026-11-17T04:09:20Z verify T-003 ok=true (0db0014; #290 merged)
## 2026-11-17T04:09:20Z verify T-004 ok=true (5165124; #293 closed; pytest ignore has no update-types)
## 2026-11-17T04:09:20Z verify T-006 ok=true (d926750; Merge PR #300 on local gates; full suite 4650)
## 2026-11-17T04:09:20Z verify T-009 ok=true (H7105 removed from README; 0 mentions)
## 2026-11-17T04:09:20Z verify T-010 ok=true (7 entries; parse_queue ok)
## 2026-11-17T04:09:46Z verify T-007 ok=true (65cc96c; +bff key in 3 exact-dict assertions, Tier 1: test_models.py, test_diagnostics.py)
## 2026-11-17T04:12:22Z verify T-008 ok=true (0642e53; count rides the setup leak-sensor BFF fetch, no extra call; +TestBffSegmentCounts in test_cov_coordinator_setup.py, Tier 2)
## 2026-11-17T04:15:30Z verify T-002 ok=true (c6dea06; core marketplace tests 1206 passed, ruff/mypy clean in core clone; draft only, nothing upstream)
## 2026-11-17T04:15:30Z T-012 local gates: 4665 passed py3.12, 99.74 % (min 96.88 % ble_advertisement), config_flow 100 %, flake8/black/mypy clean; py3.14 suite 4664 passed 1 skipped; HA 2026.11.1 import: no govee deprecations
## 2026-11-17T04:21:27Z verify T-012 ok=true (0b5bf4e; main CI 4/4 success). Remaining: T-013 after tonight's release (2026-11-18T02:00Z) creates the next tag
