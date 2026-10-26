# Reverse-camera guide lines (Toyota Prius camera, lines misaligned)
**Status:** analysed statically, nothing tested on the unit. Evidence tags: *parsed / strings / decompiler / assembly /
runtime / inferred*. Firmware `X3_20250718`; offsets are file offsets in the stock files under `fw/fs/spapp/`.

## Short answer
1. **Two possible sources of lines.** The head unit *can* draw its own lines, but they are a **static 8-bit bitmap**
   (a trapezoid with green/yellow/red distance marks, no box, no steering link). They are drawn **only when
   `system.OptionReverLine=true`** (UI: "Reversing Guidance Line"). In **every** diag capture that key is `false`
   (*runtime*, 17 SETUPMENU.ini copies). With the stock setting the unit draws nothing, so any lines or guide box
   you see then come from the camera or its box, baked into the CVBS video, and cannot be moved from firmware
   (*inferred*: our bitmap has no box, so a "guide box" is not ours).
2. If the setting was on, the unit's trapezoid is **pixel data inside `libapis.so.1.0.0`** (read-only squashfs `spapp`),
   blitted at a fixed position. Smallest fix: a 1-2 byte patch (vertical) or a same-size bitmap rewrite
   (horizontal / scale), delivered through the nvm library override.
3. No steering-angle input exists: `McuCallback::updateSystemCanbus` is an empty function (*decompiler*,
   `libinfomodule` regen export line 11042), and `ParklineCtrl` only takes a bool and a language.

## Pipeline (how the lines get on screen)
| Stage | Where | Evidence |
| --- | --- | --- |
| Reverse gear switching | `libreversemodule.so`: `ReverseModule::onEnterReverseGear/onLeaveReverseGear/onLeaveFrontChannel`, uses `videoin::SPVideoIn` only; no drawing code, no `libapis` import | strings |
| Video | `libspvideoin.so` / `libvideoin_service.so` (SDK, Binder proxy to a remote VideoIn service): `setMirror/setReverseMirror/setReverseFrontMirror`, channel, `enableUILayer`. No crop/scale/offset/aspect strings | strings |
| Line overlay | `videoin::SPReverseAssistLine` ("RAL"): `getRALHandle`, `updateRAL`, `releaseRALHandle`, `destroyRAL`, `getRALBufferCount`; buffer + 256-entry palette mmap'd by the app | strings |
| Painter | `ParklineCtrl` in `libapis.so.1.0.0`: `updateParkLine`, `updateParkNoSignal`, `updateData`, `setParkLine(bool)`, `setNoSignalLanguage(int)` | decompiler `fw/decomp/regen/libapis.so.1.0.0.c:37486` |
| Trigger | `FUN_41ee9fb8` in `libinfomodule.so`: `setParkLine(systemOptioReverLine()==0x99)`, `setNoSignalLanguage(systemLanguage()==7)`, `updateData()`. Called from `exec_option_ReverLine` and two other sites (regen export lines 10802, 11020; probably boot restore, not checked) | assembly (`41ee9fca subs r3,r0,#0x99; rsbs r1,r3; adcs r1,r3` = `==`) |
| UI toggle | `SetupExec::exec_option_ReverLine(0x99)` saves `true`; `systemOptioReverLine()` returns 0x99 for true, 0x9a for false | decompiler |

`ParklineCtrl::updateParkLine` (disassembled at `0x41d605ee`) allocates a 0x1c-byte descriptor, gets RAL buffer 0, clears
the buffer (`memset(buf,0,[0x14]*[0x18]*[0x10])`), clears and refills the **0x400-byte palette**, and **only if the
enable byte (`this+8`) is set** copies the bitmap with `FUN_41d605ac(x=0, y=0x16f, w=0x400, h=0xe9, stride=[desc+0x14], dst, src)`,
then calls `updateRAL`. With the setting off the buffer is just cleared and pushed. The blit (`0x41d605ac`) is a plain
byte copy with no clipping and `w` = 0x400 is the leftover loop counter of the palette copy (not an immediate).

