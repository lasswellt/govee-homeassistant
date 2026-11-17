# Release reply queue

Replies to post after the next release, on threads that no released commit references with `#N`. Those get replies automatically. The daily-release workflow (`.github/workflows/daily-release.yml`) posts each entry below after it creates the release, then empties this list and commits that. On a night with no release, the entries wait.

Add one `##` section per thread:
- `close: yes|no|duplicate|declined`: close the thread after replying (`no` leaves it open; `duplicate` and `declined` close it as not planned). Only use `yes` when the reporter confirmed the fix or asked for it to be closed.
- `brief:` what the reply must say. The workflow writes the comment from the brief, citing the released version only where the brief asks for it.

<!-- entries below; the workflow removes them once posted -->


## #284
close: yes
brief: Thank jhemak for confirming the kettle's deprecation warnings are gone on v2026.11.0. Say this is resolved and the thread is closed.

## #286
close: yes
brief: Thank jhemak for confirming on their H16B0 that the Music Mode switch now turns off with power-off and with a solid colour on v2026.11.0. Say this is resolved and the thread is closed.

## #285
close: no
brief: Thank Irmark40 for the details (HA 2026.10.4, integration 2026.10.2, Lightning-A and Lightning-B listed separately, both sent with scene id 136 and both showing Lightning-C on the H619E). Ask them to update to the latest release and attach the H619E diagnostics file they offered (Settings > Devices & services > Govee > the H619E > Download diagnostics). Explain that Govee's scene list carries a second identifier (paramId) next to the id, which we currently don't send; the diagnostics will show whether Lightning-A, B and C differ only in that value, which tells us whether sending it fixes this. Don't promise a fix yet.

## #296
close: no
brief: Thank Pauli29 for the detailed report and diagnostics. Explain that for the H60B3 Govee's Platform API returns an empty DIY list (the diyScene capability in their diagnostics has no options), and the integration can only show what that API returns, so there is no DIY select or DIY effect to create. Point out that "helles TV-Licht" appears in their diagnostics as a snapshot, not a DIY scene, so it is already available through the device's Snapshot select. DIY scenes that exist only in the Govee app account aren't reachable through the Platform API; reading them would need an undocumented app endpoint and a way to activate them, which we don't have yet. Keep the issue open to track that.

## #297
close: no
brief: Thank khilzinger for the suggestion. Explain that Govee's Platform API has no playlist or auto-run capability, and we don't know an app endpoint for reading or starting playlists, so the integration can't pull them in today. Suggest a Home Assistant script or automation that cycles the light's effects (scenes) on a timer as a workaround. Say that a capture of the Govee app's traffic while starting a playlist would be the starting point if anyone wants to help. Keep the issue open as a feature request.

## #298
close: no
brief: Thank khilzinger. Ask for the integration and Home Assistant versions, whether "DIY scenes" is enabled in the integration options, and a diagnostics download for the H7087 (Settings > Devices & services > Govee > the device > Download diagnostics). Explain that the integration lists the DIY scenes Govee's Platform API returns for the device; for some models that list is empty even though the app shows DIY scenes (see #296). Also ask whether the scenes they mean are under DIY or under Snapshots in the Govee app, since snapshots are offered through a separate Snapshot select.

## #299
close: no
brief: Thank khilzinger. Ask which device model (SKU) this is and for a diagnostics download of it. Explain that for most models Govee's Platform API only exposes DreamView as an on/off toggle, with no way to choose a scene; a few models (for example some TV backlights) also expose a movie-mode list, and if their device is one of them we can add a select for it.
