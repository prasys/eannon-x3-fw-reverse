# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for building standalone EanonX3-Patcher executable."""
import os
from pathlib import Path
import customtkinter

SPEC_DIR = Path(SPECPATH).resolve()
REPO_ROOT = SPEC_DIR.parents[1]
CTK_DIR = Path(customtkinter.__file__).resolve().parent

datas = [
    (str(REPO_ROOT / "tools" / "usb-diag" / "gemn_auto.sh"), "tools/usb-diag"),
    (str(REPO_ROOT / "tools" / "usb-patch" / "x3patch" / "patch.sh"), "tools/usb-patch/x3patch"),
    (str(REPO_ROOT / "tools" / "usb-patch" / "wifi40" / "wifi40.sh"), "tools/usb-patch/wifi40"),
    (str(REPO_ROOT / "tools" / "strings" / "tables_final.json"), "tools/strings"),
    (str(CTK_DIR), "customtkinter"),
]

a = Analysis(
    [str(SPEC_DIR / "main.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=["customtkinter", "tools.patcher.gui.app"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "scipy"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="EanonX3-Patcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
)
