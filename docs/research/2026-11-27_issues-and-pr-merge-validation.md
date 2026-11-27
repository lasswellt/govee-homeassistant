---
date: 2026-11-27
topic: issues-and-pr-merge-validation
type: Feature Investigation
status: complete
---

# Open issues and PR merge validation, 2026-11-27

## Summary

This is a delta on `docs/research/2026-11-26_issues-and-pr-merge-validation.md`. That plan (`docs/plans/open-issues/`, T-001..T-012) is fully built and on `main`. CI moved to Python 3.14 with the newest Home Assistant afterwards (`7ed0e10`). The next release window is 2026-11-28T02:00Z, which ships all of it as v2026.11.2.

Since that research:
- **0 open PRs** (`gh pr list --state open`). Nothing to validate for merge.
- **One new issue, #322:** reconfigure, reauth and verification-code steps call `async_update_reload_and_abort` while the entry has an update listener. This is the only code change found.
- **One new comment, #318:** a second user has the same Marketplace error ("i have the exact same problem", 2026-11-27T06:58Z). The queued duplicate-close reply already covers it.
- **#316 was closed by our own commit.** Plan commit `b39c86e` says "fix #316 and #317" in its body, and GitHub read "fix #316" as a closing keyword (the issue event names `lasswellt`, commit `b39c86e`, 2026-11-27T03:08:19Z). That breaks the CLAUDE.md rule to leave issues open until the reporter validates.
- **#317 was closed by its reporter** (`spenceh14`, 2026-11-25T22:30Z, no commit), before our fix. No action; the release bot still replies on the referenced thread.

## Research questions

1. Which issue and PR threads changed since the 2026-11-26 research, closed threads included?
2. Is #322's claim right that HA 2026.12 makes these flow steps fail?
3. Which fix removes the warning and still runs on HA 2024.11 (`hacs.json`)?
4. Does the fix risk a double reload, or a missed reload for credential changes?
5. Which tests change, and does `config_flow.py` stay at 100 % coverage?
6. What else needs doing before the release: thread state and replies?

## Findings

### Q1 Delta since 2026-11-26

- I swept the repo-wide comment feed (`issues/comments?since=2026-11-26T10:00:00Z`). It holds two comments:
  - #308 (szoszo94, 2026-11-26T12:31Z): already covered by the queued #308 reply.
  - #318 (sto-codes, 2026-11-27T06:58Z): a "+1".
- New issue: #322 (RavenX447l, 2026-11-27T03:51Z, 0 comments).
- PRs: #319, #320 and #321 merged on 2026-11-27 (03:14Z, 03:48Z, 03:41Z); #311, #312 and #313 merged at 03:08Z. No PR is open.

### Q2 Does HA 2026.12 break the steps?

**Not as of the beta, for a custom integration.**
- The warning comes from core PR #169198, merged to dev on 2026-05-07. The blog dates the deprecation from Core 2026.6. It calls `report_usage` in two places: `async_update_reload_and_abort` and `_abort_if_unique_id_configured` (when an existing entry's data changed). (web-researcher; https://github.com/home-assistant/core/pull/169198/files)
- **HA 2026.10.4 source** (`config_entries.py`, `async_update_reload_and_abort`) calls `report_usage("has an update listener and should use it for scheduling a reload", core_behavior=ReportBehavior.LOG, breaks_in_ha_version="2026.12.0", ...)` when `entry.update_listeners` is non-empty.
- **HA 2026.12.0b2 source** (PyPI wheel, same function) has `report_usage(..., core_integration_behavior=ReportBehavior.ERROR, custom_integration_behavior=ReportBehavior.LOG, ...)`.
  - The `breaks_in_ha_version` argument is gone.
  - Core integrations now get an error; custom integrations are still only logged.
  - The reload is still scheduled and the flow still aborts with the success reason.
