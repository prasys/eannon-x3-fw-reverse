# AGENTS.md

Guidance for AI agents working in this repo. `CLAUDE.md` carries the same rules for Claude Code; keep the two in step.

## Project
Reverse engineering and patching the **Eanon X3** car head unit: Sunplus SPHE8368-U SoC (ARM32, Linux 4.9,
Qt 5.3.2 userland) plus a JieLi AC690x MCU. Firmware `X3_20250718`. Goal: fix userland bugs, starting with
DSP/Emperor settings not persisting. Patches reach the unit from a USB stick; there is no reflash path yet.
Overview: `README.md`.

## Read first
1. `README.md`: what the project is, patch state, tools.
2. `docs/discoveries/`: one short file per topic. Start here for any task.
3. `docs/FINDINGS.md`: the evidence. `docs/MAP.md`: where things live. `docs/MCU_PROTOCOL.md`. `docs/ROADMAP.md`: priorities.
4. `patches/*.json`: exact bytes and md5s of each patch.

## Hard rules
- **Nothing is written to the head unit without the owner's go-ahead.** USB scripts that modify
  `/media/flash/nvm` must log what they did and ship with an undo path. Formatting or erasing needs explicit
  confirmation of the exact device.
- **Never read raw NAND/MTD from userspace on the unit** (it rebooted once). Copy files from mounted paths instead.
- **Never write to `/dev/ttyS1` while `mcusrv` runs; never send MCU command `0x75` (firmware upgrade).**
  Do not kill or restart `mcusrv` in the car.
- **Cite only what a tool showed.** Every address, offset and key must come from tool output. Tag the evidence
  level (*parsed / strings / decompiler / assembly / runtime / inferred*) and say what is unverified.
- **No personal data in the repo.** USB captures contain phone names, media file names, Wi-Fi passphrases and a
  full settings backup. Never copy raw INI or logcat; quote only the keys needed. `diag/`, `*.ini`, `*.bin`,
  `*.zip` are gitignored; keep it that way.
- **Untrusted inputs.** Extract each firmware archive into its own empty directory, run Python that parses
  firmware with `python3 -I`, never execute anything from the extracted tree.
- **Don't commit unless asked.** Work branch `Pradeesh/rorqual`; `main` is the default branch.

## Keeping docs honest
- Learned something: add it to `docs/FINDINGS.md` and the matching `docs/discoveries/` file.
- Located something: `docs/MAP.md`. Priorities changed: `docs/ROADMAP.md`.
- New or changed patch: update `patches/*.json`, `tools/usb-patch/README.md` (md5 table, `KNOWN_MD5` in `patch.sh`)
  and the README patch table. Don't let them drift apart.
- Commit work that is only in a worktree or scratchpad. A previous worktree was lost with uncommitted tooling.

## Inputs (not in repo)
`~/Downloads/X3_20250718.zip` (`ISPBOOOT.BIN`) and `~/Downloads/X3-series MCU_240723.zip`. Carve and
`unsquashfs` per `docs/FINDINGS.md` section 4. The stock `libinfomodule.so` is also copied to the stick at
`x3patch/stock/` by `patch.sh`.

## Tools
| Tool | Use |
| --- | --- |
| `tools/build_payload.py` | stock `libinfomodule.so` + patch B + string tables → payload |
| `tools/strings/strtab.py` | `extract` / `build` / `check` the UI string tables |
| `tools/usb-diag/gemn_auto.sh` | stick entry point: diagnostics, then calls the patch scripts |
| `tools/usb-patch/x3patch/patch.sh` | install or uninstall the nvm library override |
| `tools/usb-patch/wifi40/wifi40.sh` | HT40 Wi-Fi patch with auto-undo |
| `tools/gotres.py`, `tools/annotate_strings.py` | resolve GOT refs and PC-relative strings in Ghidra exports |

Host (macOS): Ghidra 12.2 headless (`JAVA_HOME=/opt/homebrew/opt/openjdk@21`; check the export ends with
`/* exported N functions */`, it exits 0 even on failure), `brew install squashfs`, LLVM `objdump
--triple=thumbv7-linux-gnueabi` for Thumb-2 (the PLT is ARM; decode stubs by hand).

## Gotchas
- Ghidra rebases non-prelinked libs to 0x10000: subtract it for file offset. Prelinked libs (`libsysset` 0x425d0000,
  `libinfomodule` 0x41eb0000, `libapis` 0x41d10000, `libmcusrv` ~0x42200000) use real vaddrs. Check the `imagebase:` line.
- `DAT_x + 0x…` in Ghidra is `*(DAT_x) + constant` (Thumb PC already folded in).
- INI key names differ from C++ symbols (`setupAEQCustom` is `audio.EQ.custom`). Map them with a runtime capture.
- `SetupExec::getSystemPowerState()` is the MCU **RstMemory** flag.
- Repacking needs per-partition md5s in the ISP script and uImage CRCs fixed; slots have 1–4 KB slack.
- Real mount points are under `/tmp/sp/…`; match on the path suffix in scripts.
- Payload binaries are built from firmware and are not committed. Rebuild with `tools/build_payload.py`. A rebuild's
  md5 can differ from an earlier build while the strings are identical; `patch.sh` accepts a payload whose md5
  matches `payload/libinfomodule.so.md5` or its known list.
- Scripts for the stick must use LF line endings on a FAT32/MBR stick.
