"""Pure-Python patch builder for Eanon X3 `libinfomodule.so` (X3_20250718).

Supports any valid combination of the verified `libinfomodule.so` patches on `main`:
  - Patch B: Emperor (DSP) restore (1 byte @ 0x41d2b, `beq.w` -> `b.w`)
  - Patch C: Day/Night follows live ILL at boot (24-byte cave @ 0x41d2c + 8-byte call @ 0x42024)
             Requires Patch B (uses the dead branch created by Patch B).
  - UI Language String Fixes (20 language tables via ELF RELA + post-.bss string blob)

Exact byte-for-byte parity with `tools/build_payload.py` and `tools/strings/strtab.py`.
"""
from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from typing import Optional

from .resources import tables_final_path
from .squashfs import STOCK_LIBINFOMODULE_MD5

# Patch B (FINDINGS 9)
PATCHB_OFF = 0x41D2B
PATCHB_OLD = 0x80
PATCHB_NEW = 0xB8

# Patch C (FINDINGS 15; prelinked base 0x41eb0000, file_off = vaddr - BASE)
BASE = 0x41EB0000
CAVE = 0x41EF1D2C
SITE = 0x41EF2024
SITE_OLD = bytes.fromhex("f2f7e0eaf2f794ef")
PLT_SYSTEM_DIMMER_STATE = 0x41EE45E8
PLT_EXEC_DIMMER_STATE_ONLY = 0x41EE4F54
PLT_EXEC_DIMMER_STATE = 0x41EE54B8
CAVE_REGION_LEN = 24

# UI string tables (FINDINGS 11)
LANGS = "en zh-CN zh-TW ja nl fr it ko pt ru es th de ar id tr he el pl cs ro".split()
TABLES_VA = 0x41F30280
NSTR = 417
R_ARM_RELATIVE = 23
LRM = "\u200e".encode("utf-8")


class PatchError(ValueError):
    """Raised when patch validation or construction fails."""


@dataclass(frozen=True)
class PatchSelection:
    emperor_restore: bool = True   # Patch B
    daynight_ill: bool = True      # Patch C (requires Patch B)
    ui_strings: bool = True        # Language string fixes (tables_final.json)
    wifi40: bool = False           # HT40 Wi-Fi AP (softap.conf, no libinfomodule change)
    diag_extras: bool = False      # Optional extras.flag for deeper hardware/UART diagnostics

    @property
    def needs_libinfomodule(self) -> bool:
        return self.emperor_restore or self.daynight_ill or self.ui_strings

    def validate(self) -> None:
        if self.daynight_ill and not self.emperor_restore:
            raise PatchError("Day/Night ILL fix (Patch C) requires Emperor Restore (Patch B).")


@dataclass
class BuiltPayload:
    binary: bytes
    md5: str
    build_txt: str
    applied_patches: list[str]
    strings_languages: int


def _md5(b: bytes | bytearray) -> str:
    return hashlib.md5(b).hexdigest()


def _thumb_call(pc: int, target: int, blx: bool) -> bytes:
    """Encode Thumb-2 BL (Thumb target) or BLX imm (ARM target, 4-aligned)."""
    base = (pc + 4) & ~3 if blx else pc + 4
    off = target - base
    if not (-(1 << 24) <= off < (1 << 24)) or (off % (4 if blx else 2)) != 0:
        raise PatchError(f"Thumb call target out of range: pc={pc:#x} target={target:#x}")
    sgn = (off >> 24) & 1
    i1, i2 = (off >> 23) & 1, (off >> 22) & 1
    j1, j2 = (~(i1 ^ sgn)) & 1, (~(i2 ^ sgn)) & 1
    hw1 = 0xF000 | (sgn << 10) | ((off >> 12) & 0x3FF)
    hw2 = (0xC000 if blx else 0xD000) | (j1 << 13) | (j2 << 11) | ((off >> 1) & 0x7FF)
    return hw1.to_bytes(2, "little") + hw2.to_bytes(2, "little")


def _apply_patch_b(d: bytearray) -> None:
    if d[PATCHB_OFF] != PATCHB_OLD:
        raise PatchError(
            f"Unexpected byte at Patch B offset {PATCHB_OFF:#x}: got {d[PATCHB_OFF]:#x}, expected {PATCHB_OLD:#x}"
        )
    d[PATCHB_OFF] = PATCHB_NEW


