#!/usr/bin/env python3
# Build the guideline payload: stock libapis.so.1.0.0 with the reverse guide lines moved
# vertically (docs/discoveries/reverse-camera-guide-lines.md).
#
#   build_guideline.py <stock libapis.so.1.0.0> <outdir> (--up N | --down N)
#
# ParklineCtrl::updateParkLine blits the 1024x233 bitmap with movw r1,#0x16f (y) at file
# 0x50658 and movs r3,#0xe9 (h) at 0x5065c. y+h must stay <= 600 (the blit does not clip).
#   --up N    y = 0x16f-N                 (N <= 0x6f), nothing cropped
#   --down N  y = 0x16f+N, h = 0xe9-N     (N <= 0x90), bottom N rows cropped
# Writes <outdir>/libapis.so.1 (the name the loader looks for in nvm), .md5 and BUILD.txt.
import hashlib, os, sys

STOCK_MD5 = '6f02e18a787acf3ce0e5afda3b7bfec5'
Y_OFF, Y_OLD = 0x5065a, 0x6f      # low byte of movw r1,#0x16f (imm3 stays 1)
H_OFF, H_OLD = 0x5065c, 0xe9      # movs r3,#0xe9
CTX = (0x50654, bytes.fromhex('0020009640f26f11e923'))   # movs r0,#0 | str r6,[sp] | movw | movs r3


def main():
    a = sys.argv[1:]
    if len(a) != 4 or a[2] not in ('--up', '--down'):
        sys.exit(__doc__)
    stock_p, out, mode, n = a[0], a[1], a[2], int(a[3])
    d = bytearray(open(stock_p, 'rb').read())
    if hashlib.md5(d).hexdigest() != STOCK_MD5:
        sys.exit('refusing: not the stock X3_20250718 libapis.so.1.0.0')
    assert d[CTX[0]:CTX[0] + len(CTX[1])] == CTX[1], 'unexpected bytes at the patch site'
    if mode == '--up':
        if not 0 <= n <= 0x6f:
            sys.exit('--up N must be 0..111')
        d[Y_OFF] = Y_OLD - n
        y, h = 0x16f - n, 0xe9
    else:
        if not 0 <= n <= 0x90:
            sys.exit('--down N must be 0..144')
        d[Y_OFF], d[H_OFF] = Y_OLD + n, H_OLD - n
        y, h = 0x16f + n, 0xe9 - n
    assert y + h <= 600 and y < 0x800
    os.makedirs(out, exist_ok=True)
    so = os.path.join(out, 'libapis.so.1')
    open(so, 'wb').write(d)
    m = hashlib.md5(d).hexdigest()
    open(so + '.md5', 'w').write(m + '\n')
    open(os.path.join(out, 'BUILD.txt'), 'w').write(
        'input  libapis.so.1.0.0 md5 %s\npatch  %s %d: y=%d h=%d (y+h=%d)\noutput libapis.so.1 md5 %s, %d bytes\n'
        % (STOCK_MD5, mode, n, y, h, y + h, m, len(d)))
    print(m)


main()
