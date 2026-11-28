"""Per-firmware metadata and patch coordinates keyed by stock `libinfomodule.so` MD5."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

STOCK_LIBINFOMODULE_MD5_20250718 = "f00b52a739bb333307ee9a841bd02369"
STOCK_LIBINFOMODULE_MD5_20250812 = "6a5ce057b32f19576dfd8b60645d3923"


@dataclass(frozen=True)
class FirmwareProfile:
    """Verified binary offsets and virtual addresses for one stock firmware release."""

    name: str
    label: str
    stock_md5: str
    stock_size: int
    base: int
    patchb_off: int
    patchb_old: int
    patchb_new: int
    patchb_md5: str
    patchc_cave: int
    patchc_site: int
    patchc_site_old: bytes
    plt_system_dimmer_state: int
    plt_exec_dimmer_state_only: int
    plt_exec_dimmer_state: int
    tables_va: int
    tested_on_unit: bool


FIRMWARES_BY_STOCK_MD5: dict[str, FirmwareProfile] = {
    STOCK_LIBINFOMODULE_MD5_20250718: FirmwareProfile(
        name="X3_20250718",
        label="X3_20250718 (Jul 2025)",
        stock_md5=STOCK_LIBINFOMODULE_MD5_20250718,
        stock_size=536128,
        base=0x41EB0000,
        patchb_off=0x41D2B,
        patchb_old=0x80,
        patchb_new=0xB8,
        patchb_md5="fae35255cfc28d530fa100af5cab4c4d",
        patchc_cave=0x41EF1D2C,
        patchc_site=0x41EF2024,
        patchc_site_old=bytes.fromhex("f2f7e0eaf2f794ef"),
        plt_system_dimmer_state=0x41EE45E8,
        plt_exec_dimmer_state_only=0x41EE4F54,
        plt_exec_dimmer_state=0x41EE54B8,
        tables_va=0x41F30280,
        tested_on_unit=True,
    ),
    STOCK_LIBINFOMODULE_MD5_20250812: FirmwareProfile(
        name="X3_20250812",
        label="X3_20250812 (Aug 12 2025 / 7047-XBB_UI02)",
        stock_md5=STOCK_LIBINFOMODULE_MD5_20250812,
        stock_size=536136,
        base=0x41EB0000,
        patchb_off=0x41D33,
        patchb_old=0x80,
        patchb_new=0xB8,
        patchb_md5="b6e16e0272070e2bbe0cbd4f2ee8cc9d",
        patchc_cave=0x41EF1D34,
        patchc_site=0x41EF202C,
        patchc_site_old=bytes.fromhex("f2f7dceaf2f790ef"),
        plt_system_dimmer_state=0x41EE45E8,
        plt_exec_dimmer_state_only=0x41EE4F54,
        plt_exec_dimmer_state=0x41EE54B8,
        tables_va=0x41F30288,
        tested_on_unit=False,
    ),
}


def lookup_firmware(stock_md5: Optional[str]) -> Optional[FirmwareProfile]:
    """Return the FirmwareProfile matching a stock `libinfomodule.so` MD5, or None."""
    if not stock_md5:
        return None
    return FIRMWARES_BY_STOCK_MD5.get(stock_md5.strip().lower())


def supported_firmware_lines() -> list[str]:
    """Return human-readable lines describing all supported stock firmwares."""
    return [f"{fw.label}: {fw.stock_md5}" for fw in FIRMWARES_BY_STOCK_MD5.values()]


def supported_firmware_summary() -> str:
    """Return a concise multi-line list of supported stock MD5s."""
    return "\n".join(f"  - {line}" for line in supported_firmware_lines())
