# Roadmap: what to do next

> **Top item (2026-10-25):** FINDINGS 9. Emperor = DSP master. Its saved state is
> skipped at boot whenever the MCU reports a memory reset (`loadSystemSettings`
> 0x41ef1d28). Patch B (1 byte, `libinfomodule.so` @0x41d2b) plus the language strings
> were installed through the nvm override by the 21:04 USB run (FINDINGS 12).
> **Status:** installed, not yet confirmed loaded. Next: cold boot, check `x3patch/log.txt`
> shows the nvm copy mapped, then the overnight Emperor test. wifi40 is done (FINDINGS 12).

> **Update (2026-10-26):** patch B's `libinfomodule` is confirmed mapped from nvm (FINDINGS 14); overnight Emperor test still pending.
> Explorations merged: Home date (parked: nvm override does not reach plugins), auto DST, AA identity (bind-mount route),
> reverse guide lines (`libapis` nvm override, test script ready). See `docs/discoveries/`. The reflash path (`repack.py`) now unlocks
> the date and AA-XML fixes, so it moves up.

Ordered by value and risk. Each item points to its evidence in `FINDINGS.md`.

## Bug 1: Emperor / DSP settings not remembered (top priority)

**Cause:** the DSP page never writes Emperor (time alignment), Filter, Loudness, VBass
or DSP balance to `SETUPMENU.ini` (FINDINGS 5.3, 5.8). Boot restores defaults
(`audio.delay=0,…`, confirmed at runtime in 5.7).

**Step 0: pin down where Emperor actually goes (in progress).** Two persistence paths
exist (FINDINGS 5.9):
- `SETUPMENU.ini` (saved on change): EQ, EQ mode, DPS key, but **not** Emperor
  (`DspEmperorView::saveSetting` is dead), Filter, Loudness, VBass.
- SpeakerExec blob in `ResumeInfo.ini` (saved **only at shutdown**): Loudness
  (+0xb/+0xd), DSP switches, and Emperor fields (+0x6..0x9).

Next USB run: change only Emperor → snapshot → full off/on → snapshot. Read
`audio_events.txt` (any `setSpeakerDelay`?) and the `ResumeInfo.ini` diff at +0x6..0x9.
That decides whether the fix belongs in `libcomdspviews` (call `saveSetting`) or in the
shutdown path (make the blob save happen on change, not just at power-off).

### Fix delivery options (least risky first)

| # | Approach | Reflash? | Revert | Notes |
| --- | --- | --- | --- | --- |
| A | **Library override via nvm.** Put a patched `libcomdspviews.so.1` in `/media/flash/nvm` (first in `LD_LIBRARY_PATH`, `init.gui.rc:4`), installed by a USB `gemn_auto.sh` | no | delete the file | *Inferred* to work: soname load, no RPATH. **Test first with an unmodified copy** plus a marker (e.g. logcat or a file write) |
| B | **Shim library** (`LD_PRELOAD`-style, or as a fake `NEEDED` dependency) that wraps `AudioControl::setSpeakerDelay` / filter / loudness setters and saves them through `DataSaveControl::saveSettingData` | no | delete the file | Needs an ARM cross-compiler matching glibc / GCC 4.8 ABI and Qt 5.3.2 headers |
| C | Repack `spapp` with the patched lib and reflash `ISPBOOOT.BIN` | yes | reflash stock | Needs the repack tool (md5s in ISP script + uImage CRC, FINDINGS 2.4/2.5); about 0.9 KB slack in spapp |

### Patch content for `libcomdspviews` (for A or C)
- Emperor: make `saveSetting()` (0x2e476) run after changes, e.g. hook the tail of
  `DspEmperorView::updataEmperorData` (0x2ec78) / `switchEmperorCurMode` (0x2e9d6)
  to also `bl saveSetting`. Needs a few bytes of code cave. `saveSetting` already
  skips the write when nothing changed.
- Filter / Loudness / VBass / Balance: no save functions exist, so new code is
  needed. That tips these toward option B (shim) over byte patching.
- Restore side already exists for delay (`SetupModule::exeDelay`) and filter
  (`exeFilter`). Check loudness/VBass/balance restore before relying on it.

## Bug 2: EQ reset "next day" (only if it recurs)
- The user reports EQ is remembered, and the capture shows it saved (5.7).
- The restore is still gated on the MCU RstMemory flag (5.2). If EQ is ever seen
  reset while `audio.EQ.custom` is intact on flash, apply **patch A**:
  `libsetupmodule.so` file 0xaa1e `90 b1` → `12 e0`. Ship it through override
  option A above (copy patched `libsetupmodule.so` to its normal path? No, it's a
  plugin loaded by path; check how `directstage` modules are loaded first).

