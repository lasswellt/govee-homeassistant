# Release reply queue

Replies to post after the next release, on threads that no released commit references with `#N`. Those get replies automatically. The daily-release workflow (`.github/workflows/daily-release.yml`) posts each entry below after it creates the release, then empties this list and commits that. On a night with no release, the entries wait.

Add one `##` section per thread:
- `close: yes|no|duplicate|declined`: close the thread after replying (`no` leaves it open; `duplicate` and `declined` close it as not planned). Only use `yes` when the reporter confirmed the fix or asked for it to be closed.
- `brief:` what the reply must say. The workflow writes the comment from the brief, citing the released version only where the brief asks for it.

<!-- entries below; the workflow removes them once posted -->

## #223
close: yes
brief: Thank holger-dunst and hanssepp123-hue. holger-dunst's retest on v2026.10.2 (clean remove and re-add) confirmed the H1252/H1232 zones work, so this is done. Cite v2026.10.2.

## #266
close: yes
brief: Thank bmcave for confirming on v2026.10.2 that probe 4 now updates (temperature replies for all four probes, limit reads included). Say this is resolved and the thread is closed.

## #258
close: yes
brief: Thank Gamlem for testing the Last measurement timestamp and the new entities across 17 H5179 devices and confirming they work. Say this is resolved and the thread is closed.

## #274
close: duplicate
brief: This is the same H7105 Tower Fan 2 problem as #209, so continue there. Explain that Govee's Developer API returns no device record for the H7105, so the integration has nothing to create. Thank archiepuneet and BradSimard for reporting.

## #209
close: no
brief: Explain that the H7105 is missing because Govee's Developer API returns no device for it, so there is nothing for the integration to create. A manual override is not possible without a capability schema. Ask for a diagnostics download (Settings > Devices > the device > Download diagnostics) and, if anyone can, a capture of the Govee app's fan commands. Thank BradSimard.

## #207
close: no
brief: The TimeoutError conversion shipped in v2026.09: api/client.py now catches TimeoutError and reports cannot_connect instead of Unknown error. Ask the reporter to confirm it behaves as expected or say whether the issue can be closed.

## #245
close: no
brief: The proposal shipped in v2026.10.1 as the option "Confirm light commands and resend dropped ones" (off by default, lights only). Thank BrawnyBravo for the design and branch, and ask whether anything is left to cover.

## #249
close: no
brief: Private vulnerability reporting is enabled and SECURITY.md links the advisory form. The held PRs #251, #252 and #256 were reviewed and merged tonight. Thank BrawnyBravo; any report is still welcome through the advisory form.

## #239
close: no
brief: Ask Peyto79 four questions: (1) does going from White Light to another scene work straight from the Govee app; (2) after White Light from HA, do colour and brightness changes from HA stay white; (3) does turning the strip off and on unstick it; (4) is the strip physically white after the failed switch to another scene.

## #244
close: no
brief: The H5054 is connected and reports dry. Ask Roachforlife to wet both probe contacts (not just tap the beeper) and to try a second sensor. If it still does not alarm, ask for a diagnostics download (Settings > Devices > the device > Download diagnostics).

## #281
close: no
brief: Govee's Platform API returns no DIY scenes for the H70B6 (the list comes back empty), so there is nothing to show in the selector. Ask Rockycjc whether the DIYs are uploaded GIFs or animations in the Govee app, and for a diagnostics download of the H70B6 (Settings > Devices > the device > Download diagnostics).

## #285
close: no
brief: Reply in English (the report was written in Danish). Ask for the Home Assistant version, the integration version, a diagnostics download of the H619E (Settings > Devices > the device > Download diagnostics), and whether Lightning-A and Lightning-B show up as two separate scenes in HA.

## #287
close: no
brief: Ask njdarrow for a diagnostics download of the H5107 (Settings > Devices > the device > Download diagnostics) and whether the Govee app is set to Fahrenheit or Celsius. Explain this decides whether it is the same fix #270 made for the H5105 and H5108.

## #288
close: no
brief: Tap-to-Run has no documented API, so the integration can only read the list, not run one. A capture of the Govee app's traffic while running a Tap-to-Run would be needed. Leave the thread open as a feature request.

## #289
close: no
brief: The integration already sends the discovery scan on every enabled interface HA's network settings list (IP_MULTICAST_IF per interface), and the device_id override does not depend on multicast. Ask stuartford to update from 2026.10.0, check Settings > System > Network has both adapters enabled, and send a diagnostics download (Settings > Devices > the device > Download diagnostics); its lan_discovery block shows the interface count.

## #201
close: no
brief: No reply in 45+ days since the fix in v2026.09. Say the thread will be closed in two weeks unless the problem is still there.

## #215
close: no
brief: Stale notice: no reply since September, when the requested data was asked for (Music Mode). Say the thread will be closed in two weeks unless the problem is still there; if so, send the requested data.

## #208
close: no
brief: Stale notice: waiting on the HCI snoop log for the H7026 segments 16-29 since the last exchange. Say the thread will be closed in two weeks unless the reporter still wants to follow it up and can send the log.

## #200
close: no
brief: The reporter said this can be closed. Say it will be closed in two weeks unless they want the H7124 air-quality states 2-4 followed up; if so, ask for a diagnostics download (Settings > Devices > the device > Download diagnostics).
