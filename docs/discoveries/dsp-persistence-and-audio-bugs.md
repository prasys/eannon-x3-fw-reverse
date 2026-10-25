# What the DSP page saves, and other audio bugs
**Status:** analysis done, fixes not written. FINDINGS 5.1, 5.5, 5.7–5.9.

## Where settings live
`DataSaveControl` (`libsysset.so`) wraps `QSettings` under `/media/flash/nvm`:
`Sunplus/SETUPMENU.ini` (settings) and `Sunplus/ResumeInfo.ini` (resume data, SpeakerExec blob).
nvm is YAFFS2. Qt 5.3.2 core imports no `fsync`, so writes rely on the kernel flushing.

INI keys (from a runtime capture): EQ curve `audio.EQ.custom`, EQ mode `system.EqMode`, delay `audio.delay`,
filters `audio.filterfront|rear|sub`, Bongiovi `audio.bongiovi.{dps,mode,stereo,usergain}`, balance
`audio.BaderBalance.{balance,fader}`. Key names differ from the C++ symbol names.

## What `libcomdspviews.so` persists
| Call site | Key | Called? |
| --- | --- | --- |
| `DspEQView` mode | `setupEqMode` | yes |
| `DspEQView::saveEqSetting` @ 0x2122c | `setupAEQCustom` | yes |
| `DspVbassView::exeDpsSetting` @ 0x2d144 | `setupDpsState` | yes |
| `DspEmperorView::saveSetting` @ 0x2e476 | `setupAudioDelay` | **never** |

Filter/crossover, Loudness, VBass sliders and DSP balance are never written to flash by this library.
Loudness and the DSP switches persist only through the shutdown blob (`SpeakerExec::DSPDataSave`, 0x27+4 bytes
from .bss 0x41f39c48). Blob map: +0x0 DSP switch 0, +0x6 Emperor mode, +0x7..0x9 Emperor delays,
+0xb Loudness enable, +0xd Loudness freq (0 → 400 Hz, 1 → 800, 2 → 2400). *assembly + runtime*

## Shutdown path
`McuCallback::updateSystemPowerState` @ 0x41ef2fe4: `RadioDataSave` → `SetupExec::DSPDataSave` →
`SpeakerExec::DSPDataSave` → `send_mcu_power_status` ack. If the radio module lookup returns null the code
may skip both the save and the ack (*unverified*; decompiler output was tangled).

## Other audio bugs
- **DPS / Bongiovi toggle does nothing.** Every enable logs `AUDIF_DpsCmd 1257 ERR: plz check dps is enabled
  in DSP`. The DSP firmware probably lacks DPS. *runtime*
- **Garbage 20-band EQ sent to the DSP.** Each DSP change and boot sends `APP_CMD_SET_EQ_ext 20band` with
  `fc:6`, `q:-1.79e36`, `gain:-nan`, often ending `set eq failed`. Suggests an uninitialised struct on the
  ext-EQ path (`AudioControlPrivate::setEqValue16`?). Not yet traced. *runtime*
- A second boot-time delay source sets channel 0x1 delay to 1.0 about 0.4 s after the defaults. *runtime*

## Open
Emperor-only test (change only Emperor, snapshot, full off/on, snapshot); hard power-off test for the
shutdown save; trace the garbage EQ params in `libapis`.