## Bug 3: classic EQ preset cycling overwrites the custom curve (low)
- `ComEqView::btnEqRelseaseSlot` 0x1acb6 (5.4). Fix: only `saveEqSetting` when
  mode == 7.

## Bug 4: garbage 20-band EQ sent to the DSP (medium, may affect sound)
- Every DSP change and boot sends `APP_CMD_SET_EQ_ext 20band` with uninitialised-looking
  params (`fc:6`, `q:-1.79e36`, `-nan`), some ending in `set eq failed` (FINDINGS 5.9).
- Next: find the caller of `AudioControlPrivate::setEqValue16` /
  `audio_ext_eq_parameter_t` in `libapis` (decompile already in scratch) and check the
  struct init. Likely a one-spot fix (memset / fill).

## Bug 5: DPS (Bongiovi) toggle does nothing (likely not fixable in app)
- The DSP rejects it: `AUDIF_DpsCmd: plz check dps is enabled in DSP` (5.9). Probably
  missing from the DSP firmware/config. Options: hide the toggle, or look for a DSP
  config flag (`runtime_cfg` / `pq` partitions, untested).

## Wireless Android Auto link quality (optional, user interest)
Evidence: FINDINGS 8.5. The AP runs 5 GHz ch149 at **HT20** (802.11n, 72 Mbps PHY).
802.11ac isn't available: the shipped hostapd lacks `CONFIG_IEEE80211AC`, and putting
`ieee80211ac`/`vht_*` keys in the config would stop the AP from starting.
1. **Read-only first:** add `hostapd_cli -p /var/run/hostapd status` and `get_config`
   plus a redacted copy of `/media/flash/nvm/softap.conf` to the diag script. Confirms
   the live config, and whether the nvm copy exists yet.
2. **HT40 (cheap, no reflash):** add `ht_capab=[HT40+][SHORT-GI-20][SHORT-GI-40]` to
   `/media/flash/nvm/softap.conf` via USB script (back up first, undo = restore the
   backup), then reboot. Check hostapd came up (`status` shows `secondary_channel=1`)
   and wireless AA connects. If hostapd rejects it (driver lacks HT40 on 5G), the AP
   is down until the undo runs, so ship the undo on the same stick.
3. **Real 802.11ac (big):** cross-build hostapd 2.10 with `CONFIG_IEEE80211AC=y`
   (+ nl80211/libnl matching the unit), drop it on nvm and `mount --bind` it over
   `/usr/local/bin/hostapd` from the USB script before `networkmanager` starts it (or
   restart the AP), with `ieee80211ac=1`, `vht_oper_chwidth=1`,
   `vht_oper_centr_freq_seg0_idx=155`. Driver already has `rtw_vht_enable=2`.
   Only worth it if HT40 still stutters; AA video at 480p/720p30 needs well under
   20 Mbps.

## Investigations
- Identify `nvm/isp_part.bin` (2.4 MB) and the garbled-name 4,918-byte file in nvm
  (FINDINGS 11.4). Use the snapshot copies on the stick only; touch nothing on the unit.
- Does the shutdown save always run? Loudness/DSP-switch persistence depends on
  `McuCallback::updateSystemPowerState` → `DSPDataSave` before power-cut (5.5). Compare
  the blob before and after a hard power-off.
- Next-day USB capture with the stick left in overnight (new boot_id? flags in logcat?).
- `mcusrv` UART protocol (`libmcusrv.so`): unlocks MCU-side behaviour (RstMemory,
  power).

## Tooling to build
1. `tools/usb-diag/`: done (v3: background logcat recorder with `-r2048 -n4`,
   timestamp windows, `window.log`, `changes.txt` diff, seeded `diag/last` baseline).
2. USB **installer/uninstaller** script for nvm overrides (copy lib, `sync`, log, and a
   `remove` mode). Always keep an uninstall path on the same stick.
3. `tools/extract.py`: carve partitions + unsquash (the commands live in FINDINGS 4).
4. `tools/repack.py`: only when option C is needed.
5. ARM toolchain for shims (option B): target `arm-linux-gnueabi`, EABI5, glibc
   (`/lib/ld-linux.so.3`), GCC 4.8.3 ABI, Qt 5.3.2.
