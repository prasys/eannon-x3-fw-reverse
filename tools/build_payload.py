#!/usr/bin/env python3
# Build the x3patch payload: stock libinfomodule.so + patch B (Emperor restore, FINDINGS 9)
# + the finished UI string tables (FINDINGS 11/12).
#
#   build_payload.py <stock libinfomodule.so> <tables.json> <outdir>
#
# tables.json is tools/strings/tables_final.json: {lang: [417 strings]}. Only strings that
# differ from stock are written as overrides (nl has no table and is skipped). Writes
# <outdir>/libinfomodule.so, .md5 and BUILD.txt, the layout usb-patch/x3patch/patch.sh expects.
import hashlib, json, os, subprocess, sys

STOCK_MD5 = 'f00b52a739bb333307ee9a841bd02369'
PATCHB_OFF, PATCHB_OLD, PATCHB_NEW = 0x41d2b, 0x80, 0xb8   # beq.w -> b.w
HERE = os.path.dirname(os.path.abspath(__file__))
STRTAB = os.path.join(HERE, 'strings', 'strtab.py')


def md5(b):
    return hashlib.md5(b).hexdigest()


def strtab(*a):
    subprocess.run([sys.executable, '-I', STRTAB, *a], check=True, stdout=subprocess.DEVNULL)


def main():
    stock_p, tables_p, out = sys.argv[1:4]
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
    strtab('build', pb, ov, so)
    h = md5(open(so, 'rb').read())
    open(so + '.md5', 'w').write(h + '\n')
    open(os.path.join(out, 'BUILD.txt'), 'w').write(
        'input   libinfomodule.so md5 %s\npatches emperor-restore\nstrings %s (%d languages)\n'
        'output  libinfomodule.so md5 %s, %d bytes\n' % (STOCK_MD5, os.path.basename(tables_p), len(over), h, os.path.getsize(so)))
    for n in os.listdir(tmp):
        os.remove(os.path.join(tmp, n))
    os.rmdir(tmp)
    print(h)


main()
