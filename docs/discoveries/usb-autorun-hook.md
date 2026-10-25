# USB autorun hook (the delivery channel)
**Status:** confirmed working. FINDINGS 7.

- `rootfs/etc/mdev.conf:3` runs `/etc/usb_action_8368-U` on every USB block-device event, as root.
- That script mounts the stick (seen at `/tmp/sp/mnt/sda1`) and executes `<usb root>/gemn_auto.sh` if present,
  logging to `<usb root>/log_mdevscr_usb.txt`.
- Works at boot with the stick already inserted (uptime about 13 s). FAT32/MBR stick, LF line endings.
- It runs once per plug-in, so a stick left in is processed at boot, before the phone joins the Wi-Fi AP. The
  script handles that with a background watcher.
- The hook runs from mdev, independent of the UI, so uninstall modes work even if the Launcher does not start.
- Also on the unit: `/usr/local/bin/logcat` and `log_server` (`ALOG_LEVEL w`).
- Stick layout: `gemn_auto.sh`, `x3patch/{patch.sh,mode,payload/,stock/,log.txt}`, `wifi40/{wifi40.sh,mode,log.txt}`,
  `diag/` (captures; personal data, never commit), `extras.flag` (optional heavy extras).
