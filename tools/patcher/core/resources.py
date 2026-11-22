"""Locate bundled repo scripts and data files in both dev and PyInstaller builds."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _base_root() -> Path:
    """Return the root directory containing `tools/` assets."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    # Dev layout: <repo>/tools/patcher/core/resources.py -> <repo>
    return Path(__file__).resolve().parents[3]


def asset_path(*parts: str) -> Path:
    """Resolve an asset relative to the repository root (or PyInstaller bundle)."""
    p = _base_root().joinpath(*parts)
    if not p.exists():
        raise FileNotFoundError(f"Required bundled asset not found: {p}")
    return p


def read_text_lf(*parts: str) -> str:
    """Read a bundled text/shell asset normalized to LF line endings."""
    raw = asset_path(*parts).read_bytes()
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").decode("utf-8")


def tables_final_path() -> Path:
    return asset_path("tools", "strings", "tables_final.json")


def gemn_auto_script() -> str:
    return read_text_lf("tools", "usb-diag", "gemn_auto.sh")


def x3patch_script() -> str:
    return read_text_lf("tools", "usb-patch", "x3patch", "patch.sh")


def wifi40_script() -> str:
    return read_text_lf("tools", "usb-patch", "wifi40", "wifi40.sh")
