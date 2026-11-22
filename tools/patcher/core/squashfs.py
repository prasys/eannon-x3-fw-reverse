"""Pure-Python SquashFS 4.0 (LZO1X / uncompressed / zlib) extractor for Eanon X3 firmware.

Extracts stock libraries (e.g. `lib/module/default/libinfomodule.so`) directly from:
  - `X3_20250718.zip` (containing `ISPBOOOT.BIN`)
  - `ISPBOOOT.BIN` (Sunplus SPHE8368-U image; `spapp` sits at offset 0x3780000)
  - `spapp.bin` (raw SquashFS 4.0 LZO image)
  - a standalone stock `.so` file (e.g. from `<USB>/x3patch/stock/libinfomodule.so`)

Zero external tools (`unsquashfs`, `python-lzo`, etc.) are required, so this works
identically on Windows, macOS, and Linux.
"""
from __future__ import annotations

import hashlib
import io
import os
import struct
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Optional

# Verified coordinates from docs/FINDINGS.md section 1 & 2.3 (X3_20250718)
SPAPP_OFFSET = 0x3780000
SPAPP_SIZE = 0x7A6000
ISPBOOOT_MAGIC = b"XBUT"
SQUASHFS_MAGIC = b"hsqs"

STOCK_LIBINFOMODULE_PATH = "lib/module/default/libinfomodule.so"
STOCK_LIBINFOMODULE_MD5 = "f00b52a739bb333307ee9a841bd02369"

STOCK_LIBAPIS_PATH = "lib/libapis.so.1.0.0"
STOCK_LIBAPIS_MD5 = "6f02e18a787acf3ce0e5afda3b7bfec5"

# SquashFS 4.0 constants
COMP_GZIP = 1
COMP_LZO = 3
META_BLOCK_SIZE = 8192
META_UNCOMPRESSED_BIT = 0x8000
DATA_UNCOMPRESSED_BIT = 0x01000000
NO_FRAGMENT = 0xFFFFFFFF

INODE_BASIC_DIR = 1
INODE_BASIC_FILE = 2
INODE_BASIC_SYMLINK = 3
INODE_EXT_DIR = 8
INODE_EXT_FILE = 9
INODE_EXT_SYMLINK = 10


class FirmwareError(ValueError):
    """Raised when a firmware archive/image cannot be parsed or does not match."""


def lzo1x_decompress(src: bytes | memoryview) -> bytes:
    """Pure-Python LZO1X decompressor (matches Linux kernel lzo1x_decompress_safe)."""
    data = memoryview(src)
    n = len(data)
    if n == 0:
        return b""

    out = bytearray()
    ip = 0

    def copy_match(m_pos: int, length: int) -> None:
        if m_pos < 0 or m_pos >= len(out):
            raise FirmwareError(f"LZO1X lookbehind out of range: m_pos={m_pos}, out={len(out)}")
        dist = len(out) - m_pos
        if dist >= length:
            out.extend(out[m_pos : m_pos + length])
        elif dist == 1:
            out.extend(bytes([out[m_pos]]) * length)
        else:
            for _ in range(length):
                out.append(out[m_pos])
                m_pos += 1

    # State machine mirrors lzo1x_decompress_safe.c
    state = "top"
    t = 0
    lit = 0

    if data[ip] > 17:
        t = data[ip] - 17
        ip += 1
        if t < 4:
            lit = t
            state = "match_next"
        else:
            out.extend(data[ip : ip + t])
            ip += t
            state = "first_literal_run"

    while True:
        if state == "top":
            if ip >= n:
                raise FirmwareError("LZO1X unexpected end of input")
            t = data[ip]
            ip += 1
            if t >= 16:
                state = "match"
                continue
            if t == 0:
                while data[ip] == 0:
                    t += 255
                    ip += 1
                t += 15 + data[ip]
                ip += 1
            count = t + 3
            out.extend(data[ip : ip + count])
            ip += count
            state = "first_literal_run"
            continue

        if state == "first_literal_run":
            t = data[ip]
            ip += 1
            if t >= 16:
                state = "match"
                continue
            m_pos = len(out) - 1 - 0x0800 - (t >> 2) - (data[ip] << 2)
            ip += 1
            copy_match(m_pos, 3)
            lit = t & 3
            state = "match_done"
            continue

        if state == "match":
            if t >= 64:
                m_pos = len(out) - 1 - ((t >> 2) & 7) - (data[ip] << 3)
                ip += 1
                match_len = (t >> 5) + 1
                lit = t & 3
                copy_match(m_pos, match_len)
            elif t >= 32:
                t &= 31
                if t == 0:
                    while data[ip] == 0:
                        t += 255
                        ip += 1
                    t += 31 + data[ip]
                    ip += 1
                w = data[ip] | (data[ip + 1] << 8)
                ip += 2
                m_pos = len(out) - 1 - (w >> 2)
                lit = w & 3
                copy_match(m_pos, t + 2)
            elif t >= 16:
                m_pos = len(out) - ((t & 8) << 11)
                t &= 7
                if t == 0:
                    while data[ip] == 0:
                        t += 255
                        ip += 1
                    t += 7 + data[ip]
                    ip += 1
                w = data[ip] | (data[ip + 1] << 8)
                ip += 2
                m_pos -= w >> 2
                if m_pos == len(out):
                    return bytes(out)
                m_pos -= 0x4000
                lit = w & 3
                copy_match(m_pos, t + 2)
            else:
                m_pos = len(out) - 1 - (t >> 2) - (data[ip] << 2)
                ip += 1
                lit = t & 3
                copy_match(m_pos, 2)
            state = "match_done"
            continue

        if state == "match_done":
            if lit == 0:
                state = "top"
            else:
                state = "match_next"
            continue

        if state == "match_next":
            out.extend(data[ip : ip + lit])
            ip += lit
            t = data[ip]
            ip += 1
            state = "match"
            continue


