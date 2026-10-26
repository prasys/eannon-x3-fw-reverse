# Eanon X3: firmware reverse-engineering notes

Running notes for patching bugs on the Eanon X3 head unit. Every claim carries a
coordinate (file offset, path, symbol) and how it's known:
*parsed* (read straight from headers/scripts), *strings*, *decompiler*, *runtime*,
or *inferred* (not yet verified).

---

## 1. Artifacts

| File | Source zip | Size | md5 | sha256 |
| --- | --- | --- | --- | --- |
| `ISPBOOOT.BIN` (SoC: bootloaders, Linux, rootfs, apps) | `X3_20250718.zip` | 77,327,360 | `b9c48d3650af355044d220f8dd546a0a` | `93d14858f2d967b21c5e8e5e683a65c16e2afbcb03bb1afb39a146d5153cf1e2` |
| `mcu8368u.bin` (MCU) | `X3-series MCU_240723.zip` | 397,824 | `6315701990346bab524a6b1c47ab772d` | `a0f888ee32c750aa6188a1be33c6c2fa1e42801330dcc709ff6d622f24a7508b` |

---

## 2. Main SoC: `ISPBOOOT.BIN`

### 2.1 Identification
- **SoC:** Sunplus SPHE8368-U, codename "Gemini". *strings*: uImage names
  `uboot_gemini_evb0716_2053`, `ecos_gemini_disc_0716_2053`; `appinfo.rc`
  `SP_CONFIG_MODEL_PLATFORM_CFG 4RlsCode_8368_U_demov1.4_openall_cfg`.
- **Build:** `version_info` partition (*parsed*):
  ```
  CustomerVersion="sunplus_demoboard_v1.4"
  SdkPublishVersion="55.0.6.0.4.0.0.0"
  GEMINI_BUILD_TIME="2025/07/16_20:53"
  ```
  So this is close to the Sunplus reference demo-board SDK, lightly customised.
- **CPU / OS:** 32-bit ARM (EABI5), Linux `4.9.217` (uImage name). There's also an
  **eCos** image for the disc/servo side.
- **Userland:** Android-style `init` (`init.rc`, services, properties), Binder IPC
  (`servicemanager`, `android::Parcel` in symbols), Qt UI, BusyBox.
- **Binaries are stripped.** Shared libraries still export C++ dynamic symbols
  (e.g. `libmcusrv.so` has 715 defined dynamic symbols), so class and method names
  are recoverable. Executables have no symbols.

### 2.2 Container format
- Byte 0: `XBUT` magic, the Sunplus **xBoot** header. The ISP banner
  `+++xBoot Jun  9 2022 17:23:56` and an `XB>` debug-monitor prompt follow.
- The image flashes itself from SD/USB. U-Boot runs an **ISP script** stored as
  uImage type 6 (script) at file offset **0x4937800** (`ISP script for
  ISPBOOOT.BIN`, 0xacc5 bytes). A second script is at **0x4942800** (0x74e9 bytes,
  probably the "update" variant; *inferred*, not yet diffed).
- Script commands follow the pattern
  `fatload … /ISPBOOOT.BIN <size> <file_offset>` → `nand write …` →
  `mtdparts add nand0 … <name>`, which gives the partition map below.

### 2.3 Partition map (*parsed* from the ISP script)

| Partition | File offset | Size in image | Contents |
| --- | --- | --- | --- |
| xboot1 | 0x104000 | 0x5000 | xBoot |
| uboot1 | 0x109000 | 0x65400 | U-Boot (uImage, load 0xf00040) |
| uboot2 | 0x16e400 | 0x65400 | U-Boot backup |
| ecos | 0x1d3800 | 0x285400 | eCos (uImage, load 0x3d00000) |
| kernel | 0x458c00 | 0x3e9400 | Linux 4.9.217 (uImage, load 0x408000) |
| rootfs. | 0x842000 | 0x217000 | SquashFS 4.0, LZO, 32K blocks, 500 inodes |
| opt. | 0xa59000 | 0x3e000 | SquashFS: just `libstdc++.so.6.0.19` |
| spsdk. | 0xa97000 | 0x2ce9000 | SquashFS: Sunplus SDK (851 inodes) |
| spapp. | 0x3780000 | 0x7a6000 | SquashFS: apps/UI (142 inodes) |
| pq | 0x3f26000 | 0x6800 | picture-quality tables (`fe494d47…` header) |
| logo | 0x3f2c800 | 0x258400 | boot logo (`PART` header) |
| tcon | 0x4184c00 | 0xb400 | LCD timing controller config |
| iop_car | 0x4190000 | 0x2800 | IOP (I/O processor) firmware |
| runtime_cfg | 0x4192800 | 0x3c00 | runtime config (`PART` header) |
| vi | 0x4196400 | 0x400 | |
| isp_logo | 0x4196800 | 0x546800 | logo shown while flashing |
| vendordata | 0x46dd000 | 0x800 | all zeros in this image |
| pat_logo | 0x46dd800 | 0x259400 | |
| version_info | 0x4936c00 | 0x400 | text, see 2.1 |
| vd_restore | 0x4937000 | 0x800 | |

The script also creates `env`, `env_redund`, `nvm`, `anm_logo` on NAND. Those
aren't carved from the image.

### 2.4 Flash integrity checks (important for repacking)
- After writing each partition, the script reads it back and compares an **md5 of
  the first 0x200000 bytes** against a value hardcoded in the script, e.g. for spapp:
  ```
  nand read $isp_ram_addr spapp. 0x200000
  md5sum $isp_ram_addr 0x200000 md5sum_value
  if test "$md5sum_value" = 11e7edb665e9c53fb5fcf28bbdc44bcb ; then
  ```
- So a patched partition needs its md5 updated **in the script**, and then the
  script's uImage header CRCs (header + data) recalculated.
- No signature or encryption on the SoC partitions so far: the SquashFS partitions
  unpack cleanly. *Not yet checked:* whether xBoot/U-Boot verify anything else at
  boot (look for `verify img...` / `bad hcrc` paths in xBoot).

### 2.5 Space budget for rebuilt filesystems
The image copies fixed sizes, so the slot sizes in 2.3 are the hard limit unless
the script is rewritten:

| FS | Used (`bytes_used`) | Slot | Free |
| --- | --- | --- | --- |
| rootfs. | 0x21617e | 0x217000 | ~3.7 KB |
| opt. | 0x3d43a | 0x3e000 | ~3 KB |
| spsdk. | 0x2ce8097 | 0x2ce9000 | ~3.9 KB |
| spapp. | 0x7a5c4d | 0x7a6000 | ~0.9 KB |

