"""Safe USB stick writer for Eanon X3 patch/diagnostic/uninstall sticks.

Rules enforced on every write:
  - All shell scripts, mode files, and .md5 files are written with LF (`\\n`) line endings
    (critical on Windows, where CRLF breaks BusyBox `/bin/sh` on the head unit).
  - Never deletes existing `diag/`, `x3patch/stock/`, `x3patch/previous_*`, or `log.txt`.
  - Verifies the MD5 of `libinfomodule.so` on disk immediately after writing.
  - Writes a clear `README_X3_PATCHER.txt` onto the stick with exact car instructions.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .patch_engine import BuiltPayload, PatchSelection, build_libinfomodule
from .resources import gemn_auto_script, wifi40_script, x3patch_script


class UsbWriteError(RuntimeError):
    """Raised when writing to the target USB stick fails verification."""


@dataclass
class WriteResult:
    action: str  # "install", "read_from_car", "uninstall"
    usb_root: str
    files_written: list[str]
    payload: Optional[BuiltPayload]
    summary_lines: list[str]
    car_steps: list[str]


def _write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    path.write_bytes(normalized.encode("utf-8"))


def _write_base_scripts(root: Path) -> list[str]:
    written: list[str] = []
    targets = [
        (root / "gemn_auto.sh", gemn_auto_script()),
        (root / "x3patch" / "patch.sh", x3patch_script()),
        (root / "wifi40" / "wifi40.sh", wifi40_script()),
    ]
    for path, content in targets:
        _write_lf(path, content)
        written.append(str(path.relative_to(root)))
    return written


def _set_extras_flag(root: Path, enabled: bool) -> list[str]:
    flag = root / "extras.flag"
    if enabled:
        _write_lf(flag, "1\n")
        return ["extras.flag"]
    if flag.exists():
        flag.unlink()
    return []


def _format_instructions_file(title: str, summary: list[str], steps: list[str]) -> str:
    lines = [
        f"Eanon X3 Patcher — {title}",
        "=" * 54,
        "",
        "What is on this USB stick:",
    ]
    lines.extend(f"  - {s}" for s in summary)
    lines.extend(["", "What to do in your car:"])
    for i, step in enumerate(steps, 1):
        lines.append(f"  {i}. {step}")
    lines.extend(
        [
            "",
            "Safety notes:",
            "  - Your head unit's stock firmware in /application is NEVER modified.",
            "  - To revert everything at any time, use the 'Undo / Restore Stock' tab in",
            "    Eanon X3 Patcher and plug the USB stick in again.",
            "",
        ]
    )
    return "\n".join(lines)


def prepare_read_from_car_stick(
    usb_root: str | os.PathLike[str],
    diag_extras: bool = False,
    progress: Optional[Callable[[str], None]] = None,
) -> WriteResult:
    """Prepare a 100% read-only USB stick that dumps stock libinfomodule.so + diagnostics."""
    root = Path(usb_root)
    if not root.is_dir():
        raise UsbWriteError(f"USB path does not exist or is not a folder: {root}")

    if progress:
        progress("Writing read-only diagnostic & stock-backup scripts...")
    written = _write_base_scripts(root)
    _write_lf(root / "x3patch" / "mode", "status\n")
    _write_lf(root / "wifi40" / "mode", "status\n")
    written.extend(["x3patch/mode", "wifi40/mode"])
    written.extend(_set_extras_flag(root, diag_extras))

    wait_s = "90 seconds" if diag_extras else "45–60 seconds"
    summary = [
        "Mode: READ-ONLY (changes NOTHING on your head unit)",
        "Copies your unit's stock libinfomodule.so to x3patch/stock/ so patches can be built without a firmware zip",
        "Collects diagnostic logs and settings snapshots into diag/",
    ]
    if diag_extras:
        summary.append("Includes deep hardware + 30-second UART/input capture (extras.flag)")

    steps = [
        "Safely eject this USB stick from your computer.",
        "Turn on your car / Eanon X3 head unit and wait until the home screen is fully loaded.",
        f"Plug the USB stick into the X3's USB port and wait {wait_s}. Nothing appears on screen — it runs silently in the background.",
        "Unplug the USB stick and bring it back to your computer.",
        "Open Eanon X3 Patcher and select this USB drive — it will automatically detect your stock library and logs.",
    ]
    _write_lf(root / "README_X3_PATCHER.txt", _format_instructions_file("Read From Car & Diagnostics", summary, steps))
    written.append("README_X3_PATCHER.txt")
    if progress:
        progress("Done! Read-only USB stick is ready.")
    return WriteResult("read_from_car", str(root), written, None, summary, steps)


def prepare_install_stick(
    usb_root: str | os.PathLike[str],
    selection: PatchSelection,
    stock_bytes: Optional[bytes] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> WriteResult:
    """Build selected patches and write an install USB stick."""
    selection.validate()
    root = Path(usb_root)
    if not root.is_dir():
        raise UsbWriteError(f"USB path does not exist or is not a folder: {root}")

    payload: Optional[BuiltPayload] = None
    if selection.needs_libinfomodule:
        if stock_bytes is None:
            raise UsbWriteError(
                "A verified stock libinfomodule.so (from X3_20250718.zip, ISPBOOOT.BIN, or your car stick) "
                "is required for the selected patches."
            )
        if progress:
            progress("Building patched libinfomodule.so...")
        payload = build_libinfomodule(stock_bytes, selection)

    if progress:
        progress("Writing installer scripts (LF line endings)...")
    written = _write_base_scripts(root)

    summary: list[str] = []
    if payload is not None:
        payload_dir = root / "x3patch" / "payload"
        payload_dir.mkdir(parents=True, exist_ok=True)
        so_path = payload_dir / "libinfomodule.so"
        if progress:
            progress("Copying patched libinfomodule.so to USB stick...")
        so_path.write_bytes(payload.binary)
        # Read-back verification
        disk_md5 = hashlib.md5(so_path.read_bytes()).hexdigest()
        if disk_md5 != payload.md5:
            raise UsbWriteError(f"USB write verification failed: wrote {payload.md5}, read back {disk_md5}")
        _write_lf(payload_dir / "libinfomodule.so.md5", payload.md5 + "\n")
        _write_lf(payload_dir / "BUILD.txt", payload.build_txt)
        _write_lf(root / "x3patch" / "mode", "install\n")
        written.extend(
            [
                "x3patch/payload/libinfomodule.so",
                "x3patch/payload/libinfomodule.so.md5",
                "x3patch/payload/BUILD.txt",
                "x3patch/mode",
            ]
        )
        if selection.emperor_restore:
            summary.append("Patch B: Emperor / DSP Restore Fix (survives cold boots / MCU memory resets)")
        if selection.daynight_ill:
            summary.append("Patch C: Day/Night Mode follows live headlights (ILL) at startup")
        if selection.ui_strings:
            summary.append(f"UI Language String Fixes ({payload.strings_languages} languages updated)")
    else:
        _write_lf(root / "x3patch" / "mode", "status\n")
        written.append("x3patch/mode")

    if selection.wifi40:
        _write_lf(root / "wifi40" / "mode", "apply\n")
        summary.append("Wi-Fi 40 MHz (HT40) for smoother wireless Android Auto / CarPlay (with auto-undo)")
    else:
        _write_lf(root / "wifi40" / "mode", "status\n")
    written.append("wifi40/mode")

    written.extend(_set_extras_flag(root, selection.diag_extras))
    summary.append("Diagnostics logging enabled (diag/ snapshot on every plug-in)")
    if selection.diag_extras:
        summary.append("Deep hardware & 30-second UART/input capture enabled (extras.flag)")

    wait_s = "90 seconds" if selection.diag_extras else "60 seconds"
    steps = [
        "Safely eject this USB stick from your computer.",
        "Turn on your car / Eanon X3 head unit and wait until the home screen finishes loading.",
        f"Plug the USB stick into the X3's USB port and wait {wait_s}. Nothing appears on screen — it runs silently.",
        "Unplug the USB stick, then turn your car COMPLETELY OFF so the head unit does a full cold boot (wait ~1 minute, or until the unit fully powers down), then turn it back on.",
        "Once booted, plug the USB stick back into the X3 for 30–60 seconds so it logs verification (and runs the Wi-Fi 40 MHz health check if enabled).",
        "Bring the USB stick back to your computer and click 'Inspect USB Stick Now' in Eanon X3 Patcher to confirm everything loaded cleanly.",
    ]
    _write_lf(root / "README_X3_PATCHER.txt", _format_instructions_file("Install Patches", summary, steps))
    written.append("README_X3_PATCHER.txt")
    if progress:
        progress("USB stick verified and ready!")
    return WriteResult("install", str(root), written, payload, summary, steps)


def prepare_uninstall_stick(
    usb_root: str | os.PathLike[str],
    uninstall_lib: bool = True,
    undo_wifi40: bool = True,
    progress: Optional[Callable[[str], None]] = None,
) -> WriteResult:
    """Prepare a recovery/uninstall USB stick that removes selected overrides."""
    if not uninstall_lib and not undo_wifi40:
        raise UsbWriteError("Select at least one patch to uninstall/undo.")
    root = Path(usb_root)
    if not root.is_dir():
        raise UsbWriteError(f"USB path does not exist or is not a folder: {root}")

    if progress:
        progress("Writing uninstall/recovery scripts...")
    written = _write_base_scripts(root)
    _write_lf(root / "x3patch" / "mode", "uninstall\n" if uninstall_lib else "status\n")
    _write_lf(root / "wifi40" / "mode", "undo\n" if undo_wifi40 else "status\n")
    written.extend(["x3patch/mode", "wifi40/mode"])
    _set_extras_flag(root, False)

    summary: list[str] = []
    if uninstall_lib:
        summary.append("Remove /media/flash/nvm/libinfomodule.so (restores stock Emperor/Day-Night/UI strings)")
    if undo_wifi40:
        summary.append("Remove ht_capab from /media/flash/nvm/softap.conf (restores stock 20 MHz Wi-Fi AP)")

    steps = [
        "Safely eject this USB stick from your computer.",
        "Turn on your car / Eanon X3 head unit (this works even if the UI screen failed to start).",
        "Plug the USB stick into the X3's USB port and wait 45 seconds.",
        "Unplug the USB stick, turn the car completely off, wait ~1 minute for a cold boot, then turn it back on.",
        "Your head unit is now back to stock. You can plug the stick back into your PC to verify in 'Check Status'.",
    ]
    _write_lf(root / "README_X3_PATCHER.txt", _format_instructions_file("Uninstall / Restore Stock", summary, steps))
    written.append("README_X3_PATCHER.txt")
    if progress:
        progress("Uninstall USB stick is ready!")
    return WriteResult("uninstall", str(root), written, None, summary, steps)


def clear_wifi40_auto_undone(usb_root: str | os.PathLike[str]) -> bool:
    """Delete wifi40/auto_undone if present so the user can retry Wi-Fi 40 MHz."""
    p = Path(usb_root) / "wifi40" / "auto_undone"
    if p.exists():
        p.unlink()
        return True
    return False
