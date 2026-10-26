# Date format: YYYY-MM-DD to DD-MM-YYYY
**Status:** analysed, patch bytes prepared and checked offline, nothing tested on the unit. Exploration only.
Evidence tags: *parsed / strings / assembly / decompiler / runtime / inferred*. Firmware `X3_20250718`.

## Where the date is shown (whole `spapp` tree searched)
| Place | Code | Format | Evidence |
| --- | --- | --- | --- |
| **Home screen widget** (date label; weekday and clock are separate labels) | `HomeWidget::timeDateSlot()` @ 0x13b48 in `spapp/lib/activity/default/libhomelauncher.so` | `QDateTime::currentDateTime().toString("yyyy-MM-dd")` | *strings* (one hit for the pattern in every ELF under `fw/fs/spapp`, `spsdk`, `opt`), *assembly* (the only literal-pool reference to it is the `ldr/add pc` pair at 0x13b5e/0x13b66, followed by the `toString` PLT call at 0x13b76) |
| Weekday next to it | same function, `"ddd"` @ 0x18476, via `_time_Week` mapping | not a date part | *assembly* |
| Status/title bar clock | `BaseBar::showTimer()` in `libcomviews.so.1.0.0` | `"hh:mm"` only | *assembly* (string ref resolved) |
| Tips/overlay clock | `ComTipsWidget::initTimeSlot()` in `libcomviews.so.1.0.0` | `"hh:mm"` / `"hh:mm AP"` | *assembly* |
| BT call timer, list helpers | `BtTalkingView::timerEvent` (`"hh:mm:ss"`), `*TableList::getCurrentTime` (`"ss"`) | time only | *assembly* |
| **Setup > Time screen** | `Ui_SetupTimeForm::setupUi` @ 0x41b08, `SetupTime::_OnTouchYear/Month/Day` in `libsetupviews.so.1.0.0` | separate columns, order Year, Month, Day (see below) | *decompiler* geometry, *assembly* |
| Media, DAB, radio, CarPlay/AA/CarLife | no date strings or `QDate`/`toString` date use found | none | *strings* |

No other `yyyy`/`MM`/`dd`/`%d-%d` style date pattern exists in the Qt UI libs. ffmpeg/hostapd/etc. hits are noise and not UI.
So the "date" the owner sees in YYYY-MM-DD is the Home widget; the setup screen is column-based, not a format string.

## Patch 1 (home date): in-place, same length
`"yyyy-MM-dd"` and `"dd-MM-yyyy"` are both 10 bytes, so no code changes and no relocation.
- File: `spapp/lib/activity/default/libhomelauncher.so` (stock md5 `a29d979a7540d312bea3bf1d8f0c3891`, sha256 `9d315044ab3e46ab5ae6abccb856af5a7473e50c12268ad95ba38f0b64c19ec9`, 431276 bytes).
- File offset **0x1846b** (`.rodata` VMA 0x165b8, file offset == VMA for this lib; `strings -t x` gives 0x1846b and the pc-relative load resolves to the same address). *parsed*
- Old: `79 79 79 79 2d 4d 4d 2d 64 64` (`yyyy-MM-dd`)
- New: `64 64 2d 4d 4d 2d 79 79 79 79` (`dd-MM-yyyy`)
- Result: 10 bytes differ (`cmp -l`), patched md5 `c08c872fe8426fc196ba0abe126c2d8e`, sha256 `170c654e1b1c42de5b7dccee971bedc0b8477a732d20a7295ad67703f8b59b19`. *parsed* (scratch copy only, not committed).
- The string is NUL-terminated, followed by `ddd\0` at 0x18476; nothing else references 0x1846b..0x18475 (checked all `ldr/add pc` pairs). The label is a `QLabel`; same character count, so no layout change.
- Reproduce: `python3 -I -c "b=bytearray(open(P,'rb').read()); assert b[0x1846b:0x18475]==b'yyyy-MM-dd'; b[0x1846b:0x18475]=b'dd-MM-yyyy'; open(OUT,'wb').write(b)"`.
- Variants if wanted (same length only): `dd/MM/yyyy`, `dd.MM.yyyy`.

