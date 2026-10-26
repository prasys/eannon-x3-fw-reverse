#!/usr/bin/env python3
# Apply patch C (day/night follows live ILL, FINDINGS 15) to an existing x3patch payload that
# already has patch B, e.g. the libinfomodule.so currently installed (patch B + strings).
#
#   apply_daynight.py <in libinfomodule.so> <outdir>
#
# Writes <outdir>/libinfomodule.so and .md5 (the sidecar x3patch/patch.sh accepts). Only the
# 24 cave bytes at 0x41ef1d2c and the 8 call-site bytes at 0x41ef2024 change (file offsets
# 0x41d2c.. and 0x42024..); the script checks that the rest is untouched.
import hashlib, os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_payload as bp

src, out = sys.argv[1:3]
d = bytearray(open(src, 'rb').read())
orig = bytes(d)
bp.patch_daynight(d)
diff = [i for i in range(len(d)) if d[i] != orig[i]]
lo = bp.CAVE - bp.BASE, bp.SITE - bp.BASE
assert all(lo[0] <= i < lo[0] + bp.CAVE_REGION_LEN or lo[1] <= i < lo[1] + 8 for i in diff), 'unexpected bytes changed'
os.makedirs(out, exist_ok=True)
so = os.path.join(out, 'libinfomodule.so')
open(so, 'wb').write(d)
h = hashlib.md5(d).hexdigest()
open(so + '.md5', 'w').write(h + '\n')
print('in  md5', hashlib.md5(orig).hexdigest())
print('out md5', h, '(%d bytes changed)' % len(diff))
