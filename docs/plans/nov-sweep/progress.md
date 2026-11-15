## 2026-11-15T01:24:39Z plan created
Ruling: class=architectural tasks=22; one-night sweep of 13 PRs + 8 code fixes + reply queue, superseding followups-2026-10-1 and issue-sweep-late-oct; #254 alone later
Ruling: user 2026-11-14 lifted the #249 hold for #251, #252, #256 (no advisory filed in 22 days); we resolve #251 ourselves
Ruling: user 2026-11-14 waived the own-day rule for tonight (all except #254); #271 gated on a hands-on try; sync-upstream.yml dropped in our merge commit
Research: docs/research/2026-11-14_nov-issue-pr-sweep.md
## 2026-11-15T01:27:42Z build T-001 start (attempt 1)
## 2026-11-15T01:28:13Z verify T-001 ok=true (cc1fe1a; Merge PR #270)
## 2026-11-15T01:28:15Z build T-002 start (attempt 1)
## 2026-11-15T01:30:20Z verify T-002 ok=false failed="pytest 4-file subset --cov-fail-under=100" tail="138 passed; config_flow.py 92.49%, lines 640-641,801,981-1034 covered only by other test files"
Ruling: spec-defect; T-002 verify[3] scoped coverage to 4 files that cannot reach 100 % of config_flow.py; merge d2ab131 is correct (full suite 100 %). T-023 re-verifies with the full suite; T-002's dependents are dispatched by id (T-002, round 0)
## 2026-11-15T01:30:30Z verify T-023 ok=true (d2ab131; full suite config_flow 100 %)
## 2026-11-15T01:30:30Z build T-003 start (attempt 1)
## 2026-11-15T01:40:34Z verify T-003 ok=true (49523a4; Merge PR #251; full suite 4359)
Ruling: spec-defect; side-agent review: GoveeLoginRejectedError covers every non-200 answer (outages, 429), so latching on it would leave MQTT off until Reconfigure after one Govee hiccup. Latch narrowed to 2FA-required / GoveeAuthError / 2FA-code-invalid; rejections back off 15 min doubling to 6 h (T-003, round 1)
Ruling: defer; pre-commit-validate.sh blocks any staged file named *credential* (tests/test_reconfigure_credentials.py from #251); maintainer committed the merge by hand (T-003, round 0)
