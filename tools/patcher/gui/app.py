"""Friendly step-by-step GUI for patching the Eanon X3 head unit via USB.

Designed for non-technical Windows users first (macOS/Linux supported too).
All heavy I/O runs on worker threads; UI updates are marshalled via `after(0, ...)`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable, Optional

import customtkinter as ctk

from .. import __version__
from ..core.patch_engine import PatchSelection
from ..core.squashfs import STOCK_LIBINFOMODULE_MD5, FirmwareError, StockLibraryResult, load_stock_libinfomodule
from ..core.stick_inspector import StickStatus, export_safe_diagnostics_zip, inspect_usb_stick
from ..core.usb_drives import DriveInfo, inspect_target_folder, list_usb_drives
from ..core.usb_writer import (
    UsbWriteError,
    WriteResult,
    clear_wifi40_auto_undone,
    prepare_install_stick,
    prepare_read_from_car_stick,
    prepare_uninstall_stick,
)

# Visual palette (works cleanly in both Light and Dark mode)
ACCENT = ("#1d63dc", "#3b82f6")
ACCENT_HOVER = ("#154eb0", "#2563eb")
SUCCESS_BG = ("#e6f6ec", "#143523")
SUCCESS_FG = ("#116b35", "#6ee7a8")
WARN_BG = ("#fef3c7", "#3d2c0a")
WARN_FG = ("#92400e", "#fcd34d")
ERR_BG = ("#fee2e2", "#3f1515")
ERR_FG = ("#991b1b", "#fca5a5")
CARD_BG = ("#f4f6f9", "#1e222b")
MUTED_FG = ("#525d6e", "#9aa5b5")


def _open_folder_in_file_manager(folder: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except OSError:
        pass


class X3PatcherApp(ctk.CTk):
    """Main window for the Eanon X3 Patcher."""

    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self.title(f"Eanon X3 USB Patcher  v{__version__}")
        self.geometry("1000x700")
        self.minsize(900, 620)

        # App state
        self._drives: list[DriveInfo] = []
        self._selected_drive: Optional[DriveInfo] = None
        self._stock_result: Optional[StockLibraryResult] = None
        self._stick_status: Optional[StickStatus] = None
        self._last_write: Optional[WriteResult] = None
        self._busy = False

        # Tk variables
        self.var_drive_choice = ctk.StringVar(value="")
        self.var_patch_emperor = ctk.BooleanVar(value=True)
        self.var_patch_daynight = ctk.BooleanVar(value=True)
        self.var_patch_strings = ctk.BooleanVar(value=True)
        self.var_patch_wifi40 = ctk.BooleanVar(value=False)
        self.var_diag_extras = ctk.BooleanVar(value=False)
        self.var_read_extras = ctk.BooleanVar(value=False)
        self.var_undo_lib = ctk.BooleanVar(value=True)
        self.var_undo_wifi40 = ctk.BooleanVar(value=True)

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._pages: dict[str, ctk.CTkScrollableFrame] = {}

        self._build_layout()
        self._show_page("setup")
        self._refresh_drives()

    # ------------------------------------------------------------------ Layout
    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Left sidebar
        sidebar = ctk.CTkFrame(self, width=230, corner_radius=0)
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(6, weight=1)

        ctk.CTkLabel(
            sidebar,
            text="Eanon X3 Patcher",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(22, 2), sticky="w")

        ctk.CTkLabel(
            sidebar,
            text="Firmware X3_20250718\nUSB Patch & Diagnostics",
            font=ctk.CTkFont(size=12),
            text_color=MUTED_FG,
            justify="left",
        ).grid(row=1, column=0, padx=20, pady=(0, 20), sticky="w")

        nav_items = [
            ("setup", "1.  USB & Firmware"),
            ("patches", "2.  Select Patches"),
            ("status", "3.  Car Guide & Status"),
            ("undo", "4.  Undo / Restore"),
        ]
        for idx, (key, label) in enumerate(nav_items, start=2):
            btn = ctk.CTkButton(
                sidebar,
                text=label,
                anchor="w",
                height=42,
                corner_radius=8,
                font=ctk.CTkFont(size=14, weight="bold"),
                fg_color="transparent",
                text_color=("gray15", "gray90"),
                hover_color=("gray84", "gray25"),
                command=lambda k=key: self._show_page(k),
            )
            btn.grid(row=idx, column=0, padx=14, pady=4, sticky="ew")
            self._nav_buttons[key] = btn

        # Bottom safety card + theme switch
        safe_box = ctk.CTkFrame(sidebar, fg_color=SUCCESS_BG, corner_radius=8)
        safe_box.grid(row=7, column=0, padx=14, pady=(10, 10), sticky="ew")
        ctk.CTkLabel(
            safe_box,
            text="100% Reversible\nStock firmware in your head\nunit is never modified.",
            font=ctk.CTkFont(size=11),
            text_color=SUCCESS_FG,
            justify="left",
        ).pack(padx=10, pady=8, anchor="w")

        theme_row = ctk.CTkFrame(sidebar, fg_color="transparent")
        theme_row.grid(row=8, column=0, padx=14, pady=(0, 14), sticky="ew")
        ctk.CTkLabel(theme_row, text="Theme:", font=ctk.CTkFont(size=12), text_color=MUTED_FG).pack(side="left")
        theme_menu = ctk.CTkOptionMenu(
            theme_row,
            values=["System", "Light", "Dark"],
            width=110,
            height=28,
            command=lambda m: ctk.set_appearance_mode(m.lower()),
        )
        theme_menu.set("System")
        theme_menu.pack(side="right")

        # Main content container
        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.grid(row=0, column=1, sticky="nsew", padx=16, pady=(14, 6))
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        # Build all 4 pages
        self._pages["setup"] = self._build_setup_page(self._content)
        self._pages["patches"] = self._build_patches_page(self._content)
        self._pages["status"] = self._build_status_page(self._content)
        self._pages["undo"] = self._build_undo_page(self._content)

        # Bottom status bar
        bar = ctk.CTkFrame(self, height=38, corner_radius=0)
        bar.grid(row=1, column=1, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)
        self._status_label = ctk.CTkLabel(bar, text="Ready — start by selecting your USB stick.", anchor="w", font=ctk.CTkFont(size=12))
        self._status_label.grid(row=0, column=0, padx=16, pady=6, sticky="ew")
        self._progress = ctk.CTkProgressBar(bar, width=160, mode="indeterminate")
        self._progress.grid(row=0, column=1, padx=16, pady=6)
        self._progress.grid_remove()

    def _show_page(self, key: str) -> None:
        for k, frame in self._pages.items():
            if k == key:
                frame.grid(row=0, column=0, sticky="nsew")
            else:
                frame.grid_forget()
        for k, btn in self._nav_buttons.items():
            if k == key:
                btn.configure(fg_color=ACCENT, text_color="white", hover_color=ACCENT_HOVER)
            else:
                btn.configure(fg_color="transparent", text_color=("gray15", "gray90"), hover_color=("gray84", "gray25"))

    # ------------------------------------------------------------ Page 1: Setup
    def _build_setup_page(self, parent: ctk.CTkFrame) -> ctk.CTkScrollableFrame:
        page = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(page, text="Step 1: USB Stick & Stock Firmware", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        ctk.CTkLabel(
            page,
            text="Plug a FAT32 USB flash drive into your computer, then choose how to supply your unit's stock library.",
            font=ctk.CTkFont(size=13),
            text_color=MUTED_FG,
            wraplength=700,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(0, 14))

        # --- Card A: USB Drive Picker ---
        card_usb = ctk.CTkFrame(page, fg_color=CARD_BG, corner_radius=12)
        card_usb.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        card_usb.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card_usb, text="A. Select your USB Stick (FAT32 / MBR)", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, padx=16, pady=(14, 6), sticky="w"
        )

        row_drive = ctk.CTkFrame(card_usb, fg_color="transparent")
        row_drive.grid(row=1, column=0, padx=16, pady=(0, 8), sticky="ew")
        row_drive.grid_columnconfigure(0, weight=1)

        self._drive_menu = ctk.CTkOptionMenu(
            row_drive,
            variable=self.var_drive_choice,
            values=["No removable USB drives found"],
            command=self._on_drive_selected,
            height=36,
        )
        self._drive_menu.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkButton(row_drive, text="Refresh", width=90, height=36, command=self._refresh_drives).grid(
            row=0, column=1, padx=(0, 8)
        )
        ctk.CTkButton(
            row_drive,
            text="Browse Folder...",
            width=125,
            height=36,
            fg_color="transparent",
            border_width=1,
            text_color=("gray15", "gray90"),
            command=self._browse_usb_folder,
        ).grid(row=0, column=2)

        self._usb_banner = ctk.CTkLabel(
            card_usb,
            text="",
            font=ctk.CTkFont(size=12),
            corner_radius=8,
            wraplength=680,
            justify="left",
            anchor="w",
            padx=12,
            pady=8,
        )
        self._usb_banner.grid(row=2, column=0, padx=16, pady=(0, 14), sticky="ew")
        self._usb_banner.grid_remove()

        # --- Card B: Firmware Source ---
        card_fw = ctk.CTkFrame(page, fg_color=CARD_BG, corner_radius=12)
        card_fw.grid(row=3, column=0, sticky="ew", pady=(0, 14))
        card_fw.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card_fw, text="B. Stock Firmware Library (libinfomodule.so)", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, padx=16, pady=(14, 4), sticky="w"
        )
        ctk.CTkLabel(
            card_fw,
            text="Because copyrighted firmware files aren't bundled in this app, choose either Option 1 (firmware zip/image) "
            "or Option 2 (read safely from your car first). If you only want Wi-Fi 40 MHz or Diagnostics, you can skip straight to Step 2.",
            font=ctk.CTkFont(size=12),
            text_color=MUTED_FG,
            wraplength=680,
            justify="left",
        ).grid(row=1, column=0, padx=16, pady=(0, 10), sticky="w")

        self._fw_status_label = ctk.CTkLabel(
            card_fw,
            text="Status: Not loaded yet (needed for Emperor Restore, Day/Night ILL, and UI Strings)",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=WARN_BG,
            text_color=WARN_FG,
            corner_radius=8,
            wraplength=680,
            justify="left",
            anchor="w",
            padx=12,
            pady=8,
        )
        self._fw_status_label.grid(row=2, column=0, padx=16, pady=(0, 12), sticky="ew")

        # Option 1 row
        opt1 = ctk.CTkFrame(card_fw, fg_color=("white", "#262b36"), corner_radius=10)
        opt1.grid(row=3, column=0, padx=16, pady=(0, 10), sticky="ew")
        opt1.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(opt1, text="Option 1: I have the firmware file on my PC", font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, padx=14, pady=(10, 2), sticky="w"
        )
        ctk.CTkLabel(
            opt1,
            text="Accepts X3_20250718.zip, ISPBOOOT.BIN, spapp.bin, or stock libinfomodule.so. Extracted automatically — no extra tools needed.",
            font=ctk.CTkFont(size=12),
            text_color=MUTED_FG,
            wraplength=500,
            justify="left",
        ).grid(row=1, column=0, padx=14, pady=(0, 10), sticky="w")
        ctk.CTkButton(opt1, text="Choose Firmware File...", width=180, height=36, command=self._choose_firmware_file).grid(
            row=0, column=1, rowspan=2, padx=14, pady=10
        )

        # Option 2 row
        opt2 = ctk.CTkFrame(card_fw, fg_color=("white", "#262b36"), corner_radius=10)
        opt2.grid(row=4, column=0, padx=16, pady=(0, 14), sticky="ew")
        opt2.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            opt2,
            text="Option 2: I don't have the firmware file — read it safely from my car (also collects logs)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=14, pady=(10, 2), sticky="w")
        ctk.CTkLabel(
            opt2,
            text="Prepares a 100% read-only USB stick. Plug it into your Eanon X3 for ~60 seconds, then plug it back into your PC. "
            "This app will auto-detect your stock library and diagnostic logs!",
            font=ctk.CTkFont(size=12),
            text_color=MUTED_FG,
            wraplength=490,
            justify="left",
        ).grid(row=1, column=0, padx=14, pady=(0, 6), sticky="w")
        ctk.CTkCheckBox(
            opt2,
            text="Include deep hardware & 30s input capture (developer use only; leave off for reboot / reset reports)",
            variable=self.var_read_extras,
            font=ctk.CTkFont(size=11),
        ).grid(row=2, column=0, columnspan=2, padx=14, pady=(0, 10), sticky="w")
        ctk.CTkButton(
            opt2,
            text="Prepare Read-Only\nCar Stick",
            width=180,
            height=44,
            command=self._prepare_read_only_stick,
        ).grid(row=0, column=1, rowspan=2, padx=14, pady=10)

        # Next button
        btn_row = ctk.CTkFrame(page, fg_color="transparent")
        btn_row.grid(row=4, column=0, sticky="ew", pady=(4, 12))
        ctk.CTkButton(
            btn_row,
            text="Continue to Step 2: Select Patches  →",
            height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            command=lambda: self._show_page("patches"),
        ).pack(side="right")

        return page

    # ---------------------------------------------------------- Page 2: Patches
    def _build_patches_page(self, parent: ctk.CTkFrame) -> ctk.CTkScrollableFrame:
        page = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(page, text="Step 2: Choose Your Patches", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        ctk.CTkLabel(
            page,
            text="Toggle the fixes you want installed on your Eanon X3. Every patch is reversible with Step 4 (Undo / Restore).",
            font=ctk.CTkFont(size=13),
            text_color=MUTED_FG,
            wraplength=700,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(0, 14))

        def add_patch_card(
            row: int,
            title: str,
            badge: str,
            desc: str,
            var: ctk.BooleanVar,
            cmd: Optional[Callable[[], None]] = None,
        ) -> None:
            card = ctk.CTkFrame(page, fg_color=CARD_BG, corner_radius=12)
            card.grid(row=row, column=0, sticky="ew", pady=(0, 10))
            card.grid_columnconfigure(0, weight=1)

            hdr = ctk.CTkFrame(card, fg_color="transparent")
            hdr.grid(row=0, column=0, padx=16, pady=(12, 2), sticky="w")
            ctk.CTkLabel(hdr, text=title, font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
            ctk.CTkLabel(
                hdr,
                text=f"  {badge}  ",
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color=SUCCESS_BG,
                text_color=SUCCESS_FG,
                corner_radius=6,
            ).pack(side="left", padx=(10, 0))

            ctk.CTkLabel(
                card,
                text=desc,
                font=ctk.CTkFont(size=12),
                text_color=MUTED_FG,
                wraplength=580,
                justify="left",
            ).grid(row=1, column=0, padx=16, pady=(2, 12), sticky="w")

            sw = ctk.CTkSwitch(card, text="", variable=var, width=52, command=cmd)
            sw.grid(row=0, column=1, rowspan=2, padx=18, pady=12)

        add_patch_card(
            2,
            "Emperor / DSP Restore Fix (Patch B)",
            "Recommended • Needs Firmware",
            "Stops the head unit from discarding saved Emperor / DSP sound settings after a cold boot or MCU memory reset "
            "(libinfomodule.so 1-byte fix, loaded via /media/flash/nvm override).",
            self.var_patch_emperor,
            cmd=self._on_emperor_toggled,
        )
        add_patch_card(
            3,
            "Day / Night Follows Headlights at Boot (Patch C)",
            "Tested • Requires Patch B",
            "Fixes the screen getting stuck in dim Night mode at startup after being parked with headlights on. "
            "Lets the live headlight (ILL) signal decide day/night instead of restoring a stale night state.",
            self.var_patch_daynight,
            cmd=self._on_daynight_toggled,
        )
        add_patch_card(
            4,
            "UI Language & Translation Fixes",
            "20 Languages • Needs Firmware",
            "Fixes mistranslated, truncated, and awkward menu strings across 20 built-in UI languages (including English).",
            self.var_patch_strings,
        )
        add_patch_card(
            5,
            "Wi-Fi 40 MHz for Wireless Android Auto / CarPlay (wifi40)",
            "Verified • No Firmware Needed",
            "Upgrades the head unit's 5 GHz Wi-Fi AP from 20 MHz to 40 MHz (HT40) for smoother wireless projection. "
            "Includes an automatic safety self-undo if hostapd fails to start on the next cold boot.",
            self.var_patch_wifi40,
        )

        # Diagnostics card (always included)
        diag_card = ctk.CTkFrame(page, fg_color=CARD_BG, corner_radius=12)
        diag_card.grid(row=6, column=0, sticky="ew", pady=(0, 14))
        diag_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            diag_card,
            text="Diagnostics & Troubleshooting Logs (Always Included)",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, padx=16, pady=(12, 2), sticky="w")
        ctk.CTkLabel(
            diag_card,
            text="Every USB plug-in automatically records redacted logs into diag/ on the stick so you can verify patches "
            "or share logs if your unit reboots or resets.",
            font=ctk.CTkFont(size=12),
            text_color=MUTED_FG,
            wraplength=680,
            justify="left",
        ).grid(row=1, column=0, padx=16, pady=(2, 6), sticky="w")
        ctk.CTkCheckBox(
            diag_card,
            text="Enable deep hardware & 30-second input capture (extras.flag; developer use only, adds ~30s wait in car)",
            variable=self.var_diag_extras,
            font=ctk.CTkFont(size=12),
        ).grid(row=2, column=0, padx=16, pady=(2, 12), sticky="w")

        # Action buttons
        act_row = ctk.CTkFrame(page, fg_color="transparent")
        act_row.grid(row=7, column=0, sticky="ew", pady=(4, 14))
        ctk.CTkButton(
            act_row,
            text="←  Back to Step 1",
            height=42,
            fg_color="transparent",
            border_width=1,
            text_color=("gray15", "gray90"),
            command=lambda: self._show_page("setup"),
        ).pack(side="left")

        ctk.CTkButton(
            act_row,
            text="Build & Write to USB Stick",
            height=44,
            width=240,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            command=self._build_and_write_usb,
        ).pack(side="right")

        return page

    # --------------------------------------------------- Page 3: Guide & Status
    def _build_status_page(self, parent: ctk.CTkFrame) -> ctk.CTkScrollableFrame:
        page = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(page, text="Step 3: Car Instructions & Stick Status", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        ctk.CTkLabel(
            page,
            text="Follow the steps below in your car, then bring the USB stick back to inspect results or export safe logs.",
            font=ctk.CTkFont(size=13),
            text_color=MUTED_FG,
            wraplength=700,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        # Instructions card
        self._guide_card = ctk.CTkFrame(page, fg_color=CARD_BG, corner_radius=12)
        self._guide_card.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        self._guide_card.grid_columnconfigure(0, weight=1)

        self._guide_title = ctk.CTkLabel(
            self._guide_card,
            text="In-Car Instructions",
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self._guide_title.grid(row=0, column=0, padx=16, pady=(12, 4), sticky="w")

        self._guide_text = ctk.CTkLabel(
            self._guide_card,
            text=(
                "1. Prepare your USB stick in Step 1 (Read-Only) or Step 2 (Install Patches).\n"
                "2. Safely eject the USB stick and plug it into your Eanon X3 while booted on the home screen.\n"
                "3. Wait 60 seconds (runs silently in the background), unplug, and perform a full cold car off/on.\n"
                "4. Plug the stick back into the X3 for 30 seconds to verify, then bring it back here and click 'Inspect USB Stick Now'."
            ),
            font=ctk.CTkFont(size=12),
            wraplength=690,
            justify="left",
            anchor="w",
        )
        self._guide_text.grid(row=1, column=0, padx=16, pady=(0, 14), sticky="w")

        # Status Inspector card
        insp_card = ctk.CTkFrame(page, fg_color=CARD_BG, corner_radius=12)
        insp_card.grid(row=3, column=0, sticky="ew", pady=(0, 14))
        insp_card.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(insp_card, fg_color="transparent")
        hdr.grid(row=0, column=0, padx=16, pady=(12, 8), sticky="ew")
        ctk.CTkLabel(hdr, text="USB Stick Status & Diagnostics", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")

        ctk.CTkButton(
            hdr,
            text="Export Safe Diagnostics (.zip)",
            width=210,
            height=34,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            command=self._export_diagnostics,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            hdr,
            text="Inspect USB Stick Now",
            width=165,
            height=34,
            command=self._inspect_current_stick,
        ).pack(side="right")

        self._status_summary_box = ctk.CTkTextbox(insp_card, height=130, font=ctk.CTkFont(size=12))
        self._status_summary_box.grid(row=1, column=0, padx=16, pady=(0, 8), sticky="ew")
        self._set_textbox(self._status_summary_box, "Select a USB stick in Step 1, then click 'Inspect USB Stick Now'.")

        self._retry_wifi_btn = ctk.CTkButton(
            insp_card,
            text="Clear Wi-Fi 40 MHz auto_undone flag (to retry)",
            fg_color=WARN_BG,
            text_color=WARN_FG,
            hover_color=("#fde68a", "#523b0e"),
            command=self._clear_wifi_auto_undo,
        )
        self._retry_wifi_btn.grid(row=2, column=0, padx=16, pady=(0, 8), sticky="w")
        self._retry_wifi_btn.grid_remove()

        ctk.CTkLabel(
            insp_card,
            text="Recent Car Log Output (x3patch / wifi40 / diag):",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=MUTED_FG,
        ).grid(row=3, column=0, padx=16, pady=(4, 4), sticky="w")

        self._raw_log_box = ctk.CTkTextbox(insp_card, height=180, font=ctk.CTkFont(family="Consolas" if sys.platform == "win32" else "Menlo", size=11))
        self._raw_log_box.grid(row=4, column=0, padx=16, pady=(0, 14), sticky="ew")
        self._set_textbox(self._raw_log_box, "(No logs read yet.)")

        return page

    # ------------------------------------------------------------ Page 4: Undo
    def _build_undo_page(self, parent: ctk.CTkFrame) -> ctk.CTkScrollableFrame:
        page = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(page, text="Step 4: Undo Patches & Restore Stock", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        ctk.CTkLabel(
            page,
            text="Every patch installed by this tool lives in /media/flash/nvm and can be cleanly removed at any time.",
            font=ctk.CTkFont(size=13),
            text_color=MUTED_FG,
            wraplength=700,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(0, 14))

        card = ctk.CTkFrame(page, fg_color=CARD_BG, corner_radius=12)
        card.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="Choose what to revert on your Eanon X3:", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, padx=16, pady=(14, 10), sticky="w"
        )

        ctk.CTkCheckBox(
            card,
            text="Uninstall Library Patches (Emperor Restore, Day/Night ILL, UI Strings)\n"
            "Deletes /media/flash/nvm/libinfomodule.so so the unit loads the untouched stock library.",
            variable=self.var_undo_lib,
            font=ctk.CTkFont(size=13),
        ).grid(row=1, column=0, padx=16, pady=(4, 12), sticky="w")

        ctk.CTkCheckBox(
            card,
            text="Undo Wi-Fi 40 MHz (wifi40)\n"
            "Removes the ht_capab line from /media/flash/nvm/softap.conf to return to stock 20 MHz.",
            variable=self.var_undo_wifi40,
            font=ctk.CTkFont(size=13),
        ).grid(row=2, column=0, padx=16, pady=(4, 14), sticky="w")

        info = ctk.CTkFrame(card, fg_color=SUCCESS_BG, corner_radius=8)
        info.grid(row=3, column=0, padx=16, pady=(0, 14), sticky="ew")
        ctk.CTkLabel(
            info,
            text="Emergency recovery note: The USB autorun hook runs from Linux mdev independently of the Launcher UI. "
            "Even if the screen fails to start, plugging in an Uninstall stick removes the override cleanly.",
            font=ctk.CTkFont(size=12),
            text_color=SUCCESS_FG,
            wraplength=660,
            justify="left",
        ).pack(padx=12, pady=10, anchor="w")

        ctk.CTkButton(
            card,
            text="Prepare Uninstall / Recovery USB Stick",
            height=44,
            width=280,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=("#b91c1c", "#dc2626"),
            hover_color=("#991b1b", "#b91c1b"),
            command=self._prepare_uninstall_stick,
        ).grid(row=4, column=0, padx=16, pady=(0, 16), sticky="e")

        return page

    # ------------------------------------------------------------ Helpers
    @staticmethod
    def _set_textbox(box: ctk.CTkTextbox, text: str) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text)
        box.configure(state="disabled")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        if message:
            self._status_label.configure(text=message)
        if busy:
            self._progress.grid()
            self._progress.start()
        else:
            self._progress.stop()
            self._progress.grid_remove()

    def _run_bg(self, label: str, fn: Callable[[], object], on_done: Callable[[object], None]) -> None:
        if self._busy:
            return
        self._set_busy(True, label)

        def worker() -> None:
            try:
                res = fn()
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda e=exc: self._on_bg_error(e))
                return
            self.after(0, lambda r=res: self._finish_bg(r, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_bg(self, result: object, on_done: Callable[[object], None]) -> None:
        self._set_busy(False)
        on_done(result)

    def _on_bg_error(self, exc: Exception) -> None:
        self._set_busy(False, f"Error: {exc}")
        messagebox.showerror("Eanon X3 Patcher", str(exc))

    # ------------------------------------------------------------ Actions
    def _refresh_drives(self) -> None:
        self._drives = list_usb_drives()
        if self._drives:
            labels = [d.display_name for d in self._drives]
            self._drive_menu.configure(values=labels)
            self.var_drive_choice.set(labels[0])
            self._select_drive_obj(self._drives[0])
        else:
            self._drive_menu.configure(values=["No removable USB drives detected (click Refresh or Browse)"])
            self.var_drive_choice.set("No removable USB drives detected (click Refresh or Browse)")
            self._selected_drive = None
            self._usb_banner.configure(
                text="Plug in a FAT32 USB flash drive and click Refresh, or use 'Browse Folder...' to choose a folder.",
                fg_color=WARN_BG,
                text_color=WARN_FG,
            )
            self._usb_banner.grid()

    def _on_drive_selected(self, choice: str) -> None:
        for d in self._drives:
            if d.display_name == choice:
                self._select_drive_obj(d)
                return

    def _browse_usb_folder(self) -> None:
        folder = filedialog.askdirectory(title="Choose USB Drive or Target Folder")
        if not folder:
            return
        d = inspect_target_folder(folder)
        self._drives.append(d)
        self._drive_menu.configure(values=[x.display_name for x in self._drives])
        self.var_drive_choice.set(d.display_name)
        self._select_drive_obj(d)

    def _select_drive_obj(self, drive: DriveInfo) -> None:
        self._selected_drive = drive
        if drive.warning:
            self._usb_banner.configure(text=drive.warning, fg_color=ERR_BG, text_color=ERR_FG)
        elif drive.fat32_ok:
            free_mb = drive.free_bytes / (1024 * 1024)
            self._usb_banner.configure(
                text=f"FAT32 USB stick ready: {drive.path}  ({free_mb:,.0f} MB free)",
                fg_color=SUCCESS_BG,
                text_color=SUCCESS_FG,
            )
        else:
            self._usb_banner.configure(
                text=f"Target folder selected: {drive.path} (Make sure your car USB stick is formatted as FAT32 / MBR).",
                fg_color=WARN_BG,
                text_color=WARN_FG,
            )
        self._usb_banner.grid()
        self._inspect_current_stick(quiet=True)

    def _require_usb_ready(self) -> Optional[Path]:
        if self._selected_drive is None:
            messagebox.showwarning("Select a USB Stick", "Please select your USB drive in Step 1 first.")
            self._show_page("setup")
            return None
        if self._selected_drive.fat32_ok is False:
            ok = messagebox.askyesno(
                "Drive is not FAT32",
                f"{self._selected_drive.warning}\n\nDo you still want to write files to {self._selected_drive.path}?",
            )
            if not ok:
                return None
        return Path(self._selected_drive.path)

    def _choose_firmware_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select X3_20250718.zip, ISPBOOOT.BIN, spapp.bin, or stock libinfomodule.so",
            filetypes=[
                ("Eanon X3 Firmware / Library", "*.zip *.BIN *.bin *.so"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        def work() -> StockLibraryResult:
            return load_stock_libinfomodule(
                path,
                progress=lambda msg: self.after(0, lambda m=msg: self._status_label.configure(text=m)),
            )

        self._run_bg("Inspecting firmware file...", work, self._on_firmware_loaded)

    def _on_firmware_loaded(self, res: object) -> None:
        assert isinstance(res, StockLibraryResult)
        if res.matches_x3_20250718:
            self._stock_result = res
            self._fw_status_label.configure(
                text=f"Stock library READY ({res.source_kind.upper()} verified — MD5 {res.md5})",
                fg_color=SUCCESS_BG,
                text_color=SUCCESS_FG,
            )
            self._status_label.configure(text="Stock X3_20250718 libinfomodule.so verified and loaded!")
        else:
            self._stock_result = None
            self._fw_status_label.configure(
                text=(
                    f"MISMATCH: extracted libinfomodule.so MD5 is {res.md5}, expected {STOCK_LIBINFOMODULE_MD5} (X3_20250718). "
                    "Refusing to patch a different firmware build."
                ),
                fg_color=ERR_BG,
                text_color=ERR_FG,
            )
            messagebox.showerror(
                "Unsupported Firmware Version",
                f"The extracted libinfomodule.so has MD5:\n{res.md5}\n\n"
                f"Expected X3_20250718 stock MD5:\n{STOCK_LIBINFOMODULE_MD5}\n\n"
                "To keep your head unit safe, this app will not patch a different firmware build. "
                "You can still use Wi-Fi 40 MHz or prepare a read-only Diagnostics Stick to share logs on GitHub.",
            )

    def _on_emperor_toggled(self) -> None:
        if not self.var_patch_emperor.get() and self.var_patch_daynight.get():
            self.var_patch_daynight.set(False)
            self._status_label.configure(text="Day/Night ILL (Patch C) was turned off because it requires Emperor Restore (Patch B).")

    def _on_daynight_toggled(self) -> None:
        if self.var_patch_daynight.get() and not self.var_patch_emperor.get():
            self.var_patch_emperor.set(True)
            self._status_label.configure(text="Emperor Restore (Patch B) was enabled automatically because Day/Night ILL depends on it.")

    def _prepare_read_only_stick(self) -> None:
        usb = self._require_usb_ready()
        if usb is None:
            return
        extras = self.var_read_extras.get()

        def work() -> WriteResult:
            return prepare_read_from_car_stick(
                usb,
                diag_extras=extras,
                progress=lambda msg: self.after(0, lambda m=msg: self._status_label.configure(text=m)),
            )

        self._run_bg("Preparing read-only car stick...", work, self._on_write_complete)

    def _build_and_write_usb(self) -> None:
        usb = self._require_usb_ready()
        if usb is None:
            return

        sel = PatchSelection(
            emperor_restore=self.var_patch_emperor.get(),
            daynight_ill=self.var_patch_daynight.get(),
            ui_strings=self.var_patch_strings.get(),
            wifi40=self.var_patch_wifi40.get(),
            diag_extras=self.var_diag_extras.get(),
        )
        if not sel.needs_libinfomodule and not sel.wifi40:
            # Diagnostics-only stick!
            def work_diag() -> WriteResult:
                return prepare_read_from_car_stick(
                    usb,
                    diag_extras=sel.diag_extras,
                    progress=lambda msg: self.after(0, lambda m=msg: self._status_label.configure(text=m)),
                )

            self._run_bg("Preparing diagnostics-only USB stick...", work_diag, self._on_write_complete)
            return

        if sel.needs_libinfomodule and self._stock_result is None:
            messagebox.showwarning(
                "Stock Firmware Needed",
                "The selected library patches need your stock X3_20250718 libinfomodule.so.\n\n"
                "Go to Step 1 and either:\n"
                "  • Choose X3_20250718.zip / ISPBOOOT.BIN, OR\n"
                "  • Click 'Prepare Read-Only Car Stick' to dump it safely from your car first.",
            )
            self._show_page("setup")
            return

        stock_bytes = self._stock_result.data if self._stock_result else None

        def work() -> WriteResult:
            return prepare_install_stick(
                usb,
                sel,
                stock_bytes=stock_bytes,
                progress=lambda msg: self.after(0, lambda m=msg: self._status_label.configure(text=m)),
            )

        self._run_bg("Building patches & writing USB stick...", work, self._on_write_complete)

    def _prepare_uninstall_stick(self) -> None:
        usb = self._require_usb_ready()
        if usb is None:
            return
        u_lib = self.var_undo_lib.get()
        u_wifi = self.var_undo_wifi40.get()
        if not u_lib and not u_wifi:
            messagebox.showwarning("Nothing Selected", "Select at least one item to uninstall / undo.")
            return

        def work() -> WriteResult:
            return prepare_uninstall_stick(
                usb,
                uninstall_lib=u_lib,
                undo_wifi40=u_wifi,
                progress=lambda msg: self.after(0, lambda m=msg: self._status_label.configure(text=m)),
            )

        self._run_bg("Writing uninstall / recovery USB stick...", work, self._on_write_complete)

    def _on_write_complete(self, res: object) -> None:
        assert isinstance(res, WriteResult)
        self._last_write = res
        titles = {
            "install": "USB Stick Ready — Install Patches",
            "read_from_car": "USB Stick Ready — Read From Car (Read-Only)",
            "uninstall": "USB Stick Ready — Uninstall / Restore Stock",
        }
        self._guide_title.configure(text=titles.get(res.action, "USB Stick Ready"))
        body = ["What was written to your USB stick:"]
        body.extend(f"  • {s}" for s in res.summary_lines)
        if res.payload:
            body.append(f"  • Verified payload MD5: {res.payload.md5}")
        body.append("")
        body.append("Steps to follow in your car:")
        for i, step in enumerate(res.car_steps, 1):
            body.append(f"  {i}. {step}")
        self._guide_text.configure(text="\n".join(body))
        self._status_label.configure(text=f"USB stick prepared successfully at {res.usb_root}")
        self._show_page("status")
        messagebox.showinfo(
            "USB Stick Ready!",
            f"Your USB stick at {res.usb_root} is ready.\n\n"
            "Safely eject the USB stick from your computer and follow the numbered steps in Step 3.",
        )

    def _inspect_current_stick(self, quiet: bool = False) -> None:
        if self._selected_drive is None:
            if not quiet:
                messagebox.showwarning("No USB Stick Selected", "Select your USB stick in Step 1 first.")
            return
        usb = Path(self._selected_drive.path)

        def work() -> tuple[StickStatus, Optional[StockLibraryResult]]:
            st = inspect_usb_stick(usb)
            auto_stock: Optional[StockLibraryResult] = None
            if st.stock_lib_path and st.stock_matches_x3_20250718 and self._stock_result is None:
                try:
                    auto_stock = load_stock_libinfomodule(st.stock_lib_path)
                except FirmwareError:
                    auto_stock = None
            return st, auto_stock

        self._run_bg(
            "Inspecting USB stick...",
            work,
            lambda pair: self._on_inspect_done(pair, quiet=quiet),
        )

    def _on_inspect_done(self, pair: object, quiet: bool) -> None:
        st, auto_stock = pair  # type: ignore[misc]
        assert isinstance(st, StickStatus)
        self._stick_status = st

        if auto_stock is not None and auto_stock.matches_x3_20250718:
            self._stock_result = auto_stock
            self._fw_status_label.configure(
                text=f"Auto-loaded stock library from USB stick! (X3_20250718 verified — MD5 {auto_stock.md5})",
                fg_color=SUCCESS_BG,
                text_color=SUCCESS_FG,
            )
        elif st.stock_lib_path and not st.stock_matches_x3_20250718 and self._stock_result is None:
            self._fw_status_label.configure(
                text=(
                    f"Stock library found on stick, but MD5 ({st.stock_lib_md5}) does NOT match X3_20250718. "
                    "Export diagnostics in Step 3 so we can check your firmware version."
                ),
                fg_color=ERR_BG,
                text_color=ERR_FG,
            )

        self._set_textbox(self._status_summary_box, "\n".join(st.human_summary()))

        if st.wifi40_auto_undone:
            self._retry_wifi_btn.grid()
        else:
            self._retry_wifi_btn.grid_remove()

        log_sections: list[str] = []
        if st.x3patch_log_tail:
            log_sections.append("=== x3patch/log.txt (tail) ===\n" + st.x3patch_log_tail)
        if st.wifi40_log_tail:
            log_sections.append("=== wifi40/log.txt (tail) ===\n" + st.wifi40_log_tail)
        if st.diag_runs_tail:
            log_sections.append("=== diag/runs.txt (tail) ===\n" + st.diag_runs_tail)
        self._set_textbox(
            self._raw_log_box,
            "\n\n".join(log_sections) if log_sections else "(No car logs found on this USB stick yet.)",
        )
        if not quiet:
            self._status_label.configure(text=f"Inspected {st.usb_root}: {st.diag_snapshot_count} diagnostic snapshot(s).")

    def _clear_wifi_auto_undo(self) -> None:
        if self._selected_drive is None:
            return
        if clear_wifi40_auto_undone(self._selected_drive.path):
            messagebox.showinfo(
                "Cleared",
                "Removed wifi40/auto_undone. You can now re-enable Wi-Fi 40 MHz in Step 2 if you want to retry.",
            )
            self._inspect_current_stick(quiet=True)

    def _export_diagnostics(self) -> None:
        if self._selected_drive is None:
            messagebox.showwarning("No USB Stick Selected", "Select your USB stick in Step 1 first.")
            return
        usb = Path(self._selected_drive.path)
        default_dir = Path.home() / "Desktop"
        if not default_dir.is_dir():
            default_dir = Path.home()
        out_path = filedialog.asksaveasfilename(
            title="Save Safe Diagnostics ZIP",
            initialdir=str(default_dir),
            initialfile="eanon-x3-diagnostics.zip",
            defaultextension=".zip",
            filetypes=[("ZIP archive", "*.zip")],
        )
        if not out_path:
            return

        def work() -> tuple[Path, list[str]]:
            return export_safe_diagnostics_zip(usb, out_path)

        def done(res: object) -> None:
            zpath, files = res  # type: ignore[misc]
            self._status_label.configure(text=f"Exported {len(files)} redacted log file(s) to {zpath.name}")
            open_now = messagebox.askyesno(
                "Diagnostics Exported",
                f"Saved {len(files)} redacted diagnostic file(s) to:\n{zpath}\n\n"
                "Raw settings backups (nvm/*.ini), Wi-Fi passphrases, device names, and firmware binaries "
                "were excluded/redacted.\n\nOpen the folder so you can attach this zip to a GitHub issue?",
            )
            if open_now:
                _open_folder_in_file_manager(zpath.parent)

        self._run_bg("Packing privacy-redacted diagnostics...", work, done)


def launch_gui() -> int:
    app = X3PatcherApp()
    app.mainloop()
    return 0
