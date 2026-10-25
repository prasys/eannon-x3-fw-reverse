"""Annotate a Ghidra DecompileExport with the C strings that PC-relative
expressions point to.

Ghidra renders Thumb `ldr rX,[pc,#n]; add rX,pc` string loads as
`(char *)(DAT_0000f048 + 0xee74)`: the value stored at DAT_0000f048 plus the
already-folded PC. This resolves each such expression and appends
`/* "string" */` to the line.

usage: python3 -I annotate_strings.py <elf> <ghidra_imagebase_hex> <export.c> > out.c
"""
import re
import struct
import sys

elf_path, ghidra_base, export_path = sys.argv[1], int(sys.argv[2], 16), sys.argv[3]
data = open(elf_path, 'rb').read()

phoff = struct.unpack_from('<I', data, 0x1c)[0]
phnum = struct.unpack_from('<H', data, 0x2c)[0]
segs = []
for i in range(phnum):
    p_type, p_off, p_vaddr, _, p_filesz, _, _, _ = struct.unpack_from('<8I', data, phoff + i * 32)
    if p_type == 1:
        segs.append((p_vaddr, p_off, p_filesz))
elf_base = min(v for v, _, _ in segs)
delta = ghidra_base - elf_base  # Ghidra address = ELF vaddr + delta


def offset(ghidra_addr):
    vaddr = (ghidra_addr - delta) & 0xffffffff
    for seg_vaddr, seg_off, size in segs:
        if seg_vaddr <= vaddr < seg_vaddr + size:
            return vaddr - seg_vaddr + seg_off
    return None


def cstring(ghidra_addr):
    off = offset(ghidra_addr)
    if off is None:
        return None
    end = data.find(b'\0', off, off + 200)
    if end <= off:
        return None
    raw = data[off:end]
    if not all(32 <= b < 127 or b in (9, 10, 13) for b in raw) or len(raw) < 2:
        return None
    return raw.decode('ascii').replace('\n', '\\n').replace('*/', '*\\/')


expr = re.compile(r'DAT_([0-9a-f]{8}) \+ 0x([0-9a-f]+)')
for line in open(export_path, errors='replace'):
    found = []
    for m in expr.finditer(line):
        lit = int(m.group(1), 16)
        off = offset(lit)
        if off is None or off + 4 > len(data):
            continue
        target = (struct.unpack_from('<I', data, off)[0] + int(m.group(2), 16)) & 0xffffffff
        s = cstring(target)
        if s:
            found.append(s)
    line = line.rstrip('\n')
    if found:
        line += '  /* ' + ' | '.join('"%s"' % s for s in found) + ' */'
    print(line)
