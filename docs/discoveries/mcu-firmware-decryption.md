# MCU firmware decryption (JieLi AC690x / BR17)
**Status:** decrypts and validates (2026-10-31). Code not yet analysed. FINDINGS 17.

**Credit:** the cipher, flash scramble and BR17 layout were documented by **kagaimiq** in
[kagaimiq/jielie](https://github.com/kagaimiq/jielie) (`misc/cipher.md`, `periph/sfc.md`, `datafmt/br17-fw.md`,
`datafmt/sdfile.md`, `cpu/pi32.md`). We re-implemented them in `tools/jieli_decrypt.py` and checked them against our image; the repo
has no `JL_UDFIR` page, so the container-to-flash step (payload at 0x200 is the flash image) is our own observation.

## Recipe (all *ran on our image*)
- `ENC(key)`: for each byte, XOR with the low byte of `key`, then advance `key` as a CRC16-CCITT (0x1021) shift register.
- Flash header (payload 0x0, 128 bytes) and `uboot.boot`: `ENC` with key 0xFFFF, restarted every 32 bytes (header) or run straight through (bootloader).
  Header CRC and bootloader bank CRCs match.
- Entries: `uboot.boot` @0x200 (0xcb10), `_____.____2` @0xd000 (0x200), `user.app` @0xd700 (0x52c00). Chip type bytes match AC690N in the repo.
- Chip key: 32-byte block at file 0x605a0 (`chip_key.bin`, ends `AC690X`); decode per `datafmt/br17-fw.md` gives **0x3496**.
- `user.app`: 32-byte blocks, each `ENC(0x3496 ^ (offset_in_area >> 2))`, offset relative to the start of `user.app` (not flash-absolute;
  flash-absolute and key 0xFFFF stay at entropy ~7.9, i.e. fail).
- File list at `user.app`+0x52a00. Four files, every CRC16 OK: `sdram.app` (0x4e8e6), `bt_cfg.bin`, `fast_run.bin`, `ring.mp3`.

## What is inside `sdram.app`
- `soundbox_sdk_v2-0-9` on `ac69_sdk_v2012_p4_opp`, build stamp "Jul 23 2024 16:17:25". BT names `AF-CAR-BT`, `AC69_HID`, `JL_BLE_BR16`.
- CRC16 table poly 0x8432 MSB-first at 0x3f5c4. `RECEIVE_CMD!`, `/jl_690x.bfu`, update sources (`uart_updata`, `usb_updata`, `bt_updata`, `ble_update`).
- Instruction set is JieLi **pi32** (`cpu/pi32.md`), so no ARM tooling works on it. *inferred from the repo docs*

## Open
- Key-light palette (cmd 0x58 index to colour), RstMemory (cmd 0x39) logic, command dispatch from `RECEIVE_CMD!` / the CRC table xrefs.
- `kagaimiq/ghidra-jieli` (linked from `links.md`) not read yet.

## Safety
Read-only. Do not re-encrypt or flash a modified image, and never send MCU cmd 0x75, without the owner's explicit go-ahead and a recovery plan.
