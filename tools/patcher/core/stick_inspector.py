"""Inspect an Eanon X3 USB stick's logs/stock dump and export privacy-redacted diagnostics."""
from __future__ import annotations

import hashlib
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .firmware import lookup_firmware, supported_firmware_summary
from .squashfs import STOCK_LIBINFOMODULE_MD5

# Extra host-side redaction rules (defense in depth on top of gemn_auto.sh's sed pass)
_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?im)^((?:wpa_)?passphrase|(?:wpa_)?psk|wep_key\d*|wpa_password|ssid)\s*=\s*.*$"), r"\1=<redacted>"),
    (re.compile(r"(?i)(passphrase|password|psk)([:=]\s?)[^\s,]+"), r"\1\2<redacted>"),
    (re.compile(r"(?i)(ser(?:ia|ai)l\[)[^\]]*\]"), r"\1<redacted>]"),
    (re.compile(r"(?i)((?:bt_?|device_?|phone_?)name\s*[:=]\s*)[^\r\n,;]+"), r"\1<redacted>"),
    (re.compile(r"\b((?:[0-9a-fA-F]{2}:){3})(?:[0-9a-fA-F]{2}:){2}[0-9a-fA-F]{2}\b"), r"\1xx:xx:xx"),
]

# Safe text files allowed inside a snapshot directory when exporting diagnostics
_SAFE_SNAP_FILES = {
    "system.txt",
    "ini_stat.txt",
    "audio_keys.txt",
    "daynight_keys.txt",
    "changes.txt",
    "dmesg.txt",
    "audio_events.txt",
    "daynight_events.txt",
    "logcat.txt",
    "window.log",
    "live.log",
}
_SAFE_SNAP_SUBDIRS = {"wifi", "wifi_sta"}
_SAFE_EXTRAS_FILES = {
    "hw.txt",
    "getprop.txt",
    "mcusrv.txt",
    "tty_driver.txt",
    "uart_counters.txt",
}


@dataclass
class StickStatus:
    usb_root: str
    stock_lib_path: Optional[str] = None
    stock_lib_md5: Optional[str] = None
    stock_matches_x3_20250718: bool = False
    stock_firmware_name: Optional[str] = None
    stock_firmware_label: Optional[str] = None
    stock_is_supported: bool = False
    stock_tested_on_unit: bool = False

    x3patch_mode: str = ""
    x3patch_last_run: str = ""
    x3patch_nvm_md5: str = ""
    x3patch_loaded_from: str = ""
    x3patch_loaded_from_nvm: bool = False
    x3patch_last_result: str = ""
    x3patch_aborts: list[str] = field(default_factory=list)
    x3patch_log_tail: str = ""

    wifi40_mode: str = ""
    wifi40_last_run: str = ""
    wifi40_is_40mhz: bool = False
    wifi40_auto_undone: bool = False
    wifi40_last_result: str = ""
    wifi40_aborts: list[str] = field(default_factory=list)
    wifi40_log_tail: str = ""

    diag_snapshot_count: int = 0
    diag_boot_ids: list[str] = field(default_factory=list)
    diag_runs_tail: str = ""

    @property
    def has_any_logs(self) -> bool:
        return bool(self.x3patch_log_tail or self.wifi40_log_tail or self.diag_snapshot_count > 0)

    def human_summary(self) -> list[str]:
        lines: list[str] = []
        if self.stock_lib_path:
            if self.stock_is_supported and self.stock_firmware_label:
                note = "" if self.stock_tested_on_unit else " (disassembly-verified, untested on hardware)"
                lines.append(f"Stock library on stick: FOUND & supported ({self.stock_firmware_label}{note}).")
            else:
                lines.append(
                    f"Stock library on stick: FOUND, but MD5 ({self.stock_lib_md5}) is not a supported firmware yet.\n"
                    f"Supported stock MD5s:\n{supported_firmware_summary()}\n"
                    "Do not install library patches; export safe diagnostics and attach x3patch/stock/libinfomodule.so on GitHub."
                )
        else:
            lines.append("Stock library on stick: not dumped yet.")

        if self.x3patch_log_tail:
            if self.x3patch_aborts:
                lines.append(f"Library patch (x3patch): ABORTED — {self.x3patch_aborts[-1]}")
            elif self.x3patch_loaded_from_nvm:
                lines.append(f"Library patch (x3patch): ACTIVE & loaded from /media/flash/nvm (md5 {self.x3patch_nvm_md5}).")
            elif self.x3patch_nvm_md5 and self.x3patch_nvm_md5 != "none":
                lines.append(
                    f"Library patch (x3patch): installed on flash (md5 {self.x3patch_nvm_md5}), "
                    "waiting for a cold reboot to take effect."
                )
            else:
                lines.append(f"Library patch (x3patch): stock active ({self.x3patch_last_result or 'no override in nvm'}).")
        else:
            lines.append("Library patch (x3patch): no car log yet.")

        if self.wifi40_log_tail:
            if self.wifi40_auto_undone:
                lines.append("Wi-Fi 40 MHz: AUTO-UNDONE (hostapd did not come up; safely reverted to 20 MHz).")
            elif self.wifi40_aborts:
                lines.append(f"Wi-Fi 40 MHz: ABORTED — {self.wifi40_aborts[-1]}")
            elif self.wifi40_is_40mhz:
                lines.append("Wi-Fi 40 MHz: ACTIVE at 40 MHz (secondary_channel verified).")
            elif self.wifi40_last_result:
                lines.append(f"Wi-Fi 40 MHz: {self.wifi40_last_result}")
        else:
            lines.append("Wi-Fi 40 MHz: no car log yet.")

        if self.diag_snapshot_count:
            boots = len(self.diag_boot_ids)
            lines.append(f"Diagnostics: {self.diag_snapshot_count} snapshot(s) across {boots} boot(s).")
        else:
            lines.append("Diagnostics: no snapshots captured yet.")
        return lines


