"""Cross-platform removable USB drive detection and FAT32 validation."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

FAT_FILESYSTEMS = {"FAT32", "FAT", "FAT16", "VFAT", "MSDOS"}


@dataclass
class DriveInfo:
    path: str
    label: str
    filesystem: str
    total_bytes: int
    free_bytes: int
    is_removable: bool
    fat32_ok: Optional[bool]  # None if unknown
    warning: str = ""

    @property
    def display_name(self) -> str:
        size_gb = self.total_bytes / (1024 ** 3) if self.total_bytes else 0.0
        lbl = f" ({self.label})" if self.label else ""
        fs = self.filesystem or "Unknown FS"
        return f"{self.path}{lbl}  —  {size_gb:.1f} GB, {fs}"


def _fat_warning(fs: str) -> tuple[Optional[bool], str]:
    if not fs:
        return None, ""
    up = fs.upper()
    if up in FAT_FILESYSTEMS:
        return True, ""
    return (
        False,
        f"This drive is formatted as {fs}. The Eanon X3 head unit requires a FAT32 (MBR) USB stick. "
        "Please format it as FAT32 in Windows Explorer (or Rufus for drives > 32 GB) before using it in the car.",
    )


def _list_windows_drives() -> list[DriveInfo]:
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    DRIVE_REMOVABLE = 2
    drives: list[DriveInfo] = []
    bitmask = kernel32.GetLogicalDrives()
    for i in range(26):
        if not (bitmask & (1 << i)):
            continue
        letter = chr(ord("A") + i)
        root = f"{letter}:\\"
        dtype = kernel32.GetDriveTypeW(root)
        if dtype != DRIVE_REMOVABLE:
            continue
        vol_buf = ctypes.create_unicode_buffer(261)
        fs_buf = ctypes.create_unicode_buffer(261)
        ok = kernel32.GetVolumeInformationW(
            root,
            vol_buf,
            len(vol_buf),
            None,
            None,
            None,
            fs_buf,
            len(fs_buf),
        )
        label = vol_buf.value.strip() if ok else ""
        fs = fs_buf.value.strip() if ok else ""
        try:
            usage = shutil.disk_usage(root)
            total, free = usage.total, usage.free
        except OSError:
            total, free = 0, 0
        fat_ok, warn = _fat_warning(fs)
        drives.append(
            DriveInfo(
                path=root,
                label=label,
                filesystem=fs,
                total_bytes=total,
                free_bytes=free,
                is_removable=True,
                fat32_ok=fat_ok,
                warning=warn,
            )
        )
    return drives


def _list_macos_drives() -> list[DriveInfo]:
    drives: list[DriveInfo] = []
    volumes = Path("/Volumes")
    if not volumes.is_dir():
        return drives
    for entry in sorted(volumes.iterdir()):
        if entry.is_symlink() or not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        fs = ""
        is_rem = False
        label = entry.name
        try:
            res = subprocess.run(
                ["diskutil", "info", "-plist", str(entry)],
                capture_output=True,
                check=False,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout:
                info = plistlib.loads(res.stdout)
                if info.get("Internal") is True and not info.get("RemovableMediaOrExternalDevice"):
                    continue
                is_rem = bool(info.get("RemovableMediaOrExternalDevice") or info.get("Ejectable"))
                fs = str(info.get("FilesystemType") or info.get("FilesystemName") or "")
                label = str(info.get("VolumeName") or entry.name)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        try:
            usage = shutil.disk_usage(entry)
            total, free = usage.total, usage.free
        except OSError:
            total, free = 0, 0
        fat_ok, warn = _fat_warning(fs)
        drives.append(
            DriveInfo(
                path=str(entry),
                label=label,
                filesystem=fs,
                total_bytes=total,
                free_bytes=free,
                is_removable=is_rem,
                fat32_ok=fat_ok,
                warning=warn,
            )
        )
    return drives


def _list_linux_drives() -> list[DriveInfo]:
    drives: list[DriveInfo] = []
    mounts = Path("/proc/mounts")
    if not mounts.is_file():
        return drives
    try:
        lines = mounts.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return drives
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        _dev, mnt, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
        if not any(mnt.startswith(p) for p in ("/media/", "/run/media/", "/mnt/")):
            continue
        try:
            usage = shutil.disk_usage(mnt)
            total, free = usage.total, usage.free
        except OSError:
            total, free = 0, 0
        fat_ok, warn = _fat_warning(fstype)
        drives.append(
            DriveInfo(
                path=mnt,
                label=Path(mnt).name,
                filesystem=fstype,
                total_bytes=total,
                free_bytes=free,
                is_removable=True,
                fat32_ok=fat_ok,
                warning=warn,
            )
        )
    return drives


def list_usb_drives() -> list[DriveInfo]:
    """Return a list of detected removable USB drives on the current OS."""
    if sys.platform == "win32":
        return _list_windows_drives()
    if sys.platform == "darwin":
        return _list_macos_drives()
    return _list_linux_drives()


def inspect_target_folder(path: str | os.PathLike[str]) -> DriveInfo:
    """Return DriveInfo for an arbitrary user-chosen folder or drive root."""
    p = Path(path).resolve()
    # Match against detected removable drives first if inside one
    for d in list_usb_drives():
        try:
            if p == Path(d.path).resolve():
                return d
        except OSError:
            pass
    try:
        usage = shutil.disk_usage(p)
        total, free = usage.total, usage.free
    except OSError:
        total, free = 0, 0

    fs = ""
    if sys.platform == "win32" and len(str(p)) >= 2 and str(p)[1] == ":":
        try:
            import ctypes

            root = str(p)[:2] + "\\"
            fs_buf = ctypes.create_unicode_buffer(261)
            if ctypes.windll.kernel32.GetVolumeInformationW(root, None, 0, None, None, None, fs_buf, len(fs_buf)):
                fs = fs_buf.value.strip()
        except Exception:
            pass
    fat_ok, warn = _fat_warning(fs)
    return DriveInfo(
        path=str(p),
        label=p.name or str(p),
        filesystem=fs,
        total_bytes=total,
        free_bytes=free,
        is_removable=False,
        fat32_ok=fat_ok,
        warning=warn,
    )
