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
## 2026-11-24T04:14:51Z verify T-004 ok=true
Ruling: T-004 new-HA path checked by hand on HA 2026.11.2 (scratchpad venv): supports_via_device_id()=True, leak DeviceInfo dataclass carries via_device_id, via_device UNDEFINED; pytest cannot load there (phcc 0.13.375 pins 2026.10.4, httpx alias RuntimeError) (T-004)
## 2026-11-24T04:15:03Z build T-008 start (attempt 1)
## 2026-11-24T04:16:06Z verify T-006 ok=false failed="review" tail="REST map applied whenever online; a poll without a numeric brightness turns the 100 default into 35"
## 2026-11-24T04:19:05Z verify T-006 ok=true (round 1: REST map gated on raw poll brightness)
## 2026-11-24T04:19:05Z build T-007 start (attempt 1)
## 2026-11-24T04:20:04Z verify T-008 ok=true
## 2026-11-24T04:20:04Z build T-009 start (attempt 1)
Ruling: scope+select.py,light.py for T-009 to swap T-008 getattr(state, "active_scene_param_id") for the attribute (T-009)
## 2026-11-24T04:21:46Z verify T-007 ok=true
## 2026-11-24T04:24:04Z verify T-009 ok=true (concern: added last_scene_param_id; out-of-scope 4-line test edit in test_cov_coordinator_events.py accepted as Tier 1)
## 2026-11-24T04:24:04Z build T-011 start (attempt 1)
## 2026-11-24T04:30:41Z verify T-011 ok=true (4721 tests, 99.73%; brightness.py guard test added; six CI checks green on feb2e31; HA 2026.11.2 hand check recorded under T-004)
Ruling: defer; T-012/T-013 wait for the v2026.11.1 release at 2026-11-25T02:00Z (T-012 verify[0]); resume with /blitz:build late-nov-sweep after it exists (T-012)