The NAND areas are bigger than the image slots: `ispsp find_area` reserves e.g.
0x360000 for rootfs and 0x2e00000 for spsdk. So growing a partition means editing
the script's copy sizes (*inferred*, untested). Rebuild with matching params:
`mksquashfs … -comp lzo -b 32768 -noappend` (also check uid/gid and timestamps).

### 2.6 Boot / service wiring (rootfs `init.rc`, spapp `appinfo.rc`)
- `early-boot`: `sbin/sys_setup.sh`, insmod `evdev.ko` and `touch-driver.ko`, then
  load `/usr/local/sdkinfo.rc` (spsdk) and `/application/appinfo.rc` (spapp).
- `sdk-boot`: `ts_sdkstart`, a root shell on the **console** (`service console
  /bin/sh`, user root, so the UART console is probably a root shell; *inferred*),
  `watchdog_pet` (`sbin/start_wd.sh`).
- `normal_start` class: `servicemanager`, `resourcemanager`, `networkmanager`,
  `startup_wifiap.sh`, `start_touchcali.sh`.
- The launcher is chosen by property: `ro.start.Launcher` / `ncLauncher` /
  `tdLauncher`. Each also starts **`mcusrv`**.
- **`/application/bin/mcusrv 1 115200`** is the UART bridge to the MCU (port arg
  `1`, 115200 baud). `libmcusrv.so` exports `mcumanager::MCUPower`, `MCUDisc`,
  `MCUStatus`, and Binder interfaces `mcusrv::IMCUStatus`, `BnMCUPower::onTransact`.
  **Bugs in power/ACC, backlight, keys and disc are probably handled here, on the
  Linux side.**

### 2.7 Mount points
- `spapp` is mounted at `/application`: `bin/` (Launcher, mcusrv, uiplayer, sendkey,
  touchcalibration, ssnand, change_pri), `lib/`, `lib/activity/`, `lib/module/`,
  `fonts/`, `keymap/`, `resource/`, `drivers/`.
- `spsdk` is mounted at `/usr/local`: `bin/`, `lib/`, `qt/`, `drivers/`, `etc/`,
  `share/`, `sdkinfo.rc`.
- *Inferred* from `appinfo.rc` and `init.rc` paths. Confirm in `sys_setup.sh`.

### 2.8 Feature → library map (spapp)
Each feature is split into an **activity** (UI, `lib/activity/`) and a **module**
(logic, `lib/module/`):

| Feature | Activity | Module | SDK daemon (spsdk/bin) |
| --- | --- | --- | --- |
| Bluetooth / phone | `libbtphone.so` | `libbluetoothmodule.so` | `bluetooth_server`, `bt_init_client` |
| CarPlay | `libcarplay.so` | `libcarplaymodule.so` | `apple_carplay_server`, `apple_iap_server` |
| Android Auto (see 8) | `libandroidauto.so` | `libandroidautomodule.so` | `aa_manager_service` (spawns `aa_accessory_service`) |
| CarLife | `libcarlifeactivity.so` | `libcarlifemodule.so` | |
| AirPlay / iOS mirror | `libairplay.so`, `libiosmirror.so` | `libairplaymodule.so`, `libiosmirrormodule.so` | `airplay_server` |
| Autolink | `libautolink.so` | `libautolinkmodule.so` | `autolink_server` |
| Radio (si473x / zt0811) | `libradio.so` | `libradiomodule.so` | |
| DAB | `libdab.so` | `libdabmodule.so` | |
| Media / USB / SD | `libusb.so`, `libcard.so` | `libmediaplaymodule.so` | |
| AV-in / AUX / camera / reverse | `libavin.so`, `libcamera.so` | `libavinmodule.so`, `libauxinmodule.so`, `libcameramodule.so`, `libreversemodule.so` | |
| Audio | | | `as_*` (as_server, as_eq, as_music, as_voice…) |

Shared UI libs: `libcomviews`, `libsetupviews`, `libapis`, `libappframework`,
`libmediaviews`, `libfileviews`, `libcomeqviews`, `libcomwidgets`.
Home screen: `lib/activity/default/libhomelauncher.so`.

