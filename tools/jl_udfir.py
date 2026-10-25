#!/usr/bin/env python3
# Parse and verify the JieLi "JL_UDFIR" upgrade container header (mcu8368u.bin). FINDINGS 3.2.
#
#   jl_udfir.py <mcu8368u.bin>
#
# Layout (big-endian), as verified on X3-series MCU_240723:
#   0x00  "JL_UDFIR"
#   0x08  u32 hdr_crc_len   length of the header block the header CRC covers (0x20)
#   0x0c  u32 hdr_crc       CRC16 over bytes [0x10, 0x10+hdr_crc_len)
#   0x10  u32 payload_off   0x200
#   0x14  u32 payload_len   0x61000
#   0x18  u32 payload_crc   CRC16 over [payload_off, payload_off+payload_len)
#   0x1c  u32 (0), 0x20 u32 (0)
#   0x24  char[] name       "JL_690X.BIN"
#   0x30..payload_off       zeros
# CRC16 = poly 0x1021, init 0, no reflection, no final XOR (XMODEM).
# The payload is encrypted/scrambled, so the CRC covers ciphertext: the container can be
# verified without decrypting it.
import struct, sys


def crc16(b, c=0):
    for x in b:
        c ^= x << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xffff if c & 0x8000 else (c << 1) & 0xffff
    return c


def main():
    d = open(sys.argv[1], 'rb').read()
    if d[:8] != b'JL_UDFIR':
        sys.exit('not a JL_UDFIR container')
    hl, hc, po, pl, pc = struct.unpack_from('>5I', d, 8)
    name = d[0x24:0x30].split(b'\0')[0].decode()
    print('name %s  file %#x  payload %#x+%#x  hdr_crc_len %#x' % (name, len(d), po, pl, hl))
    ok = True
    for what, want, got in (('header crc', hc, crc16(d[0x10:0x10 + hl])),
                            ('payload crc', pc, crc16(d[po:po + pl]))):
        print('%-12s stored %#06x  computed %#06x  %s' % (what, want, got, 'OK' if want == got else 'MISMATCH'))
        ok &= want == got
    ok &= po + pl == len(d)
    print('size check   %s' % ('OK (offset + length == file size)' if po + pl == len(d) else 'MISMATCH'))
    print('zeros 0x30..%#x: %s' % (po, 'yes' if not any(d[0x30:po]) else 'NO'))
    sys.exit(0 if ok else 1)


main()
