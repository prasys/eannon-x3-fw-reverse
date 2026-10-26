# Android Auto identity ("Rolls-Royce") and AA settings
**Status:** source found, fix designed, **nothing tested on the unit**. FINDINGS 8.2, 8.3; related: [wifi-ap-and-android-auto.md](wifi-ap-and-android-auto.md), [nvm-library-override.md](nvm-library-override.md), [usb-autorun-hook.md](usb-autorun-hook.md).

Evidence tags: *parsed* (file read), *strings*, *decompiler* (Ghidra 12.2 scratch export, not the `fw/gh` project), *assembly*, *runtime*, *inferred*.
Nothing here is *runtime*; the user's report that the phone shows "Rolls-Royce" is the only observation.

## Answer
- The car name is **data, not code**: `CarInfo.VehicleMake/Model/Year` in `androidauto_config.xml`, shipped as the Google SDK
  placeholder `Rolls-Royce / Phantom / 2016` (*parsed*). Head unit fields are `XXCompany / XXModel / XXOS / 0.1`.
- The file is on a **read-only squashfs** (`/application`, `init.platform.rc:5`, `mount squashfs … ro`), and the app module passes its
  path as a **hardcoded string**, so there is no existing writable override. Changing it needs either a bind mount over the file
  (no reflash, needs the USB hook each boot) or a spapp reflash. A library override in nvm cannot reach the module itself (see Delivery).
- Nothing is encoded. Search of the extracted trees (`fw/fs`: rootfs, spsdk, spapp, opt) for `rolls` / `royce` (case-insensitive ASCII) and
  UTF-16LE "Rolls-Royce": the only real hits are the 3 AA XMLs, `spapp/etc/autolink/autolink_config.xml` and
  `spsdk/etc/easyconnect/easyconnect_config.xml`; the UTF-16 search found nothing. `libQt5Gui/Widgets` match "Scroll…" only.
  `libavformat` matched `phantom` (not checked further; not related). Raw partitions (compressed squashfs) were not searched.

## Where the identity comes from (the chain)
| Step | Where | Evidence |
| --- | --- | --- |
| 1. Module tells the AA manager which config to use | `libandroidautomodule.so` `AndroidAutoModuleImpl::initAAMngr` @ Ghidra 0x2a2e4 (file off = addr − 0x10000). Calls `SPAndroidAutoManager` vtable `+0xc` with `/application/etc/Androidauto/androidauto_config.xml` (string at file 0x37954, 51 bytes); the result is only logged ("AA API return error!"), so a bad path is non-fatal | decompiler |
| 2. Same function also hardcodes vehicle id and cert time | `+0x18` ← `8d11fb57-c7cb-44c6-8599-19d763969437` (file 0x3791b); `+0x14` ← `2015-05-05 08:56:48` (file 0x37940); `+0x28(1)` ← `SetVideoResolutionHightPriority(true)` | decompiler |
| 3. Slot → API mapping | `libspandroidauto.so.2.2.20` registers actions in order SetCallback, SetConfigFile, SetClientCreds, SetCertificateTime, SetVehicleId, SetDisplayName, SetNightMode, SetDriverPosition, SetVideoResolutionHightPriority, SetBluetoothConfig (@ 0x22c44–0x22c8c). Slots 0xc/0x14/0x18/0x2c line up with the strings passed (path / time / uuid / "set bluetooth mac address:"), so the rest of the mapping follows | decompiler; the mapping of slots 0x1c–0x28 is inferred |
| 4. Path travels over IPC to `aa_manager_service` (`SetConfigFile`), whose own default is `/usr/local/etc/androidauto/androidauto_config.xml` | strings | strings |
| 5. `aa_accessory_service` parses the file at the start of a session | caller `FUN_0001365c` calls the parser `FUN_0012e410` (Ghidra base 0x8000) with the stored path (`param_2+0x3c`). Each `PropertyGet(key, buf, 0x80)` returns 0 on success; on **any miss** a built-in default is used. So the value buffer is **128 bytes max**, and an unreadable file silently gives defaults, not an error | decompiler |
| 6. Built-in defaults | `CarInfo.VehicleMake` → `XX_CAR_MAKE`, Model → `XX_CAR_MODEL`, Year → `2016`, HU make/model/build/version → `XX_HU_MAKE` / `XX_HU_Model` / `XX_OS` / `0.1` | decompiler |
| 7. Phone gets them in the AA service discovery | standard AA fields (make, model, year, vehicle id, head-unit make/model/sw build/sw version) | **inferred** from the Google receiver library; not decoded here |

