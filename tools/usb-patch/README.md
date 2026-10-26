# usb-patch: library override via /media/flash/nvm

Installs a replacement `libinfomodule.so` into `/media/flash/nvm`, which is first in
`LD_LIBRARY_PATH` (`init.gui.rc:4`). The stock file in `/application` is never
modified. Background: `docs/FINDINGS.md` sections 7 and 9.

## Stick layout
```
gemn_auto.sh            (tools/usb-diag, calls x3patch/patch.sh first)
x3patch/patch.sh
x3patch/mode            install | uninstall | status
x3patch/payload/libinfomodule.so
x3patch/log.txt         written by the unit
```
`patch.sh` refuses to install unless the unit's stock library has the expected md5
(`f00b52a7…`, firmware X3_20250718) and the payload is a known build:

| Payload | md5 |
| --- | --- |
| stock copy (proof run) | `f00b52a739bb333307ee9a841bd02369` |
| patch B (Emperor restore, FINDINGS 9) | `fae35255cfc28d530fa100af5cab4c4d` |
| string fixes only (FINDINGS 11) | `c8fceef18c51ab983634744470663672` |
| patch B + string fixes | `573843500395e13431fde51d2d5f1422` |
| patch C (day/night, FINDINGS 15.8) on the installed B + strings payload | `13dc16cefa948c1cc9914a15ef6560b2` (accepted via its `.md5` sidecar; build with `tools/apply_daynight.py`) |

String builds come from `tools/strings/strtab.py build <stock or patch B lib>
tools/strings/overrides.json <out>`. If `overrides.json` changes, the md5 changes too:
update this table and `KNOWN_MD5` in `patch.sh`.

## Procedure
1. **Proof run:** payload = *unmodified* stock copy, `mode` = `install`. Plug in, wait
   for `log.txt` to say `install: OK`, then do a full car off/on and plug in again.
   `log.txt` should show `Launcher … has libinfomodule.so from:
   /media/flash/nvm/libinfomodule.so`, and the unit should behave exactly as before.
2. Only after that works: payload = patch B, `mode` = `install`, same check.
3. Confirm the fix with an overnight off (diag snapshot at boot).

## Recovery
- Set `mode` to `uninstall`, plug in, wait for `uninstall: removed`, and reboot. The USB hook
  runs from mdev, independent of the UI, so this works even if Launcher doesn't come up.
- A full stock reflash (`ISPBOOOT.BIN`, `nand erase.chip`) also wipes nvm.

## guideline: reverse guide-line position test (nvm `libapis` override)
`guideline/guideline.sh` installs a patched `libapis` as `/media/flash/nvm/libapis.so.1` (same mechanism as above, own
`mode`/`log.txt`). Background and evidence: `docs/discoveries/reverse-camera-guide-lines.md`. Build the payload on the host:
`python3 -I tools/build_guideline.py <stock libapis.so.1.0.0> tools/usb-patch/guideline/payload --up 40` (also `--down N`).
Stock md5 `6f02e18a787acf3ce0e5afda3b7bfec5`; the `--up 40` test build is `03476349f1048a635b5d19fb0203f9a2` (one byte,
file 0x5065a `6f`→`47`). The script aborts unless stock md5, payload md5, payload size and free space check out, and keeps a
copy of the stock library on the stick. Needs a `gemn_auto.sh` line: `[ -f "$USB/guideline/guideline.sh" ] && sh "$USB/guideline/guideline.sh"`.
Test: set "Reversing Guidance Line" ON, `mode`=`install`, plug in, cold boot, plug in again (log shows which `libapis`
Launcher mapped), look at the reverse view: lines should sit 40 px higher. Revert: `mode`=`uninstall`, plug in, reboot.
**Do not run it together with another nvm-override test** (e.g. `datefmt`): a UI change would not be attributable.
