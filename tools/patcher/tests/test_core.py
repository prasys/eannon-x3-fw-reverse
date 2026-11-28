"""Unit & integration tests for the Eanon X3 Patcher core engine."""
from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.patcher.core.firmware import (
    STOCK_LIBINFOMODULE_MD5_20250718,
    STOCK_LIBINFOMODULE_MD5_20250812,
    lookup_firmware,
)
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

# Optional local firmware / user-capture paths for real-image integration tests (gitignored, not in CI)
_CANDIDATE_FW_DIRS = [
    Path(__file__).resolve().parents[3] / "fw",
    Path("/Users/pradeesh/orca/projects/eanon-fw/fw"),
]
_FW_DIR = next((p for p in _CANDIDATE_FW_DIRS if (p / "X3_20250718.zip").is_file()), None)
_AUG_SO = Path(__file__).resolve().parents[3] / "diag" / "issue6_round2" / "libinfomodule_aug2025.so"


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
    def test_lookup_firmware_registry(self) -> None:
        fw07 = lookup_firmware(STOCK_LIBINFOMODULE_MD5_20250718)
        fw08 = lookup_firmware(STOCK_LIBINFOMODULE_MD5_20250812)
        self.assertIsNotNone(fw07)
        self.assertIsNotNone(fw08)
        assert fw07 is not None and fw08 is not None
        self.assertEqual(fw07.name, "X3_20250718")
        self.assertTrue(fw07.tested_on_unit)
        self.assertEqual(fw08.name, "X3_20250812")
        self.assertFalse(fw08.tested_on_unit)
        self.assertEqual(fw08.patchb_off, 0x41D33)
        self.assertEqual(fw08.tables_va, 0x41F30288)
        self.assertIsNone(lookup_firmware("0" * 32))

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
            # Stale reboot-loop guard marker from an interrupted extras run must be cleared
            (root / "extras.running").write_text("aaaa1111 10:00:00\n", encoding="utf-8")

            res = prepare_read_from_car_stick(root, diag_extras=True)
            self.assertFalse((root / "extras.running").exists())
            script = (root / "gemn_auto.sh").read_text(encoding="utf-8")
            # These reads rebooted units (issue #6, FINDINGS 8); they must stay out of the script
            code = "\n".join(l for l in script.splitlines() if not l.lstrip().startswith("#"))
            self.assertNotIn("/proc/tty/driver/", code)
            self.assertNotIn("pidof mcusrv", code)
            self.assertNotIn("fw_printenv", code)
            self.assertNotIn("nanddump", code)
            self.assertIn("extras.running", code)
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