Because step 5 is per session, an edited file should take effect on the **next AA connection without a reboot** (*inferred*; the parser is an action handler that allocates a new config each call).

## Other places an identity shows up
| Item | Source | Writable? |
| --- | --- | --- |
| AA car name, year, HU strings | `androidauto_config.xml` `CarInfo` (above) | no (ROM) |
| Vehicle id | hardcoded UUID in `libandroidautomodule` (same on every X3). `aacmd --setVehicleID` text says "random number generated at first boot", but the module overrides it | no |
| AOA identity (wired) | `spsdk/etc/aoa_feature/aoa_androidauto.xml`: `Android` / `Android Auto`, serial `0000000012345678`, mode 3, node `aoa-skel%d`. These are the strings the phone's AOA stack matches to enter AA mode | no; **do not change** (not shown to the user) |
| Wi-Fi AP SSID | `/media/flash/nvm/softap.conf` if present, else ROM `DEMO-WiFi-AP` ([wifi note](wifi-ap-and-android-auto.md)) | **yes** |
| Bluetooth name | not found in an AA/BT XML (`spsdk/etc/bluetooth/bluetooth_config.xml` and `btconfig.ini` have no name key); comes from the Setup UI / nvm | UI |
| AutoLink (`autolink_config.xml`) and EasyConnect | same Rolls-Royce / Phantom / 2016 placeholder in `VehicleInfo` / `CarInfo` | no |

**Wireless AA gate (decompiler, `AndroidAutoWirelessManager::getWifiInfo` @ 0x40eb8):** the module compares the AP SSID with the literal `DEMO-WiFi-AP` and logs "get ssid is default" instead of sending Wi-Fi credentials to the phone. So wireless AA only works after the SSID has been changed from the default. Don't revert `softap.conf` to the ROM SSID.

## Every AA setting we found
Parser keys are the strings at `aa_accessory_service` file 0x140e9e–0x141d9b (*strings*), defaults from the parser (*decompiler*). "Shipped" is the spapp XML. All of these live in the one ROM file today.

