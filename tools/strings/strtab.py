#!/usr/bin/env python3
# UI string tables in libinfomodule.so (X3_20250718). See docs/FINDINGS.md section 11.
#
#   strtab.py extract <libinfomodule.so> <out.json>
#   strtab.py build   <libinfomodule.so> <overrides.json> <out.so>
#   strtab.py check   <libinfomodule.so>          (re-extract, sanity checks)
#
# GetSystemString(id) returns tables[curLang][id] (UTF-8 const char*). tables[] has 21
# slots at 0x41f30280, each a 417-entry pointer array. The lib is prelinked with RELA
# relocs, so a slot's runtime value is base_delta + the reloc addend; build rewrites
# both the addend and the in-place word.
#
# build appends every changed string to a blob placed after .bss: the RW PT_LOAD gets
# the bss as file-backed zeros plus the blob, and the trailing non-alloc sections and
# section header table move down. Nothing else in the image changes.
#
# overrides.json: {"en": {"215": "new text", ...}, "fr": {...}}; key "*" applies to
# every language that has a table (language names in the menu). A language whose
# table is NULL (nl) can't be added this way (it would need a new 417-entry table).
import json, struct, sys, hashlib

LANGS = 'en zh-CN zh-TW ja nl fr it ko pt ru es th de ar id tr he el pl cs ro'.split()
TABLES = 0x41f30280        # SetSystemLanguage/GetSystemString literal (decoded in FINDINGS 11)
NSTR = 417
KNOWN = {'f00b52a739bb333307ee9a841bd02369': 'stock',
         'fae35255cfc28d530fa100af5cab4c4d': 'patch B'}
R_ARM_RELATIVE = 23
LRM = '\u200e'.encode()


class Elf:
    def __init__(self, data):
        self.d = bytearray(data)
        d = self.d
        assert d[:4] == b'\x7fELF' and d[4] == 1 and d[5] == 1
        self.phoff, self.shoff = struct.unpack_from('<II', d, 0x1c)
        self.phnum, = struct.unpack_from('<H', d, 0x2c)
        self.shnum, self.shstrndx = struct.unpack_from('<HH', d, 0x30)
        self.ph = [list(struct.unpack_from('<8I', d, self.phoff + 32 * i)) for i in range(self.phnum)]
        self.sh = [list(struct.unpack_from('<10I', d, self.shoff + 40 * i)) for i in range(self.shnum)]
        so = self.sh[self.shstrndx][4]
        self.shname = [bytes(d[so + s[0]:d.index(b'\0', so + s[0])]).decode() for s in self.sh]

    def loads(self):
        return [p for p in self.ph if p[0] == 1]

    def v2o(self, v):
        for p in self.loads():
            if p[2] <= v < p[2] + p[4]:
                return v - p[2] + p[1]
        raise ValueError('vaddr %#x not file-backed' % v)

    def u32(self, v):
        return struct.unpack_from('<I', self.d, self.v2o(v))[0]

    def put32(self, v, x):
        struct.pack_into('<I', self.d, self.v2o(v), x)

    def cstr(self, v):
        o = self.v2o(v)
        return bytes(self.d[o:self.d.index(b'\0', o)])

    def section(self, name):
        return self.sh[self.shname.index(name)]


def tables(elf):
    out = []
    for li in range(len(LANGS)):
        t = elf.u32(TABLES + 4 * li)
        out.append([TABLES + 4 * li, t])
    return out


def extract(elf):
    res = {}
    for li, (_, t) in enumerate(tables(elf)):
        res[LANGS[li]] = None if t == 0 else [elf.cstr(elf.u32(t + 4 * i)).decode('utf-8') for i in range(NSTR)]
    return res


def rela_index(elf):
    s = elf.section('.rel.dyn')
    assert s[9] == 12, 'expected prelink RELA entries (entsize 12), got %d' % s[9]
    idx = {}
    for o in range(s[4], s[4] + s[5], 12):
        off, info, add = struct.unpack_from('<IIi', elf.d, o)
        if info & 0xff == R_ARM_RELATIVE:
            idx[off] = o
    return idx