## Delivery: the nvm library override probably does NOT reach this file
- The `nvm` override works for libs resolved by soname through `LD_LIBRARY_PATH` (`init.gui.rc:4`). `libhomelauncher.so` is an **activity plugin**, not a `NEEDED` dependency: `ActivityManagerImpl::loadDefaultActivity()` in `libappframework.so.1.0.0` references the literal `/application/lib/activity/default` and the framework imports `QPluginLoader` (*strings*, *assembly*). `QPluginLoader` given a full path calls `dlopen(path)`, which ignores `LD_LIBRARY_PATH`. *Inferred; the join of directory and file name was not read.*
- **Runtime test A (cheap, safe):** put an *unmodified* copy of `libhomelauncher.so` in `/media/flash/nvm`, reboot, check `/proc/<Launcher pid>/maps`. If only `/application/lib/activity/default/libhomelauncher.so` appears, the override does not work for plugins.
- Options if A fails, least risky first:
  1. `mount --bind /media/flash/nvm/libhomelauncher.so /application/lib/activity/default/libhomelauncher.so` from the USB hook. Problems: the USB hook runs at about 13 s uptime and only when the stick is present; Launcher may already have loaded the plugin; the bind mount does not survive a reboot. Needs a runtime check of boot ordering and of `mount --bind` support (the Wi-Fi roadmap item assumes it works; unverified). Do not restart Launcher/mcusrv in the car.
  2. Reflash `spapp` with the edited file (ROADMAP option C). The edit does not change file size, but SquashFS-LZO recompresses the 32 KiB block containing 0x1846b (block index 0x1846b / 0x8000 = 3), so the partition image size can change by a few bytes; slack is about 0.9 KB. Needs the repack tool (ISP md5, uImage CRC). Highest effort and risk; do it with the other reflash-only fixes.
  3. Library interposition (override `QDateTime::toString`) is not viable: `libQt5Core` is searched before `libinfomodule`, and there is no `LD_PRELOAD` hook.

## Patch 2 (Setup > Time order): optional, hard
Geometry from the Ghidra decompile of `Ui_SetupTimeForm::setupUi` (a scratch export of `libsetupviews.so.1.0.0`; QRect stored as x1,y1,x2,y2). *decompiler*
| Widget (Ui field offset) | x1..x2 | y |
| --- | --- | --- |
| label_year (0xc) / month (0x10) / day (0x14) | 83..182 / 186..285 / 289..388 | 49..81 |
| hour (0x18) / minute (0x1c) / second (0x20) | 392..491 / 495..594 / 598..697 | 49..81 |
| time_text_year (0x30) / month (0x34) / day (0x40) | 83..182 / 189..288 / 292..391 | 196..251 |
| time_text_hour (0x3c) / minute (0x38) / second (0x44) | 395..494 / 498..597 / 600..699 | 196..251 |
Up and down buttons sit in two `QHBoxLayout`s (`layoutWidget` at 0x68, a second one at 0x48), so their order follows `addWidget` order. *decompiler, partial*
- A plain geometry swap does not fit in place: year x is encoded `movs #imm8`, day x (289, 292) needs `movw`, so the instruction sizes differ.
- Idea that keeps instruction sizes: inside `setupUi` only, swap the Ui field offsets used for the year and day widgets (label 0xc/0x14, text 0x30/0x40, the up/down buttons). Then the widget at column 1 is stored as "day". `SetupTime` and the caption setter (`initTranslation`) use fields, so they follow without changes. Sites to edit are 16-bit `ldr/str [r4,#off]`; offsets above 0x7c use 32-bit forms and must stay in the same class. Not enumerated or tried; the button field offsets were not resolved. *inferred*
- Risk: touching a settings screen that sets the system clock. Value: low, since the screen works in either order and the user only asked about the displayed date. Recommend skipping unless the owner wants it.

## Risks
- Patch 1 is data-only and cannot change control flow. If `toString` got an invalid pattern Qt would print junk; `dd-MM-yyyy` is valid Qt 5.3.
- The patched file must be a complete copy of the stock library with the 10 bytes changed; any other build would break the Launcher. Gate on the stock md5 like `patch.sh` does.
- Unverified: that the Home date label is the one patched string (no screenshot or runtime capture); that the Home activity is the one used (vs another layout in `activity/directstage`, which contains no date pattern: not opened).

## Needs a runtime test on the unit
1. Test A above (does an nvm copy of the plugin get loaded?).
2. After any delivery: Home screen shows `26-10-2026` style; weekday still correct; clock unchanged; AM/PM mode unchanged.
3. Setup > Time still sets the clock correctly (untouched by patch 1).
4. Reboot persistence for the chosen delivery method.
