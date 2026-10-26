# Where to find things

A lookup table for the X3 firmware: which file, library or function owns which
behaviour. Addresses are as Ghidra shows them unless marked *file/vaddr* (Ghidra
rebases non-prelinked libs to 0x10000, so Ghidra 0x1a9d4 = vaddr/file 0xa9d4 in
those). The evidence behind each entry is in `FINDINGS.md`.

## Inputs (not in repo)

| What | Where |
| --- | --- |
| SoC update image | `fw/X3_20250718.zip` → `ISPBOOOT.BIN` (sha256 `93d14858…cf1e2`) |
| MCU image | `fw/X3-series MCU_240723.zip` → `mcu8368u.bin` (JieLi AC690x, encrypted) |
| USB diag captures | the `X3DIAG` stick → `diag/<boot_id>_<n>/` (personal data, keep out of git) |

## ISPBOOOT.BIN layout
See `FINDINGS.md` 2.3 for the full partition table. The ones you'll actually touch:

| Partition | File offset | Size | Mounted at | Contains |
| --- | --- | --- | --- | --- |
| rootfs. | 0x842000 | 0x217000 | `/` | init `.rc`, `sbin/*.sh`, `etc/` (USB hook) |
| spsdk. | 0xa97000 | 0x2ce9000 | `/usr/local` | Sunplus daemons, Qt 5.3.2, AudioService |
| spapp. | 0x3780000 | 0x7a6000 | `/application` | UI + app logic (all the bugs so far) |
| ISP script | 0x4937800 (+0x40 hdr) | 0xacc5 | — | flash script; holds per-partition md5s |

## Runtime paths on the unit

| Path | What |
| --- | --- |
| `/media/flash/nvm/Sunplus/SETUPMENU.ini` | `DataSaveControl::save/readSettingData` (all settings) |
| `/media/flash/nvm/Sunplus/ResumeInfo.ini` | `save/readResumeData` (radio, SWC, last file, legacy DSP blobs) |
| `/media/flash/nvm` | YAFFS2, MTD `nvm` (`init.platform.rc:6`) |
| `/tmp/sp/mnt/sda1` | where the USB stick is mounted |
| `/media/flash/userdata` | YAFFS2 rw, mtd25, 132 MB (130 MB free). Not auto-loaded; use for tools/backups |
| `/tmp/sp/...` | real mount roots; `/application`, `/media/flash/nvm` etc. resolve here (shows up in `/proc/*/maps`) |
| nvm size | 16 MB (mtd12), ~12 MB free; keep ≥ 8 MB free (settings live here). Full layout: FINDINGS 11 |
| `/usr/local/bin/logcat` | `logcat -d` dumps the Android-style log |

## Settings plumbing

| Thing | Library | Symbol / address |
| --- | --- | --- |
| INI read/write wrapper | `spapp/lib/libsysset.so.1.0.0` | `DataSaveControl::saveSettingData` @ 0x425db794, `readSettingData` @ 0x425db82c, `initQSettingPath` @ 0x425db764 |
| Setting key names | `libsysset` | `SetupStrIdConst::*` globals (`.bss`, filled at init) |
| Factory defaults | `libsysset` | `DataDefaultPrivate::initEQDefaultSetting` @ 0x425dce8c, `initAudioSetting`, `initDefaultSetting`; `DataDefault::getDefault` |
| Boot-time audio restore | `spapp/lib/module/directstage/libsetupmodule.so` | `SetupModule::exeSetupAudioSetting` @ 0x1b01c → `exeSetupEq` @ 0x1a9d4, `exeDelay` @ 0x18c70, `exeFilter` @ 0x1ace0, `exeBongiovi` @ 0x18ab8 |
| EQ restore gate (patch A site) | `libsetupmodule` | *file* 0xaa1e `cbz r0,0xaa46` |

INI key ↔ `SetupStrIdConst` (from the runtime capture):
`audio.EQ.custom` = `setupAEQCustom` · `system.EqMode` = `setupEqMode` ·
`audio.delay` = `setupAudioDelay` · `audio.bongiovi.dps` ≈ `setupDpsState` ·
`audio.filter{front,rear,sub}` · `audio.BaderBalance.{balance,fader}`.

## Audio UI → DSP