## The bitmap (*parsed*; stock libapis md5 `6f02e18a787acf3ce0e5afda3b7bfec5`, 719 728 B)
The text segment is mapped at 0x41d10000 with file offset 0, so `file offset = vaddr - 0x41d10000`.

| Item | vaddr | file offset | Format |
| --- | --- | --- | --- |
| Palette (shared by lines and no-signal text) | 0x41d71fb4 | 0x61fb4 | 256 x 4 B, byte order A,R,G,B (index 0 = `00 ff 00 fe`, transparent) |
| **Guide-line bitmap** | 0x41d723b4 | **0x623b4** | **1024 x 233**, 8 bpp, 0x3a400 B, row-major, index 0 transparent |
| No-signal text, `setNoSignalLanguage==0` | 0x41dbc7b4 | 0xac7b4 | 170 x 45 8 bpp, drawn at (0x1ab, 0x115) |
| No-signal text, language 1 | 0x41dae596 | 0x9e596 | 170 x 45 8 bpp |

Addresses come from the PC-relative literals (`0x41d60694`=0x1197c, `0x41d60764`=0x118d0, `0x41d60768`=0x4bdf2,
`0x41d6076c`=0x4cece) plus the `add pc` base and addend. Pairing of language value to no-signal image is *inferred*.

Rendering the data to a PNG (kept out of the repo, firmware-derived) shows: a green top bar with diagonals down to a
yellow tick pair, then a red tick pair, the diagonals ending at the bottom corners. Palette index 197 = red (4 054 px),
22 = green (3 531), 147 = yellow (3 043), 0 = transparent (227 309 px). Non-zero pixels span all 233 rows and all 1024 columns.

**Geometry (inferred):** y 0x16f + h 0xe9 = 367 + 233 = **600** and w = 1024 = stride, so the RAL layer looks like the
full **1024 x 600** panel with the bitmap hugging the bottom edge; the no-signal text is centred on (512, 300).
The descriptor fields `[0x10]/[0x14]/[0x18]` are not decoded, and the process behind `libvideoin_service` is **not in the
extracted rootfs/spsdk/spapp**, so how the RAL plane is scaled or blended is unverified.

## Where the numbers live, and writability
* Position/size: **immediates in `ParklineCtrl::updateParkLine`**, in the read-only squashfs `spapp` partition. Not in
  an INI, XML or PNG: no cam/park/rev/line/track resource or config files exist under `spapp` (*parsed* by name search).
* Bitmap and palette: `.rodata` of `libapis.so.1.0.0`, same partition.
* Writable state is only `/media/flash/nvm/Sunplus/SETUPMENU.ini`; relevant key names and captured values:
  `system.OptionReverLine=false`, `system.ParkMirror=off`, `driving.reverseChl=CVBS1`, `system.AutoFcam=off`,
  `system.AppCamera=true` (*runtime*). **No key** controls line position, scale, angle or steering link.
* `libcamera.so` (the AV camera viewer, `camera_label_fcam/rcam`, `camera_btn_switch`) only paints a background and switches
  channel; no lines. `SetupExec::exec_parkmirror` calls `SPVideoIn::setMirror`; it flips the video, not the overlay.

## Proposed changes (smallest first)
**0. Config only.** To get rid of the unit's lines (leaving the camera's own box): keep `system.OptionReverLine=false`,
already the case in all captures. If lines were turned on and are misaligned, switch "Reversing Guidance Line" off in the UI.

**1. Move the unit's lines vertically: byte patch in `libapis.so.1.0.0`** (*assembly*). Bytes at file 0x50650 are
`01 90 02 93 | 00 20 | 00 96 | 40 f2 6f 11 | e9 23` = `movs r0,#0` (x) at 0x50654, `movw r1,#0x16f` (y) at 0x50658,
`movs r3,#0xe9` (h) at 0x5065c.