def _apply_patch_c(d: bytearray) -> None:
    if d[PATCHB_OFF] != PATCHB_NEW:
        raise PatchError("Patch C requires Patch B to be applied first")
    c, o = CAVE, CAVE - BASE
    code = bytearray()
    code += bytes.fromhex("08b5")                                        # push {r3, lr}
    code += _thumb_call(c + len(code), PLT_SYSTEM_DIMMER_STATE, True)    # r0 = saved state 0xa5/0xa6
    code += bytes.fromhex("a628")                                        # cmp r0, #0xa6
    code += bytes.fromhex("03d1")                                        # bne +3 -> only
    code += bytes.fromhex("a520")                                        # movs r0, #0xa5
    code += _thumb_call(c + len(code), PLT_EXEC_DIMMER_STATE, True)      # night saved: go to day
    code += bytes.fromhex("08bd")                                        # pop {r3, pc}
    code += _thumb_call(c + len(code), PLT_EXEC_DIMMER_STATE_ONLY, True) # day saved: as stock
    code += bytes.fromhex("08bd")                                        # pop {r3, pc}
    if len(code) != CAVE_REGION_LEN:
        raise PatchError(f"Patch C cave length mismatch: {len(code)}")
    d[o : o + len(code)] = code
    so = SITE - BASE
    if bytes(d[so : so + 8]) != SITE_OLD:
        raise PatchError("Patch C call site does not match stock bytes")
    d[so : so + 8] = _thumb_call(SITE, CAVE, False) + bytes.fromhex("00bf00bf")


# ---- ELF / String table manipulation (mirrors tools/strings/strtab.py) ----

class _Elf:
    def __init__(self, data: bytes | bytearray) -> None:
        self.d = bytearray(data)
        d = self.d
        if d[:4] != b"\x7fELF" or d[4] != 1 or d[5] != 1:
            raise PatchError("Input is not a 32-bit little-endian ELF binary")
        self.phoff, self.shoff = struct.unpack_from("<II", d, 0x1C)
        (self.phnum,) = struct.unpack_from("<H", d, 0x2C)
        self.shnum, self.shstrndx = struct.unpack_from("<HH", d, 0x30)
        self.ph = [list(struct.unpack_from("<8I", d, self.phoff + 32 * i)) for i in range(self.phnum)]
        self.sh = [list(struct.unpack_from("<10I", d, self.shoff + 40 * i)) for i in range(self.shnum)]
        so = self.sh[self.shstrndx][4]
        self.shname = [bytes(d[so + s[0] : d.index(b"\0", so + s[0])]).decode() for s in self.sh]

    def loads(self) -> list[list[int]]:
        return [p for p in self.ph if p[0] == 1]

    def v2o(self, v: int) -> int:
        for p in self.loads():
            if p[2] <= v < p[2] + p[4]:
                return v - p[2] + p[1]
        raise PatchError(f"vaddr {v:#x} not file-backed")

    def u32(self, v: int) -> int:
        return struct.unpack_from("<I", self.d, self.v2o(v))[0]

    def put32(self, v: int, x: int) -> None:
        struct.pack_into("<I", self.d, self.v2o(v), x)

    def cstr(self, v: int) -> bytes:
        o = self.v2o(v)
        return bytes(self.d[o : self.d.index(b"\0", o)])

    def section(self, name: str) -> list[int]:
        return self.sh[self.shname.index(name)]


def _strtab_tables(elf: _Elf) -> list[tuple[int, int]]:
    return [(TABLES_VA + 4 * li, elf.u32(TABLES_VA + 4 * li)) for li in range(len(LANGS))]


def _strtab_extract(elf: _Elf) -> dict[str, Optional[list[str]]]:
    res: dict[str, Optional[list[str]]] = {}
    for li, (_, t) in enumerate(_strtab_tables(elf)):
        res[LANGS[li]] = None if t == 0 else [elf.cstr(elf.u32(t + 4 * i)).decode("utf-8") for i in range(NSTR)]
    return res


def _strtab_rela_index(elf: _Elf) -> dict[int, int]:
    s = elf.section(".rel.dyn")
    if s[9] != 12:
        raise PatchError(f"Expected prelink RELA entries (entsize 12), got {s[9]}")
    idx: dict[int, int] = {}
    for o in range(s[4], s[4] + s[5], 12):
        off, info, _add = struct.unpack_from("<IIi", elf.d, o)
        if (info & 0xFF) == R_ARM_RELATIVE:
            idx[off] = o
    return idx


def _strtab_check(elf: _Elf) -> None:
    rel = _strtab_rela_index(elf)
    for li, (_, t) in enumerate(_strtab_tables(elf)):
        if not t:
            continue
        for i in range(NSTR):
            slot = t + 4 * i
            add = struct.unpack_from("<i", elf.d, rel[slot] + 8)[0] & 0xFFFFFFFF
            if add != elf.u32(slot):
                raise PatchError(f"RELA mismatch in string table {LANGS[li]}[{i}]")
            elf.cstr(add).decode("utf-8")


