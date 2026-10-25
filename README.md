# Eanon X3 head unit: firmware reverse engineering and userland patches

Reverse engineering and bug-fixing the **Eanon X3** Android-Auto / CarPlay car head unit.
Everything here is for a unit the author owns. No firmware images, extracted filesystems,
decompiler output or USB captures are in the repo (they are large, copyrighted, or hold
personal data; see `.gitignore`).

## The hardware and software
| Part | What it is |
| --- | --- |
| SoC | Sunplus SPHE8368-U "Gemini", ARM32, Linux 4.9, Qt 5.3.2 userland |
| MCU | JieLi AC690x (BR17), image `mcu8368u.bin` is encrypted |
| Firmware | `X3_20250718` (`ISPBOOOT.BIN`, 77 MB, SquashFS 4.0 LZO + YAFFS2 for `nvm`) |
| UI | `spapp` (`/application`): Launcher plus plugin libraries (`libinfomodule`, `libsetupmodule`, `libcomdspviews`, …) |
| Settings | INI files in `/media/flash/nvm/Sunplus/` (`SETUPMENU.ini`, `ResumeInfo.ini`) |

## Where things are documented
| File | Contents |
| --- | --- |
| `docs/FINDINGS.md` | Everything learned. Each claim carries a coordinate and an evidence level (*parsed / strings / decompiler / assembly / runtime / inferred*) |
| `docs/MAP.md` | Where things live: partitions, libraries, functions, INI keys, boot files |
| `docs/MCU_PROTOCOL.md` | SoC to MCU UART framing, CRC, command tables |
| `docs/ROADMAP.md` | Prioritised next steps and fix-delivery options |
| `AGENTS.md` / `CLAUDE.md` | Working rules, gotchas and toolchain notes for AI agents |
| `docs/discoveries/` | One short file per topic (Emperor bug, EQ gate, MCU protocol, nvm override, strings, Wi-Fi/AA, USB hook, storage, incident) |
| `patches/` | Byte-level descriptors of each patch |

## What we learned (headlines)
- **Emperor (DSP master) is not restored after a short or long off** (FINDINGS 5, 9). Emperor
  is the switch that enables the EQ and all sound processing. At boot `SetupExec::loadSystemSettings`
  skips restoring the saved DSP state whenever the MCU reports a "memory reset", and the next
  shutdown then overwrites the saved state with defaults. Short offs keep MCU memory, so they
  looked fine. A related gate in `libsetupmodule` (`exeSetupEq`) does the same for the EQ curve (patch A).
- **Emperor / time alignment is never written to `SETUPMENU.ini`** (FINDINGS 5.3, 5.8):
  `DspEmperorView::saveSetting` is dead code. It only survives through the shutdown blob in `ResumeInfo.ini`.
- **A library placed in `/media/flash/nvm` overrides the stock one** (FINDINGS 10). `nvm` is first in
  `LD_LIBRARY_PATH`, so no reflash is needed and removing the file restores stock. Proven with an
  unmodified copy.
- **UI strings live in 21 language tables** of 417 entries each in `libinfomodule.so`
  (FINDINGS 11). Many non-English entries are untranslated or wrong. `nl` has no table in this build.
- **Wi-Fi AP** for wireless Android Auto / CarPlay ran at 20 MHz. hostapd lacks 802.11ac but accepts
  an `ht_capab` line for HT40 (FINDINGS 8.5).
- **A USB stick runs a script as root.** The stock autorun hook runs `<usb>/gemn_auto.sh` on insert
  (FINDINGS 7). That is the delivery channel for every diagnostic and patch here. Raw `nanddump`
  reads during extras are suspected of having rebooted the unit once (FINDINGS 8, incident).

## The MCU protocol (summary; full tables in `docs/MCU_PROTOCOL.md`)
Recovered **statically** from `mcusrv` and `libmcusrv.so`. Nothing has been seen on the wire yet,
because sniffing `ttyS1` would steal bytes from `mcusrv`.
- Link: `/dev/ttyS1`, **115200 baud**, started as `mcusrv 1 115200`.
- Frame: `FF AA LEN P[0..LEN-1] CHK CRC_hi CRC_lo`. `P[0]` is the command byte.
  `CHK = -(LEN + ΣP) & 0xFF`. CRC16 is table-driven, **poly 0x8432**, MSB-first, init 0, no final XOR.
  Max frame 0x7F bytes. Which bytes the CRC covers is *not yet verified*.
- MCU to SoC includes: `0x00` power state, `0x39` **RstMemory** (the flag behind the Emperor and EQ bug),
  `0x36` time, `0x37` detect inputs, `0x6a` steering-wheel keys, `0x68` CAN bus, `0x41` Bluetooth,
  `0x12` IR remote, `0x7e` backlight, `0x0a/0x0b` volume knob.