| Group | Keys | Shipped | Default if key missing |
| --- | --- | --- | --- |
| Identity | `CarInfo.VehicleMake/Model/Year`, `HeadUnitMake/Model/SwBuild/SwVersion` | Rolls-Royce / Phantom / 2016 / XXCompany / XXModel / XXOS / 0.1 | XX_… / 2016 / 0.1 |
| Phone-side video | `Display.DisplayWidth/DisplayHeight` (mm) , `PixelAspectRatioE4`, `ViewingDistance` | 154, 87, −1 (auto), 950 | 154, 87, −1, 400 |
| Resolution tiers | `Display.Resolution480P` / `720P` (`true`/`false`) + `.fps` (30 or 60), `.WidthMargin`, `.HeightMargin`, `.Density`, `.RealDensity` | both true, fps 30; 480P: margins 2/0, density 170/140; 720P: margins 256/120, density 213/133 | fps 30, density 160 (480P), 0 (720P) |
| Which tier wins | `SetVideoResolutionHightPriority(1)` hardcoded in `initAAMngr` | high priority | n/a (module code) |
| Head-unit window | `DisplayWindow.DisplayWidth/DisplayHeight/X_Coordinate/Y_Coordinate` | 1024×600 at 0,0 | 800×480 at 0,0 |
| Touch | `Input.TouchScreen` + `.ScreenType` (1 capacitive, 2 resistive), `.ScreenWidth/Height` | true, 2, 1024×600 | type 1, 800×480 |
| Keys | `Input.Key` + 24 `KEYCODE_*` | SEARCH, CALL, ENDCALL, MEDIA_NEXT/PREVIOUS/PLAY_PAUSE true; HOME, BACK, DPAD, ROTARY false | – |
| Sensors | `Sensor.SENSOR_*` (21 flags), `FuelType.*`, `EvConnectorType.*` | location, compass, speed, parking brake, night mode, driving status, accel, gyro, GPS satellites true | – |
| Audio channels | `Audio.MediaStream/NavStream/SystemStream` | true / true / false | – |
| Audio gain | `AudioGain.PgaLeft/PgaRight/OpLeft/OpRight` (floats) | **not in the shipped file** | 0.0, 0.0, const, const. Meaning (mic input vs output gain) **not determined** |
| Nav / media / notifications | `NavigationStatus` (+`MinIntervalMs`, `Height`, `Width`, `ColourDepthBits`, `InstrumentClusterType`), `MediaPlaybackStatus`, `GenericNotification`, `PhoneStatus` | NavStatus on 100×100, 32-bit, enum cluster; media status off; notifications off | colour depth 8, cluster 2 |
| Connection | `Connection.ProbeTimeout` (s) | 15 | 5 |
| Version gate | `ModuleInfo.*` (name `AndroidAuto`, 2.1.3); a mismatch logs "config release version mismatch" only | – | – |

Not in the XML (set elsewhere):
- **Driving side:** the module reads the Setup setting `SetupStrIdConst::setupRightDrive` (resolved with `tools/gotres.py`; attached handler @ 0x300b8) and calls vtable `+0x24` with 1 if it equals `setupOn`. The `else` call's argument was dropped by the decompiler, so "0 = left" is *inferred*. This is a user setting in the head-unit UI (stored in nvm), not an AA file. Stored by `DataSaveControl` in `/media/flash/nvm/Sunplus/SETUPMENU.ini`; the key is **`system.RightDrive`**, values `on`/`off` (*runtime*: present in `SETUPMENU.ini` of every 2026-10-25 USB capture, `on` in all of them, i.e. right-hand drive was set). Menu toggle confirmed (*runtime*, 2026-10-26): `system.RightDrive` `on` → `off` in `SETUPMENU.ini` between boots `cab688b1` and `d5335af5`, written before the reboot (only other change: the `S+AndroidAuto_APP` volume counter). Those two captures contain no AA session (only a BT RFCOMM connect/disconnect), so the driver position sent to the phone is still unobserved.
- **Night mode:** pushed at runtime through `AndroidAutoModuleImpl::setNightMode` (SPAndroidAuto vtable `+0x78`) from the head unit's state. `SENSOR_NIGHT_MODE` only advertises that it exists.
- **Wireless on/off:** `AndroidAutoModuleImpl::enableWirelessAndroidAuto` @ 0x30e58 and the smartphone-link setting (`exeSmartphoneSetting`, FINDINGS 8.3). Not an XML key. Not analysed further here.
- **Microphone:** no mic key in the XML (voice session callbacks exist in the module). Unknown.
- **Wi-Fi band/channel/HT:** `softap.conf` in nvm (writable; HT40 already verified).
- Debug helper `aacmd` (`spsdk/bin`): `--setConfigFile`, `--setVehicleID`, `--setDisplayName`, `--setDriverPosition`, `--setNightMode`, `--setBluetoothConfig`, `--AAstart/--AAstop`. **Untested**; it would be a second client of the same manager.