| Screen | Library | Key classes | Persists? |
| --- | --- | --- | --- |
| Classic EQ | `spapp/lib/libcomeqviews.so.1.0.0` | `ComEqView` (`saveEqDataSlot` 0x1af8c, `btnEqRelseaseSlot` 0x1acb6, `initData` 0x1a6f4) | yes (preset-cycle overwrite bug) |
| DSP menu | `spapp/lib/libcomdspviews.so.1.0.0` | `DspMenuView`, `SubDspView` | — |
| DSP EQ | `libcomdspviews` | `DspEQView` (`saveEqSetting` 0x2122c) | yes |
| Emperor / time alignment | `libcomdspviews` | `DspEmperorView` (`saveSetting` 0x2e476, **dead**; `updataEmperorMode` 0x2e640) | **no** |
| Filter / crossover | `libcomdspviews` | `DspFilterView`, `FilterMoveArea` | **no** |
| Loudness | `libcomdspviews` | `DspLoudnessview` | not in SETUPMENU; **yes via SpeakerExec blob at shutdown** (+0xb/+0xd) |
| VBass + DPS | `libcomdspviews` | `DspVbassView` (`exeDpsSetting` 0x2d144 saves DPS only) | DPS key only; **DPS itself fails in the DSP** (`AUDIF_DpsCmd` error) |
| DSP balance | `libcomdspviews` | `DspBalanceView` | **no** (unless a signal saves it, unchecked) |
| App-side audio API | `spapp/lib/libapis.so.1.0.0` | `AudioControl::{setEqValue,setSpeakerDelay,setDpsEnable,…}` → AudioService | — |
| Audio daemon | `spsdk/bin/as_server` et al. | logs as `AudioService` (`ClientAppSetting.cpp`, `SpAudSysController.cpp`) | — |

Useful AudioService log lines (`logcat -v time`): `ClientAppSetting.cpp setSpeakerDelay 588`
(delay applied), `SpAudSysController.cpp control 648` (loudness), `control 744/766/777`
(20-band ext EQ send / per-band params / failure), `control 1020/1024` +
`auddrv_linux.cpp AUDIF_DpsCmd 1257` (DPS enable/failure), `control 1157` (per-channel
delay).

## Power / MCU

| Thing | Library | Symbol / address |
| --- | --- | --- |
| MCU UART daemon | `spapp/bin/mcusrv`, `spapp/lib/libmcusrv.so` | `mcusrv 1 115200` → `/dev/ttyS1`; protocol in `MCU_PROTOCOL.md` (RX dispatch `FUN_00014554`, framing `FUN_0000ec04`/`FUN_0000f9e8`) |
| App-side MCU API | `libapis` | `MCUControl::*`, `send_mcu_power_status`, `send_mcu_mute` |
| MCU callbacks into the app | `spapp/lib/module/default/libinfomodule.so` | `McuCallback::updateSystemPowerState` @ 0x41ef2fe4 (shutdown save path), `updateSystemRstMemory` @ 0x41ef3138 |
| "Power state" / RstMemory flag | `libinfomodule` | `SetupExec::getSystemPowerState` @ 0x41ee6584 → byte 0x41f39854 |
| DSP state engine (**live**, saved at shutdown) | `libinfomodule` | `SpeakerExec::*`, state blob 0x41f39c48 (+0x2b) → `ResumeInfo.ini` `[DSPOff Data]` (+0x0..0x26) / `[DSPOn Data]` (+0x27..0x2a). Save `DSPDataSave` @ 0x41ef9d40, restore `DSPDataResume` @ 0x41efa154 |

### SpeakerExec blob layout (base 0x41f39c48; FINDINGS 5.9)

| Offset | Field | Evidence |
| --- | --- | --- |
| +0x0 | DSP switch 0 | `setDSPSwitch` case 0 |
| +0x1 | switch 1 "adjust" flag | `setDSPSwitch` case 1 |
| +0x5 | switch 3 | `setDSPSwitch` case 3 |
| +0x6 | Emperor mode | `set/getEmperorMode`, `execEmperor` |
| +0x7..0x9 | Emperor delays ch0..2 | `setEmperorDelay` |
| +0xb | Loudness enable | `getLoudnessEnable` (`ldrb #0xb` @ 0x41efa166) |
| +0xd | Loudness freq (int: 0=400 Hz, 1=800, 2=2400) | `getLoudnessFreq` (@ 0x41efa178) |
| +0x27 | switch 1 value | `setDSPSwitch` case 1 |

## Boot wiring

