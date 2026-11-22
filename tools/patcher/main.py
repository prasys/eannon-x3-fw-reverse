#!/usr/bin/env python3
"""Entry point for the Eanon X3 USB Patcher GUI (and headless self-test)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Support launching directly as `python tools/patcher/main.py` or as a PyInstaller script
if __package__ in (None, ""):
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from tools.patcher import __version__  # noqa: E402
from tools.patcher.core.resources import (  # noqa: E402
    gemn_auto_script,
    tables_final_path,
    wifi40_script,
    x3patch_script,
)


def _run_self_test(out_path: str | None = None) -> int:
    """Verify bundled scripts and tables load cleanly (used in CI on the built .exe)."""
    assert gemn_auto_script().startswith("#!/bin/sh\n"), "gemn_auto.sh missing or invalid"
    assert x3patch_script().startswith("#!/bin/sh\n"), "patch.sh missing or invalid"
    assert wifi40_script().startswith("#!/bin/sh\n"), "wifi40.sh missing or invalid"
    assert tables_final_path().is_file(), "tables_final.json missing"
    msg = f"Eanon X3 Patcher v{__version__} self-test OK\n"
    if out_path:
        Path(out_path).write_text(msg, encoding="utf-8")
    elif sys.stdout is not None:
        sys.stdout.write(msg)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Eanon X3 USB Patcher")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    parser.add_argument("--self-test", action="store_true", help="Verify bundled assets and exit")
    parser.add_argument("--self-test-out", help="Optional path to write self-test result (for windowed builds)")
    args = parser.parse_args(argv)

    if args.version:
        if sys.stdout is not None:
            print(f"Eanon X3 Patcher v{__version__}")
        return 0
    if args.self_test:
        return _run_self_test(args.self_test_out)

    try:
        from tools.patcher.gui.app import launch_gui
    except ModuleNotFoundError as exc:
        print(
            f"GUI dependency missing: {exc}\n"
            "Install requirements with: pip install -r tools/patcher/requirements.txt\n"
            "(Make sure Python includes Tk / tkinter.)",
            file=sys.stderr,
        )
        return 1

    return launch_gui()


if __name__ == "__main__":
    raise SystemExit(main())
