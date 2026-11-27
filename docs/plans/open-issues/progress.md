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