def _apply_ui_strings(data: bytearray, tables_json: dict[str, Optional[list[str]]]) -> tuple[bytearray, int]:
    elf = _Elf(data)
    stock = _strtab_extract(elf)
    tabs = _strtab_tables(elf)

    # Match build_payload.py + strtab.py expand() ordering & LRM preservation
    want: dict[str, dict[int, str]] = {}
    for lang, tab in tables_json.items():
        if not tab or not stock.get(lang):
            continue
        t = tabs[LANGS.index(lang)][1]
        if t == 0:
            continue
        ch: dict[int, str] = {}
        for i, s in enumerate(tab):
            if s != stock[lang][i]:
                val = s
                if elf.cstr(elf.u32(t + 4 * i)).startswith(LRM) and not val.startswith("\u200e"):
                    val = "\u200e" + val
                ch[i] = val
        if ch:
            want[lang] = ch

    if not want:
        return data, 0

    rel = _strtab_rela_index(elf)
    rw = [p for p in elf.loads() if p[6] & 2]
    if len(rw) != 1 or rw[0] is not elf.loads()[-1]:
        raise PatchError("Unexpected ELF PT_LOAD layout in libinfomodule.so")
    p = rw[0]
    fend = p[1] + p[4]
    blob_va = (p[2] + p[5] + 15) & ~15
    blob = bytearray()
    seen: dict[bytes, int] = {}
    edits: list[tuple[int, int]] = []

    for lang, m in want.items():
        t = tabs[LANGS.index(lang)][1]
        for i, s in m.items():
            b = s.encode("utf-8")
            if b"\0" in b:
                raise PatchError("Embedded NUL in UI string")
            if elf.cstr(elf.u32(t + 4 * i)) == b:
                continue
            if b not in seen:
                seen[b] = blob_va + len(blob)
                blob += b + b"\0"
            edits.append((t + 4 * i, seen[b]))

    grow = blob_va - p[2] - p[4] + len(blob)
    grow_al = (grow + 3) & ~3
    blob += b"\0" * (grow_al - grow)
    ins = bytes(blob_va - p[2] - p[4]) + bytes(blob)
    d = elf.d
    d[fend:fend] = ins
    shift = len(ins)
    elf.shoff += shift
    struct.pack_into("<I", d, 0x20, elf.shoff)

    for q in elf.ph:
        if q is p:
            q[4] = q[5] = blob_va - p[2] + len(blob)
        elif q[1] >= fend and q[4]:
            q[1] += shift
    for i, q in enumerate(elf.ph):
        struct.pack_into("<8I", d, elf.phoff + 32 * i, *q)
    for i, s in enumerate(elf.sh):
        if (s[2] & 2) == 0 and s[4] >= fend:
            s[4] += shift
        struct.pack_into("<10I", d, elf.shoff + 40 * i, *s)

    for slot, va in edits:
        if slot not in rel:
            raise PatchError(f"Slot {slot:#x} has no R_ARM_RELATIVE relocation")
        elf.put32(slot, va)
        struct.pack_into("<i", d, rel[slot] + 8, va)

    # Verify rebuilt ELF string tables round-trip cleanly
    check_elf = _Elf(d)
    _strtab_check(check_elf)
    got = _strtab_extract(check_elf)
    for lang, m in want.items():
        for i, s in m.items():
            if got[lang][i] != s:
                raise PatchError(f"Post-build verification failed for {lang}[{i}]")

    return d, len(want)


def build_libinfomodule(
    stock_bytes: bytes,
    selection: PatchSelection,
    tables_json: Optional[dict[str, Optional[list[str]]]] = None,
) -> BuiltPayload:
    """Build a patched `libinfomodule.so` from verified stock X3_20250718 bytes."""
    selection.validate()
    if not selection.needs_libinfomodule:
        raise PatchError("No libinfomodule.so patches were selected.")

    in_md5 = _md5(stock_bytes)
    if in_md5 != STOCK_LIBINFOMODULE_MD5:
        raise PatchError(
            f"Refusing to patch: stock libinfomodule.so MD5 is {in_md5}, "
            f"expected {STOCK_LIBINFOMODULE_MD5} (X3_20250718)."
        )

    d = bytearray(stock_bytes)
    applied: list[str] = []

    if selection.emperor_restore:
        _apply_patch_b(d)
        applied.append("emperor-restore")

    n_langs = 0
    if selection.ui_strings:
        if tables_json is None:
            tables_json = json.loads(tables_final_path().read_text(encoding="utf-8"))
        d, n_langs = _apply_ui_strings(d, tables_json)
        applied.append("ui-strings")

    # Apply Patch C after string table rebuild (matches tools/build_payload.py)
    if selection.daynight_ill:
        _apply_patch_c(d)
        applied.append("daynight-ill")

    out_bytes = bytes(d)
    out_md5 = _md5(out_bytes)
    strings_line = f"tables_final.json ({n_langs} languages)" if selection.ui_strings else "stock (unchanged)"
    patch_names = " ".join(p for p in applied if p != "ui-strings") or "none"
    build_txt = (
        f"input   libinfomodule.so md5 {STOCK_LIBINFOMODULE_MD5}\n"
        f"patches {patch_names}\n"
        f"strings {strings_line}\n"
        f"output  libinfomodule.so md5 {out_md5}, {len(out_bytes)} bytes\n"
    )
    return BuiltPayload(
        binary=out_bytes,
        md5=out_md5,
        build_txt=build_txt,
        applied_patches=applied,
        strings_languages=n_langs,
    )