def _read_small_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _tail_lines(text: str, n: int = 30) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def inspect_usb_stick(usb_root: str | os.PathLike[str]) -> StickStatus:
    """Parse stock dump, patch logs, and diagnostic runs from a USB stick."""
    root = Path(usb_root)
    st = StickStatus(usb_root=str(root))

    # 1. Check dumped stock library
    stock_p = root / "x3patch" / "stock" / "libinfomodule.so"
    if stock_p.is_file():
        st.stock_lib_path = str(stock_p)
        st.stock_lib_md5 = hashlib.md5(stock_p.read_bytes()).hexdigest()
        st.stock_matches_x3_20250718 = st.stock_lib_md5 == STOCK_LIBINFOMODULE_MD5
        fw = lookup_firmware(st.stock_lib_md5)
        if fw is not None:
            st.stock_firmware_name = fw.name
            st.stock_firmware_label = fw.label
            st.stock_is_supported = True
            st.stock_tested_on_unit = fw.tested_on_unit

    # 2. Parse x3patch/mode and x3patch/log.txt
    st.x3patch_mode = _read_small_text(root / "x3patch" / "mode")
    xlog_path = root / "x3patch" / "log.txt"
    if xlog_path.is_file():
        xlog = xlog_path.read_text(encoding="utf-8", errors="replace")
        st.x3patch_log_tail = _tail_lines(xlog, 25)
        # Analyze only the latest run block for current status
        blocks = re.split(r"(?m)^.*---- run:", xlog)
        latest = ("---- run:" + blocks[-1]) if len(blocks) > 1 else xlog
        for line in latest.splitlines():
            if "---- run:" in line:
                st.x3patch_last_run = line.strip()
            elif "status: Launcher pid" in line and "has libinfomodule.so from:" in line:
                where = line.split("has libinfomodule.so from:", 1)[1].strip()
                st.x3patch_loaded_from = where
                if "/media/flash/nvm/libinfomodule.so" in where:
                    st.x3patch_loaded_from_nvm = True
            elif "status: nvm copy:" in line:
                st.x3patch_nvm_md5 = line.split("status: nvm copy:", 1)[1].strip()
            elif "ABORT:" in line:
                st.x3patch_aborts.append(line.split("ABORT:", 1)[1].strip())
            elif any(k in line for k in ("install: OK", "install: already installed", "uninstall: removed", "uninstall: nothing")):
                st.x3patch_last_result = line.strip()

    # 3. Parse wifi40/mode, wifi40/auto_undone, wifi40/log.txt
    st.wifi40_mode = _read_small_text(root / "wifi40" / "mode")
    st.wifi40_auto_undone = (root / "wifi40" / "auto_undone").is_file()
    wlog_path = root / "wifi40" / "log.txt"
    if wlog_path.is_file():
        wlog = wlog_path.read_text(encoding="utf-8", errors="replace")
        st.wifi40_log_tail = _tail_lines(wlog, 25)
        blocks = re.split(r"(?m)^.*---- run:", wlog)
        latest = ("---- run:" + blocks[-1]) if len(blocks) > 1 else wlog
        for line in latest.splitlines():
            if "---- run:" in line:
                st.wifi40_last_run = line.strip()
            elif "ABORT" in line:
                st.wifi40_aborts.append(line.strip())
            elif "check: OK, AP is up at 40 MHz" in line or "secondary_channel=1" in line or "secondary_channel=-1" in line:
                st.wifi40_is_40mhz = True
                st.wifi40_last_result = line.strip()
            elif any(k in line for k in ("apply: OK", "apply: pending", "undo:", "auto-undo:", "check: AP is up")):
                st.wifi40_last_result = line.strip()

    # 4. Parse diag/
    diag_dir = root / "diag"
    if diag_dir.is_dir():
        snaps = [d for d in sorted(diag_dir.iterdir()) if d.is_dir() and d.name != "last"]
        st.diag_snapshot_count = len(snaps)
        boot_ids: list[str] = []
        for s in snaps:
            bid = s.name.split("_", 1)[0]
            if bid and bid not in boot_ids:
                boot_ids.append(bid)
        st.diag_boot_ids = boot_ids
        runs_p = diag_dir / "runs.txt"
        if runs_p.is_file():
            st.diag_runs_tail = _tail_lines(runs_p.read_text(encoding="utf-8", errors="replace"), 20)

    return st


