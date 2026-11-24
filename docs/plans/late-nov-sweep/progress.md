## 2026-11-24T03:56:42Z plan created
Ruling: class=architectural tasks=13; from research 2026-11-24_late-nov-issue-pr-sweep.md, interview reduced to one question (maintainer chose to ship #309 tonight); T-001..T-011 before the 2026-11-25 02:00Z release, T-012/T-013 after it.

## 2026-11-24T03:59:02Z build T-001 start (attempt 1)
Ruling: sequential; --parallel refused, worktree.baseRef unset in .claude/settings.json (needs "head") (plan, wave 0)
## 2026-11-24T03:59:47Z build T-003 start (attempt 1)
## 2026-11-24T04:05:18Z verify T-001 ok=true
## 2026-11-24T04:05:18Z verify T-003 ok=true
## 2026-11-24T04:05:18Z build T-002 start (attempt 1)
Ruling: T-001/T-002 done on main thread (2-file CI edit; fork-PR merge needs gh + push), devs reserved for code tasks (T-001, T-002)
## 2026-11-24T04:06:20Z verify T-002 ok=true
Ruling: parallel via worktrees for wave 1 despite unset baseRef; origin/HEAD == HEAD (bbb0310) so a fresh-base worktree starts from current main; main thread pushes before each wave (wave 1)
## 2026-11-24T04:06:20Z build wave 1 start: T-004 T-005 T-010 (T-008 held: shares tests/test_cov_models.py with T-004)
## 2026-11-24T04:08:45Z verify T-005 ok=true
## 2026-11-24T04:08:45Z verify T-010 ok=true
Ruling: defer; #241 reply asks for a second diagnostics (video mode off) instead of a ptReal frame, the off frame is not derivable from the one aa05 sample (T-010)
## 2026-11-24T04:08:45Z build T-006 start (attempt 1)
