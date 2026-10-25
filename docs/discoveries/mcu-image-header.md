# MCU image header (JieLi JL_UDFIR)
**Status:** header fully decoded and verified; payload still encrypted. FINDINGS 3.

- `fw/mcu/mcu8368u.bin` (397,824 bytes, md5 `63157019…`) is a JieLi upgrade container: 0x200-byte header plus a
  0x61000-byte payload for chip image `JL_690X.BIN` (AC690x / BR17 family; SDK banner `br17_v2-0-9`).
- Header fields (big-endian) and both CRCs are listed in FINDINGS 3.2. `python3 -I tools/jl_udfir.py <file>`
  recomputes them: header CRC `0xe3df` and payload CRC `0xcff6` both match. *parsed*
- CRC16 here is poly 0x1021, init 0, no reflection (XMODEM). It differs from the SoC to MCU UART CRC
  (poly 0x8432, `docs/MCU_PROTOCOL.md`).
- The payload is high-entropy (about 7.99), so it is encrypted or scrambled. The CRC covers the ciphertext, so
  the image can be validated without the key. Only a small plaintext table of SDK library banners near 0xd400
  is readable.
- What this gives us: a way to confirm an MCU image is intact and to identify its chip family. What it does not
  give us: any view of the MCU code, so why the MCU reports RstMemory stays unknown.
- Safety: nothing here modifies or flashes the MCU. A changed payload would need a valid CRC and would have to be
  re-encrypted with the unknown scheme, so MCU changes are out of scope. `send_mcu_upgrade` (0x75) must never be sent.