@unittest.skipIf(sys.platform == "win32" or shutil.which("sh") is None, "POSIX /bin/sh required")
class TestPatchShShell(unittest.TestCase):
    """Run tools/usb-patch/x3patch/patch.sh against a fake root via X3PATCH_TEST_ROOT."""

    def _run_patch_sh(
        self,
        td: Path,
        unit_stock_data: bytes,
        payload_data: bytes,
        mode: str = "install",
        payload_md5: str | None = None,
        stock_md5_file: str | None = None,
    ) -> tuple[int, str, Path]:
        fake_root = td / "unit"
        usb = td / "usb"
        x3 = usb / "x3patch"
        (fake_root / "media" / "flash" / "nvm").mkdir(parents=True, exist_ok=True)
        (fake_root / "application" / "lib" / "module" / "default").mkdir(parents=True, exist_ok=True)
        (fake_root / "proc").mkdir(parents=True, exist_ok=True)
        (fake_root / "proc" / "uptime").write_text("42.0 40.0\n", encoding="utf-8")
        (fake_root / "application" / "lib" / "module" / "default" / "libinfomodule.so").write_bytes(unit_stock_data)

        x3.mkdir(parents=True, exist_ok=True)
        (x3 / "patch.sh").write_text(x3patch_script(), encoding="utf-8", newline="\n")
        (x3 / "mode").write_text(mode + "\n", encoding="utf-8", newline="\n")
        (x3 / "payload").mkdir(parents=True, exist_ok=True)
        (x3 / "payload" / "libinfomodule.so").write_bytes(payload_data)
        if payload_md5 is not None:
            (x3 / "payload" / "libinfomodule.so.md5").write_text(payload_md5 + "\n", encoding="utf-8", newline="\n")
        if stock_md5_file is not None:
            (x3 / "payload" / "libinfomodule.so.stock_md5").write_text(stock_md5_file + "\n", encoding="utf-8", newline="\n")

        # Provide minimal pidof + python-backed md5sum shim for macOS/CI compatibility
        shim_bin = td / "bin"
        shim_bin.mkdir(parents=True, exist_ok=True)
        pidof_p = shim_bin / "pidof"
        pidof_p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        pidof_p.chmod(0o755)
        if shutil.which("md5sum") is None:
            md5sum_p = shim_bin / "md5sum"
            md5sum_p.write_text(
                f"#!/bin/sh\n{sys.executable} -I -c \"import hashlib,sys; print(hashlib.md5(open(sys.argv[1],'rb').read()).hexdigest() + '  ' + sys.argv[1])\" \"$1\"\n",
                encoding="utf-8",
            )
            md5sum_p.chmod(0o755)

        env = os.environ.copy()
        env["X3PATCH_TEST_ROOT"] = str(fake_root)
        env["PATH"] = f"{shim_bin}:{env.get('PATH', '')}"
        proc = subprocess.run(["sh", str(x3 / "patch.sh")], env=env, capture_output=True, text=True, check=False)
        log_text = (x3 / "log.txt").read_text(encoding="utf-8") if (x3 / "log.txt").is_file() else ""
        return proc.returncode, log_text, fake_root / "media" / "flash" / "nvm" / "libinfomodule.so"

    @staticmethod
    def _dummy_for_md5(target_md5: str) -> bytes:
        raise AssertionError("unused")

    def test_stock_binding_and_uninstall(self) -> None:
        # We can only test exact md5 matching if we have bytes with known stock md5s or via sidecars;
        # use real stock binaries if available, otherwise verify rejection of unsupported unit stock.
        if _FW_DIR is None or not _AUG_SO.is_file():
            with tempfile.TemporaryDirectory() as td:
                rc, log, target = self._run_patch_sh(Path(td), b"not_supported", b"payload")
                self.assertNotEqual(rc, 0)
                self.assertIn("not a supported firmware", log)
                self.assertFalse(target.exists())
            return

        stock07 = load_stock_libinfomodule(_FW_DIR / "X3_20250718.zip").data
        stock08 = _AUG_SO.read_bytes()
        pay07 = build_libinfomodule(stock07, PatchSelection(emperor_restore=True, daynight_ill=True, ui_strings=True))
        pay08 = build_libinfomodule(stock08, PatchSelection(emperor_restore=True, daynight_ill=True, ui_strings=True))

        # 1. X3_20250718 payload installs on X3_20250718 stock
        with tempfile.TemporaryDirectory() as td:
            rc, log, target = self._run_patch_sh(
                Path(td), stock07, pay07.binary, payload_md5=pay07.md5, stock_md5_file=pay07.stock_md5
            )
            self.assertEqual(rc, 0, log)
            self.assertIn("install: OK", log)
            self.assertEqual(hashlib.md5(target.read_bytes()).hexdigest(), pay07.md5)

        # 2. X3_20250812 payload installs on X3_20250812 stock
        with tempfile.TemporaryDirectory() as td:
            rc, log, target = self._run_patch_sh(
                Path(td), stock08, pay08.binary, payload_md5=pay08.md5, stock_md5_file=pay08.stock_md5
            )
            self.assertEqual(rc, 0, log)
            self.assertIn("install: OK", log)
            self.assertEqual(hashlib.md5(target.read_bytes()).hexdigest(), pay08.md5)

        # 3. X3_20250718 payload on X3_20250812 stock -> ABORT
        with tempfile.TemporaryDirectory() as td:
            rc, log, target = self._run_patch_sh(
                Path(td), stock08, pay07.binary, payload_md5=pay07.md5, stock_md5_file=pay07.stock_md5
            )
            self.assertNotEqual(rc, 0)
            self.assertIn("ABORT: payload was built for stock md5", log)
            self.assertFalse(target.exists())

        # 4. X3_20250812 payload on X3_20250718 stock -> ABORT
        with tempfile.TemporaryDirectory() as td:
            rc, log, target = self._run_patch_sh(
                Path(td), stock07, pay08.binary, payload_md5=pay08.md5, stock_md5_file=pay08.stock_md5
            )
            self.assertNotEqual(rc, 0)
            self.assertIn("ABORT: payload was built for stock md5", log)
            self.assertFalse(target.exists())

        # 5. Legacy stick (no .stock_md5): installs on X3_20250718, aborts on X3_20250812
        with tempfile.TemporaryDirectory() as td:
            rc, log, target = self._run_patch_sh(Path(td), stock07, pay07.binary, payload_md5=pay07.md5, stock_md5_file=None)
            self.assertEqual(rc, 0, log)
            self.assertTrue(target.exists())
            # Uninstall removes target
            (Path(td) / "usb" / "x3patch" / "mode").write_text("uninstall\n", encoding="utf-8")
            env = os.environ.copy()
            env["X3PATCH_TEST_ROOT"] = str(Path(td) / "unit")
            env["PATH"] = f"{Path(td) / 'bin'}:{env.get('PATH', '')}"
            rc2 = subprocess.run(["sh", str(Path(td) / "usb" / "x3patch" / "patch.sh")], env=env, check=False).returncode
            self.assertEqual(rc2, 0)
            self.assertFalse(target.exists())

        with tempfile.TemporaryDirectory() as td:
            rc, log, target = self._run_patch_sh(Path(td), stock08, pay07.binary, payload_md5=pay07.md5, stock_md5_file=None)
            self.assertNotEqual(rc, 0)
            self.assertIn("ABORT: payload was built for stock md5", log)
            self.assertFalse(target.exists())


