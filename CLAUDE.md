# CLAUDE.md

Reverse engineering and bug-fixing the **Eanon X3** car head unit firmware (Sunplus
SPHE8368-U "Gemini", ARM32 Linux 4.9 + Qt 5.3.2 userland; JieLi AC690x MCU). The goal
is to patch userland bugs, starting with DSP/Emperor settings not persisting.

## Docs (read these first)
- `docs/FINDINGS.md`: everything learned, each claim with a coordinate and how it's
  known (*parsed / strings / decompiler / assembly / runtime / inferred*).
- `docs/MAP.md`: where things live (partitions, libs, functions, INI keys, boot
  files).
- `docs/ROADMAP.md`: prioritised next steps and fix-delivery options.
- `docs/MCU_PROTOCOL.md`: SoC↔MCU UART framing, CRC, and command tables (from mcusrv).

Update FINDINGS when you learn something, MAP when you locate something, and ROADMAP
when priorities change. Don't let them drift apart.

## Repo layout
- `docs/`: the files above.
- `tools/gotres.py`: resolve GOT-relative symbol refs (keys like `SetupStrIdConst::*`)
  from Ghidra output.
- `tools/annotate_strings.py`: annotate a Ghidra export with the C strings behind
  `DAT_x + 0x…` PC-relative loads (`<elf> <ghidra_base_hex> <export.c>`).
- `tools/usb-diag/gemn_auto.sh`: read-only diagnostic, run on the unit via the USB
  autorun hook. Put `extras.flag` on the stick for the heavy extras (hw inventory,
  getprop, U-Boot env, mcusrv info, raw `nanddump` backups of small partitions once
  per boot, and a 30 s evdev + UART-counter capture window).

Firmware images, extracted filesystems, decompiler output and USB captures are **not**
in the repo (see `.gitignore`). Recreate them in the session scratchpad:
- Inputs: `~/Downloads/X3_20250718.zip` (ISPBOOOT.BIN), `~/Downloads/X3-series MCU_240723.zip`.
- Carve + `unsquashfs` per `docs/FINDINGS.md` section 4 (offsets in 2.3).

## Working rules
- **Untrusted inputs:** extract each archive into its own empty dir, run Python that
  parses firmware with `python3 -I`, never execute anything from the extracted tree.
- **Cite only what a tool showed.** Every address/offset/key must come from tool
  output; tag the evidence level and say what isn't verified.
- **USB captures contain personal data** (paired phone names, media file names; the
  extras `mtd/nvm.bin` backup holds all settings). Never
  copy raw INI/logcat into the repo; quote only the specific keys needed.
- **Nothing gets written to the head unit without the user's go-ahead.** USB scripts
  that modify `/media/flash/nvm` must log what they did and ship with an undo path.
  Formatting or erasing disks needs explicit confirmation of the exact device.
- Don't commit unless asked. Branch `Pradeesh/rorqual`; main is `main`.

## Toolchain notes (macOS host)
- Ghidra 12.2 headless: `/opt/homebrew/Cellar/ghidra/12.2/libexec/support/analyzeHeadless`;
  needs `JAVA_HOME=/opt/homebrew/opt/openjdk@21`. Export script:
  `~/.claude/skills/reverse-engineering/scripts/ghidra/DecompileExport.java`
  (`-postScript DecompileExport.java <out.c> [name-filter] [max]`). Check the
  output ends with `/* exported N functions */`; it exits 0 even on failure.
- `brew install squashfs` gives `unsquashfs`/`mksquashfs`. Filesystems are SquashFS 4.0,
  **LZO**, 32 KiB blocks.
- Use `objdump` (LLVM) with `--triple=thumbv7-linux-gnueabi` for app code (Thumb-2);
  the **PLT is ARM**, so decode PLT stubs by hand (add/add/ldr → GOT slot, then
  `objdump -R`).

## Gotchas
- **Ghidra base vs file offset.** Non-prelinked libs (most of `spapp/lib`) load at Ghidra
  base **0x10000**, so subtract 0x10000 to get vaddr == file offset. Prelinked ones
  (`libsysset` 0x425d0000, `libinfomodule` 0x41eb0000, `libapis` 0x41d10000,
  `libmcusrv` ~0x42200000) use their real vaddr. Check the `imagebase:` line in each export.
- Ghidra shows PC-relative loads as `DAT_x + 0x...`. The value is `*(DAT_x) + that
  constant` (Thumb PC already folded in). `gotres.py` does this for GOT loads.
- INI key names ≠ C++ symbol names (`setupAEQCustom` → `audio.EQ.custom`). Use a
  runtime capture to map them.
- `SetupExec::getSystemPowerState()` is actually the MCU **RstMemory** flag.
- Repacking: the ISP script hardcodes an md5 of the first 0x200000 bytes of each
  partition, and the script itself is a uImage with CRCs. Partition slots have
  ~1–4 KB of slack.
- `/media/flash/nvm` is first in `LD_LIBRARY_PATH` (`init.gui.rc:4`), so it can be
  used for library overrides (untested).
