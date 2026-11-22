"""Unit & integration tests for the Eanon X3 Patcher core engine."""
from __future__ import annotations

import hashlib
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.patcher.core.patch_engine import PatchError, PatchSelection, build_libinfomodule
from tools.patcher.core.resources import gemn_auto_script, tables_final_path, wifi40_script, x3patch_script
from tools.patcher.core.squashfs import (
    STOCK_LIBINFOMODULE_MD5,
    FirmwareError,
    SquashFSReader,
    load_stock_libinfomodule,
    lzo1x_decompress,
)
from tools.patcher.core.stick_inspector import export_safe_diagnostics_zip, inspect_usb_stick, redact_text
from tools.patcher.core.usb_writer import (
    UsbWriteError,
    clear_wifi40_auto_undone,
    prepare_install_stick,
    prepare_read_from_car_stick,
    prepare_uninstall_stick,
)

# Optional local firmware directory for real-image integration tests (gitignored, not in CI)
_CANDIDATE_FW_DIRS = [
    Path(__file__).resolve().parents[3] / "fw",
    Path("/Users/pradeesh/orca/projects/eanon-fw/fw"),
]
_FW_DIR = next((p for p in _CANDIDATE_FW_DIRS if (p / "X3_20250718.zip").is_file()), None)


class TestResources(unittest.TestCase):
    def test_bundled_scripts_have_lf_only(self) -> None:
        for fn in (gemn_auto_script, x3patch_script, wifi40_script):
            text = fn()
            self.assertTrue(text.startswith("#!/bin/sh\n"))
            self.assertNotIn("\r", text)
        self.assertTrue(tables_final_path().is_file())