class SquashFSReader:
    """Minimal read-only SquashFS 4.0 reader supporting uncompressed, gzip, and LZO1X."""

    def __init__(self, stream: BinaryIO, base_offset: int = 0) -> None:
        self._f = stream
        self._base = base_offset
        hdr = self._read_at(0, 96)
        if len(hdr) < 96 or hdr[:4] != SQUASHFS_MAGIC:
            raise FirmwareError("Not a valid SquashFS 4.0 image (missing 'hsqs' magic)")
        (
            _magic,
            self.inode_count,
            _mkfs_time,
            self.block_size,
            self.fragment_count,
            self.compression,
            self.block_log,
            self.flags,
            _no_ids,
            self.s_major,
            self.s_minor,
            self.root_inode_ref,
            self.bytes_used,
            _id_table_start,
            _xattr_table_start,
            self.inode_table_start,
            self.dir_table_start,
            self.fragment_table_start,
            _lookup_table_start,
        ) = struct.unpack("<IIIIIHHHHHHQQQQQQQQ", hdr)
        if self.s_major != 4 or self.s_minor != 0:
            raise FirmwareError(f"Unsupported SquashFS version {self.s_major}.{self.s_minor}")
        if self.compression not in (COMP_GZIP, COMP_LZO):
            raise FirmwareError(f"Unsupported SquashFS compression id {self.compression}")
        self._meta_cache: dict[int, tuple[bytes, int]] = {}
        self._frag_entries: Optional[list[tuple[int, int]]] = None

    def _read_at(self, rel_offset: int, size: int) -> bytes:
        self._f.seek(self._base + rel_offset)
        return self._f.read(size)

    def _decompress(self, raw: bytes) -> bytes:
        if self.compression == COMP_LZO:
            return lzo1x_decompress(raw)
        if self.compression == COMP_GZIP:
            return zlib.decompress(raw)
        raise FirmwareError(f"Unsupported compression {self.compression}")

    def _read_meta_block(self, abs_offset: int) -> tuple[bytes, int]:
        cached = self._meta_cache.get(abs_offset)
        if cached is not None:
            return cached
        hdr = self._read_at(abs_offset, 2)
        if len(hdr) < 2:
            raise FirmwareError("Unexpected EOF reading SquashFS metadata block")
        (raw_len,) = struct.unpack("<H", hdr)
        uncompressed = bool(raw_len & META_UNCOMPRESSED_BIT)
        disk_len = raw_len & 0x7FFF
        payload = self._read_at(abs_offset + 2, disk_len)
        data = payload if uncompressed else self._decompress(payload)
        next_off = abs_offset + 2 + disk_len
        self._meta_cache[abs_offset] = (data, next_off)
        return data, next_off

    def _read_meta_bytes(self, table_start: int, block_offset: int, inner_offset: int, length: int) -> tuple[bytes, int, int]:
        out = bytearray()
        cur_block = table_start + block_offset
        cur_off = inner_offset
        while len(out) < length:
            block_data, next_block = self._read_meta_block(cur_block)
            take = min(length - len(out), len(block_data) - cur_off)
            if take <= 0:
                raise FirmwareError("SquashFS metadata cursor out of range")
            out.extend(block_data[cur_off : cur_off + take])
            cur_off += take
            if cur_off >= len(block_data):
                cur_block = next_block
                cur_off = 0
        return bytes(out), cur_block - table_start, cur_off

    def _read_inode(self, inode_ref: int) -> dict:
        blk = (inode_ref >> 16) & 0xFFFFFFFF
        off = inode_ref & 0xFFFF
        base_hdr, blk, off = self._read_meta_bytes(self.inode_table_start, blk, off, 16)
        itype, mode, _uid, _gid, _mtime, ino = struct.unpack("<HHHHII", base_hdr)

        if itype == INODE_BASIC_DIR:
            body, blk, off = self._read_meta_bytes(self.inode_table_start, blk, off, 16)
            dir_blk, _nlink, fsize, dir_off, _parent = struct.unpack("<IIHHI", body)
            return {
                "type": "dir",
                "inode": ino,
                "dir_block": dir_blk,
                "dir_offset": dir_off,
                "file_size": fsize,
            }
        if itype == INODE_EXT_DIR:
            body, blk, off = self._read_meta_bytes(self.inode_table_start, blk, off, 24)
            _nlink, fsize, dir_blk, _parent, _idx_cnt, dir_off, _xattr = struct.unpack("<IIIIHHI", body)
            return {
                "type": "dir",
                "inode": ino,
                "dir_block": dir_blk,
                "dir_offset": dir_off,
                "file_size": fsize,
            }
        if itype == INODE_BASIC_FILE:
            body, blk, off = self._read_meta_bytes(self.inode_table_start, blk, off, 16)
            blocks_start, frag_idx, blk_off, fsize = struct.unpack("<IIII", body)
            nblocks = fsize // self.block_size
            if frag_idx == NO_FRAGMENT and (fsize % self.block_size) != 0:
                nblocks += 1
            sizes_raw, _, _ = self._read_meta_bytes(self.inode_table_start, blk, off, nblocks * 4)
            block_sizes = list(struct.unpack(f"<{nblocks}I", sizes_raw)) if nblocks else []
            return {
                "type": "file",
                "inode": ino,
                "blocks_start": blocks_start,
                "frag_index": frag_idx,
                "frag_offset": blk_off,
                "file_size": fsize,
                "block_sizes": block_sizes,
            }
        if itype == INODE_EXT_FILE:
            body, blk, off = self._read_meta_bytes(self.inode_table_start, blk, off, 40)
            blocks_start, fsize, _sparse, _nlink, frag_idx, blk_off, _xattr = struct.unpack("<QQQIIII", body)
            nblocks = fsize // self.block_size
            if frag_idx == NO_FRAGMENT and (fsize % self.block_size) != 0:
                nblocks += 1
            sizes_raw, _, _ = self._read_meta_bytes(self.inode_table_start, blk, off, nblocks * 4)
            block_sizes = list(struct.unpack(f"<{nblocks}I", sizes_raw)) if nblocks else []
            return {
                "type": "file",
                "inode": ino,
                "blocks_start": blocks_start,
                "frag_index": frag_idx,
                "frag_offset": blk_off,
                "file_size": fsize,
                "block_sizes": block_sizes,
            }
        if itype in (INODE_BASIC_SYMLINK, INODE_EXT_SYMLINK):
            body, blk, off = self._read_meta_bytes(self.inode_table_start, blk, off, 8)
            _nlink, target_size = struct.unpack("<II", body)
            target, _, _ = self._read_meta_bytes(self.inode_table_start, blk, off, target_size)
            return {"type": "symlink", "inode": ino, "target": target.decode("utf-8", errors="replace")}
        return {"type": f"other({itype})", "inode": ino}

    def _list_dir(self, dir_inode: dict) -> dict[str, int]:
        entries: dict[str, int] = {}
        # SquashFS directory file_size includes 3 extra bytes for '.' and '..'
        remaining = max(0, dir_inode["file_size"] - 3)
        blk = dir_inode["dir_block"]
        off = dir_inode["dir_offset"]
        consumed = 0
        while consumed < remaining:
            hdr, blk, off = self._read_meta_bytes(self.dir_table_start, blk, off, 12)
            consumed += 12
            count, start_block, _inode_num = struct.unpack("<III", hdr)
            for _ in range(count + 1):
                ehdr, blk, off = self._read_meta_bytes(self.dir_table_start, blk, off, 8)
                consumed += 8
                e_off, _e_ino_delta, _e_type, name_len_m1 = struct.unpack("<HhHH", ehdr)
                name_len = name_len_m1 + 1
                name_bytes, blk, off = self._read_meta_bytes(self.dir_table_start, blk, off, name_len)
                consumed += name_len
                name = name_bytes.decode("utf-8", errors="replace")
                inode_ref = (start_block << 16) | e_off
                entries[name] = inode_ref
        return entries

    def _load_fragments(self) -> list[tuple[int, int]]:
        if self._frag_entries is not None:
            return self._frag_entries
        entries: list[tuple[int, int]] = []
        if self.fragment_count == 0 or self.fragment_table_start == 0xFFFFFFFFFFFFFFFF:
            self._frag_entries = entries
            return entries
        # Fragment table contains 8-byte pointers to metadata blocks; each entry is 16 bytes
        entries_per_block = META_BLOCK_SIZE // 16
        num_meta = (self.fragment_count + entries_per_block - 1) // entries_per_block
        ptr_bytes = self._read_at(self.fragment_table_start, num_meta * 8)
        ptrs = struct.unpack(f"<{num_meta}Q", ptr_bytes)
        for ptr in ptrs:
            block_data, _ = self._read_meta_block(ptr)
            pos = 0
            while pos + 16 <= len(block_data) and len(entries) < self.fragment_count:
                start, size, _unused = struct.unpack_from("<QII", block_data, pos)
                entries.append((start, size))
                pos += 16
        self._frag_entries = entries
        return entries

    def _read_data_block(self, disk_offset: int, encoded_size: int) -> bytes:
        disk_size = encoded_size & 0x00FFFFFF
        if disk_size == 0:
            # Sparse block
            return b"\x00" * self.block_size
        raw = self._read_at(disk_offset, disk_size)
        if encoded_size & DATA_UNCOMPRESSED_BIT:
            return raw
        return self._decompress(raw)

    def read_file(self, path: str) -> bytes:
        """Extract a file at `path` (e.g. 'lib/module/default/libinfomodule.so')."""
        parts = [p for p in path.strip("/").split("/") if p]
        cur = self._read_inode(self.root_inode_ref)
        for i, part in enumerate(parts):
            if cur["type"] != "dir":
                raise FirmwareError(f"Path component '{part}' parent is not a directory")
            children = self._list_dir(cur)
            if part not in children:
                raise FirmwareError(f"File '{path}' not found inside firmware image (missing '{part}')")
            cur = self._read_inode(children[part])
            if cur["type"] == "symlink" and i < len(parts):
                raise FirmwareError(f"Unexpected symlink at '{part}' -> {cur['target']}")
        if cur["type"] != "file":
            raise FirmwareError(f"'{path}' is not a regular file (type={cur['type']})")

        out = bytearray()
        pos = cur["blocks_start"]
        for enc_size in cur["block_sizes"]:
            chunk = self._read_data_block(pos, enc_size)
            out.extend(chunk)
            pos += enc_size & 0x00FFFFFF

        frag_idx = cur["frag_index"]
        if frag_idx != NO_FRAGMENT:
            frags = self._load_fragments()
            if frag_idx >= len(frags):
                raise FirmwareError(f"Fragment index {frag_idx} out of bounds")
            f_start, f_size = frags[frag_idx]
            frag_block = self._read_data_block(f_start, f_size)
            rem = cur["file_size"] - len(out)
            f_off = cur["frag_offset"]
            out.extend(frag_block[f_off : f_off + rem])

        if len(out) > cur["file_size"]:
            del out[cur["file_size"] :]
        if len(out) != cur["file_size"]:
            raise FirmwareError(f"Extracted size mismatch for '{path}': got {len(out)}, expected {cur['file_size']}")
        return bytes(out)


