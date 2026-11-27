---
result: CONDITIONAL
ts: 2026-11-27T23:47:27Z
ref: cf870fb9b346136d89846da491bd50f9dcda6b18
scope: plan
plan: open-issues
---

# Check: open-issues

**Result:** CONDITIONAL · **Base:** v2026.11.1 · **Files:** 39 (custom_components + tests) · **LOC:** +1342 / −81

## Summary

The plan covers 14 tasks: Dependabot bumps, #310 4-byte segment mask, #241 H6099 video mode, DIY diagnostics, protocol doc, community PRs #319/#320/#321, #316, #317, #322 and replies.
- Gates, every task's checks, the ratchet and the reject critic (LGTM, 14/14 held-out) are clean.
- No blocker. CONDITIONAL because three items need a human:
  1. Hardware confirmation of H6099 ON and H7038 bulbs 16–30 by the reporters after the release.
  2. T-001..T-012 `verify[]` names the removed `.cc-sessions/venv312`, so `tasks.sh verify` can't reproduce them. Their status rests on a substituted re-run.
  3. `startup-validate --strict` flags two `.cc-sessions` cache files outside the plan.

## Gates

| Gate | Before fix | After fix | Status |
|---|---|---|---|
| typecheck (mypy strict, Python 3.14 + HA 2026.10.4) | 0 errors | 0 errors | PASS |
| lint (flake8, black --check 26.5.1) | 0 errors, 0 warnings | same | PASS |
| tests (full, pytest) | 4809/4810 passed (1 skip), cov 99.69 %, config_flow.py 100 % | n/a | PASS |
| build | no build step (HA custom integration) | n/a | SKIPPED |
| CI on `main` HEAD `cf870fb` | Tests, Type Checking, Style check, HACS/HASS: success | n/a | PASS |
| pyright lane (`python-pyright` toolchain row) | not the repo gate; CI and the ratchet use mypy | n/a | SKIPPED |

## Tasks (plan scope)

T-001..T-012 were re-run with `venv312` replaced by `venv314` (`reverify.sh`). `tasks.sh verify` was not run on them, because it would write `passes=false` over the missing path. T-013 and T-014 passed `tasks.sh verify` at HEAD.

| Task | passes | re-run ok | failed | tail |
|---|---|---|---|---|
| T-001 | true | 4/4 | none | none |
| T-002 | true | 5/5 | none | none |
| T-003 | true | 5/5 | none | none |
| T-004 | true | 5/5 | none | none |
| T-005 | true | 4/4 | none | none |
| T-006 | true | 2/2 | none | none |
| T-007 | true | 3/3 | none | CI check went rc=1 while Tests ran on `cf870fb`, then passed |
| T-008 | true | 4/4 | none | none |
| T-009 | true | 5/5 | none | none |
| T-010 | true | 4/4 | none | none |
| T-011 | true | 3/3 | none | none |
| T-012 | true | 3/3 | none | same as T-007 |
| T-013 | true | `tasks.sh verify` 6/6 | none | none |
| T-014 | true | `tasks.sh verify` 4/4 | none | none |

The test diff has +111 asserts and −5. All 5 deleted asserts were replaced one-for-one by the 4-byte-mask goldens (T-002). No skips were added and no trivial asserts.

## Held-out checks (critic-authored, plan scope)

| Task | ok | Command | Tail |
|---|---|---|---|
| T-001 | true | `(#N)` commit for each of #311/#312/#313/#292/#291; `yaml.safe_load` on daily-release.yml and hacs-hass.yaml | yaml ok |
| T-002 | true | colour and colour-temp packets equal the old 3-byte packets for masks 0x1, 0xFFFF, 0x10000, 0x1FFFF, 0xFFFFFF; a 30-bulb mask ends `ff ff ff 3f` | H7038 frame `…ffffff3f…e2` |
| T-003 | true | H6099 in all three DreamView sets, and the sets are used outside const.py | const ok |
| T-004 | true | `_diy_fetch_results` store and accessor in scene_cache.py; no-DIY log in select.py | :82, :98, select.py:192 |
| T-005 | true | protocol reference names H6099, H7038, #241 | doc-ok |
| T-006 | true | each of the 6 reply sections has ≥2 body lines | bodies-ok |
| T-007 | true | clean tree; `compileall custom_components/govee` | clean-compiles |
| T-008 | true | `build_ripple_flow_ptreal` clamps 150→100 and −5→0; f(10) frame; SKU sets | ripple ok |
| T-009 | true | `_count_mqtt_status_query`: offline +0, unknown +0, online ×2 +2; publish count = count count | ok |
| T-010 | true | FAHRENHEIT_REPORTING_SKUS used outside const.py; H5074 member | ok |
| T-011 | true | #308 reply thanks the second owner; #318 cites the duplicate target | replies-ok |
| T-012 | true | lines added by T-013 (config_flow.py 411–431) are covered | not in Missing |
| T-013 | true | helper on a mock: one update, one scheduled reload, never `async_reload`, reason by source; no `async_update_reload_and_abort(` anywhere | helper ok |
| T-014 | true | `ast.parse(feature_version=(3,12))` on every integration file; no `OptionsFlowWithReload` | py3.12 grammar ok |

## Cannot verify (survey)