### 2.9 Other strings worth chasing
- IPs: `192.168.0.88`, `192.168.0.1` (**AP gateway; wireless-AA TCP server :8765**, 8.4),
  `192.168.44.2` (likely CarPlay link), `120.77.203.1`, `47.92.37.111` (Aliyun ranges, maybe an update or
  activation server; *unverified*, find which binary they're in).
- Crypto tables (AES, SHA-256/512, MD5, CRC-32) at many offsets. That's expected
  from OpenSSL and the CarPlay/AA stacks. Nothing unusual flagged yet.

---

## 3. MCU: `mcu8368u.bin`

### 3.1 Identification
- **Chip family: JieLi (杰理) AC690x / BR17**, not a classic 8051/STM32 MCU.
  *strings*: header name `JL_690X.BIN` at 0x24, `pdn:jl_690x` at 0x290, SDK lib
  banner `br17_v2-0-9` at 0xd5c3.
- The JieLi SDK libs it lists (0xd41a–0xd8d0): `rtos`, `dac`, `aec`, `dec`, `fat`,
  `usb`, `rtc`, `echo`, `rcsp`, `sbc_dec`, `BT_AAC`, `bt_syn`, `BtCalling`,
  `escosyn`, `btrepair`, `mp3_dec/enc`, `FLAC`, `wma`, `APE`, `wav`, `DAGC`,
  `SpeechDetection`, `CRC16_IBM`. Lib build dates are 2016–2017. The zip is dated 2024-07-23.
- So the "MCU" is a JieLi Bluetooth-audio SoC that probably also does the car
  MCU jobs (ACC/power, keys, maybe BT audio/calls). How much of BT runs here vs. on
  the Linux `bluetooth_server` is **open** (*inferred*).

### 3.2 Format
- Header magic `JL_UDFIR` at 0x0: JieLi upgrade-firmware container. Header words
  (big-endian) include `0x00000020`, `0x0000e3df`, `0x00000200`, `0x00061000`,
  `0x0000cff6`. **Decoded 2026-10-26 (*parsed*, verified by `tools/jl_udfir.py`):**
  `0x08` u32 `0x20` = length of the CRC'd header block · `0x0c` `0xe3df` = CRC16 over
  `[0x10, 0x30)` · `0x10` `0x200` = payload offset · `0x14` `0x61000` = payload length ·
  `0x18` `0xcff6` = CRC16 over the payload `[0x200, 0x61200)` · `0x24` name `JL_690X.BIN` ·
  `0x30..0x200` zeros. File size `0x61200` = offset + length. CRC16 is poly 0x1021, init 0,
  no reflection, no final XOR. The payload CRC covers the *encrypted* bytes, so the container
  can be checked without decrypting. (The header CRC span is only pinned down up to
  leading zero bytes: any start 0x10–0x12 gives the same value.)
- Entropy is ~7.99 across 0x0–0x5ffff, so the code is **encrypted/scrambled**
  (JieLi uses a chip-keyed scrambler on flash images; *inferred*, needs confirming
  with public JieLi tooling). Only the lib-banner table around 0xd400 is plaintext.
- Tail: the last bytes of the payload (to 0x61200) are 0xFF padding with a `BOOT_START_FIRST` marker at 0x611e0.
- **No disassembly possible until it's descrambled.** Out of scope for now. Treat
  MCU behaviour as a black box seen through `mcusrv`'s UART protocol.

---

## 4. Reproduce

Work in a scratch dir, never inside the extracted tree. Needs `unsquashfs`
(`brew install squashfs`).

```bash
unzip X3_20250718.zip -d fw/
# carve a partition (offset/size from 2.3), e.g. spapp:
python3 -I -c "d=open('fw/ISPBOOOT.BIN','rb').read(); open('spapp.bin','wb').write(d[0x3780000:0x3780000+0x7a6000])"
unsquashfs -d fs/spapp spapp.bin
# ISP script (skip the 64-byte uImage header):
dd if=fw/ISPBOOOT.BIN of=isp_script.txt bs=1 skip=$((0x4937840)) count=$((0xacc5))
```

---

## 5. Bug: EQ and DSP (Emperor / time alignment) settings lost after car off

**Symptom:** EQ and DSP settings (Emperor = speaker delay / listening position)
reset after ACC off/on. Bluetooth and other settings survive.

### 5.1 Where settings live
- `DataSaveControl` (`libsysset.so`) wraps `QSettings(IniFormat, UserScope,
  "Sunplus", <file>)`, with the path set by `initQSettingPath` @ 0x425db764 to
  `/media/flash/nvm`:
  - `saveSettingData`/`readSettingData` → `/media/flash/nvm/Sunplus/SETUPMENU.ini`
  - `saveResumeData`/`readResumeData` → `/media/flash/nvm/Sunplus/ResumeInfo.ini`
  - *decompiler* + string resolution
- `/media/flash/nvm` is **YAFFS2** on the NAND `nvm` MTD partition
  (`rootfs/init.platform.rc:6`).
- Qt is 5.3.2 (`spsdk/qt/lib/libQt5Core.so.5.3.2`). `libQt5Core` imports **no
  `fsync`/`fdatasync`**, and nothing in spapp calls `sync`. Writes rely on the kernel
  flushing. That's a secondary risk, but it can't be the main cause because BT
  settings use the same path and survive.
- Keys (`SetupStrIdConst::*`, defined in libsysset, resolved through the GOT):
  EQ curve = `setupAEQCustom`, EQ mode = `setupEqMode`, delay = `setupAudioDelay`.
  `libcomeqviews` and `libcomdspviews` both use the same `setupAEQCustom` key.
  No key mismatch.

### 5.2 Root cause A: EQ restore at boot is gated on the MCU "reset memory" flag
- At boot, `SetupModule::exeSetupAudioSetting` → `exeSetupEq()`, `exeDelay(false)`,
  `exeFilter(false)` (`libsetupmodule.so`, *decompiler*).
- `SetupModule::exeSetupEq` @ 0x1a9d4 (Ghidra base 0x10000, file/vaddr **0xa9d4**):
  ```
  0xaa12  blx  getSystemPowerState@plt (0x7dd8 → GOT 0x19528)
  0xaa1e  cbz  r0, 0xaa46        ; 0 → read saved   (0xaa6e blx readSettingData@plt)
                                 ; else → defaults  (0xaa2c blx DataDefault::getDefault@plt)
  ```
  *Confirmed in assembly* (PLT stubs decoded to GOT relocs).
- `SetupExec::getSystemPowerState` @ 0x41ee6584 (`libinfomodule.so`) returns the byte
  at **0x41f39854** (.bss). `McuCallback::updateSystemRstMemory(uchar)` @ 0x41ef3138
  stores its argument to the **same byte** (`0x41ef3198 + 0x466b8 + 4`). *Confirmed in
  assembly.* So the "power state" is the MCU's **RstMemory** flag, and
  whenever the MCU reports it, the saved EQ is ignored and factory defaults are
  applied. BT settings aren't gated like this, which explains why they survive.
- **Not verified:** when the JieLi MCU sends RstMemory≠0 (its firmware is encrypted).
  Candidates: every cold boot (e.g. constant +12 V lost, or the yellow wire on ACC), or
  every ACC cycle. To check: on the UART console, watch the qDebug line printed by
  `updateSystemRstMemory` (logs the value) during an ACC off/on.
- `initVolumes` (libsetupmodule, ~line 1393 of the export) also calls
  `getSystemPowerState()`, so volume may be gated the same way.

**Proposed patch A (not applied):** `libsetupmodule.so` file offset **0xaa1e**:
`90 b1` (`cbz r0,0xaa46`) → `12 e0` (`b 0xaa46`). This always reads the saved value.
If the key is missing, `readSettingData`'s default is used and
`exeSetupEq` only applies a list when it has 16 entries, so a first boot falls back
safely (*inferred* from decompile; test it).

### 5.3 Root cause B: Emperor / time alignment is never saved
- `DspEmperorView::saveSetting` @ 0x2e476 (`libcomdspviews.so`, vaddr 0x1e476) builds
  the per-channel delay list and writes `setupAudioDelay`.
- **It has no callers.** No direct BL found in Ghidra's analysis, no PLT import in any
  spapp/spsdk binary, and the only occurrence of its Thumb address 0x1e477 is its own
  `.dynsym` entry (file 0x2be8, inside `.dynsym` 0x13a4–0x3e34). *Confirmed by
  byte scan + reloc listing.*
- Changes are applied live (`updataEmperorMode` → `AudioControl::setSpeakerDelay`),
  so they sound right until reboot. Then `exeDelay(false)` reads
  `setupAudioDelay`, which was never written, so you get defaults.
- **Fix options:** call `saveSetting()` from `updataEmperorData` /
  `switchEmperorCurMode` or from the destructor (needs a code cave or a hook; a
  plain branch patch isn't enough). Easier alternative: an `LD_PRELOAD` shim that
  wraps `AudioControl::setSpeakerDelay` and persists the values itself. Still to
  design.

### 5.4 Secondary issue: classic EQ screen overwrites the custom curve
- `ComEqView::btnEqRelseaseSlot` @ 0x1acb6 (`libcomeqviews.so`): next/prev mode
  buttons call `saveEqSetting(map[newMode])`, so **the preset's values overwrite
  the saved custom curve** (`setupAEQCustom`). The in-memory User curve hides this until
  reboot. Moving a slider correctly switches to User (mode 7) first
  (`updateEqDataSlot` @ 0x1aec8). *decompiler*
- The same pattern probably exists in `DspEQView::btnReleasedSlot` @ 0x218e0
  (`libcomdspviews`). Not yet read.

### 5.5 Shutdown path (for reference)
- `McuCallback::updateSystemPowerState` @ 0x41ef2fe4 (`libinfomodule.so`):
  `RadioModule::RadioDataSave()` → `SetupExec::DSPDataSave()` →
  `SpeakerExec::DSPDataSave()` (saves 0x27 + 4 bytes of live DSP state from .bss
  0x41f39c48/0x41f39c6f to ResumeInfo) → `send_mcu_power_status` ack.
- *Unverified (decompiler output tangled):* if the radio plugin exists but
  `getRadioModule()` returns null, the code jumps past `DSPDataSave` **and** the MCU
  ack. Check against the assembly.

### 5.6 Tools
- `tools/gotres.py`: resolves `*(GOT + lit)` symbol references from Ghidra
  output. `python3 -I gotres.py <elf> <ghidra_base_hex> name:litGOT:pc:litKey,litGroup`.

### 5.7 Runtime evidence (USB diag capture, 2026-10-25, boot_id `90b9460c`)
Captured with `tools/usb-diag/gemn_auto.sh` through the stock USB autorun hook (see 7).
The raw capture stays off-repo because it has personal data (paired phone names, media
file names).

- **The user reports EQ is remembered.** `SETUPMENU.ini` has
  `audio.EQ.custom=<16 values>` and `system.EqMode=7`. So on this boot the RstMemory
  gate (5.2) didn't fire. **Patch A drops to "defensive / only if seen"**, since the
  next-day reset may have been Emperor/DSP all along. *runtime*
- **Emperor confirmed lost:** `audio.delay=0, 0, 0, 0, 0, 0` on flash, and logcat
  lines 1538–1549 show `ClientAppSetting::setSpeakerDelay delay 0.000000` for channels
  0x1…0x20 at boot (`exeDelay(false)` restoring the never-written key). *runtime*
  This matches 5.3.
- **Real INI key names** (`SetupStrIdConst` value → INI key): EQ curve
  `audio.EQ.custom`, EQ mode `system.EqMode`, delay `audio.delay`, filters
  `audio.filterfront|rear|sub`, Bongiovi `audio.bongiovi.{dps,mode,stereo,usergain}`,
  balance `audio.BaderBalance.{balance,fader}`.
- `ResumeInfo.ini` `[DSPOn Data]` / `[DSPOff Data]` come from the `SpeakerExec`
  engine (`libinfomodule`, 5.5). No library outside libinfomodule imports its setters
  (only reached through `SpeakerExec::AUDIF_AppCmd`). ~~so it looks unused~~
  **Corrected in 5.9: it is live** and holds Loudness + DSP switch state.
- **Bonus bug, unexplored:** the boot `APP_CMD_SET_EQ_ext 20band` (logcat ~1904–1925,
  `SpAudSysController.cpp:744/766`) sends garbage band params: every band `fc:6`,
  `q:-1.79e36`, random `method`/`slope`/`phase`. It looks like an uninitialised
  struct in the 20-band ext path (`AudioControlPrivate::setEqValue16`?). It might
  just be a logging bug. *runtime*

### 5.8 What the DSP page persists (`libcomdspviews.so`)
Only 4 `saveSettingData` call sites exist in the whole library (keys via `gotres.py`):

| Call site | Key | Called? |
| --- | --- | --- |
| `FUN_00020f44` (DspEQView mode) | `setupEqMode` | yes (from 0x21538-ish, 0x21866-ish) |
| `DspEQView::saveEqSetting` @ 0x2122c | `setupAEQCustom` | yes |
| `DspVbassView::exeDpsSetting` @ 0x2d144 | `setupDpsState` (Bongiovi DPS on/off) | yes |
| `DspEmperorView::saveSetting` @ 0x2e476 | `setupAudioDelay` | **never** (5.3) |

So from the DSP page, **Emperor/time alignment, Filter/crossover (HPF/LPF,
`DspFilterView`), Loudness (`DspLoudnessview`), VBass sliders and DSP balance
(`DspBalanceView`) are never written to flash** by this library. They're applied live
through `AudioControl` and revert at boot. *decompiler; not yet checked whether some
other module saves them on their behalf through a signal (e.g. `saveBalanceDataSig`)*.
`audio.filterfront=through, butterworth, 12, 0, 0, 100` (the default) in the capture
fits this.

**Open: which "DSP toggle" the user means** (DPS/Bongiovi is saved; Loudness/Filter
on/off isn't). Confirm on the unit.

### 5.9 Toggle session (USB diag, boot_id `0114eb58`, 2026-10-25 16:37–16:42)
Six snapshots in one boot. Logs merged, deduped, and bucketed by timestamp (the v2 script's
`logcat -r 2048` rotated at about 16 KB, because the optional arg must be attached
`-r2048`, so 16:39:05–16:39:56 may be lost; fixed in v3).

- **The SpeakerExec blob is live (corrects 5.7).** In `ResumeInfo.ini`, between the
  previous boot and this one, `[DSPOff Data]` went from all zeros to `01 @+0x0, 01 @+0xb`
  and `[DSPOn Data]` to `01 00 00 00` (@+0x27). Written at the previous shutdown by
  `SpeakerExec::DSPDataSave`. *runtime*
- Blob field map (*assembly*, literal-pool resolution; blob base 0x41f39c48):
  +0x0 DSP switch 0 · +0x1 switch 1 flag · +0x5 switch 3 · **+0x6 Emperor mode** ·
  **+0x7..0x9 Emperor delays** · **+0xb Loudness enable** (`getLoudnessEnable`
  `ldrb [r3,#0xb]` @ 0x41efa166) · **+0xd Loudness freq** (int, `ldr.w [r3,#0xd]`
  @ 0x41efa178; 0 → 400 Hz, 1 → 800, 2 → 2400) · +0x27 switch 1 value.
- Boot applied `set loundness extend: Enable=1 Freq=400`, matching blob +0xb=1,
  +0xd=0. So **Loudness/DSP switches persist only via the shutdown save**, never on
  change (`SETUPMENU.ini` unchanged across all toggles). Whether they survive depends on
  `McuCallback::updateSystemPowerState` running before power is cut (5.5).
- **Loudness** toggles reach the DSP (`SpAudSysController.cpp:648 set loundness
  extend: Enable=0/1`, at 16:38:57, 16:40:25, 16:40:28). *runtime*
- **DPS / Bongiovi is broken at DSP level:** every enable gives
  `auddrv_linux.cpp AUDIF_DpsCmd 1257 ERR: plz check dps is enabled in DSP` →
  `enable dps failed` (16:40:55, 16:41:17, 16:42:00). Only "enable" is ever logged.
  The DSP firmware probably doesn't include DPS, so the toggle is cosmetic. *runtime*
- **Every DSP change re-sends `APP_CMD_SET_EQ_ext 20band` with garbage band params**
  (e.g. `fc:6` for all bands, `q:-1.79e36`, `gain:-nan`, random `method/slope/phase`),
  often ending in `control 777 ERR: set eq failed`. Seen at boot and after each
  Loudness/DPS change. Strongly suggests an uninitialised struct on the
  ext-EQ path. *runtime*
- **Emperor:** no `setSpeakerDelay` lines after boot in any window. Either it wasn't
  changed in this session, or this build's Emperor UI drives the SpeakerExec engine
  (`AUDIF_Set_Emperor`, no logging) instead of `AudioControl::setSpeakerDelay`. **To
  confirm:** change only Emperor, reboot, and check blob +0x6..0x9 and `audio.delay`.

---

## 6. Open questions / next steps
- [ ] **Emperor-only run** (v3 script): change only Emperor → snapshot → full off/on →
      snapshot. Look for `setSpeakerDelay` in `audio_events.txt`, the `audio.delay` diff,
      and the `ResumeInfo.ini` blob at +0x6..0x9 (5.9).
- [ ] Hard power-off test: does the shutdown blob save always happen?
- [ ] Trace the garbage `APP_CMD_SET_EQ_ext` params to their source in `libapis` (5.9).
- [ ] Design the persistence fix for Emperor + Filter + Loudness + VBass (5.3/5.8).
      Options in `docs/ROADMAP.md`.
- [ ] Next-day capture (leave the stick in overnight): new boot_id? `audio.EQ.custom`
      intact? Decides whether patch A is needed at all.
- [ ] Look into the garbage `APP_CMD_SET_EQ_ext` params (5.7).
- [ ] Apply patch A only if the EQ is ever seen reset with `audio.EQ.custom` intact.
- [ ] Read `DspEQView::btnReleasedSlot` for the 5.4 pattern.
- [ ] Collect the actual X3 bug list (symptoms) and map each to a library from 2.8.
- [ ] Decompile `libmcusrv.so` + `bin/mcusrv`: recover the MCU UART frame format
      (header, cmd ids, checksum).
- [ ] Read `sys_setup.sh`, `start_wd.sh`, `startup_wifiap.sh` (easy wins, plain shell).
- [ ] Diff ISP script #1 (0x4937800) vs #2 (0x4942800).
- [ ] Decode `runtime_cfg` / `logo` `PART` container format.
- [ ] Write a repack tool: rebuild SquashFS, patch the md5 values in the script, fix
      the uImage CRCs, splice back into `ISPBOOOT.BIN`. Test on hardware with a
      UART attached and a known-good image on hand.
- [ ] Find which binary holds `120.77.203.1` / `47.92.37.111`.
- [ ] JieLi `JL_UDFIR` header fields + descrambling (only if an MCU-side bug needs it).

---

## 7. Access without UART: USB autorun hook
- `rootfs/etc/mdev.conf:3`: `sd.* … */etc/usb_action_8368-U`, so mdev runs the script
  on every USB block-device event, as root.
- `rootfs/etc/usb_action_8368-U:1-16`: once the stick is mounted (observed at
  `/tmp/sp/mnt/sda1`), if `<usb root>/gemn_auto.sh` exists it is **executed as root**,
  and a log goes to `<usb root>/log_mdevscr_usb.txt`.
- Confirmed working on the X3 (2026-10-25): it ran at boot with the stick already
  inserted (uptime 13.3 s). FAT32/MBR stick, script with LF endings.
- This gives a root shell-equivalent with no reflash, so fixes can be prototyped as
  scripts before patching binaries. Tooling: `tools/usb-diag/`.
- Also on the unit: `/usr/local/bin/logcat` (`logcat -d` works), and
  `log_server` with `ALOG_LEVEL w` (`init.rc:186`).

---

## 8. Android Auto (wired + wireless)

Sunplus "AAS" stack v2.2.20 (Google receiver-library based). Wireless AA **works** on
this unit: the 2026-10-25 capture (boot_id `0114eb58`) shows a full wireless session.
*Runtime + strings unless marked.* Personal identifiers from logs (phone serial, BT/Wi-Fi
MACs, hotspot passphrase) stay out of the repo.

### 8.1 Processes and who starts them
| Piece | File | Role |
| --- | --- | --- |
| Manager daemon | `spsdk/bin/aa_manager_service` (`aaserver`, `init.rc:257`, started by `on start_accessory` `init.rc:299-313`) | state machine (`Startup → Activating → Wired/WirelessActivating → Shutdown`), wireless TCP server, BT RFCOMM service, device list |
| Session process | `spsdk/bin/aa_accessory_service` (1.3 MB) | **not in any `.rc`**: `aa_manager_service` `execl`s it per session (`AndroidAutoSubServiceController`, string `execl /usr/local/bin/aa_accessory_service fail!`). Does the AA protocol: SSL (`libssl.so.1.1`), protobuf-lite, channels, video (`libvideo.so`), audio (`libasclient`, `libfdk-aac`). Transports `Transport_AOA` / `Transport_TCP` |
| Client lib | `spsdk/lib/libspandroidauto.so.2.2.20` | `spandroidauto::SPAndroidAutoManager::create/createBTService` over `service_framework` IPC |
| App module | `spapp/lib/module/libandroidautomodule.so` | `AndroidAutoModuleImpl` (resources, audio focus/duck, BT pairing, key/touch dispatch), `AndroidAutoWirelessManager` (BT SPP/RFCOMM + Wi-Fi AP), `AOADeviceManager` |
| App UI | `spapp/lib/activity/libandroidauto.so` | `AndroidAutoActivity`, `AndroidAutoViewUI` (touch → `pointDispatchSig`) |
| Test CLI | `spsdk/bin/aacmd` | `--startup`, `--AAstart`, `--AAstop`, config, night mode, sensors (untested; would fight the running module) |

`accessory_server` is **CarLife**, not AA, despite its name.

### 8.2 Configuration
- Three `androidauto_config.xml` copies. The app module passes
  **`/application/etc/Androidauto/androidauto_config.xml`** (string in
  `libandroidautomodule`, *strings*); `aa_manager_service`'s built-in default is
  `/usr/local/etc/androidauto/androidauto_config.xml`. The spapp copy is the
  X3-tuned one: DisplayWindow **1024×600**, `Resolution480P` + **`Resolution720P`
  both true** (720P margins 256×120, density 213), touch 1024×600. The spsdk copy is
  the 800×480, 480P-only SDK default. `spsdk/etc/accessory/androidauto_config.xml` is a
  third, older-format copy.
- CarInfo is SDK placeholder: `Rolls-Royce / Phantom / 2016`, head unit `XXCompany /
  XXModel / XXOS 0.1`. That's what the phone shows as the car name.
- Sensors advertised: location, compass, speed, parking brake, night mode, driving
  status, accel, gyro, GPS satellites (the unit has `gps_server`/`gps_main` in
  `init.rc`; whether real data flows is unchecked).
- Keys: SEARCH, CALL, ENDCALL, MEDIA_NEXT/PREV, PLAY_PAUSE. No HOME/BACK/DPAD.
- `aa_accessory_service` embeds the Google AA cert chain: root `O=Google Automotive
  Link` (serial `C14EE7A5A4544D42`) and two leaf certs with keys, `O=Android-Auto-Developer,
  OU=01` and `O=Google-Linux-Reference` (file offsets 0x12c124–0x12d5fc). These are the
  stock SDK identities, not a vendor-issued cert. `setClientCreds` /
  `setCertificateTime` exist as IPC calls; the module carries a hardcoded time string
  `2015-05-05 08:56:48`, probably for cert validation without an RTC (*inferred*).

### 8.3 USB / AOA mode selection
- `UsbMControl::setAndroidDeviceMode(mode)` (`libapis` @ 0x41d57xxx, *decompiler +
  annotate_strings*) loads the AOA identity XML and toggles AOA:
  0 = `aoa_feature/aoa_androidauto.xml` · 1 = `aoa_autolink.xml` · 2 = AOA off ·
  3 = `aoa_carlife.xml` · 4 = re-enable · 5 = `/application/etc/EasyConnect/aoa_easyconnect.xml`
  (**file missing** in spapp) · 6 = `aoa_weblink.xml`.
- Called from `SetupModule::exeSmartphoneSetting` (`libsetupmodule`, ~line 3070 of the
  export), which compares a smartphone-link setting string against three
  `SetupStrIdConst` values → modes 0/1/2. Also from `libinfomodule` (modes 0, 1, 3).
  So **wired AA only happens when the "smartphone link" setting picks AA**. The
  setting's INI key is not mapped yet.
- AA AOA identity: `Android / Android Auto`, serial `0000000012345678`, node
  `aoa-skel%d`.

### 8.4 Wireless AA path (*runtime*, 16:37:03–16:37:22)
1. Boot: `wlan0` up → `[AAS][AndroidAutoNetworkServer] create tcp server socket
   success.ip[192.168.0.1] port[8765]` (so `192.168.0.1` from 2.9 is the AP gateway).
2. Phone (already BT-paired) opens RFCOMM to the AA BT service → 13-byte exchange →
   `version response:status[0]`.
3. AA's BT wireless protocol hands the phone the AP's SSID/passphrase/channel
   (`AndroidAutoWirelessManager::getWifiInfo`, `WifiCredentialsRequest/Response`).
4. Phone joins the AP and connects TCP 8765 → `aa_accessory_service` runs
   `Transport_TCP` → `AudioFocusNotify`, `RunningStateNotify`,
   `SourceVideoConfigNotify`, `VideoFocusNotify` at 16:37:21 (~9 s after RFCOMM).
- **Privacy:** `libandroidautomodule` logs `Wifiap Info,retSSID:%s,retpassphrase:%s,retchannel:%s`,
  so the hotspot passphrase ends up in logcat. Redact it from diag captures.

### 8.5 Wi-Fi AP (shared with CarPlay)
- Chip: **RTL8821CS** (`spsdk/drivers/8821cs.ko`, 1×1 Wi-Fi 5 + BT combo), loaded with
  `rtw_vht_enable=2` (`init.rc:347`). The driver has full VHT code (`rtw_vht.c`).
- `startup_wifiap.sh` → `wifi_control --enableap` / `nw_control --ebap wlan0` →
  `networkmanager` → `/usr/local/bin/hostapd` (path hardcoded).
- Config resolution in `networkmanager` (*decompiler + disassembly*):
  `getSoftapCfgFileContent` @ 0x17908 reads **`/media/flash/nvm/softap.conf`** if it
  exists, else `/usr/local/etc/wifimanager/softap.conf`, and parses **every**
  `key=value` line into a `std::map`. `setSoftapCfgFileContent` @ 0x16ef4 (missed by
  Ghidra, read from the Thumb disassembly) writes the whole map back as `key=value\n`
  (PLT: `std::string::operator+=`, `_Rb_tree_increment`, `open64`, `write`,
  **`fsync`**, `close`). So **extra keys survive SSID/password changes from the UI**
  (written back in alphabetical order).
- ROM AP config: `hw_mode=a`, `channel=149`, `ieee80211n=1`, **no `ht_capab`** (so
  HT20 only, 72 Mbps PHY max on 1×1), `beacon_int=15`, WPA2-PSK/CCMP, SSID
  `DEMO-WiFi-AP` with a fixed default passphrase. The `vendor_elements` IE is Apple OUI
  `00:A0:40` (CarPlay wireless: "Car CD/DVD", "Sunplus", "Angel 1.0").
- **802.11ac is not possible with the shipped hostapd.** It's `2.10-rtw-9-g422792039`
  built **without `CONFIG_IEEE80211AC`**: its config parser has `ieee80211n` and
  `ht_capab` but no `ieee80211ac`, `vht_capab`, `vht_oper_chwidth` or `require_vht`
  key strings (only the unconditional `STATUS` printf `ieee80211ac=%d`). Unknown keys
  hit `Line %d: unknown configuration item '%s'`, the config load fails and **the AP
  doesn't start**, which kills wireless AA and CarPlay. *strings; inferred from
  hostapd 2.10 source layout.*
- **HT40 is possible** with the shipped binary: `[HT40+]`, `[SHORT-GI-40]`, the
  `Driver does not support configured HT capability [HT40*]` check and `require_ht`
  are present. Channel 149 + HT40+ (secondary 153) is a valid pair. That doubles the
  PHY rate to 150 Mbps. *Untested on the unit.*
- `hostapd_cli` and `wpa_cli` are on the unit (`spsdk/bin`); there's no `iw`.

---

## 8. Incident: unit rebooted during USB extras (2026-10-25 ~17:19)
- With `extras.flag` set, the unit rebooted twice in a row while the v4 script ran
  (boot_ids `4f26a3fd`, `9030de22`, both started at ~13 s uptime).
- The furthest run wrote `hw.txt` and `getprop.txt`. `uboot_env.txt`, `mcusrv.txt` and
  `tty_driver.txt` were created but empty, so the failure happened during or shortly
  after `fw_printenv` (raw read of the NAND env partition). The step can't be pinned
  down further because FAT writes weren't synced per step. The `nanddump` backups never ran.
- **Action taken:** `extras.flag` removed from the stick. `fw_printenv`, the `nanddump`
  backups and `stty -F /dev/ttyS1` were removed from the script, and per-step `sync` +
  `step … done` markers were added. **Don't re-enable raw NAND / MTD reads from userspace
  on this unit.** Get backups another way (e.g. copy files from the mounted
  `/media/flash/nvm`, which is a normal file read).
- No settings were lost as a side effect: `SETUPMENU.ini` is intact in both
  snapshots, and the unit came back up normally.
- Observations from these two boots (normal snapshot part):
  - Boot restores `audio.delay=0,…` to all channels, then ~0.4–0.5 s later something
    sets **channel 0x1 delay 1.0** (`ClientAppSetting.cpp setSpeakerDelay`). Seen on both
    boots, so there's a second boot-time delay source (Emperor mode preset?). *runtime*
  - The `ResumeInfo.ini` SpeakerExec blob went from `01@+0x0, 01@+0xb` (session
    `0114eb58`) to all zeros after the car-off. That's either the user's final state
    (Loudness off) or the shutdown save writing empty state. *Ambiguous; recheck.*

---

## 9. Root cause: Emperor (DSP master) not restored after an MCU memory reset
**User observation (2026-10-25):** Emperor is the setting that enables the EQ and all
other sound processing. With it off, everything sounds unprocessed even though the EQ
values are saved (5.7). So "EQ not remembered" is really "Emperor not restored".

- `SetupExec::loadSystemSettings` @ 0x41ef1cd0 (`libinfomodule.so`, prelinked
  0x41eb0000) *confirmed in assembly*:
  ```
  41ef1d22  ldrb r3,[r5,#4]     ; byte 0x41f39854 = MCU RstMemory flag (same as 5.2)
  41ef1d26  cmp  r3,#0
  41ef1d28  beq.w 0x41ef1f24    ; flag==0 → restore path
  ```
  - flag == 0 → 0x41ef1fdc `SetupExec::DSPDataResume` (load ResumeInfo blob) →
    `exec_dspMasterSwitch` → `exec_dspMasterEmperorDelay` → `exec_dspMasterBalance`.
  - flag != 0 → fall through: `setSystemInitDateTime`, 8× `DataDefault::getDefault`,
    then the same `exec_dspMaster*` calls **without** `DSPDataResume`. The defaults
    get applied.
  (Calls identified by decoding the ARM PLT stubs to GOT relocs.)
- This explains the timing pattern the user reported: a short off keeps MCU memory
  (flag 0) so it's restored, while overnight the MCU resets (flag 1) and you get defaults. It
  also explains the blob going `01…` → all zeros (8): a default boot's in-RAM state is
  what the next shutdown `DSPDataSave` writes back, which overwrites the user's saved
  state.
- `SpeakerExec::DSPDataResume_Off/_On` (0x41efa082 / 0x41ef9e74) restore 0x27 + 4
  bytes, or call `setting_ToDefault` / `DSPSetting_ToDefault` if the key is missing or
  the wrong size. That's a safe fallback for first boot.

**Patch B (built, NOT installed):** `libinfomodule.so` file offset **0x41d2b**:
`0x80` → `0xb8`, so `00 f0 fc 80` (`beq.w 0x41ef1f24`) becomes `00 f0 fc b8`
(`b.w 0x41ef1f24`). The restore path then always runs. Verified by disassembly. Original md5
`f00b52a739bb333307ee9a841bd02369`, patched `fae35255cfc28d530fa100af5cab4c4d`.
Side effect: on a real MCU reset the date/time init (`setSystemInitDateTime`) and the
8 defaulted `exec_adjust` values are also skipped. Those come from saved settings
instead (*inferred*; check what the 8 `getDefault` keys are before shipping).
Pairs with patch A (5.2) for the EQ itself.

**Still to confirm on the unit:** that a "next-day" boot really has RstMemory≠0. The
next-day USB snapshot should show the Loudness/Emperor defaults applied and the blob
reset after that session's shutdown.

---

## 10. nvm library override: proof run (2026-10-25, stock copy)
- Installed an **unmodified** `libinfomodule.so` (md5 `f00b52a7…`) to
  `/media/flash/nvm/` with `tools/usb-patch` (boot `afe28939`: `install: OK`, md5 verified).
- After a car off/on (boot `24b1c425`), `/proc/711/maps` (Launcher) shows **both**
  `/tmp/sp/media/flash/nvm/libinfomodule.so` and
  `/tmp/sp/application/lib/module/default/libinfomodule.so`. *runtime*
  - The nvm copy is loaded as Launcher's `NEEDED` dependency (search path). The stock
    copy is loaded a second time by full path, presumably by the module manager, since
    `libinfomodule` is also a plugin. There's no SONAME, so the two aren't deduplicated.
  - The UI came up normally on both boots.
- **Does a patch in the nvm copy take effect?** `SetupExec::loadSystemSettings` is only
  called from inside libinfomodule, **through its PLT** (stub 0x41ee444c), and no other
  binary imports it. PLT binding uses the global scope, where the startup dependency
  (the nvm copy) comes first, so the patched function should run. *Inferred from
  dynamic-linker rules; confirm with the overnight test.*
- Caveat: two copies mean two sets of non-exported static data. That's harmless with
  identical code (as observed). Keep patches to code-path changes that don't depend
  on which copy's statics are used.
- **Patch B review (the 8 defaults):** both branches of `loadSystemSettings` call the same
  `exec_adjust(0x64..0x6d)` (picture adjust), `exec_cvbs_out_type`, the dimmer, and
  the `exec_dspMaster*` trio. The restore branch uses saved values, the reset branch uses
  `DataDefault::getDefault`. Patch B only additionally skips `setSystemInitDateTime()`
  on an MCU reset (clock init). Acceptable.

---

## 11. On-device storage layout (from USB snapshot `24b1c425_1`, 2026-10-25)
*runtime*: `/proc/mtd`, `/proc/mounts` and `df` captured by `tools/usb-diag`.

### 11.1 NAND MTD table (as the kernel sees it)
Erase block 0x20000 (128 KiB) everywhere. Sizes are the on-NAND partition sizes, which
are larger than the image slots in 2.3.

| mtd | Name | Size | Notes |
| --- | --- | --- | --- |
| 0 | nand_header | 128 K | boot header |
| 1 | xboot1 | 128 K | |
| 2 | uboot1 | 896 K | |
| 3 | uboot2 | 1.9 M | |
| 4 | env | 512 K | U-Boot env (`fw_env.config`: `/dev/mtd4`). **Don't read raw from userspace** (8) |
| 5 | env_redund | 512 K | |
| 6 | ecos | 4 M | |
| 7 | kernel | 4.5 M | |
| 8 | rootfs. | 3.4 M | squashfs, ro |
| 9 | opt. | 384 K | squashfs, ro |
| 10 | spsdk. | 46 M | squashfs, ro → `/usr/local` (100% full) |
| 11 | spapp. | 14.6 M | squashfs, ro → `/application` (fs 7.7 MB) |
| **12** | **nvm** | **16 M** | **YAFFS2 rw → `/media/flash/nvm`** |
| 13 | pq | 128 K | |
| 14 | logo | 2.4 M | |
| 15 | tcon | 128 K | |
| 16 | iop_car | 256 K | |
| 17 | runtime_cfg | 1 M | |
| 18 | vi | 128 K | |
| 19 | isp_logo | 9.5 M | |
| 20 | vendordata | 256 K | `VENDORDATA` logcat tag reads it at boot |
| 21 | pat_logo | 11.8 M | |
| 22 | version_info | 256 K | |
| 23 | vd_restore | 256 K | |
| 24 | anm_logo | 5 M | |
| **25** | **userdata** | **132 M** | **YAFFS2 rw → `/media/flash/userdata`** |

### 11.2 Mounts and the `/tmp/sp` prefix
- Real mount points live under **`/tmp/sp/…`** (`/tmp/sp/application`,
  `/tmp/sp/usr/local`, `/tmp/sp/media/flash/nvm`, USB at `/tmp/sp/mnt/sda1`). The
  familiar paths (`/application`, `/media/flash/nvm`, …) resolve to them. That's why
  `/proc/<pid>/maps` shows `/tmp/sp/...` paths (10). Match on the suffix in scripts.
- `/etc`, `/root`, `/tmp` are tmpfs (RAM, ~28 MB shared, wiped every boot).
  `/system/etc` is an overlay on `/usr/local/etc` with a tmpfs upper layer
  (`sys_setup.sh`).
- `nvm` and `userdata` are mounted `yaffs2 rw,noatime` (no `noexec`), so binaries
  stored there can run.

### 11.3 Writable storage and what goes where
| Area | Size / free | Auto-loaded? | Use for |
| --- | --- | --- | --- |
| `/media/flash/nvm` | 16 MB / 12.2 MB free (after the 536 KB proof-run lib) | **yes**: first in `LD_LIBRARY_PATH` | library overrides **only**. Keep ≥ 8 MB free: it also holds all settings, and a full nvm would make settings saves fail |
| `/media/flash/userdata` | 132 MB / 130 MB free | no | tools, logs, backups, experiments (must be called explicitly) |
| `/tmp` (tmpfs) | ~28 MB | no | scratch per boot (diag recorder lives here) |
| USB stick | 30 GB | via `gemn_auto.sh` hook | one-off runs; nothing lands on the unit |

### 11.4 nvm contents (2026-10-25)
| File | Size | Notes |
| --- | --- | --- |
| `isp_part.bin` | 2,400,336 | **unknown**, biggest file; probably left over from an update. Inspect a copy's header before any conclusion; don't delete |
| `libinfomodule.so` | 536,128 | ours (proof run, stock bytes) |
| `Sunplus/SETUPMENU.ini` | ~2.8–4.9 K | settings |
| `Sunplus/ResumeInfo.ini` | <1 K | resume + SpeakerExec DSP blob |
| `sd.db` | 4,918 | media DB? |
| **garbled name** (`????…`) | 4,918 | same size as `sd.db` / one `SETUPMENU.ini` copy. Possibly a stray QSettings/QSaveFile temp file or a YAFFS name issue. Check from a snapshot copy; don't delete |
| `touch_conf`, `touch_conf.md5`, Apple auth cache, `dfb_crash.dump` | — | referenced by init scripts (2.6) |

---

## 12. Shipped patch state on the stick (recovered 2026-10-25, after the rorqual worktree was lost)
*runtime* = `x3patch/log.txt` and `wifi40/log.txt` on the stick; *parsed* = checks run on the files.
- **Installed on the unit by the 21:04 run:** `/media/flash/nvm/libinfomodule.so` md5
  `68f0bcf18a4790f749e47fa44bf303c5` (patch B + all-language UI strings), replacing the
  stock-copy proof run. Needs a cold boot to load; **not yet confirmed loaded** (no run after 21:04).
  Undo: `x3patch/mode` = `uninstall`.
- **wifi40:** `ht_capab=[HT40+]…` added to `/media/flash/nvm/softap.conf`; after a cold boot
  hostapd came up with `secondary_channel=1`, `cur_bwmode=2` (boots 5eced8c3, dbce6923). Backup is
  `softap.conf.pre-wifi40` on the unit.
- **Rebuild:** `tools/build_payload.py <stock .so> tools/strings/tables_final.json <outdir>` gives
  md5 `0b1ab572…` (not the stick's `68f0bcf1…`: the blob is laid out differently). *Parsed*:
  all 417 strings × 20 language tables decode identically between the two. The stick payload was
  built by a script that was never committed; `tables_final.json` is its recovered input.
  If you want the rebuilt one on the stick, its md5 goes in `payload/libinfomodule.so.md5`.
- `nl` has no table in this build (NULL), so it can't be translated by this method (see strtab.py).

## 13. Home date format YYYY-MM-DD (2026-10-26, exploration; detail in `docs/discoveries/date-format-ddmmyyyy.md`)
- `HomeWidget::timeDateSlot()` @ 0x13b48 (`libhomelauncher.so`, md5 `a29d979a…`) formats the Home date with
  `QDateTime::toString("yyyy-MM-dd")`; the pattern is at file offset 0x1846b and has exactly one code reference
  (0x13b5e/0x13b66). *strings, assembly*
- No other date pattern exists in `spapp`. Bars and tips show `hh:mm`/`hh:mm AP` only (`BaseBar::showTimer`,
  `ComTipsWidget::initTimeSlot`). Setup > Time uses separate Year/Month/Day columns (`Ui_SetupTimeForm::setupUi`
  @ 0x41b08, `libsetupviews`). *assembly, decompiler*
- Candidate patch: 10 bytes at 0x1846b, `yyyy-MM-dd` to `dd-MM-yyyy`. Prepared offline only; not run on the unit.
- Delivery: the lib is an activity plugin loaded by full path (`/application/lib/activity/default`,
  `QPluginLoader`). *Runtime* (2026-10-26): with the patched copy in `/media/flash/nvm` and a cold boot, the Launcher
  still mapped only the `/tmp/sp/application/...` copy, so the nvm override does not reach plugins. Parked until a
  reflash path exists (or a bind mount before Launcher starts).
