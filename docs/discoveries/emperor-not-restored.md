# Emperor (DSP master) not restored after an MCU memory reset
**Status:** root cause found, patch B built and installed (not yet confirmed on the unit). FINDINGS 9, 5.3.

## Symptom
EQ and DSP settings seem lost after car off. Emperor is the switch that enables the EQ and
all sound processing, so with it off everything sounds unprocessed even though the EQ values are saved.

## What we found
- `SetupExec::loadSystemSettings` @ 0x41ef1cd0 (`libinfomodule.so`, prelinked 0x41eb0000) reads the
  MCU RstMemory flag (byte 0x41f39854) at 0x41ef1d22 and branches at 0x41ef1d28
  (`beq.w 0x41ef1f24`). *assembly*
  - flag == 0: `SetupExec::DSPDataResume` loads the saved blob, then `exec_dspMasterSwitch`,
    `exec_dspMasterEmperorDelay`, `exec_dspMasterBalance`.
  - flag != 0: factory defaults are applied and the saved blob is skipped. The next shutdown
    `DSPDataSave` then writes those defaults over the user's state.
- This matches the pattern reported: a short off keeps MCU memory (flag 0) and works, an overnight off
  resets it (flag 1) and gives defaults. *The overnight link is inferred, not yet observed.*
- Emperor delays are also never written to `SETUPMENU.ini`: `DspEmperorView::saveSetting` @ 0x2e476
  (`libcomdspviews.so`) has **no callers** (byte scan + reloc listing). Emperor only survives through the
  `SpeakerExec` blob in `ResumeInfo.ini` (+0x6 mode, +0x7..0x9 delays), saved only at shutdown.

## The fix (patch B)
`libinfomodule.so` file offset 0x41d2b, `0x80` → `0xb8`: `beq.w` becomes `b.w`, so the restore path always
runs. Stock md5 `f00b52a7…`, patched `fae35255…`. Side effect: on a real MCU reset `setSystemInitDateTime`
is skipped too. Reviewed as acceptable (FINDINGS 10). Descriptor: `patches/patch-b-emperor-restore.json`.

## Still to confirm on the unit
- That the library loads from nvm and Emperor survives an overnight off.
- That the "next day" boot really has RstMemory != 0.
