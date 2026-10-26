# Day/night (dimmer state) stale vs headlight (ILL)
**Status:** fixed on the unit by patch C (FINDINGS 15.8, 15.9); owner confirmed in the car. Not committed. Evidence: FINDINGS 15.

## Symptom
Day/night mode that should track the auto-headlight signal stays at its old value (e.g. dark after the lights go off).

## What we found
- Day/night is `SetupExec` dimmer state (0xa5 day / 0xa6 night), persisted as `setupDimmerState` (bool, true = night). *decompiler*
- ILL arrives as bit 2 of MCU cmd 0x37; `libapis` `MCUControlPrivate::updateSystemDetectState` @ 0x41d5f5a8 raises the ILL callback only when
  `old ^ new == 4` exactly, then overwrites the shadow (0x41dc680c, .bss) every time. *assembly*
- `updateSystemILLActivation` @ 0x41ef35d0 flips the dimmer state and emits constant 1/0 to the CarPlay/AA night signals. *decompiler + assembly*
- `loadSystemSettings` restores the persisted night bit when RstMemory == 0 and its tail can only go day to night. *decompiler*

## Hypothesis
Stuck dark after a short off (persisted night, no edge, no night to day branch at boot); missed edges when ILL changes with another bit; zero shadow at start.

## Patch C (built, not installed)
`patches/patch-c-daynight.json`, FINDINGS 15.8: saved night is restored as day at boot, the live ILL decides. md5 `13dc16ce…` on top of the installed payload. Test 2 decides whether the libapis change is also needed.

## Fix options
libapis `& 4` / first-message patch, and/or a day branch in the `loadSystemSettings` tail (libinfomodule). Both libs load from the nvm override. Not designed byte-for-byte.

## Runtime check
Test 1 reproduced on 2026-10-26: lights on at 16:09 (`DimmerState=true`, correct), car off with lights on, restart at 16:12 still dark until the switch was flicked (`DimmerState=true`); keys are `panel.DimmerState`, `panel.DimmerLevel`, `system.IllCtrl=1`. App debug lines are not in logcat, so ILL events are not observable this way. Overnight-reset-to-day is unproven.

## To confirm
Read-only diagnostic in FINDINGS 15.6. Open: 0x37 bit layout, `setupIllCtrl` value on the unit, UI theme consumer beyond `ComTipsWidget`.