- The same b2 file makes `ConfigSubentryFlow.async_update_reload_and_abort` raise `ValueError("Cannot update and reload entry with update listeners")`. The integration has no subentry flows (`grep -n Subentry custom_components/govee/config_flow.py` is empty).
- The blog post says the combination "will result in an error from 2026.12" (https://contributors.home-assistant.io/blog/2026/05/07/config-entry-listener-together-with-reloading-methods). That holds for core integrations in b2, not for custom ones; see Dissent.

### Q3 A fix that keeps HA 2024.11

Call sites (codebase-analyst, verified with `sed`): `config_flow.py:378` (`async_step_verification_code`, `data=new_data`), `:577` (`async_step_reauth_confirm`, `data_updates={CONF_API_KEY: ...}`) and `:711` (`async_step_reconfigure`, `data=new_data`). None passes `options`, `reason` or `reload_even_if_entry_is_unchanged`. The listener is registered at `__init__.py:363`.

Which helpers each HA version has (a `def` grep of `config_entries.py` in each wheel):

| Helper | HA 2024.11.0 | HA 2026.10.4 | HA 2026.12.0b2 |
|---|---|---|---|
| `ConfigFlow.async_update_and_abort` | **no** | yes | yes |
| `ConfigFlow.async_update_reload_and_abort` | yes | yes | yes |
| `ConfigEntries.async_schedule_reload` | yes | yes | yes |
| `OptionsFlowWithReload` | **no** | yes | yes |

- **#322's fix 2** (`async_update_and_abort` plus `async_schedule_reload`) fails on HA 2024.11 with `AttributeError`.
- **#322's fix 1** (`OptionsFlowWithReload`, drop the listener) needs `hacs.json` raised past 2025.8, which is a user-facing minimum bump. It also breaks `repairs.py:228`, where `RateLimitRepairFlow` writes `options` and its comment says "The entry's update listener reloads it when options change." That repair flow would then need its own reload.
- **Version-safe pattern:**
  ```python
  self.hass.config_entries.async_update_entry(entry, data=...)
  self.hass.config_entries.async_schedule_reload(entry.entry_id)
  return self.async_abort(reason=...)
  ```
  It uses only APIs that exist in all three versions. HA's report check is not on `async_update_entry` or `async_schedule_reload` (PR #169198 diff).
- **Reason strings.** HA's helper picks `reconfigure_successful` when `self.source == SOURCE_RECONFIGURE`, else `reauth_successful`, with `translation_domain=homeassistant`. `strings.json` `config.abort` already defines both: "Re-authentication was successful" and "Reconfiguration was successful". A plain `self.async_abort(reason=...)` therefore renders the same text.
- **Data updates.** `data_updates` merges into `entry.data` (`{**entry.data, **data_updates}`), and `data` replaces it.

### Q4 Reload count

- `_async_update_listener` (`__init__.py:716-733`) compares `coordinator.options_snapshot` with `dict(entry.options)` and returns early when they are equal. Data-only writes (token refresh #132, IoT credential caching at `config_flow.py:472/492`) never reload, and `tests/test_update_listener.py` pins that.
- So today the flow's explicit reload is the only reload for a credential change. Under the version-safe pattern, `async_update_entry` fires the listener, the listener sees unchanged options and returns, and `async_schedule_reload` does the single reload. That means **one reload, as today**.
- HA's helper defaults to `reload_even_if_entry_is_unchanged=True`, so an unconditional `async_schedule_reload` keeps behaviour parity.
- The blog's double-reload concern applies to a listener that reloads on every update. This listener filters on options (codebase-analyst Q6).

### Q5 Tests and coverage

- These tests patch the HA helper, so they must change:
  - `tests/test_config_flow.py:1060-1100` mocks `flow.async_update_reload_and_abort` and asserts on its call args.
  - `tests/test_reconfigure_credentials.py:128-133` patches `GoveeConfigFlow.async_update_reload_and_abort` to capture `data`. Note the memory: a filename containing "credential" is blocked by the pre-commit hook, so the maintainer commits it with `! git commit -F`.
- These should pass unchanged, because they assert the abort reason and `entry.data`: `tests/test_config_flow_manager.py:84-95` (`reauth_successful`), and `tests/test_config_flow_manager_steps.py:223`, `:268` and `:565` (`reconfigure_successful`).
- No test asserts that a reload happens after a flow. Add one per step in the flow-manager style (patch `hass.config_entries.async_schedule_reload`), plus a test that the warning text is absent from `caplog`.
- Coverage: one shared helper with a `self.source` branch adds two branches, both reached by the reconfigure and reauth tests. That keeps 100 %.

### Q6 Threads before the release

- **#316:** reopen it. It was closed by our commit, not by the reporter, and the fix is not released yet. The release bot replies on it anyway, because `89237ab`'s subject cites `(#316)`.
- **#318:** no change. The queued `close: duplicate` brief answers the thread, and the second user sees it too.
- **#322:** commit subjects that cite `(#322)` get the automatic post-release reply. Its content should say what this research found: 2026.12 b2 still only logs for custom integrations, and the release removes the warning anyway.
- **Process:** commit bodies must not contain GitHub closing keywords (`fix`, `fixes`, `close`, `resolve` followed by `#N`) for issues that should stay open.

## Dissent / Contradictory Evidence

- **"Error from 2026.12" vs the beta source.** The blog and #322 say these steps will fail in 2026.12. HA 2026.12.0b2 code logs for custom integrations (`custom_integration_behavior=ReportBehavior.LOG`) and errors only for core ones. A later beta or 2027.x could flip the custom behaviour, so the fix is still worth shipping. It is not a hotfix-grade regression, and it ships in the normal release.
- **The blog's preferred migration is to drop the listener** (web-researcher, library-docs). That is right in spirit, but here it costs the HA 2024.11 minimum and the repair flow's reload. The version-safe pattern meets the letter of the check and keeps the listener, which already prevents double reloads. If HA widens the check to `async_schedule_reload` with listeners, revisit option B together with a `hacs.json` bump.
- **HA 2026.12 release date** is inferred only: first Wednesday, 2026-12-02 (https://www.home-assistant.io/faq/release/). library-docs couldn't confirm it.

## Recommendation

### Decision

1. **Fix #322 with a version-safe helper** in `config_flow.py`:
   - Add `_update_entry_reload_and_abort(entry, *, data=None, data_updates=None)`. It writes the entry with `hass.config_entries.async_update_entry`, calls `hass.config_entries.async_schedule_reload(entry.entry_id)`, and returns `self.async_abort(reason="reconfigure_successful" if self.source == SOURCE_RECONFIGURE else "reauth_successful")`.
   - Use it at the three call sites. Keep the update listener and `hacs.json` unchanged.
   - Update the two tests that patch the HA helper, add reload and no-warning assertions, and keep `config_flow.py` at 100 %.
   - Ship it in tonight's release (02:00Z) if CI is green before then; otherwise it ships the next night.
2. **Reopen #316** (`gh issue reopen 316`), because it was closed by plan commit `b39c86e`'s "fix #316".
3. **No PR work:** none are open.
4. **#318:** no new reply; the queued duplicate-close covers both users.

### Rationale

- It removes the logged warning on HA 2026.6+ and pre-empts any later tightening.
- It doesn't move the HA minimum. HACS users on HA 2024.11 to 2025.7 keep working, and the APIs exist in all three versions.
- The listener's options-snapshot filter keeps it to exactly one reload, and data-only token writes still don't reload (#132).
- It is the smallest diff: one helper, three call sites, two test updates.

### Implementation sketch

- `custom_components/govee/config_flow.py`:
  - Import `SOURCE_RECONFIGURE` from `homeassistant.config_entries`; it exists in 2024.11.
  - Add the helper near `_sync_cached_creds`.
  - Replace the calls at `:378`, `:577` and `:711`.
  - Update the docstring mention at `:416`.
- `tests/test_config_flow.py:1060-1100`: mock `flow.hass.config_entries` and assert `async_update_entry(entry, data=...)`, `async_schedule_reload("test_entry_id")` and the abort reason.
- `tests/test_reconfigure_credentials.py:128-133`: capture `data` from `async_update_entry` instead of the HA helper.
- `tests/test_config_flow_manager.py` and `tests/test_config_flow_manager_steps.py`: in one reauth, one reconfigure and one verification-code test, patch `hass.config_entries.async_schedule_reload` and assert it is called once. Assert "has an update listener" is not in `caplog.text`.
- `custom_components/govee/quality_scale.yaml:106`: the text mentions `async_update_reload_and_abort`, so update it if it describes these steps.
- Commit subject: `fix(config_flow): schedule the reload ourselves after reauth and reconfigure (#322)`, with no closing keyword in the body.

## Risks

- **Abort reason drift.** HA's helper aborts with `translation_domain=homeassistant`; ours uses the integration's own strings. Both keys are present in `strings.json` and in `translations/ca.json`, `en.json` and `es.json` (checked 2026-11-27), so no string work is needed.
- **Reload timing.** `async_schedule_reload` is deferred, as it already was inside HA's helper. No behaviour change.
- **Pre-commit hook.** `tests/test_reconfigure_credentials.py` matches the `*credential*` block (memory). The maintainer must commit that file with `! git commit -F`, or the change must avoid touching it, for example by patching a lower-level call the test already captures.
- **Time.** About 3 h remain to 02:00Z. If CI isn't green by about 01:30Z, the fix lands for the next night.

## Sources

- https://github.com/home-assistant/core/pull/169198
- https://github.com/home-assistant/core/pull/169198/files
- https://contributors.home-assistant.io/blog/2026/05/07/config-entry-listener-together-with-reloading-methods (301 from developers.home-assistant.io)
- https://contributors.home-assistant.io/core/integrations/reauth-reconfigure/
- https://contributors.home-assistant.io/blog/2024/10/21/reauth-reconfigure-helpers/
- https://www.home-assistant.io/faq/release/
- PyPI wheels inspected locally: `homeassistant==2024.11.0`, `homeassistant==2026.12.0b2`; installed `homeassistant==2026.10.4` (`.cc-sessions/venv314`)
- GitHub: issue #322 body; issue events for #316 and #317; `issues/comments?since=2026-11-26T10:00:00Z`