class TestLzoAndSquashFS(unittest.TestCase):
    def test_lzo1x_literal_and_eof(self) -> None:
        # Initial literal run of 5 bytes ("hello") + M4 EOF (0x11, 0x00, 0x00)
        # First byte = 17 + 5 = 22 (0x16), followed by b"hello", then 0x11 0x00 0x00
        comp = bytes([22]) + b"hello" + bytes([0x11, 0x00, 0x00])
        self.assertEqual(lzo1x_decompress(comp), b"hello")

    def test_lzo1x_overlapping_match(self) -> None:
        # Literal "a" (first byte 17+1=18 -> match_next lit=1 -> "a"), then M2 match dist=1 len=4
        # Wait: simpler: top literal run of 4 bytes "abcd" (0x15 + "abcd"), then M2 match dist=1 len=3 (0x40, 0x00), then EOF (0x11, 0x00, 0x00)
        comp = bytes([17 + 4]) + b"abcd" + bytes([0x40, 0x00]) + bytes([0x11, 0x00, 0x00])
        self.assertEqual(lzo1x_decompress(comp), b"abcdddd")

    def test_reject_unknown_file_format(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "random.bin"
            bad.write_bytes(b"NOT_A_FIRMWARE_IMAGE_12345")
            with self.assertRaises(FirmwareError):
                load_stock_libinfomodule(bad)

    def test_reject_wrong_zip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            zp = Path(td) / "mcu.zip"
            with zipfile.ZipFile(zp, "w") as zf:
                zf.writestr("mcu8368u.bin", b"dummy")
            with self.assertRaises(FirmwareError):
                load_stock_libinfomodule(zp)

    def test_mismatched_so_returns_matches_false(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fake_so = Path(td) / "libinfomodule.so"
            fake_so.write_bytes(b"\x7fELF" + b"\x00" * 64)
            res = load_stock_libinfomodule(fake_so)
            self.assertEqual(res.source_kind, "so")
            self.assertFalse(res.matches_x3_20250718)

    def test_synthetic_squashfs_extract(self) -> None:
        # Build a minimal uncompressed SquashFS 4.0 image containing `/a/hello.txt`
        file_content = b"Eanon X3 SquashFS test payload!\n"
        data_offset = 96

        # Inode 2: basic file (hello.txt) at inode_table offset 0
        # Base header (16B): type=2, mode=0644, uid=0, gid=0, mtime=0, ino=2
        # Body (16B): blocks_start=96, frag_idx=0xffffffff, blk_off=0, file_size=len
        # Block sizes (4B): len | DATA_UNCOMPRESSED_BIT (0x01000000)
        file_inode = (
            struct.pack("<HHHHII", 2, 0o644, 0, 0, 0, 2)
            + struct.pack("<IIII", data_offset, 0xFFFFFFFF, 0, len(file_content))
            + struct.pack("<I", len(file_content) | 0x01000000)
        )
        # Dir entry for root -> "hello.txt" pointing to inode_ref=0 (block 0, off 0)
        name = b"hello.txt"
        dir_data = (
            struct.pack("<III", 0, 0, 2)  # count-1=0, start_block=0, inode_num=2
            + struct.pack("<HhHH", 0, 0, 2, len(name) - 1)
            + name
        )
        # Root dir inode at inode_table offset len(file_inode)
        root_off = len(file_inode)
        root_inode = (
            struct.pack("<HHHHII", 1, 0o755, 0, 0, 0, 1)
            + struct.pack("<IIHHI", 0, 2, len(dir_data) + 3, 0, 1)
        )
        inode_meta_payload = file_inode + root_inode
        inode_meta_block = struct.pack("<H", len(inode_meta_payload) | 0x8000) + inode_meta_payload
        dir_meta_block = struct.pack("<H", len(dir_data) | 0x8000) + dir_data

        inode_tbl_start = data_offset + len(file_content)
        dir_tbl_start = inode_tbl_start + len(inode_meta_block)
        bytes_used = dir_tbl_start + len(dir_meta_block)

        sb = struct.pack(
            "<IIIIIHHHHHHQQQQQQQQ",
            int.from_bytes(b"hsqs", "little"),
            2,            # inodes
            0,            # mkfs_time
            32768,        # block_size
            0,            # frag_cnt
            3,            # comp=LZO
            15,           # block_log
            0,            # flags
            1,            # no_ids
            4, 0,         # version 4.0
            root_off,     # root_inode_ref (block 0, off root_off)
            bytes_used,
            0xFFFFFFFFFFFFFFFF,
            0xFFFFFFFFFFFFFFFF,
            inode_tbl_start,
            dir_tbl_start,
            0xFFFFFFFFFFFFFFFF,
            0xFFFFFFFFFFFFFFFF,
        )
        img = sb + file_content + inode_meta_block + dir_meta_block
        import io
        reader = SquashFSReader(io.BytesIO(img))
        self.assertEqual(reader.read_file("hello.txt"), file_content)


class TestPatchValidation(unittest.TestCase):
    def test_patch_c_requires_patch_b(self) -> None:
        sel = PatchSelection(emperor_restore=False, daynight_ill=True, ui_strings=False)
        with self.assertRaises(PatchError):
            sel.validate()

    def test_refuses_wrong_stock_md5(self) -> None:
        sel = PatchSelection(emperor_restore=True, daynight_ill=False, ui_strings=False)
        with self.assertRaises(PatchError):
            build_libinfomodule(b"\x7fELF" + b"\x00" * 100, sel)


class TestUsbWriterAndInspector(unittest.TestCase):
    def test_read_from_car_and_uninstall_preserve_existing_files_and_use_lf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Pre-existing logs & stock must NEVER be deleted
            (root / "diag").mkdir()
            (root / "diag" / "runs.txt").write_text("2026-11-22 10:00:00 boot_id=aaaa1111 uptime=42s snap=aaaa1111_1 recorder=started\n", encoding="utf-8")
            (root / "x3patch" / "stock").mkdir(parents=True)
            (root / "x3patch" / "stock" / "keep.txt").write_text("keep", encoding="utf-8")

            res = prepare_read_from_car_stick(root, diag_extras=True)
            self.assertEqual(res.action, "read_from_car")
            self.assertTrue((root / "extras.flag").is_file())
            self.assertEqual((root / "x3patch" / "mode").read_bytes(), b"status\n")
            self.assertEqual((root / "wifi40" / "mode").read_bytes(), b"status\n")
            self.assertNotIn(b"\r", (root / "gemn_auto.sh").read_bytes())
            self.assertNotIn(b"\r", (root / "x3patch" / "patch.sh").read_bytes())
            self.assertNotIn(b"\r", (root / "wifi40" / "wifi40.sh").read_bytes())
            self.assertTrue((root / "x3patch" / "stock" / "keep.txt").is_file())

            # Uninstall stick removes extras.flag, sets uninstall/undo, preserves stock & diag
            u = prepare_uninstall_stick(root, uninstall_lib=True, undo_wifi40=True)
            self.assertEqual(u.action, "uninstall")
            self.assertFalse((root / "extras.flag").exists())
            self.assertEqual((root / "x3patch" / "mode").read_bytes(), b"uninstall\n")
            self.assertEqual((root / "wifi40" / "mode").read_bytes(), b"undo\n")
            self.assertTrue((root / "diag" / "runs.txt").is_file())
            self.assertTrue((root / "x3patch" / "stock" / "keep.txt").is_file())

    def test_wifi40_only_install_requires_no_stock_binary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sel = PatchSelection(emperor_restore=False, daynight_ill=False, ui_strings=False, wifi40=True)
            res = prepare_install_stick(root, sel, stock_bytes=None)
            self.assertIsNone(res.payload)
            self.assertEqual((root / "x3patch" / "mode").read_bytes(), b"status\n")
            self.assertEqual((root / "wifi40" / "mode").read_bytes(), b"apply\n")

    def test_lib_patch_without_stock_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sel = PatchSelection(emperor_restore=True, daynight_ill=False, ui_strings=False)
            with self.assertRaises(UsbWriteError):
                prepare_install_stick(td, sel, stock_bytes=None)

    def test_inspector_parses_logs_and_redacts_export(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "x3patch" / "payload").mkdir(parents=True)
            (root / "wifi40").mkdir(parents=True)
            snap = root / "diag" / "1234abcd_1"
            (snap / "nvm").mkdir(parents=True)
            (snap / "wifi").mkdir(parents=True)

            (root / "x3patch" / "mode").write_text("install\n", encoding="utf-8")
            (root / "x3patch" / "log.txt").write_text(
                "2026-11-22 10:00:00 ---- run: mode=install uptime=55.1s\n"
                "2026-11-22 10:00:00 status: Launcher pid 412 has libinfomodule.so from: /media/flash/nvm/libinfomodule.so\n"
                "2026-11-22 10:00:00 status: nvm copy: cf3cb416b72f7fb4d576e95095c2b7a7\n"
                "2026-11-22 10:00:01 install: already installed (cf3cb416b72f7fb4d576e95095c2b7a7), nothing to do\n",
                encoding="utf-8",
            )
            (root / "wifi40" / "mode").write_text("apply\n", encoding="utf-8")
            (root / "wifi40" / "log.txt").write_text(
                "2026-11-22 10:00:02 ---- run: mode=apply boot_id=1234abcd uptime=57s\n"
                "2026-11-22 10:00:03 check: OK, AP is up at 40 MHz. hostapd: state=ENABLED secondary_channel=1\n",
                encoding="utf-8",
            )
            (root / "wifi40" / "auto_undone").write_text("test\n", encoding="utf-8")
            (root / "diag" / "runs.txt").write_text("2026-11-22 10:00:04 boot_id=1234abcd uptime=58s snap=1234abcd_1 recorder=started\n", encoding="utf-8")

            # Sensitive files that MUST be excluded or redacted
            (snap / "nvm" / "SETUPMENU.ini").write_text("secret_user_setting=PradeeshPhone\n", encoding="utf-8")
            (root / "x3patch" / "payload" / "libinfomodule.so").write_bytes(b"\x7fELFSECRET")
            (snap / "logcat.txt").write_text(
                "wpa_passphrase=SuperSecretWifiPass\n"
                "ssid=MyCarSSID\n"
                "phone_name=Alice iPhone\n"
                "MAC aa:bb:cc:11:22:33 serial[XYZ999]\n",
                encoding="utf-8",
            )
            (snap / "wifi" / "ap.txt").write_text("state=ENABLED\n", encoding="utf-8")

            st = inspect_usb_stick(root)
            self.assertTrue(st.x3patch_loaded_from_nvm)
            self.assertEqual(st.x3patch_nvm_md5, "cf3cb416b72f7fb4d576e95095c2b7a7")
            self.assertTrue(st.wifi40_is_40mhz)
            self.assertTrue(st.wifi40_auto_undone)
            self.assertEqual(st.diag_snapshot_count, 1)
            self.assertEqual(st.diag_boot_ids, ["1234abcd"])

            self.assertTrue(clear_wifi40_auto_undone(root))
            self.assertFalse((root / "wifi40" / "auto_undone").exists())

            out_zip = root / "export.zip"
            zpath, included = export_safe_diagnostics_zip(root, out_zip)
            self.assertTrue(zpath.is_file())
            with zipfile.ZipFile(zpath, "r") as zf:
                names = set(zf.namelist())
                self.assertIn("EXPORT_SUMMARY.txt", names)
                self.assertIn("x3patch/log.txt", names)
                self.assertIn("diag/1234abcd_1/logcat.txt", names)
                self.assertIn("diag/1234abcd_1/wifi/ap.txt", names)
                # Raw INI & .so MUST NOT be included
                self.assertFalse(any("SETUPMENU.ini" in n for n in names))
                self.assertFalse(any(n.endswith(".so") for n in names))
                lc = zf.read("diag/1234abcd_1/logcat.txt").decode("utf-8")
                self.assertNotIn("SuperSecretWifiPass", lc)
                self.assertNotIn("MyCarSSID", lc)
                self.assertNotIn("Alice iPhone", lc)
                self.assertNotIn("11:22:33", lc)
                self.assertNotIn("XYZ999", lc)
                self.assertIn("aa:bb:cc:xx:xx:xx", lc)

    def test_redact_text(self) -> None:
        s = redact_text("retpassphrase:mySecret123 serial[ABC123] mac 01:23:45:67:89:ab")
        self.assertNotIn("mySecret123", s)
        self.assertNotIn("ABC123", s)
        self.assertIn("01:23:45:xx:xx:xx", s)


@unittest.skipIf(_FW_DIR is None, "Local firmware directory fw/ not present")
class TestRealFirmwareIntegration(unittest.TestCase):
    def test_end_to_end_zip_to_usb_stick(self) -> None:
        assert _FW_DIR is not None
        res = load_stock_libinfomodule(_FW_DIR / "X3_20250718.zip")
        self.assertTrue(res.matches_x3_20250718)
        self.assertEqual(res.md5, STOCK_LIBINFOMODULE_MD5)

        # Patch B only
        pb = build_libinfomodule(res.data, PatchSelection(emperor_restore=True, daynight_ill=False, ui_strings=False))
        self.assertEqual(pb.md5, "fae35255cfc28d530fa100af5cab4c4d")

        # Patch B + C + Strings -> full USB stick write & inspect
        sel = PatchSelection(emperor_restore=True, daynight_ill=True, ui_strings=True, wifi40=True)
        with tempfile.TemporaryDirectory() as td:
            wr = prepare_install_stick(td, sel, stock_bytes=res.data)
            self.assertIsNotNone(wr.payload)
            assert wr.payload is not None
            self.assertEqual(wr.payload.md5, "cf3cb416b72f7fb4d576e95095c2b7a7")
            so_on_usb = Path(td) / "x3patch" / "payload" / "libinfomodule.so"
            self.assertEqual(hashlib.md5(so_on_usb.read_bytes()).hexdigest(), "cf3cb416b72f7fb4d576e95095c2b7a7")
            self.assertEqual((Path(td) / "x3patch" / "mode").read_bytes(), b"install\n")
            self.assertEqual((Path(td) / "wifi40" / "mode").read_bytes(), b"apply\n")
            self.assertTrue((Path(td) / "README_X3_PATCHER.txt").is_file())


if __name__ == "__main__":
    unittest.main()
