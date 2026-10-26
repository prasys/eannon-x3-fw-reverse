#!/usr/bin/env python3
# Build the x3patch payload: stock libinfomodule.so + patch B (Emperor restore, FINDINGS 9)
# + the finished UI string tables (FINDINGS 11/12).
#
#   build_payload.py <stock libinfomodule.so> <tables.json> <outdir> [--daynight]
#
# --daynight adds patch C (FINDINGS 15): loadSystemSettings no longer restores a saved night
# state on the RstMemory==0 path; it calls exec_dimmer_state(0xa5) so the live ILL decides.
# The code sits in the (dead with patch B) MCU-reset branch, so patch C requires patch B.
#
# tables.json is tools/strings/tables_final.json: {lang: [417 strings]}. Only strings that
# differ from stock are written as overrides (nl has no table and is skipped). Writes
# <outdir>/libinfomodule.so, .md5 and BUILD.txt, the layout usb-patch/x3patch/patch.sh expects.
import hashlib, json, os, subprocess, sys

STOCK_MD5 = 'f00b52a739bb333307ee9a841bd02369'
PATCHB_OFF, PATCHB_OLD, PATCHB_NEW = 0x41d2b, 0x80, 0xb8   # beq.w -> b.w

# patch C (day/night): all addresses are libinfomodule.so vaddrs, prelinked base 0x41eb0000 (file offset = vaddr - base)
BASE = 0x41eb0000
CAVE = 0x41ef1d2c                       # first insn after patch B's `b.w`: dead code
SITE = 0x41ef2024                       # blx systemDimmerState; blx exec_dimmer_state_only (8 bytes)
SITE_OLD = bytes.fromhex('f2f7e0eaf2f794ef')
PLT_SYSTEM_DIMMER_STATE = 0x41ee45e8
PLT_EXEC_DIMMER_STATE_ONLY = 0x41ee4f54
PLT_EXEC_DIMMER_STATE = 0x41ee54b8
CAVE_REGION_LEN = 24
HERE = os.path.dirname(os.path.abspath(__file__))
STRTAB = os.path.join(HERE, 'strings', 'strtab.py')


def thumb_call(pc, target, blx):
    # Thumb-2 BL (Thumb target) / BLX imm (ARM target, 4-aligned) at address pc
    base = (pc + 4) & ~3 if blx else pc + 4
    off = target - base
    assert -(1 << 24) <= off < (1 << 24) and off % (4 if blx else 2) == 0
    sgn = (off >> 24) & 1
    i1, i2 = (off >> 23) & 1, (off >> 22) & 1
    j1, j2 = (~(i1 ^ sgn)) & 1, (~(i2 ^ sgn)) & 1
    hw1 = 0xf000 | sgn << 10 | (off >> 12) & 0x3ff
    hw2 = (0xc000 if blx else 0xd000) | j1 << 13 | j2 << 11 | (off >> 1) & 0x7ff
    return hw1.to_bytes(2, 'little') + hw2.to_bytes(2, 'little')


def patch_daynight(d):
    """restore path: if the saved state is night, exec_dimmer_state(0xa5); else exec_dimmer_state_only as before"""
    c, o = CAVE, CAVE - BASE
    assert d[PATCHB_OFF] == PATCHB_NEW, 'patch C needs patch B'
    code = bytearray()
    code += bytes.fromhex('08b5')                                                  # push {r3, lr}
    code += thumb_call(c + len(code), PLT_SYSTEM_DIMMER_STATE, True)               # r0 = saved state 0xa5/0xa6
    code += bytes.fromhex('a628')                                                  # cmp r0, #0xa6
    code += bytes.fromhex('03d1')                                                  # bne +3 -> only
    code += bytes.fromhex('a520')                                                  # movs r0, #0xa5
    code += thumb_call(c + len(code), PLT_EXEC_DIMMER_STATE, True)                 # night saved: go to day
    code += bytes.fromhex('08bd')                                                  # pop {r3, pc}
    code += thumb_call(c + len(code), PLT_EXEC_DIMMER_STATE_ONLY, True)            # day saved: as stock
    code += bytes.fromhex('08bd')                                                  # pop {r3, pc}
    assert len(code) == CAVE_REGION_LEN, len(code)
    d[o:o + len(code)] = code
    so = SITE - BASE
    assert bytes(d[so:so + 8]) == SITE_OLD, 'call site is not the stock bytes'
    d[so:so + 8] = thumb_call(SITE, CAVE, False) + bytes.fromhex('00bf00bf')       # bl cave; nop; nop


def md5(b):
    return hashlib.md5(b).hexdigest()


def strtab(*a):
    subprocess.run([sys.executable, '-I', STRTAB, *a], check=True, stdout=subprocess.DEVNULL)


def main():
    daynight = '--daynight' in sys.argv
    stock_p, tables_p, out = [a for a in sys.argv[1:] if not a.startswith('--')][:3]
    d = bytearray(open(stock_p, 'rb').read())
    if md5(d) != STOCK_MD5:
        sys.exit('refusing: not the stock X3_20250718 libinfomodule.so')
    assert d[PATCHB_OFF] == PATCHB_OLD
    d[PATCHB_OFF] = PATCHB_NEW
    os.makedirs(out, exist_ok=True)
    tmp = os.path.join(out, '.work')
    os.makedirs(tmp, exist_ok=True)
    pb, st, ov = (os.path.join(tmp, n) for n in ('patchB.so', 'stock.json', 'ov.json'))
    open(pb, 'wb').write(d)
    strtab('extract', stock_p, st)
    stock, want = json.load(open(st)), json.load(open(tables_p, encoding='utf-8'))
    over = {}
    for lang, tab in want.items():
        if not stock.get(lang):
            continue
        ch = {str(i): s for i, s in enumerate(tab) if s != stock[lang][i]}
        if ch:
            over[lang] = ch
    json.dump(over, open(ov, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    so = os.path.join(out, 'libinfomodule.so')
    strtab('build', pb, ov, so)    # strtab only accepts known builds, so patch C goes on afterwards
    if daynight:
        e = bytearray(open(so, 'rb').read())
        patch_daynight(e)
        open(so, 'wb').write(e)
    h = md5(open(so, 'rb').read())
    open(so + '.md5', 'w').write(h + '\n')
    open(os.path.join(out, 'BUILD.txt'), 'w').write(
        'input   libinfomodule.so md5 %s\npatches emperor-restore%s\nstrings %s (%d languages)\n'
        'output  libinfomodule.so md5 %s, %d bytes\n' % (STOCK_MD5, ' daynight-ill' if daynight else '', os.path.basename(tables_p), len(over), h, os.path.getsize(so)))
    for n in os.listdir(tmp):
        os.remove(os.path.join(tmp, n))
    os.rmdir(tmp)
    print(h)


if __name__ == "__main__":
    main()