@dataclass
class StockLibraryResult:
    data: bytes
    md5: str
    source_kind: str  # "zip", "ispbooot", "spapp", "so"
    source_path: str
    matches_x3_20250718: bool


def _extract_from_ispbooot_stream(
    stream: BinaryIO,
    target_path: str,
    progress: Optional[Callable[[str], None]] = None,
) -> bytes:
    stream.seek(0)
    magic = stream.read(4)
    if magic != ISPBOOOT_MAGIC:
        raise FirmwareError("Not a valid Sunplus ISPBOOOT.BIN file (missing 'XBUT' header)")

    stream.seek(SPAPP_OFFSET)
    sq_magic = stream.read(4)
    if sq_magic != SQUASHFS_MAGIC:
        raise FirmwareError(
            "Could not locate the spapp SquashFS partition at 0x3780000. "
            "This image may be a different firmware version than X3_20250718."
        )
    if progress:
        progress("Reading spapp SquashFS partition...")
    reader = SquashFSReader(stream, base_offset=SPAPP_OFFSET)
    if progress:
        progress(f"Extracting {target_path}...")
    return reader.read_file(target_path)


def load_stock_libinfomodule(
    path: str | os.PathLike[str],
    progress: Optional[Callable[[str], None]] = None,
) -> StockLibraryResult:
    """Load stock `libinfomodule.so` from a .zip, ISPBOOOT.BIN, spapp.bin, or .so file.

    Always verifies the MD5 against the known X3_20250718 stock library without
    executing anything from the input archive.
    """
    p = Path(path)
    if not p.is_file():
        raise FirmwareError(f"File does not exist: {p}")

    with p.open("rb") as fh:
        head = fh.read(4)
    data: bytes
    kind: str

    if head == b"\x7fELF":
        if progress:
            progress("Reading standalone libinfomodule.so...")
        data = p.read_bytes()
        kind = "so"
    elif head == SQUASHFS_MAGIC:
        if progress:
            progress("Reading spapp.bin SquashFS image...")
        with p.open("rb") as f:
            reader = SquashFSReader(f, base_offset=0)
            data = reader.read_file(STOCK_LIBINFOMODULE_PATH)
        kind = "spapp"
    elif head == ISPBOOOT_MAGIC:
        with p.open("rb") as f:
            data = _extract_from_ispbooot_stream(f, STOCK_LIBINFOMODULE_PATH, progress)
        kind = "ispbooot"
    elif head[:2] == b"PK":
        if progress:
            progress("Opening firmware ZIP archive...")
        with zipfile.ZipFile(p, "r") as zf:
            isp_member = None
            for info in zf.infolist():
                if not info.is_dir() and Path(info.filename).name.upper() == "ISPBOOOT.BIN":
                    isp_member = info
                    break
            if isp_member is None:
                raise FirmwareError(
                    "This ZIP file does not contain ISPBOOOT.BIN. "
                    "Please choose X3_20250718.zip (the SoC firmware, ~56 MB), not the MCU zip."
                )
            if progress:
                progress("Decompressing ISPBOOOT.BIN from ZIP (this takes a few seconds)...")
            raw_isp = zf.read(isp_member)
        data = _extract_from_ispbooot_stream(io.BytesIO(raw_isp), STOCK_LIBINFOMODULE_PATH, progress)
        kind = "zip"
    else:
        raise FirmwareError(
            "Unrecognized file format. Please select X3_20250718.zip, ISPBOOOT.BIN, "
            "spapp.bin, or a stock libinfomodule.so."
        )

    digest = hashlib.md5(data).hexdigest()
    return StockLibraryResult(
        data=data,
        md5=digest,
        source_kind=kind,
        source_path=str(p),
        matches_x3_20250718=(digest == STOCK_LIBINFOMODULE_MD5),
    )
