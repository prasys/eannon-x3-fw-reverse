# Eanon X3 USB Patcher (GUI)

A friendly, step-by-step desktop app for preparing Eanon X3 (`X3_20250718`) patch, diagnostic, and recovery USB sticks — **no Python, command line, or coding needed**.

## For Windows Users (Standalone `.exe`)

1. Download **`EanonX3-Patcher-Windows-x64.exe`** from the GitHub **Releases** page (or from the **Build Eanon X3 Patcher** GitHub Actions artifact).
   > **Note:** Because the executable is not code-signed, Windows SmartScreen may show *"Windows protected your PC"*. Click **More info → Run anyway** (or build it yourself from source below).
2. Plug a **FAT32 (MBR)** USB flash drive into your PC.
3. Double-click `EanonX3-Patcher-Windows-x64.exe` and follow the 4 steps:
   - **Step 1 — USB & Firmware:** Pick your USB drive, then either:
     - Select your `X3_20250718.zip` / `ISPBOOOT.BIN` file (extracted automatically — no extra tools required), **OR**
     - Click **Prepare Read-Only Car Stick** if you don't have the firmware file (plug into your car for ~60 s, bring it back to your PC, and the app automatically reads your unit's stock library and logs).
   - **Step 2 — Select Patches:** Toggle the fixes you want:
     - **Emperor / DSP Restore Fix (Patch B)** — keeps DSP/Emperor settings after cold boots & MCU resets.
     - **Day / Night Follows Headlights (Patch C)** — stops the screen from starting stuck in dim night mode (requires Patch B).
     - **UI Language & Translation Fixes** — fixes broken/untranslated labels across 20 languages.
     - **Wi-Fi 40 MHz (`wifi40`)** — upgrades the 5 GHz wireless AA/CarPlay AP to HT40, with automatic self-undo if Wi-Fi doesn't start.
     - **Diagnostics & Logging** — automatically saves redacted logs to `diag/` (with an optional deep hardware/input capture toggle for developers; for random reboot reports, leave it off and just export the normal logs).
   - **Step 3 — Car Guide & Status:** Follow the numbered in-car checklist, then plug the stick back into your PC and click **Inspect USB Stick Now** or **Export Safe Diagnostics (.zip)** to share privacy-scrubbed logs on GitHub.
   - **Step 4 — Undo / Restore:** 1-click uninstall/recovery stick that cleanly removes overrides and restores stock behaviour.

## Safety Guarantees

- Stock firmware on `/application` is **never modified**. Library patches live in `/media/flash/nvm` and can be removed at any time.
- Every stock binary is MD5-verified (`f00b52a739bb333307ee9a841bd02369`) before patching; unsupported firmware versions are refused safely.
- All shell scripts and mode files are written with Unix LF (`\n`) line endings so they work reliably from Windows.
- **Export Safe Diagnostics (.zip)** excludes raw `nvm/*.ini` backups and binary `.so` files, and redacts Wi-Fi passphrases, SSIDs, device names, serials, and MAC suffixes.

## Running or Building from Source (Windows / macOS / Linux)

Requires Python 3.10+ with `tkinter` support (included by default in official Windows Python installers).

```bash
pip install -r tools/patcher/requirements.txt
python tools/patcher/main.py
```

### Build the Windows `.exe` locally

```bash
pip install -r tools/patcher/requirements-build.txt
pyinstaller --clean --noconfirm tools/patcher/x3_patcher.spec
# Output: dist/EanonX3-Patcher.exe
```

### Run tests

```bash
python3 -m unittest discover -s tools/patcher/tests -v
```
