# Automatic DST / region-timezone (exploration)
**Status:** static analysis only, nothing run on the unit. Exploration note, not a delivered patch.
Evidence tags: *parsed / strings / decompiler / assembly / runtime / inferred*. Addresses are Ghidra vaddrs of the
prelinked libs (`libinfomodule` 0x41eb0000, `libapis` 0x41d10000, see CLAUDE.md gotchas).

## Short answer
- **No tzdata anywhere, and nobody sets `TZ`.** The unit runs as UTC; the app treats the kernel clock as the local
  wall clock. There is **no timezone/region-for-time setting** in the UI.
- **glibc can still honour a POSIX `TZ` string with DST rules without any files** (the string parser is present), and Qt
  uses `localtime_r`/`mktime`. So `TZ=EST5EDT,M3.2.0,M11.1.0` would be honoured *if it reached the Launcher's environment*.
- **But `TZ` alone does not give automatic DST**, because the MCU owns the RTC and re-pushes its own `h:m` into the SoC
  (below). The MCU clock is not DST-aware, so the next MCU time message would undo the hour jump.
- There is **no persistent, no-code way to get `TZ` into the Launcher**: the service env lives in `init.rc` on the
  read-only rootfs; the USB hook only runs at plug-in; `/etc` is a tmpfs copy rebuilt every boot.
- Recommended minimal path: a **small libinfomodule patch (via the existing nvm override) that steps the clock ±1 h at the
  DST boundaries and pushes the result to the MCU**, with the rule chosen by a config file in nvm (v1) and later by the
  existing *radio area* setting (v2). Details and risks below.

## 1. What the rootfs has
- `find` over `fw/fs/{rootfs,spsdk,spapp,opt}` for `*zoneinfo*`, `*tzdata*`, `localtime`, `tz*`, `*timezone*`, `*.tz`:
  **no hits**. `rootfs/etc` has no `localtime` or `TZ`; `rootfs/usr/share` does not exist. *parsed (file listing)*
- No `TZ` export in `init.environ.rc`, `init.gui.rc`, `init.rc` (only `setenv SDK_*`/`APPLE_AUTH_CACHE_PATH`), `etc/profile`,
  `etc/init.d/rcS`, `sys_setup.sh`. *strings (grep)*
- `libc-2.18.so` strings include `/etc/localtime`, `TZDIR`, `/usr/share/zoneinfo`, `TZif`, `posixrules`, `tzfile.c`, and the
  POSIX rule format `M%hu.%hu.%hu%n`. *strings*. That the `M` rule parser works with no zone file is from my knowledge
  of glibc, *inferred, test on unit*.
- `busybox` has applets `date`, `hwclock`, `rtcwake` and strings `/dev/rtc`, `/dev/rtc0`, `/etc/localtime`, `/usr/share/zoneinfo/`.
  *strings*. Nothing in the init scripts calls `hwclock`. *strings (grep)*
- `libQt5Core.so.5.3.2` imports `tzset`, `localtime_r`, `mktime` and has `QTimeZone` symbols. *strings*
  `QDateTime::currentDateTime()` is the clock source for the UI (libcomviews at export lines 1480, 12822, 12831; SetupTime
  page imports `QDateTime::currentDateTime`). *decompiler/strings*

## 2. How the clock actually works (MCU owns it)
```
MCU (JieLi, RTC)  --UART 0x36 (h,m), 0x3d (y,m,d)-->  mcusrv  --binder-->  MCUControlPrivate::updateSystemTime/Date
                                                                           (libapis 0x41d5f4e2 / 0x41d5f51e)
   -> McuCallback::updateSystemTime/Date (libinfomodule 0x41ef35c4 / 0x41ef35ca)
   -> PLT stub 0x41ee540c / 0x41ee4964 -> GOT 0x41f39120 / 0x41f38db4 -> SetupExec::setSystemTime / setSystemDate
   -> QDateTime::currentDateTime(); setTime/setDate(MCU value); stime(toTime_t())
```
- `MCUControlPrivate::updateSystemTime(uint)`: `QTime::setHMS((p&0xffff)>>8, p&0xff, 0)`; `updateSystemDate(uint)`:
  year `=((p&0xffffff)>>16)+2000`, month `=(p&0xffff)>>8` (the day argument is not visible in the decompile). *decompiler*
- PLT stubs hand-decoded (`add ip,pc,#..; add ip,ip,#0x53000/0x54000; ldr pc,[ip,#0xd0c/0x448]!`), slots resolved with
  `objdump -R`. *assembly*