| File (rootfs unless noted) | What |
| --- | --- |
| `init.rc` | services, `sdk-boot`, launcher property triggers, `logserver` |
| `init.platform.rc` | mounts (squashfs ro, YAFFS2 nvm/userdata) |
| `init.gui.rc` | `LD_LIBRARY_PATH` (**`/media/flash/nvm` comes first**, so a lib dropped there overrides system libs; *inferred*, untested) |
| `sbin/sys_setup.sh` | early mounts, overlay on `/system/etc` |
| `etc/mdev.conf` + `etc/usb_action_8368-U` | USB autorun of `gemn_auto.sh` |
| spapp `appinfo.rc` | starts `mcusrv` |

## Home date / clock (FINDINGS 13)
| What | Where |
| --- | --- |
| Home date format string `yyyy-MM-dd` | `spapp/lib/activity/default/libhomelauncher.so` file offset 0x1846b (read by `HomeWidget::timeDateSlot` @ 0x13b48) |
| Activity plugin dir (hardcoded) | `/application/lib/activity/default`, `ActivityManagerImpl::loadDefaultActivity` in `libappframework` |
| Setup > Time screen layout | `Ui_SetupTimeForm::setupUi` @ 0x41b08, `libsetupviews.so.1.0.0` |

## Android Auto / Wi-Fi AP (FINDINGS 8)

| Thing | Where |
| --- | --- |
| AA manager daemon | `spsdk/bin/aa_manager_service` (`aaserver`, `init.rc:257`; TCP server `192.168.0.1:8765`) |
| AA session process | `spsdk/bin/aa_accessory_service`, `execl`'d by the manager (not in any `.rc`); embedded Google SDK certs @ file 0x12c124–0x12d5fc |
| App module / UI | `spapp/lib/module/libandroidautomodule.so` (`AndroidAutoModuleImpl`, `AndroidAutoWirelessManager`), `spapp/lib/activity/libandroidauto.so` |
| AA config actually used | `/application/etc/Androidauto/androidauto_config.xml` (1024×600, 480P+720P) |
| AOA identity XMLs | `/usr/local/etc/aoa_feature/aoa_*.xml`, picked by `UsbMControl::setAndroidDeviceMode` (`libapis`; 0=AA 1=Autolink 2=off 3=CarLife 5=EasyConnect 6=WebLink) |
| Who picks the AOA mode | `SetupModule::exeSmartphoneSetting` (`libsetupmodule`) |
| AP config (live, writable) | `/media/flash/nvm/softap.conf` (falls back to ROM `/usr/local/etc/wifimanager/softap.conf`); `networkmanager` read @ 0x17908, write @ 0x16ef4 (keeps all keys) |
| hostapd | `/usr/local/bin/hostapd` v2.10-rtw, **no 802.11ac support compiled in**; HT40 OK |
| Wi-Fi driver | `spsdk/drivers/8821cs.ko rtw_vht_enable=2` (`init.rc:347`) |
| On-unit tools | `hostapd_cli`, `wpa_cli` (no `iw`) |

## Added 2026-10-26 (see FINDINGS 14)
| What | Where |
| --- | --- |
| AA car identity (make/model/year) | `spapp/etc/Androidauto/androidauto_config.xml` `CarInfo`; path string in `libandroidautomodule.so` file 0x37954 |
| Reverse guide-line bitmap / palette | `libapis.so.1.0.0` file 0x623b4 (1024x233, 8bpp) / 0x61fb4; position immediates at 0x5065a |
| Driving side setting | `SETUPMENU.ini` `system.RightDrive` |
| Clock path | MCU 0x36/0x3d → `MCUControlPrivate::updateSystemTime/Date` → `SetupExec::setSystemTime/Date` → `stime` |

## Day/night and ILL (FINDINGS 15)
| What | Where |
| --- | --- |
| MCU 0x37 detect handler | `mcusrv` `FUN_000139fc` (file 0x139fc) |
| ILL/brake edge detector, shadow 0x41dc680c | `libapis` `MCUControlPrivate::updateSystemDetectState` 0x41d5f5a8; `DetectIll` 0x41d5f294 |
| ILL consumer (dimmer state, AA/CP night sigs) | `libinfomodule` `McuCallback::updateSystemILLActivation` 0x41ef35d0 |
| Dimmer state set / persist | `SetupExec::exec_dimmer_state` 0x41eee2e0, `exec_dimmer_state_only` 0x41eea68c, `systemDimmerState` 0x41eea5d0 |
| Boot restore + night-only tail | `SetupExec::loadSystemSettings` 0x41ef1cd0 |
| Keys | `setupDimmerState`, `setupDimmerLevel`, `setupIllCtrl` (INI `.DimmerState`, `.DimmerLevel`, `.IllCtrl`) |