| Goal | File offset | Old | New |
| --- | --- | --- | --- |
| Up N px (N <= 0x6f), nothing clipped | 0x5065a (imm8 of the `movw`) | `6f` | `6f-N` |
| Down N px (N <= 0x90), bottom N rows cropped | 0x5065a and 0x5065c (`movs r3` imm) | `6f`, `e9` | `6f+N`, `e9-N` |

Keep `y + h <= 600`; a larger sum writes past the end of the buffer (no clipping in the blit: memory corruption, do not).
Up moves leave N empty rows at the bottom (buffer is pre-cleared). Example, up 20 px: `0x5065a: 6f -> 5b`.

**2. Horizontal move or scale: rewrite the bitmap in place** (no code change). Resample the 1024x233 index image, keep the
palette indices and the same length (0x3a400 B at 0x623b4), so nothing else moves. An x offset through the `movs r0,#0`
immediate is **not safe**: width equals stride, so shifted rows wrap into the next row and the last row runs past the
buffer end. Cropping columns inside the image (or scaling about the bottom centre) is safe.

**Delivery** for 1 and 2: patched copy of `libapis.so.1.0.0` installed as `/media/flash/nvm/libapis.so.1` through the nvm
override (`nvm-library-override.md`). `libapis` is the shared dependency of `libinfomodule` and `libsetupmodule`
(`ParklineCtrl` imports in both), so the nvm copy should be picked up. **The override is proven only for `libinfomodule`.**
`libapis` (prelinked at 0x41d10000, 0.7 MB, used by almost every app) is untested, and the "stock copy also mapped by
full path" caveat applies: if the stock copy wins, a patch is harmless but has no effect.

## What to test on the unit (nothing is written without the owner's go-ahead)
1. Read only: confirm `system.OptionReverLine` now. In reverse, note whether the 1024-wide trapezoid with three coloured
   tick pairs is visible, or only the camera's own box/lines. A photo of the screen helps to size an offset in pixels.
2. Toggle "Reversing Guidance Line": if the misaligned lines vanish they were ours; if they stay, they are in the video
   (camera or a Toyota parking ECU), so only a camera-side setting or our own overlay can help.
3. Check whether the video is letter-boxed or scaled (the "Aspect Ratio" UI item maps to `DisplayControl::setAspectRatioMode`,
   referenced by `uiplayer` and the media libs; **unverified that it affects the reverse view**). A letter-boxed
   image means mostly vertical error, which patch 1 addresses.
4. Apply patch 1 with a visible sentinel (e.g. up 40 px) to prove the nvm copy is used, then fine-tune.
5. Check Launcher's `/proc/<pid>/maps` for `nvm/libapis.so.1`, as was done for `libinfomodule`.

**Revert:** delete `/media/flash/nvm/libapis.so.1` and reboot (the stock library in `/application/lib` is never touched);
`x3patch/mode = uninstall` once `patch.sh` knows the file. For the setting, toggle it back in the UI.

## Risks
* A wrong `y`/`h` pair (sum > 600) corrupts memory.
* A bad `libapis` override can break the whole UI: ship with the stock-md5 check and auto-undo like `wifi40.sh`.
* A camera-baked guide box cannot be corrected here. Scaling or offsetting the video itself is not exposed by any
  userland API found (SPVideoIn offers mirror, channel and layer control only).
* The remote VideoIn/RAL service, the kernel display driver and the MCU camera-power command (0x7a) were not analysed.

## Not verified
* RAL compositing and the real buffer size (1024x600 is inferred from 367+233 and the centred no-signal text).
* Which of 0x99/0x9a is labelled On or Off in the UI (that `true` means lines drawn is *assembly*-verified).
* Whether the Prius lines come from the camera or a Toyota parking ECU; the unit only sees CVBS.
* The other two callers of `FUN_41ee9fb8`.