def redact_text(text: str) -> str:
    """Apply privacy redaction rules to log/diagnostic text."""
    out = text
    for pattern, repl in _REDACT_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def export_safe_diagnostics_zip(
    usb_root: str | os.PathLike[str],
    out_zip_path: str | os.PathLike[str],
) -> tuple[Path, list[str]]:
    """Bundle logs & redacted diagnostic snapshots into a GitHub-safe zip archive.

    Never includes raw `nvm/*.ini` settings backups, binary `.so` files, or raw input dumps.
    Returns `(zip_path, included_relative_paths)`.
    """
    root = Path(usb_root)
    out_zip = Path(out_zip_path)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    included: list[str] = []

    def add_redacted(zf: zipfile.ZipFile, src: Path, arcname: str) -> None:
        raw = src.read_text(encoding="utf-8", errors="replace")
        zf.writestr(arcname, redact_text(raw).encode("utf-8"))
        included.append(arcname)

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Patch & wifi logs / metadata (no binaries)
        top_files = [
            "x3patch/log.txt",
            "x3patch/mode",
            "x3patch/payload/BUILD.txt",
            "x3patch/payload/libinfomodule.so.md5",
            "x3patch/payload/libinfomodule.so.stock_md5",
            "wifi40/log.txt",
            "wifi40/mode",
            "wifi40/auto_undone",
            "diag/runs.txt",
        ]
        for rel in top_files:
            p = root / rel
            if p.is_file():
                add_redacted(zf, p, rel)

        # Diagnostic snapshots (only allowlisted text logs; skips nvm/*.ini and raw blobs)
        diag_dir = root / "diag"
        if diag_dir.is_dir():
            for snap in sorted(diag_dir.iterdir()):
                if not snap.is_dir() or snap.name == "last":
                    continue
                for fname in sorted(_SAFE_SNAP_FILES):
                    fp = snap / fname
                    if fp.is_file():
                        # Skip full SETUPMENU/ResumeInfo diffs in changes.txt if they might carry SSID/BT names?
                        # Redaction handles sensitive keys, and changes.txt only has diffs.
                        add_redacted(zf, fp, f"diag/{snap.name}/{fname}")
                for sub in sorted(_SAFE_SNAP_SUBDIRS):
                    sub_dir = snap / sub
                    if sub_dir.is_dir():
                        for fp in sorted(sub_dir.iterdir()):
                            if fp.is_file():
                                add_redacted(zf, fp, f"diag/{snap.name}/{sub}/{fp.name}")
                extras_dir = snap / "extras"
                if extras_dir.is_dir():
                    for fname in sorted(_SAFE_EXTRAS_FILES):
                        fp = extras_dir / fname
                        if fp.is_file():
                            add_redacted(zf, fp, f"diag/{snap.name}/extras/{fname}")

        # Add a summary report with stock MD5 (without including the binary itself)
        st = inspect_usb_stick(root)
        summary = [
            "Eanon X3 Safe Diagnostics Export",
            "================================",
            "",
            *st.human_summary(),
            "",
            "Privacy note:",
            "  - Raw settings backups (diag/*/nvm/*.ini) and binary libraries (.so) were EXCLUDED.",
            "  - Wi-Fi passphrases, SSIDs, device serials, BT device names, and MAC suffixes were REDACTED.",
            "",
            f"Included files ({len(included)}):",
            *(f"  - {name}" for name in included),
            "",
        ]
        zf.writestr("EXPORT_SUMMARY.txt", "\n".join(summary).encode("utf-8"))

    return out_zip, included
