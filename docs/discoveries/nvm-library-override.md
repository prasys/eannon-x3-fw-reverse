# nvm library override (how patches reach the unit without a reflash)
**Status:** proven with a stock copy. FINDINGS 10, 11.3, `tools/usb-patch/README.md`.

- `/media/flash/nvm` is **first in `LD_LIBRARY_PATH`** (`init.gui.rc:4`). A library of the same name there is
  loaded instead of the stock one. The stock file is never touched; deleting ours restores stock.
- Proof run (stock bytes, md5 `f00b52a7…`): after a car off/on, `/proc/<Launcher>/maps` showed both
  `/tmp/sp/media/flash/nvm/libinfomodule.so` and the stock path. The UI came up normally. *runtime*
- Both copies are loaded: the nvm one as Launcher's `NEEDED` dependency, the stock one again by full path
  (probably the module manager, since there is no SONAME to deduplicate). Keep patches to code-path changes
  that do not depend on which copy's statics are used.
- `loadSystemSettings` is called only inside `libinfomodule` through its PLT, which binds through the global
  scope, so the nvm copy's patched function should run. *Inferred from linker rules; confirm on the unit.*
- Keep nvm at least 8 MB free: it also holds all settings. A full nvm would make settings saves fail.
- Safety: `patch.sh` refuses unless the stock md5 matches and the payload md5 is known; it keeps a copy of any
  replaced library on the stick. Recovery is `x3patch/mode` = `uninstall`.
