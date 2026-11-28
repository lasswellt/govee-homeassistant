## 2026-11-25T15:10:00Z plan created
Ruling: class=architectural tasks=7; four independent streams (release-workflow PRs, #310 mask, #241 H6099 video mode, DIY diagnostics) plus docs, replies and gates, from docs/research/2026-11-25_open-issues-and-prs.md; maintainer chose H6099 control over read-only.

## 2026-11-26T03:05:00Z plan extended
Ruling: class=architectural tasks=12 (+5: T-008..T-012); from docs/research/2026-11-26_issues-and-pr-merge-validation.md. The fork PRs #319, #321 and #320 were gated locally (combined 4,789 passed) and merge first, then #316 and #317. The release moved to 2026-11-28T02:00Z because the 2026-11-27 run skipped with nothing built.

## 2026-11-27T03:42:32Z verify T-001 ok=true
## 2026-11-27T03:42:32Z build T-008 start (attempt 1): #319 merged 03:14Z, #321 merged 03:2xZ, #320 branch updated (485e570), CI pending
## 2026-11-27T03:42:32Z build T-002 start (attempt 1)
## 2026-11-27T03:45:02Z verify T-002 ok=true (commit a5ddca9; scope+2 test files, raw-list goldens gained trailing 00, packets byte-identical)
## 2026-11-27T03:45:15Z build T-003 start (attempt 1)
## 2026-11-27T03:47:04Z verify T-003 ok=true (commit 9e5c3b7)
## 2026-11-27T03:47:21Z build T-010 start (attempt 1; dispatched while T-008 #320 CI runs, no overlapping const.py lines)
## 2026-11-27T03:49:35Z verify T-008 ok=true (#319 24ec65e, #321 30f5dad, #320 b4fb8e7; fork branches updated with main, conflicts resolved by keeping both blocks)
## 2026-11-27T03:49:35Z verify T-010 ok=true (commit 6dfa6fe)
## 2026-11-27T03:49:45Z build T-009 start (attempt 1)
## 2026-11-27T03:51:22Z verify T-009 ok=true (commit 89237ab)
## 2026-11-27T03:51:22Z build T-004 start (attempt 1)
## 2026-11-27T03:52:43Z build T-005 start (attempt 1; docs-only, concurrent with T-004)
## 2026-11-27T03:53:44Z verify T-005 ok=true (commit f7dd483; reviewed: no device ids or usernames)
## 2026-11-27T03:56:18Z verify T-004 ok=true (commit 588c525)
## 2026-11-27T03:56:18Z verify T-006 ok=true; verify T-011 ok=true (12 queued briefs: #310 #208 #241 #314 #308 #304 #315 #318 #298 #299 #288 #211)
## 2026-11-27T03:56:18Z gates: 4810 passed, cov 99.73%, config_flow 100%, mypy/flake8/black clean
## 2026-11-27T04:01:13Z verify T-007 ok=true; verify T-012 ok=true (main 28c0df2: Tests, Type Checking, Style check, HACS/HASS green)
## 2026-11-27T04:01:13Z build open-issues 12/12 done

## 2026-11-27T22:49:48Z plan extended (2026-11-27 research)
Ruling: class=bounded tasks=2; #322 version-safe reload helper in config_flow.py (T-013), reopen #316 + 3.14-lane final gate (T-014); no open PRs
## 2026-11-27T22:57:04Z build T-013 start (dev, sonnet)
## 2026-11-27T23:00:21Z verify T-013 ok=true (staged; maintainer commit pending: credential filename hook). Mutation check: old config_flow.py fails the new no-warning asserts with the #322 text
## 2026-11-27T23:34:43Z verify T-014 ok=true (#316 reopened; 4809 passed, cov 99.69%; main 1e40975: Tests, Type Checking, Style check, HACS/HASS green)
## 2026-11-27T23:34:43Z build open-issues 14/14 done
## 2026-11-27T23:48:30Z check CONDITIONAL check-report.md
Ruling: cannot-verify; H6099 ON frame and H7038 bulbs 16-30 (needs reporter hardware confirmation after the 2026-11-28T02:00Z release, #241 #310)
Ruling: spec-defect; T-001..T-012 verify[] pin .cc-sessions/venv312, removed 2026-11-27 (system Python 3.14 only); re-run with venv314 55/57 rc=0, the 2 rc=1 were CI-still-running and passed after; tasks.sh cannot edit verify[], accepted as historical (check)
## 2026-11-28T00:29:38Z check CONDITIONAL check-report.md (re-run: venv paths repaired in 6 plans by operator, T-001..T-014 tasks.sh verify ok; startup-validate --strict ok; remaining: reporter hardware confirmation #241 #310)