- `SetupExec::setSystemTime` @0x41eeb94c, `setSystemDate` @0x41eeba04, `setSystemDateTime` @0x41eeb7f0 all end in
  `stime()` on a **local-spec** `QDateTime::toTime_t()`. Only `setSystemDateTime` (the Setup screen path) also calls
  `MCUControl::send_mcu_time/send_mcu_date` to write the MCU RTC. *decompiler*
- The Setup "time" page (`SetupTime`, `SetupTimePage` in `libsetupviews.so.1.0.0`) edits year/month/day/hour/min/sec and
  `SetupTime::setDateTime(time_e,int)`. No timezone, DST or region widget in its symbols. *strings*
- The SoC sends `send_mcu_sync` every **8000 ms** (`McuSyncTimer` ctor @0x41d5fb44: `mov.w r1,#0x1f40; QTimer::start`;
  slot @0x41d5fbf0 calls `send_mcu_sync`). *assembly/decompiler*.
  **Unverified:** whether the MCU answers a sync with a time frame, or sends 0x36/0x3d on its own cadence. This decides
  how fast an MCU message would revert a clock change made only on the SoC.
- `setSystemInitDateTime` @0x41eeb8e4 sets 2022-01-01 00:00:00 (`0x7e6`) and is called from the reset branch of
  `loadSystemSettings` (libinfomodule.c:10893); patch B already skips it on MCU reset (FINDINGS 5/11).

## 3. Why `TZ` alone is not enough
With `TZ` set to a DST zone, `setSystemTime` still does "current local date + MCU `h:m`" -> `stime`. At the DST
instant the SoC local time jumps +1 h, the next MCU time frame carries the MCU RTC's un-shifted `h:m` and the SoC
snaps back. At every cold start the MCU value wins again. To work, MCU time would have to be interpreted as *standard*
time (UTC-like) in `setSystemTime/Date` and the Setup screen and the display would have to convert. That touches
three setters plus the Setup page. *inferred from the code above*

## 4. Ways to get a TZ / DST rule in, ranked
| # | Approach | Persistent? | Verdict |
| - | --- | --- | --- |
| A | `setenv TZ` on the `Launcher` service in `init.rc` | yes | rootfs repack (md5/uImage CRC, FINDINGS 4); no reflash path yet. **Not now.** And does not fix the MCU override. |
| B | USB hook writes `/etc/localtime` (TZif) or exports TZ | no (tmpfs `/etc`, hook runs at plug-in only) | Only useful as a probe. Also glibc `localtime_r` initialises TZ once per process (*inferred*), so a running Launcher would not notice; needs a restart. |
| C | **libinfomodule override: SoC steps the clock ±1 h at DST boundaries and writes it to the MCU** | yes (MCU RTC keeps the shifted time; flag in nvm) | **Recommended.** No `TZ`, no tzdata. |
| D | `setenv`+`tzset` early in the patched libinfomodule plus MCU-as-standard-time conversion | yes | More invasive than C (section 3). Revisit only if C works and a real zone model is wanted. |

### Approach C in more detail (proposal, not built)
1. **Rule source (v1, no UI):** a tiny file `/media/flash/nvm/dst.conf` containing `off`, `us` or `eu`, written by a
   USB script with a log and an undo (same style as `wifi40`). Rules: US = second Sunday of March 02:00 -> first Sunday of
   November 02:00 (local); EU = last Sunday of March 01:00 UTC -> last Sunday of October 01:00 UTC (the transition is
   at a fixed UTC moment, the MCU clock is local, so store the local hour per rule). Southern-hemisphere zones are
   not covered by v1.
2. **State:** one persisted bit `dst_applied` (nvm, next to the Setup settings, or a one-byte file `dst.state`).
3. **Trigger:** a periodic check (hook into the existing 8 s `McuSyncTimer::updateTimerSlot` path or `SetupExec::mcuInit`
   at 0x41ee6650+, called once at boot, plus a `QTimer`). On each check compute `expected = isDST(local now)`. If
   `expected != dst_applied`: build `now ± 1 h`, call `SetupExec::setSystemDateTime()` (it already does `stime` + `send_mcu_time` +
   `send_mcu_date`), then store `dst_applied = expected`. A unit that was off over the boundary is corrected at the next boot.
4. **User sets the clock by hand:** the Setup path should also recompute and store `dst_applied = isDST(new time)`, otherwise
   the next check shifts the user's value by an hour. That is one extra call in the Setup path.