@unittest.skipIf(_FW_DIR is None, "Local firmware directory fw/ not present")
class TestRealFirmwareIntegration(unittest.TestCase):
    def test_end_to_end_zip_to_usb_stick(self) -> None:
        assert _FW_DIR is not None
        res = load_stock_libinfomodule(_FW_DIR / "X3_20250718.zip")
        self.assertTrue(res.matches_x3_20250718)
        self.assertTrue(res.is_supported)
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
            self.assertEqual(
                (Path(td) / "x3patch" / "payload" / "libinfomodule.so.stock_md5").read_text(encoding="utf-8"),
                STOCK_LIBINFOMODULE_MD5_20250718 + "\n",
            )
            self.assertEqual((Path(td) / "x3patch" / "mode").read_bytes(), b"install\n")
            self.assertEqual((Path(td) / "wifi40" / "mode").read_bytes(), b"apply\n")
            self.assertTrue((Path(td) / "README_X3_PATCHER.txt").is_file())


@unittest.skipIf(not _AUG_SO.is_file(), "Local Aug 2025 user library not present")
class TestAug2025Integration(unittest.TestCase):
    def test_aug2025_builds_usb_and_cli_parity(self) -> None:
        res = load_stock_libinfomodule(_AUG_SO)
        self.assertTrue(res.is_supported)
        self.assertFalse(res.matches_x3_20250718)
        self.assertIsNotNone(res.firmware)
        assert res.firmware is not None
        self.assertEqual(res.firmware.name, "X3_20250812")
        self.assertEqual(res.md5, STOCK_LIBINFOMODULE_MD5_20250812)

        # Patch B only
        pb = build_libinfomodule(res.data, PatchSelection(emperor_restore=True, daynight_ill=False, ui_strings=False))
        self.assertEqual(pb.md5, "b6e16e0272070e2bbe0cbd4f2ee8cc9d")

        # Patch B + Strings
        pbs = build_libinfomodule(res.data, PatchSelection(emperor_restore=True, daynight_ill=False, ui_strings=True))
        self.assertEqual(pbs.md5, "e86ad71d591090a4bed9becb41f9f3ca")

        # Patch B + C + Strings
        pbsc = build_libinfomodule(res.data, PatchSelection(emperor_restore=True, daynight_ill=True, ui_strings=True))
        self.assertEqual(pbsc.md5, "e55d9270791f0402fa85975c6776dca0")

        # Exact parity with tools/build_payload.py, tools/apply_daynight.py, and tools/strings/strtab.py check
        repo_root = Path(_REPO_ROOT)
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            out_b = td_p / "cli_b"
            out_bc = td_p / "cli_bc"
            out_dn = td_p / "cli_dn"
            subprocess.run(
                [
                    sys.executable, "-I", str(repo_root / "tools" / "build_payload.py"),
                    str(_AUG_SO), str(repo_root / "tools" / "strings" / "tables_final.json"), str(out_b),
                ],
                check=True,
                capture_output=True,
            )
            self.assertEqual((out_b / "libinfomodule.so.md5").read_text(encoding="utf-8").strip(), pbs.md5)
            self.assertEqual((out_b / "libinfomodule.so.stock_md5").read_text(encoding="utf-8").strip(), STOCK_LIBINFOMODULE_MD5_20250812)

            subprocess.run(
                [
                    sys.executable, "-I", str(repo_root / "tools" / "build_payload.py"),
                    str(_AUG_SO), str(repo_root / "tools" / "strings" / "tables_final.json"), str(out_bc), "--daynight",
                ],
                check=True,
                capture_output=True,
            )
            self.assertEqual((out_bc / "libinfomodule.so.md5").read_text(encoding="utf-8").strip(), pbsc.md5)

            subprocess.run(
                [
                    sys.executable, "-I", str(repo_root / "tools" / "apply_daynight.py"),
                    str(out_b / "libinfomodule.so"), str(out_dn),
                ],
                check=True,
                capture_output=True,
            )
            self.assertEqual((out_dn / "libinfomodule.so.md5").read_text(encoding="utf-8").strip(), pbsc.md5)

            subprocess.run(
                [sys.executable, "-I", str(repo_root / "tools" / "strings" / "strtab.py"), "check", str(out_bc / "libinfomodule.so")],
                check=True,
                capture_output=True,
            )

            # USB stick write + inspect round-trip
            usb = td_p / "usb"
            usb.mkdir()
            (usb / "x3patch" / "stock").mkdir(parents=True)
            shutil.copy2(_AUG_SO, usb / "x3patch" / "stock" / "libinfomodule.so")
            wr = prepare_install_stick(usb, PatchSelection(emperor_restore=True, daynight_ill=True, ui_strings=True), stock_bytes=res.data)
            assert wr.payload is not None
            self.assertEqual(wr.payload.md5, pbsc.md5)
            self.assertEqual((usb / "x3patch" / "payload" / "libinfomodule.so.stock_md5").read_text(encoding="utf-8"), STOCK_LIBINFOMODULE_MD5_20250812 + "\n")
            st = inspect_usb_stick(usb)
            self.assertTrue(st.stock_is_supported)
            self.assertEqual(st.stock_firmware_name, "X3_20250812")
            self.assertFalse(st.stock_tested_on_unit)


if __name__ == "__main__":
    unittest.main()
