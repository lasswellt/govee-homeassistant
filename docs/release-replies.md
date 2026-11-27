# Release reply queue

Replies to post after the next release, on threads that no released commit references with `#N`. Those get replies automatically. The daily-release workflow (`.github/workflows/daily-release.yml`) posts each entry below after it creates the release, then empties this list and commits that. On a night with no release, the entries wait.

Add one `##` section per thread:
- `close: yes|no|duplicate|declined`: close the thread after replying (`no` leaves it open; `duplicate` and `declined` close it as not planned). Only use `yes` when the reporter confirmed the fix or asked for it to be closed.
- `brief:` what the reply must say. The workflow writes the comment from the brief, citing the released version only where the brief asks for it.

<!-- entries below; the workflow removes them once posted -->



## #310
close: no
brief: Thank ajw1980 for the hardware-verified frames and the local patch, which this follows. Say that in this release (cite the released version) the H7038's segments 16-30 are sent over AWS IoT in one frame with a 4-byte segment mask, the layout they confirmed, so govee.set_segment_color and the segment entities reach all 30 bulbs. This needs the Govee account sign-in (AWS IoT); with the API key alone, Govee's Platform API still accepts indices 15-29 and does nothing. Ask them to update and confirm that all 30 bulbs respond. Keep the issue open until they confirm.

## #208
close: no
brief: Follow-up for jjnuecht on the H7026. Explain that the H7038 (#310) turned out to need a 4-byte segment mask over AWS IoT to reach bulbs past 24, and that this release sends that layout for the H7038. Ask whether they can test the same frame on their H7026 with the govee.send_raw_ptreal action: frame "33 05 15 01 RR GG BB 00 00 00 00 00 m0 m1 m2 m3", where RR GG BB is the colour and m0-m3 is a little-endian bitmask of bulbs (for example "33 05 15 01 FF 00 00 00 00 00 00 00 00 00 01 00" for bulb 17 only, and "... 00 00 00 20" for bulb 30 only). If bulbs 16-29 change individually, we can raise the H7026's segment count from 16 to 30 and route it the same way. Keep the issue open.

## #241
close: no
brief: Thank Migz93 and the other H6099 owner for the diagnostics, which is what made this possible. Say that in this release (cite the released version) the H6099 gets a DreamView (video mode) switch: its state is read from the light's own status frame, ON sends the app's DreamView frame over AWS IoT, and OFF restores the last colour. It needs the Govee account sign-in. The ON frame comes from another model and is unverified on the H6099. Ask them to update and confirm three things: ON from Home Assistant enters video mode, switching the app from Video to Color turns the switch off, and OFF from Home Assistant restores the colour. If ON does nothing, ask for a diagnostics download taken right after pressing it. Keep the issue open until confirmed.

## #314
close: no
brief: Thank cwar36. Ask for the integration and Home Assistant versions, and a diagnostics download for the H61D3 (Settings > Devices & services > Govee > the device > Download diagnostics). Say this release adds each device's DIY scene fetch result to diagnostics (how many DIY scenes Govee's Platform API returned, or the error it hit), which shows whether the list comes back empty upstream. Also ask whether the scenes they made are under DIY or under Snapshots in the Govee app. Explain that the Refresh scenes button only refreshes an existing DIY selector and can't create a missing one; reloading the integration after the scenes exist can. Keep the issue open.

## #308
close: no
brief: Thank szoszo94 for confirming that firmware 1.00.49 fixes the reboot on the H60A6 itself, and Rballesteros for the report. Explain that since v2026.11.1 the integration sends the H60A6's power-off over the cloud instead of LAN, so the light stays off on 1.00.48 too; Govee rolls firmware out on its own schedule and we know of no manual update path. Also mention that this release (cite the released version) makes the H60A6's main and background light switches follow the light's own reports (PR #319). Ask muccijacopo to confirm whether the light now stays off. Keep the issue open until they confirm.

## #304
close: no
brief: Thank justcop for the thorough H607C capture (the sync-centre 33 60 02 frames for the Lighting Show and Carnival effects, their variants and sensitivity, and the 8-colour frames). Say the frames are now documented in the protocol reference (docs/govee-protocol-reference.md) as reporter-captured, so they are not lost, and that the integration does not control Music DreamView yet. Keep the issue open as the feature request for it.

## #315
close: no
brief: Thank alauver. Say the H6022 Table Lamp 2 is already supported (light, colour, colour temperature, segments, scenes, DIY scenes, snapshots and music mode), so if it is missing the cause is usually discovery. Ask for the integration and Home Assistant versions, whether the lamp is on the same Govee account as the Developer API key used in the integration, and a diagnostics download (Settings > Devices & services > Govee > three-dot menu > Download diagnostics). Keep the issue open.

## #318
close: duplicate
brief: Thank RafZie. Say this is the same Home Assistant Marketplace error as #295: Home Assistant's Marketplace still records the old LaggAt/hacs-govee repository as the owner of the govee directory, so it blocks the update. Point them to the workaround posted on #295 and to the upstream Home Assistant issue home-assistant/core#190454, where a thumbs-up helps. Say the thread is closed as a duplicate so the discussion stays in #295.

## #298
close: no
brief: Thank khilzinger for confirming v2026.11.1 with DIY scenes enabled and that only the H7087 is affected. Say this release (cite the released version) adds each device's DIY scene fetch result to diagnostics. Ask them to update and attach a diagnostics download for the H7087 (Settings > Devices & services > Govee > the device > Download diagnostics); it will show whether Govee's Platform API returns an empty DIY list for that model or the fetch fails. Also ask again whether the scenes are under DIY or under Snapshots in the Govee app. Keep the issue open.

## #299
close: no
brief: Thank khilzinger. Say that Govee's Platform API exposes DreamView only as on/off for most models, so the scenic and music DreamView modes from the app have to be added per model from the light's own frames (as is happening for the H6099 in #241 and is documented for the H607C in #304). Ask again which device model (SKU) they mean, and for a diagnostics download of it, so we can check whether it can be done for that model. Keep the issue open.

## #288
close: no
brief: Thank miller79 for the govee2mqtt analysis, which is clear and useful. Say the open question before planning this is whether Govee's Tap-to-Run list endpoint (GET https://app2.govee.com/bff-app/v1/exec-plat/home) accepts the account token this integration already has, since govee2mqtt uses a separate community login and adding a second login is a larger change. Ask whether anyone with Tap-to-Runs can test that request with the integration's account token, or share a redacted response. Say we will plan Tap-to-Run buttons once that is known. Keep the issue open as the feature request.

## #211
close: no
brief: Thank m4ary for PRs #320 and #321, and RuslanMelnychenko and everyone who posted the H60B0 frame table. Say that this release (cite the released version) adds a Light mode select for the H60B0 (Bottom or Ripple light, which the lamp never runs together; the side light runs with either) and a Ripple flow slider (0 stops the ripple, 100 is fastest), and the bottom and ripple light switches now follow each other. Ask H60B0 owners to report if the side light ever turns off when they switch between bottom and ripple. Separate colour and brightness per section stays open. Keep the issue open.