def expand(ov, elf):
    """-> {lang: {id: final string}}. Per-language entries win over "*". The ar/he
    tables start every string with U+200E; keep that prefix on replacements."""
    tabs = tables(elf)
    want = {}
    for lang, m in sorted(ov.items(), key=lambda kv: kv[0] != '*'):
        langs = [LANGS[li] for li, (_, t) in enumerate(tabs) if t] if lang == '*' else [lang]
        for l in langs:
            t = tabs[LANGS.index(l)][1]
            if t == 0:
                sys.exit('language %s has no table in this build' % l)
            for k, v in m.items():
                i = int(k)
                assert 0 <= i < NSTR, k
                if elf.cstr(elf.u32(t + 4 * i)).startswith(LRM) and not v.startswith('\u200e'):
                    v = '\u200e' + v
                want.setdefault(l, {})[i] = v
    return want


def build(elf, want):
    rel = rela_index(elf)
    tabs = tables(elf)
    rw = [p for p in elf.loads() if p[6] & 2]
    assert len(rw) == 1 and rw[0] is elf.loads()[-1]
    p = rw[0]
    fend = p[1] + p[4]                       # end of RW file data
    blob_va = (p[2] + p[5] + 15) & ~15       # after .bss
    blob = bytearray()
    seen = {}
    edits = []
    for lang, m in want.items():
        t = tabs[LANGS.index(lang)][1]
        for i, s in m.items():
            b = s.encode('utf-8')
            assert b'\0' not in b
            if elf.cstr(elf.u32(t + 4 * i)) == b:
                continue
            if b not in seen:
                seen[b] = blob_va + len(blob)
                blob += b + b'\0'
            edits.append((t + 4 * i, seen[b]))
    if not edits:
        sys.exit('no changes')
    # grow the RW segment: bss becomes zeros, then the blob
    grow = blob_va - p[2] - p[4] + len(blob)
    grow_al = (grow + 3) & ~3
    blob += b'\0' * (grow_al - grow)
    ins = bytes(blob_va - p[2] - p[4]) + bytes(blob)
    d = elf.d
    d[fend:fend] = ins
    # fix headers: everything at or after fend moves by len(ins)
    shift = len(ins)
    elf.shoff += shift
    struct.pack_into('<I', d, 0x20, elf.shoff)
    for q in elf.ph:
        if q is p:
            q[4] = q[5] = blob_va - p[2] + len(blob)
        elif q[1] >= fend and q[4]:
            q[1] += shift
    for i, q in enumerate(elf.ph):
        struct.pack_into('<8I', d, elf.phoff + 32 * i, *q)
    for i, s in enumerate(elf.sh):
        if s[2] & 2 == 0 and s[4] >= fend:   # non-alloc sections after the segment
            s[4] += shift
        struct.pack_into('<10I', d, elf.shoff + 40 * i, *s)
    # repoint slots: in-place word and RELA addend
    for slot, va in edits:
        assert slot in rel, 'slot %#x has no R_ARM_RELATIVE' % slot
        elf.put32(slot, va)
        struct.pack_into('<i', d, rel[slot] + 8, va)
    return len(edits), len(blob), blob_va


def check(elf):
    rel = rela_index(elf)
    for li, (_, t) in enumerate(tables(elf)):
        if not t:
            continue
        for i in range(NSTR):
            slot = t + 4 * i
            add = struct.unpack_from('<i', elf.d, rel[slot] + 8)[0] & 0xffffffff
            assert add == elf.u32(slot), (LANGS[li], i)
            elf.cstr(add).decode('utf-8')
    return True


def main():
    cmd, src = sys.argv[1], sys.argv[2]
    raw = open(src, 'rb').read()
    md5 = hashlib.md5(raw).hexdigest()
    print('input', md5, KNOWN.get(md5, 'UNKNOWN'), file=sys.stderr)
    if md5 not in KNOWN and cmd != 'check':
        sys.exit('refusing: not a known libinfomodule build')
    elf = Elf(raw)
    if cmd == 'extract':
        json.dump(extract(elf), open(sys.argv[3], 'w'), ensure_ascii=False, indent=1)
    elif cmd == 'build':
        ov = json.load(open(sys.argv[3], encoding='utf-8'))
        want = expand(ov, elf)
        n, size, va = build(elf, want)
        out = bytes(elf.d)
        e2 = Elf(out)
        check(e2)
        got = extract(e2)
        for lang, m in want.items():
            for i, s in m.items():
                assert got[lang][i] == s, (lang, i)
        open(sys.argv[4], 'wb').write(out)
        print('%d slots repointed, blob %d bytes at %#x, out md5 %s'
              % (n, size, va, hashlib.md5(out).hexdigest()))
    elif cmd == 'check':
        check(elf)
        print('ok')
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