- SoC to MCU includes: `0x17` system power, `0x10` backlight, `0x1a` amp, `0x67` audio channel/gain,
  `0x6a` wheel control, `0x41` Bluetooth command, `0x68` CAN bus, and **`0x75` MCU upgrade: never send**.
- Safety: never write to `ttyS1` while `mcusrv` runs, and do not restart `mcusrv` in the car. It
  carries the power handshake, and killing it can skip the shutdown save.
- `SetupExec::getSystemPowerState()` is misnamed. It returns the MCU RstMemory flag.

## Patches
All are delivered through the USB stick and are reversible. **Nothing is written to the unit without
the owner's go-ahead.**

| Patch | Where | State |
| --- | --- | --- |
| **B: Emperor restore** (1 byte, `libinfomodule.so` file offset `0x41d2b`, `0x80`→`0xb8`, `beq.w`→`b.w`) | nvm library override | on the unit (installed 2026-10-25), **not yet confirmed loaded** |
| **Language strings** (all 20 languages with tables, plus English fixes) | same library | in the same build |
| **wifi40** (adds `ht_capab=[HT40+][SHORT-GI-20][SHORT-GI-40]` to `softap.conf`) | `/media/flash/nvm/softap.conf` | applied, verified after cold boots (`secondary_channel=1`) |
| **A: EQ restore gate** (`libsetupmodule.so` `0xaa1e`, `90 b1`→`12 e0`) | not delivered | only if EQ resets with a good `audio.EQ.custom` |

Each patch is described in `patches/*.json` (offsets, bytes, md5s). Built binaries are not committed.

Undo: set `x3patch/mode` to `uninstall` (or `wifi40/mode` to `undo`) and plug the stick in again.
The USB hook runs from mdev, independent of the UI, so this works even if the Launcher won't start.

## Tools
| Path | Use |
| --- | --- |
| `tools/usb-diag/gemn_auto.sh` | Read-only diagnostic snapshot per plug-in: settings diff, audio log window, Wi-Fi/AA state, redacted logs. `extras.flag` on the stick adds a hardware inventory and a 30 s input/UART capture. It also calls the patch scripts first |
| `tools/usb-patch/x3patch/patch.sh` | Installs or removes the `libinfomodule.so` override. Checks the stock md5 and that the payload md5 is known. Keeps a copy of the replaced file |
| `tools/usb-patch/wifi40/wifi40.sh` | HT40 patch with a health check and automatic undo if hostapd doesn't come up |
| `tools/build_payload.py` | Builds the payload: stock library + patch B + string tables → `libinfomodule.so`, `.md5`, `BUILD.txt` |
| `tools/strings/strtab.py` | Extracts and rebuilds the string tables (`extract` / `build` / `check`) |
| `tools/strings/overrides.json`, `tables_final.json` | Hand fixes, and the finished tables for all languages |
| `tools/gotres.py`, `tools/annotate_strings.py` | Resolve GOT-relative symbol refs and PC-relative strings in Ghidra exports |

### Building and deploying the payload
```
python3 -I tools/build_payload.py <stock libinfomodule.so> tools/strings/tables_final.json <outdir>
```
Copy the three output files to `<stick>/x3patch/payload/`, set `<stick>/x3patch/mode` to `install`, plug
in, wait for `install: OK` in `x3patch/log.txt`, then do a full car off/on. The stock library comes from
the firmware, or from `<stick>/x3patch/stock/`, which `patch.sh` fills from the unit on first run.
Payloads are built from firmware so they are not committed.

## Roadmap (short form; details in `docs/ROADMAP.md`)
1. Confirm the override loads and Emperor survives an overnight off (log should show the library loaded
   from `/media/flash/nvm/`).
2. Review the language tables on the unit, and fix what is wrong in `tables_final.json`.
3. Emperor / Filter / Loudness / VBass are not persisted on change. Needs new code, so probably a shim library.
4. Classic EQ preset cycling overwrites the custom curve (`ComEqView::btnEqRelseaseSlot`).
5. Garbage 20-band EQ params sent to the DSP on every change (`set eq failed`).
6. DPS (Bongiovi) toggle is rejected by the DSP. Probably not fixable in the app.
7. Optional: real 802.11ac for wireless AA by cross-building hostapd.
8. Tooling: `extract.py`, `repack.py` (only if a reflash route is needed), an ARM cross toolchain for shims.

## Safety and privacy
- USB captures hold paired phone names, media file names and a full settings backup. Keep `diag/` out of git.
- The Wi-Fi AP passphrase is in `softap.conf`. Scripts redact it before anything is copied to the stick.
- Treat all firmware as untrusted input: extract each archive into its own empty directory and
  never execute anything from the extracted tree.
- Formatting or erasing flash needs explicit confirmation of the exact device.

## Environment
macOS host. Ghidra 12.2 headless (`JAVA_HOME` = openjdk@21), `brew install squashfs`, LLVM `objdump`
with `--triple=thumbv7-linux-gnueabi` for app code (the PLT is ARM). See `CLAUDE.md` for details.