5. **Region choice in the UI (v2):** reuse the existing radio area setting (`SetupExec::systemRadioArea` @0x41ee7278,
   `exec_radio_area` @0x41eebabc; enum strings China, Europe, Russia, Australia, MiddleEast, Japan, USA1, USA2 *strings*;
   GOT slot of the key symbol `SetupStrIdConst::setupRadioArea` = 0x41f394bc *assembly*; the INI key string itself is **not
   resolved**, needs a runtime capture per CLAUDE.md). Map USA1/USA2 -> `us`, Europe -> `eu`, others -> `off`.
   Coupling DST to the tuner band is a hack: a US-market owner who picked Europe for the radio would get EU rules. A
   dedicated "DST: Off/US/EU" row means a new SetupItem plus `SetupStrIdConst` entry plus string tables
   (`ui-string-tables.md`), which is considerably more work than anything done so far.

## 5. Open lead: CarPlay
`libcarplaymodule.so` contains the strings `seconds:`, `timeZone`, `daylight` next to `deviceName`/`language` (file offsets
0x47307-0x47368, *strings*). CarPlay/AirPlay time sync (`AirPlayReceiverSessionScreen_SetTimeSynchronizer`,
`AirPlayTimeSyncClient` in `libcarplay_plugin.so`) may deliver the phone's zone and daylight flag. Unknown whether the
app uses them; if it does, the phone could drive DST for CarPlay users without any new setting. Needs a decompile of
that module. *unverified*

## 6. Risks
- **MCU RTC writes:** `send_mcu_time/date` is already used by the Setup screen, so the command is known-good, but a
  patch that calls it from a timer is new. A bug could repeatedly rewrite the RTC (guard with the `dst_applied` bit and a
  once-per-hour cap).
- **Wrong hour twice a year if the state bit is lost** (nvm wiped, settings reset). The user can fix it by hand on the
  Setup screen.
- **Midnight/date rollover:** +1 h at 23:xx rolls the date; `setSystemDateTime` takes a full `QDateTime`, so it handles that.
  The transition hours (02:00 US) avoid it in practice.
- **MCU reset path:** `setSystemInitDateTime` (2022-01-01) on MCU reset would make the check fire a bogus shift. Patch B already
  skips that call; verify the check does nothing when the date is clearly invalid.
- **Library override caveats** (nvm-library-override.md): both the nvm and the stock copy of libinfomodule are mapped; the
  timer/state must not depend on one copy's statics (two timers = double shift!). Use the persisted bit as the only state.
- Space for new code in libinfomodule is **not yet checked** (code cave / free space in the Thumb text). Patch size is
  maybe 150-250 bytes of Thumb for the date math; may need a C-compiled blob. *unverified*
- Do not use `hwclock`/`/dev/rtc` from scripts: no evidence the SoC RTC is used; the MCU holds the time. And the reboot
  incident rules (AGENTS.md) apply: no raw MTD reads.

## 7. Needs a runtime test on the unit (read-only first)
1. In `gemn_auto.sh` extras: `grep -a TZ /proc/$(pidof Launcher)/environ` (expect empty), `date`, `ls -l /etc/localtime`
   (expect missing), `echo $TZ`.
2. Probe the libc parser with the unit's own `date` (the busybox applet uses libc `localtime`), no writes:
   `TZ=EST5EDT,M3.2.0,M11.1.0 date -d @<epoch before 2nd Sun Mar>` and `@<epoch after>`; compare offsets. Confirms glibc 2.18
   handles POSIX DST rules without files.
3. **Cadence of MCU time frames:** log 0x36 and 0x3d RX counts per minute over a few minutes (the extras already have
   a 30 s UART counter window; add per-command counters or a longer window). Also whether a frame follows each 8 s sync.
4. Does the displayed status-bar clock follow `date` (i.e. is it `QDateTime::currentDateTime()` or the MCU value)?
5. After a candidate patch: set the clock to a minute before a boundary, wait, check the display, the kernel clock (`date`),
   and after car off/on the MCU value (does the shifted time survive?). Then test the reverse boundary, a manual clock
   change on each side of the boundary, and a cold boot with the stick out.
6. Verify the MCU RTC survives accessory off (power-cycle behaviour is unknown from static analysis).

## 8. What I did not verify
Anything dynamic; the MCU-side behaviour (JieLi image payload is encrypted, see `mcu-image-header.md`); the INI key of the
radio area; code-cave availability; that `localtime_r` caches the TZ per process (glibc knowledge only).
