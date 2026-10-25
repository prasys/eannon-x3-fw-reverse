# UI string tables and the language patch
**Status:** rebuilt and verified offline; not yet seen on the unit. FINDINGS 11, 12.

- `GetSystemString(id)` returns `tables[curLang][id]`: 21 slots at 0x41f30280 in `libinfomodule.so`, each a
  417-entry pointer array. Order: en zh-CN zh-TW ja nl fr it ko pt ru es th de ar id tr he el pl cs ro.
- The library is prelinked with RELA relocs, so both the reloc addend and the in-place word are rewritten.
- `nl` has a NULL table in this build, so it cannot be translated this way (it would need a new table).
- ar/he strings start with U+200E; replacements keep that prefix.
- `tools/strings/strtab.py build` appends changed strings in a blob after .bss and repoints the slots; nothing
  else in the image changes.
- `tools/strings/tables_final.json` holds the finished 417 strings for each language; `overrides.json` holds the
  earlier hand fixes (English wording, a few menu names).
- Rebuild: `tools/build_payload.py`. Its output decodes identically to the payload installed from the stick,
  though the bytes differ (blob layout).
- Quality of the non-English text has not been reviewed on the unit. Fix wording in `tables_final.json`, rebuild.
