#!/usr/bin/env python3
"""Build the DD-MM-YYYY libhomelauncher.so payload from the stock library.

usage: python3 -I build_payload.py <stock libhomelauncher.so> <outdir>
Changes exactly 10 bytes at 0x1846b ("yyyy-MM-dd" -> "dd-MM-yyyy") and refuses any other input.
"""
import hashlib, os, sys

STOCK_MD5 = "a29d979a7540d312bea3bf1d8f0c3891"
PATCHED_MD5 = "c08c872fe8426fc196ba0abe126c2d8e"
OFF, OLD, NEW = 0x1846B, b"yyyy-MM-dd", b"dd-MM-yyyy"

src, out = sys.argv[1], sys.argv[2]
b = bytearray(open(src, "rb").read())
assert hashlib.md5(b).hexdigest() == STOCK_MD5, "not the stock X3_20250718 library"
assert b[OFF:OFF + len(OLD)] == OLD
b[OFF:OFF + len(OLD)] = NEW
m = hashlib.md5(b).hexdigest()
assert m == PATCHED_MD5, m
os.makedirs(out, exist_ok=True)
open(os.path.join(out, "libhomelauncher.so"), "wb").write(b)
open(os.path.join(out, "libhomelauncher.so.md5"), "w").write(m + "\n")
print("wrote", out, m)
