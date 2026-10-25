# Firmware layout, storage and boot
**Status:** parsed from the image and a runtime snapshot. FINDINGS 2, 11.

- Image `ISPBOOOT.BIN`, 77 MB, X3_20250718. Filesystems are SquashFS 4.0, **LZO**, 32 KiB blocks. Partition
  slots have only about 1–4 KB of slack, and the ISP script hardcodes an md5 of the first 0x200000 bytes of each
  partition inside a uImage with CRCs, so repacking needs a dedicated tool (not built).
- Boot: `early-boot` loads drivers and the `.rc` files; the launcher is chosen by `ro.start.Launcher`; each also
  starts `mcusrv`. A root shell runs on the console service (*inferred*).
- Mounts: `spapp` → `/application`, `spsdk` → `/usr/local`. Real mount points are under `/tmp/sp/…`, which is
  why `/proc/<pid>/maps` shows those paths. `/etc`, `/root`, `/tmp` are tmpfs, wiped every boot.
- Writable NAND: `nvm` (16 MB YAFFS2, settings and library overrides) and `userdata` (132 MB YAFFS2, free for
  tools and backups, not auto-loaded). Neither has `noexec`.
- nvm also holds `isp_part.bin` (2.4 MB, unknown; do not delete) and a file with a garbled name (4,918 bytes).
- Feature to library map (activity UI vs module logic vs SDK daemon) is in FINDINGS 2.8; function and key
  lookups are in `docs/MAP.md`.
- Unverified: IPs `120.77.203.1` and `47.92.37.111` appear in strings (possibly update or activation servers).
