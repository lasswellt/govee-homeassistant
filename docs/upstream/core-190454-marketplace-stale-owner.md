# Draft: Marketplace stale-owner block (home-assistant/core#190454)

Status: draft only. Nothing here has been pushed, forked, or posted upstream. The change is in `core-190454.patch` next to this file, made against `dev` of home-assistant/core. Apply it with `git apply core-190454.patch`.

## Proposed PR title

Marketplace: reconcile a stale same-domain record from the manifest on disk

## PR body

### Proposed change

`IntegrationRepository.async_pre_install` refuses to install an integration when another installed integration record has the same domain (`integration_owned`). That is right when the other repository really owns the files. It is wrong when the record is a leftover: the user installed the integration from a different repository (for example by hand, or with an earlier HACS), the Marketplace imported the old record, and the files in `custom_components/<domain>` now come from the repository the user is trying to install. The user is then stuck. The install is refused because of a record whose files are gone, and the only exit is deleting the files by hand.

This change checks the files on disk before it refuses. When another installed integration record shares the domain, the Marketplace reads `custom_components/<domain>/manifest.json` (in the executor, with the same `json_loads_object` call `_check_loadable_manifest` uses) and decides who the files belong to:

1. The manifest cannot be read, or its `documentation` or `issue_tracker` points at the other repository: raise `integration_owned`, as today.
2. Otherwise, if `documentation` or `issue_tracker` points at the repository being installed (`https://github.com/<owner>/<repo>`, case-insensitive, followed by `/`, `?`, `#` or the end of the string): the other record is stale.
3. Otherwise, if the manifest `version` equals the installed version this repository already has recorded, and differs from the other record's installed version: the other record is stale.
4. Anything else is ambiguous: raise `integration_owned`.

For a stale record the Marketplace marks it as not installed the way a deleted install is forgotten at startup (`installed`, `installed_version` and `installed_commit` cleared, the device removed, the change dispatched), without touching any file. It then saves the repository data, logs a warning naming both repositories, and carries on with the install.

No new user-facing string is added: the warning is a log line, and the `integration_owned` error keeps its text.

### Type of change

- [x] Bugfix (non-breaking change which fixes an issue)

### Additional information

- This PR fixes or closes issue: fixes #190454
- Integrations named in the issue as hitting this: `govee` (LaggAt/hacs-govee against lasswellt/govee-homeassistant), `bermuda`, `senec`, `miwifi`, `eon_next`, `sigen`, `ha_mcp_tools`, `alarmdotcom`, `climate_ip`.
- Example for the `govee` case. The manifest of lasswellt/govee-homeassistant has `domain: govee`, `documentation: https://github.com/lasswellt/govee-homeassistant/blob/main/README.md` and `issue_tracker: https://github.com/lasswellt/govee-homeassistant/issues`. A Marketplace record for LaggAt/hacs-govee still says it is installed for `govee`. Installing lasswellt/govee-homeassistant fails with "The govee directory belongs to LaggAt/hacs-govee" today. With this change the manifest on disk names lasswellt/govee-homeassistant, so the LaggAt record is cleared and the install goes ahead.

### Checklist

- [x] Tests added for the new behaviour
- [x] `ruff format`, `ruff check` and `mypy` (the component is in `.strict-typing`) clean on the component and its tests
- [x] No new dependency, no new translation string

## Design and rationale

- Where: in `async_pre_install`, the one place `integration_owned` is raised for a same-domain record. Every install path (GitHub API, release asset, catalog) already runs through it, so no other call site changes.
- Why the manifest: it is the only evidence on disk of which repository the files came from. Marketplace records say what was installed through the Marketplace at some point, not what is in the directory now.
- Why the other record is checked first: if the manifest names the other repository, that repository still owns the files, even when it also mentions the new one (a fork, a migration note). Refusing is the safe answer there.
- Why the URL match is anchored: `github.com/owner/repo` must be followed by a path separator, query, fragment or the end, so `owner/repo-fork` does not count as `owner/repo`. The scheme and host are matched too, so a URL that merely contains the string elsewhere does not count.
- Why the version rule exists: some manifests carry no GitHub URL. A matching version is weaker evidence than a URL, so it only applies when the new repository has an installed version recorded and the other record disagrees with the manifest.
- What is not touched: no file is removed or moved. The only change to the other record is that it is no longer marked installed, which is what the startup check already does when a directory is missing. If the other repository is wrongly cleared, installing it again restores the record.
- Locking: `async_pre_install` already runs inside the install lock and filesystem lock of the repository being installed. The other repository's install lock is not taken, as only its stored flags change.

## Alternative the core team may prefer

An explicit "stop tracking" action, for example a repair issue raised when a same-domain conflict is found, with a fix flow (or a button on the repository) that clears `installed`, `installed_version` and `installed_commit` for the stale record without deleting files. The user would confirm which record is stale instead of the Marketplace deciding from the manifest.

Trade-offs: it is more explicit and cannot clear a record by mistake, but it needs new strings, a repair flow and a second step for the user, and the install still fails the first time. This draft keeps to an automatic, evidence-based fix, and the same clearing code could be reused behind such an action.

## Risks

- A manifest can name the wrong repository: a fork that keeps the upstream `documentation` URL, or a repository whose manifest was never updated after a move. The first case is covered when the manifest names the other repository (refuse). The second can wrongly clear the other record. The effect is limited to one flag set, no files are lost, and the record comes back when that repository is installed again.
- The version rule can match by chance when two repositories ship the same version number. It requires the other record to disagree with the manifest, and the new repository to have a recorded installed version, which narrows it a lot. If the core team finds it too loose it can be dropped, and the URL rules alone cover the govee case.
- A manifest read happens only when a same-domain conflict exists, so the normal install path pays nothing extra.
- The warning logs full names of two public repositories and the domain. No other data is logged.

## Tests run

Environment: Python 3.14.9 venv (outside the repo), `dev` of home-assistant/core at the clone's HEAD, installed with `uv pip install -e . -r requirements_test.txt`, `-e packages/pytest-homeassistant`, `-r requirements_all.txt --no-deps` and `aiohasupervisor==0.11.0`. Translations generated with `python -m script.translations develop --all` (the tests need them).

Changed files: `homeassistant/components/marketplace/repositories/integration.py`, `tests/components/marketplace/test_repositories.py`.

New tests (in `test_repositories.py`, beside `test_integration_domain_owned_by_another_repository`):

- `test_integration_domain_owned_by_a_stale_record[documentation|issue_tracker|version]`: the stale record is cleared, the warning names both repositories, the repository data is saved, and `async_pre_install` returns.
- `test_integration_domain_owned_when_the_files_are_not_ours[names_the_other_repository|names_a_longer_repository|same_version_as_the_other|missing|unreadable]`: `integration_owned` is still raised and the other record is left installed.

Commands and results:

```
pytest tests/components/marketplace/test_repositories.py -k domain_owned -p no:cacheprovider -q -n0
9 passed, 266 deselected, 21 warnings in 1.01s

pytest tests/components/marketplace -p no:cacheprovider -q -n4
55 snapshots passed.
1206 passed, 983 warnings in 20.91s

ruff format homeassistant/components/marketplace tests/components/marketplace
76 files left unchanged
ruff check homeassistant/components/marketplace tests/components/marketplace
All checks passed!

mypy homeassistant/components/marketplace
Success: no issues found in 47 source files
```

Not run: the full Home Assistant test suite, `hassfest`, `pylint`, and `pre-commit`. Only the marketplace component and its tests were exercised.
