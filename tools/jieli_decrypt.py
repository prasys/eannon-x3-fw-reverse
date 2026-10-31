#!/usr/bin/env python3
# usage: python3 -I jieli_decrypt.py <mcu8368u.bin> <out_dir>
# Decrypts a JieLi JL_UDFIR upgrade image (AC690x/BR17 flash layout) into out_dir.
# Scheme and layout are from kagaimiq/jielie (misc/cipher.md, periph/sfc.md,
# datafmt/br17-fw.md), re-implemented here and checked against our image only.
# Read-only on the input; never modifies or flashes anything.
import os, struct, sys

def enc(b, key):
    # "ENC": CRC16-CCITT shift register as keystream, low byte XORed in
    o = bytearray()
    for x in b:
        o.append(x ^ (key & 0xff))
        key = ((key << 1) & 0xffff) ^ (0x1021 if key >> 15 else 0)
    return bytes(o)

def crc(b, c=0):
    for x in b:
        c ^= x << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021 if c & 0x8000 else c << 1) & 0xffff
    return c

def blocks(buf, key, base=0):
    # flash scramble: 32-byte blocks, key ^ (offset >> 2); offset is relative to the area
    return b''.join(enc(buf[i:i+32], (key ^ ((base + i) >> 2)) & 0xffff)
                    for i in range(0, len(buf), 32))

def chipkey(k):
    # 32-byte chip_key.bin block (recipe from datafmt/br17-fw.md)
    s = sum(k[:16]) & 0xff
    x = [k[i] ^ k[31 - i] for i in range(16)]
    return int(''.join('1' if v < s else '0' for v in x), 2)

def main(path, out):
    img = open(path, 'rb').read()
    d = img[0x200:]                       # flash image after the UDFIR header
    os.makedirs(out, exist_ok=True)
    hdr = b''.join(enc(d[i:i+32], 0xffff) for i in range(0, 128, 32))
    print('flash header CRC calc/stored: %#x/%#x' % (crc(hdr[2:32]), struct.unpack('<H', hdr[0:2])[0]))
    for i in range(1, 4):
        e = hdr[i*32:i*32+32]
        t, _, c, off, sz, idx = struct.unpack('<BBHIII', e[:16])
        print(' entry %-12s off %#x size %#x' % (e[16:].split(b'\0')[0].decode(), off, sz))
    ub = d[0x200:0x200 + 0xcb10]
    open(os.path.join(out, 'uboot.boot.dec'), 'wb').write(enc(ub, 0xffff))
    key = chipkey(img[0x605a0:0x605a0 + 32])
    print('chipkey %#x' % key)
    ua = blocks(d[0xd700:0xd700 + 0x52c00], key)
    open(os.path.join(out, 'user.app.dec'), 'wb').write(ua)
    # file list at user.app + 0x52a00: 32-byte header, then 32-byte entries
    for i in range(0x52a20, 0x52c00, 32):
        e = ua[i:i+32]
        if e[16] in (0, 0xff):
            break
        t, _, c, off, sz, idx = struct.unpack('<BBHIII', e[:16])
        nm = e[16:].split(b'\0')[0].decode()
        ok = crc(ua[off:off+sz]) == c
        print(' %-14s off %#x size %#x crc %s' % (nm, off, sz, 'OK' if ok else 'BAD'))
        open(os.path.join(out, nm), 'wb').write(ua[off:off+sz])

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