## What needs no reflash vs what needs a patch
| Change | No reflash? | How |
| --- | --- | --- |
| Wi-Fi SSID, channel, `ht_capab` | yes | `/media/flash/nvm/softap.conf` (already proven) |
| Driving side | yes | Setup UI |
| Any `androidauto_config.xml` value (make/model/year, resolution, margins, fps, sensors, keys, audio) | **yes, but only with the USB hook each boot** | bind mount of a writable copy over the ROM file (below) |
| Same, permanently with no stick | no | rebuild `spapp` (edit the XML; squashfs repack, ISP md5 + uImage CRC fixes, 1–4 KB slack) |
| Path, vehicle id, cert time, resolution priority, `DEMO-WiFi-AP` check | no | binary patch of `libandroidautomodule.so` in spapp |
| `aoa_androidauto.xml`, `hostapd` features | no | ROM; don't touch |

### Why the nvm library override doesn't help for the XML
- `LD_LIBRARY_PATH` starts with `/media/flash/nvm` for **every** process (exported in `on init`, `init.gui.rc:4`, *parsed*), so `aa_accessory_service` also searches nvm first for its `NEEDED` libs.
- But `libandroidautomodule.so` is **not** a `NEEDED` lib of `Launcher`. It is a Qt plugin found by `ExeSvcControl::loadModulePlugins` under `/application/lib/module` (strings in `libexesvc`: `/application/lib/module`, `/application/lib/activity`). A full-path `dlopen` does not use `LD_LIBRARY_PATH`. *inferred; the loader function wasn't decompiled.* So a patched module copy in nvm would most likely be ignored.
- The module's `NEEDED` libs *are* searched in nvm: `libspandroidauto.so.2`, `libapis.so.1`, `libusb_manager.so.2`, `libdevice_manager.so.2` (*parsed*). A patched `libspandroidauto.so.2` in nvm could rewrite the `SetConfigFile` argument (or the CarInfo path) for the whole Launcher process. Same caveat as the existing override: both a nvm and a ROM copy might be loaded. I did **not** locate the `SetConfigFile` wrapper body in that library (the client-side action builder is around Ghidra 0x21bbc / 0x22454), so there is no exact byte edit for this route yet.

## Recommended edit (no reflash): bind-mount an edited XML
1. Copy the shipped file (6296 bytes, CRLF, *parsed*) to the stick, change only the three values, keep everything else:
   ```
   <VehicleMake value="Rolls-Royce"/>   ->  <VehicleMake value="YOUR MAKE"/>      (file byte 285 in the spapp copy)
   <VehicleModel value="Phantom"/>      ->  <VehicleModel value="YOUR MODEL"/>    (byte 323)
   <VehicleYear value="2016"/>          ->  <VehicleYear value="YOUR YEAR"/>      (byte 358)
   ```
   Optionally set `HeadUnitMake/Model/SwBuild/SwVersion` (bytes 389/426/462/497). Values are limited to 127 characters; keep XML-safe characters. The three phone-visible fields are Make, Model, Year.
2. In the stick script (`gemn_auto.sh` hook), after nvm is mounted and before a phone connects:
   ```
   cp <stick>/aa/androidauto_config.xml /media/flash/nvm/androidauto_config.xml   # keep a copy; check free space (nvm needs ≥ 8 MB free)
   mount -o bind /media/flash/nvm/androidauto_config.xml /tmp/sp/application/etc/Androidauto/androidauto_config.xml
   ```
   `/application` is a symlink to `/tmp/sp/application/` (`rootfs/application`, *parsed*), so bind the resolved path. Log the mount result and `md5`/`wc -c` of both files.
3. Undo: `umount /tmp/sp/application/etc/Androidauto/androidauto_config.xml` (or just reboot without the stick). The ROM file is never written. Put a `mode` file like the other patches (`install`/`uninstall`) in front of it, and make the script refuse unless the ROM file's md5 is the known one (`1b4518224c422c23fdec24877972e978`, *parsed*, also `md5` of the spapp copy).
4. A copy of the file in nvm survives reboots, the mount does not. Without the stick the unit falls back to Rolls-Royce.

This route changes no binary, so the md5 gates of the ISP image are irrelevant.

