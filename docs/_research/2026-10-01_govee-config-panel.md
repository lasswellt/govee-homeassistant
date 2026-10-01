# Govee config panel (insteon-panel pattern)

- **Date**: 2026-10-01
- **Type**: Feature Investigation
- **Question**: Can the Govee integration ship a custom frontend panel like [pyinsteon/insteon-panel](https://github.com/pyinsteon/insteon-panel), and should it?

## Summary

It's technically feasible, but it isn't worth building. The HA APIs are standard: `panel_custom.async_register_panel`, `hass.http.async_register_static_paths` and `websocket_api.async_register_command`. A HACS integration can bundle prebuilt JS without a PyPI package, as Alarmo does. But Insteon needs a panel because its configuration (link tables, device properties) has no other UI in HA. Govee devices are configured in the Govee app, and HA only controls and reads them, so there is nothing a Govee user must configure that lacks a home in HA. The issue tracker confirms this (§4a): under 10% of recent issues concern transport or connectivity visibility, and the maintainer triages those from the diagnostics download, which already carries per-transport health. The recommendation is to skip the panel and ship three small changes inside the existing HA UI.

## Research questions

1. What does insteon-panel do and how does core register it?
2. Can a HACS integration ship a panel without a PyPI frontend package?
3. Can the panel reuse HA's built-in `ha-*` elements?
4. What would a Govee panel surface that entities and options flows can't?
5. What does it cost, and what are the cheaper alternatives?

## Findings

### 1. insteon-panel

- The source is TypeScript/LitElement under `src/`. It's built with rspack (plus gulp and rollup) and yarn. The output goes to `insteon_frontend/`, which is published to PyPI as `insteon-frontend-home-assistant`. Source: https://github.com/pyinsteon/insteon-panel
- The core backend is `homeassistant/components/insteon/api/` with `aldb.py`, `config.py`, `device.py`, `properties.py` and `scenes.py`: one websocket module per concern. Source: https://github.com/home-assistant/core/tree/dev/homeassistant/components/insteon/api
- The registration code was checked against core `dev`, `insteon/api/__init__.py:104-115`:
  ```python
  await hass.http.async_register_static_paths([StaticPathConfig(URL_BASE, path, cache_headers=not is_dev)])
  await panel_custom.async_register_panel(
      hass=hass, frontend_url_path=DOMAIN, webcomponent_name="insteon-frontend",
      config_panel_domain=DOMAIN, module_url=f"{URL_BASE}/entrypoint.{build_id}.js",
      embed_iframe=True, require_admin=True,
  )
  ```
  `config_panel_domain=DOMAIN` adds a "Configure" entry on the integration's page under Settings → Devices & services. That makes it a config panel, not just a sidebar item.

### 2. Shipping without PyPI (HACS)

- Alarmo bundles its Lit configuration panel inside the integration. It has also been hit by the API drift: `config_panel_domain` was an unexpected kwarg on older cores ([alarmo#891](https://github.com/nielsfaber/alarmo/issues/891)), and the `StaticPathConfig` import failed ([alarmo#988](https://github.com/nielsfaber/alarmo/issues/988)).
- The old `hass.http.register_static_path` is deprecated and blocking; only `async_register_static_paths` works on current cores ([hacs#3828](https://github.com/hacs/integration/issues/3828)). Our minimum HA version is already well past 2024.7, so the drift risk is low today.
- The HACS publishing rules neither require nor forbid committed build output (https://www.hacs.xyz/docs/publish/include). Committing the built bundle is accepted practice.
- `panel_custom` is the supported route for custom integrations; `frontend.async_register_built_in_panel` is for core panels (https://developers.home-assistant.io/docs/frontend/custom-ui/creating-custom-panels/).
- The manifest `dependencies` would gain `http`, `panel_custom` and `websocket_api`. hassfest flags imports of integrations that aren't declared.

### 3. Reusing HA elements

- `ha-data-table`, `hass-tabs-subpage-data-table` and similar elements are lazy-loaded. They only exist once some built-in panel has loaded them, so a custom panel that renders them earlier gets "custom element doesn't exist" ([community thread](https://community.home-assistant.io/t/custom-element-doesnt-exist-but-loads-a-few-seconds-later/266349)).
- There's also a scoped-registry polyfill race ([frontend#53890](https://github.com/home-assistant/frontend/issues/53890)), and HA offers no stable import surface for its internal components ([frontend discussion #11294](https://github.com/home-assistant/frontend/discussions/11294)).
- **Consequence**: the panel should depend only on `lit` and its own components, plus `hass` and the websocket connection passed in as properties. That is also why insteon uses `embed_iframe=True`: the iframe isolates it from the host's element registry.

### 4. What a Govee panel would surface

There is no panel, websocket or static-path code today. A grep for `websocket_api|register_panel|StaticPathConfig` in `custom_components/govee/*.py` finds nothing. The data is all reachable through `entry.runtime_data` (`custom_components/govee/__init__.py:259`).

| Gap | Data that already exists | What users see today |
|---|---|---|
| Transport health matrix (cloud / MQTT / LAN / BLE per device) | `TransportHealth` (`models/transport.py:20-31`): last success, send, read and failure timestamps, plus the failure reason. `coordinator.get_transport_health` at `coordinator.py:670` | One collapsed "active transport" sensor (`sensor.py:978-1013`), or the diagnostics download (`diagnostics.py:166-170`) |
| Request budget | Pacing math in `request_budget.py` | One options number (`config_flow.py:828`) and a `rate_limited` repair after the fact |
| Segment-count resolution | Parser → `size.max` clamp → `SKU_SEGMENT_OVERRIDES` (`models/device.py:1196-1236`) | Invisible, so mismatches arrive as issues (H7075, H7026 cap `565ca28`) |
| Per-device segment mode | `CONF_SEGMENT_MODE_BY_DEVICE` | An options sub-step that handles one device at a time |
| LAN targets | LAN discovery in `api/lan*.py` | Hand-typed IP list in a text field (`config_flow.py:777-796`) |
| BLE enrolment | `ble_advertisement.py` correlation, plus the verified-SKU gate (`:206`) and adapter gate (`:226`) | Silent; nobody can see why a device isn't on BLE |
| Raw protocol tool | `govee.send_raw_ptreal` (`services.py:181-258`, #208) | Developer Tools service call with a hex string |
| Scene cache | `SceneCacheManager` (`scene_cache.py:22-53`), 24 h TTL | Flat dropdown and a blind refresh button |

The earlier research docs `docs/_research/2026-06-05_diagnostics-per-device-connectivity.md` and `2026-04-09_multi-transport-single-entity.md` cover the same visibility gap. Recent transport fixes (`f996fac`, `89a5963`, `8e7de04`, `e480452`) were diagnosed from logs and diagnostics. Per-transport health is already in the per-device diagnostics (`diagnostics.py:166-170`, `:235`). Segment-count resolution is not.

### 4a. Demand check: issue tracker

`gh issue list --state all --limit 80` covers #93–#226, from 2026-06-03 to 2026-10-01. It was classified by hand from the titles:

- **The large majority** are device support or wrong values: a missing capability or SKU (#224, #200, #197, #135), °C/°F mislabels (#129, #171, #173), a misclassified entity (#124). A panel does nothing for these.
- **About 7** are transport or connectivity visibility (#226, #222, #214, #198, #195, #151, #164). A transport matrix would help, but the reader is the maintainer or a power user, and diagnostics already exports `transport_health` per device.
- **About 5** are segment counts (#223, #208, #160, #143, #104). Seeing where the count came from would speed up triage, but the fix is still a `SKU_SEGMENT_OVERRIDES` entry that needs an issue.
- **#202** (too many updates) shows that users want less churn, not more surface.

The device inspector would mostly benefit the maintainer, and the diagnostics download already serves that audience at no extra toolchain cost.

### 5. Costs and alternatives

- **A second toolchain.** It adds node, a bundler, a lockfile, a CI lane and a check that the committed bundle is up to date. The repo is Python-only today: tox, black, flake8, mypy strict and a 95% coverage floor.
- **Frontend API churn.** `panel_custom`'s kwargs and the static-path API have each broken Alarmo once. Each HA bump would need a smoke test.
- **Coverage.** JS isn't measured by `.coveragerc`. The websocket handlers are Python and must meet the floor and the `MockConfigEntry` test style.
- **Alternatives already in the architecture:** diagnostic sensors, repairs, the options flow, and the device-page diagnostics download. A Lovelace card skips panel registration but can't reach coordinator internals without a websocket API anyway. A standalone websocket API was considered as a middle step, but without a panel nothing in the HA UI uses it, and `diagnostics.py` reads the coordinator directly.

## Dissent / contradictory evidence

- The codebase analysis argued that a panel gives *live* and *cross-device* views that a per-device diagnostics download and per-device entities can't. That's true, but the audience for those views is the maintainer during triage, not users running their homes, and the issue sample (§4a) shows little user-facing demand.
- An earlier draft of this doc recommended a websocket API plus a read-mostly panel, and claimed transport health and budget "account for most of the recent support load". §4a contradicts that claim (under 10%). The recommendation was revised on 2026-10-01.
- On `embed_iframe`: `True` gives isolation but loses HA theming and dialogs. `False` gets a native look but runs into the lazy-load and registry races. This only matters if a panel is revisited.

## Recommendation

### Decision

**Don't build a panel or a standalone websocket API.** Make one change: add segment-count resolution to the per-device diagnostics. That means the parser's `elementRange` count, the `fields[].size.max` clamp, and any `SKU_SEGMENT_OVERRIDES` hit, with the resolved source (`models/device.py:1196-1236`, `diagnostics.py` around `:230`). Target: faster triage of #223, #208 and #160 style reports.

Two candidates were considered and rejected while planning on 2026-10-01:

- **LAN target picker:** rejected. `CONF_LAN_TARGETS` exists only for devices discovery *can't* reach, such as cross-VLAN IPs, subnets and `device_id=ip` overrides (#164; see the `lan_targets` data_description in `strings.json`). A picker built from discovery results would list exactly the devices that need no entry.
- **Transport-failure repair:** rejected. Offline devices already go unavailable through `state.online` (`entity.py:87`), so a repair would only catch devices that are online but whose readings have frozen (#222, #214). Those are Govee-side faults the user can't act on, which conflicts with the platinum `repair-issues` rule.

Revisit a panel only if the integration gains configuration that has no HA-native home, and only if issues show users asking for it.

### Rationale

- Insteon's panel exists because Insteon configuration has no other UI. Govee configuration lives in the Govee app, so a panel would mostly duplicate diagnostics for the maintainer.
- Under 10% of the sampled issues would be helped by the panel's MVP, and those are already triaged from diagnostics, which carries `transport_health` per device (§4a).
- A panel adds node tooling, a committed bundle, a frontend CI job, JS that `.coveragerc` can't see, and a smoke test on every HA release. That's a permanent cost for a screen most users never open.
- Segment resolution is the one maintainer-facing gap diagnostics doesn't already cover.

### Implementation sketch

- `models/device.py`: a read-only `segment_count_resolution` property returning `{"api_count", "size_max", "override", "effective", "source"}`, where `source` is `api`, `size_max` or `override`. `segment_count` keeps its behavior.
- `diagnostics.py`: add `"segment_resolution"` to the per-device record, or `None` for devices without a segment capability.
- Tests: the property's three source branches, plus the diagnostics key.

### Risks

- None material. It's an additive diagnostics key, and `segment_count` behavior is unchanged.

Next: `/blitz:build "add segment-count resolution (api count, size.max clamp, override, source) to per-device diagnostics"`