| What | Needs | Resolution |
|---|---|---|
| `ConfigEntries.async_schedule_reload` exists on the HA minimum | HA 2024.11 source | Resolved: present in the `homeassistant==2024.11.0` wheel (research 2026-11-27 §Q3) |
| H6099 DreamView ON frame enters video mode; H7038 bulbs 16–30 take colour | hardware, reporter | Open by design (spec §Verification). Reporters on #241 and #310 confirm after the 2026-11-28T02:00Z release |

## Findings

### Critical (blocks)

LGTM

### Major

LGTM

### Minor

| # | Where | Finding | Source | Confidence |
|---|---|---|---|---|
| 1 | docs/plans/open-issues/tasks.json T-001..T-012 | 🔵 `verify[]` names the removed `.cc-sessions/venv312`; `tasks.sh verify` fails on the path, not the code. Accepted as historical, see Ruling. | critic | 1.0 |
| 2 | .cc-sessions/cov.json, .cc-sessions/typecheck-baseline.json | 🔵 `startup-validate --strict` exit 2 on a schema check (no `session_id`/`status`). No injection marker. One is a stale coverage dump from 2026-11-14; the other is owned by the post-edit typecheck hook. | sec posture | 1.0 |

### Info

| # | Where | Finding | Source | Confidence |
|---|---|---|---|---|
| 1 | custom_components/govee/select.py:192 | "no DIY scenes" logs at DEBUG. That matches the CLAUDE.md logging rule (info only for user action). Diagnostics carry the count. | critic | 0.75 |
| 2 | custom_components/govee/api/ble_packet.py:228 | the brightness builder keeps a 3-byte mask, as planned. It is used only for light-zone SKUs with a 16-bit ring mask. | survey:backend | 0.8 |
| 3 | custom_components/govee/select.py:1105 | a multi-toggle light mode could be half-applied if a later toggle fails. Latent: each H60B0 mode has one toggle. | survey:patterns | 0.3 (suppressed below `--min-confidence high`) |
| 4 | setup.cfg:28 | mypy `python_version = 3.14` while hacs.json allows HA 2024.11 (Python 3.12). The style job's 3.12 `compileall` and the held-out grammar check cover syntax, not stdlib API use. | critic | 0.5 |

## Auto-fix (`--fix`)

| Category | Found | Fixed | Remaining | Skipped |
|---|---|---|---|---|
| Imports / exports | 0 | 0 | 0 | 0 |
| Type errors | 0 | 0 | 0 | 0 |
| Lint | 0 | 0 | 0 | 0 |
| Framework (F5/V3/P2) | n/a (python stack) | 0 | 0 | 0 |
| Naming / return types | 0 | 0 | 0 | 0 |
| Unused | 0 | 0 | 0 | 0 |

No fix commits. Minor #1 is not auto-fixable: `tasks.sh set` can't edit `verify[]`, and `tasks.json` is hook-guarded.

## Ratchet

| Metric | Baseline | Current | Threshold | Δ | Action |
|---|---|---|---|---|---|
| type_errors (mypy) | 0 | 0 | 0 (absolute) | 0 | none |
| test_count (pytest --co) | 3514 | 4810 | 4810 | ↑ tightened | history appended |
| as_any_count (`type: ignore`) | 10 | 7 | 7 | ↓ tightened | history appended |
| lint_violations (flake8) | 0 | 0 | 0 | 0 | none |
| mocks_in_src | 0 | 0 | 0 | 0 | none |
| todo_count | 0 | 0 | 0 | 0 | none |
| stale_worktree_branch_count | 0 | 0 | 0 | 0 | none |

## Critic

`--mode reject` verdict: LGTM · mode: in-Claude (opus, high; the first spawn hit its turn limit with no reply, the one re-spawn completed) · findings: 5 advisory, 0 reject-authority confirmed · held_out: 14/14 ok

## Security posture

registry-validate: ok · startup-validate --strict: non-zero (2 schema findings on `.cc-sessions` cache files, no injection) · pre-trust execution: none · survey:security CLEAN (credentials written only after validation, diagnostics redacted, no secret logging)

## Automation coverage

Deterministic gates: 8/8 run (mypy, flake8, black, pytest+coverage, CI on HEAD, registry rows, ratchet, critic) plus build skipped (none exists) · e2e: skipped_unavailable (no rendered surface; hardware outcomes are reporter-confirmed)
Not auto-verified (human owns): architectural fit, UX correctness, business-logic intent, regressions in untested paths, device behaviour on real hardware.
**Recommendation:** needs-human-review (hardware validation pending; already merged and shipping in the 2026-11-28T02:00Z daily release)

## Before merge

1. Nothing blocks. All work is on `main` with CI green, and the 02:00Z run ships it.

## Later

1. Read the reporters' hardware results on #241 (H6099 ON/OFF, app Video→Color flips the switch) and #310 (all 30 H7038 bulbs) after the release, and keep the issues open until they confirm.
2. Optional: move `.cc-sessions/cov.json` (stale coverage dump) to `.cc-sessions/quarantine/` so `startup-validate --strict` reports only real findings.
3. Future plans: name the venv through one path that survives an interpreter change (e.g. `.cc-sessions/venv/bin`) so verify[] stays reproducible.