## Alternative: permanent edit via spapp reflash
Edit `spapp/etc/Androidauto/androidauto_config.xml` in the squashfs and rebuild. Not attempted; ROADMAP says there is no reflash path yet. If a reflash route appears later, this is the smallest change (same-length replacement is **not** possible in a compressed squashfs; rebuild the image).

## Alternative: binary patch of the module path (reflash only)
`libandroidautomodule.so` file offset **0x37954**: 51-byte string `/application/etc/Androidauto/androidauto_config.xml`. A same-length-or-shorter writable path fits, e.g. `/media/flash/nvm/androidauto_config.xml` (39 bytes + NUL padding). This makes the file live in nvm permanently and editable without the stick, but the patched module must be in spapp (ROM), so it needs a reflash. If the spapp ever allows a module in nvm to win, this becomes a no-reflash patch. Same edit idea for the cert time (0x37940) and vehicle id (0x3791b). Untested.

## Risks
- **Phone caches cars by vehicle id** (*inferred* from how AA remembers head units, not verified here). The vehicle id is fixed in the module, so after changing make/model the phone may keep showing "Rolls-Royce" until the car is forgotten in the phone's AA settings and re-added. Removing the old entry on the phone is part of the test.
- A malformed XML makes `PropertyOpen` fail → the parser silently uses defaults (`XX_CAR_MAKE`, 800×480 window, touch 800×480, 480P defaults) → wrong video size and touch scaling. Keep the file well-formed and keep the shipped structure; validate on the host with a parser before copying.
- The bind mount hides the ROM file for **all** readers, including `aa_accessory_service` of other sessions. A stale mount after you replace the nvm file keeps the old inode only for processes that already opened it; remount if you change it.
- Changing `DisplayWindow`, touch or resolution values can break the video/touch mapping. Change identity first, display later.
- Don't change `aoa_androidauto.xml`; the phone matches on it to enter AA mode.
- `busybox mount` support for `-o bind` is *inferred* from a `bind` string in the binary, not confirmed on the unit.
- Keep `nvm` free space ≥ 8 MB (settings live there).

## Needs a runtime test on the unit
1. Is `mount -o bind` available and does it work over a squashfs file? (`mount | grep Androidauto`, `cat` the file through `/application/...`.)
2. After the bind, does the phone show the new name on the **next** AA connection without a reboot (wired and wireless)? Forget the car on the phone first, then connect again.
3. Which config is really used: look in logcat for `[AAS][ConfigParser]` lines (an open failure logs "open android auto config file fail"). Confirm the path in use is `/application/...` and not the `/usr/local/etc/androidauto` copy.
4. Does a mismatching/absent key fall back to `XX_*` as the decompiler suggests (try a copy with `VehicleMake` removed, in a throwaway test, with the undo ready)?
5. Does a vehicle-id change matter on the phone? (Only possible with a module patch; not planned.)
6. Optional: `aacmd --getDeviceList` / `--ver` as read-only probes of the manager. `--setConfigFile` was not tested and could race with the module's own call.
7. The INI key behind `setupRightDrive` and whether the else-branch driver position is left-hand (needs the setting toggled and AA reconnected; the phone shows lane/route side).

## Reproduce (host)
- Strings: `strings -a -t x spsdk/bin/aa_accessory_service` around 0x140e9e–0x141d9b; `strings -a -t x spapp/lib/module/libandroidautomodule.so | grep androidauto_config`.
- Ghidra scratch exports (not committed): imported `aa_accessory_service`, `libandroidautomodule.so`, `libspandroidauto.so.2.2.20` into a fresh project; `DecompileExport.java` needs its `max` argument raised (default 4000 cut `aa_accessory_service` at the first 4000 functions; the parser is above that). Annotated with `tools/annotate_strings.py <elf> <base> <export>` (bases 0x8000 / 0x10000 / 0x10000).
- Setting key: `python3 -I tools/gotres.py <libandroidautomodule.so> 10000 drvside:0x309e0:0x300f2:0x30a68,0x30a6c drvconst:0x309e0:0x300f2:0x30a70,0x30a6c` → `setupRightDrive`, `setupOn`.
