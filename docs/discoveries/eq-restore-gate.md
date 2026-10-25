# EQ restore gate and the classic EQ screen
**Status:** patch A designed, not delivered. FINDINGS 5.2, 5.4, 5.7.

## Findings
- `SetupModule::exeSetupEq` @ 0x1a9d4 (`libsetupmodule.so`, file 0xa9d4) calls
  `getSystemPowerState` and, if it returns non-zero, applies defaults instead of the saved EQ (`cbz r0` at
  0xaa1e). *assembly*
- `getSystemPowerState` is misnamed: it returns the **MCU RstMemory flag** (byte 0x41f39854), the same one
  `McuCallback::updateSystemRstMemory` sets. *assembly*
- `initVolumes` also calls it, so volume may be gated the same way (not checked).
- At runtime the EQ **was** remembered (`audio.EQ.custom` = 16 values, `system.EqMode=7`), so the gate did not
  fire on that boot. Patch A is defensive. *runtime*
- Separate bug: `ComEqView::btnEqRelseaseSlot` @ 0x1acb6 (`libcomeqviews.so`) saves the preset's values over
  the custom curve when cycling modes. The in-memory User curve hides it until reboot. The same pattern
  probably exists in `DspEQView::btnReleasedSlot` @ 0x218e0 (not read). *decompiler*

## Patch A (not applied)
`libsetupmodule.so` 0xaa1e: `90 b1` → `12 e0` (`cbz` → `b`). Only apply if the EQ is seen reset while
`audio.EQ.custom` is intact. `libsetupmodule` is a plugin loaded by path, so check how it is loaded before
trying the nvm override for it. Descriptor: `patches/patch-a-eq-restore-gate.json`.
