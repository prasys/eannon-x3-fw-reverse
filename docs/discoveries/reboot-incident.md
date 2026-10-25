# Incident: unit rebooted during USB extras (2026-10-25 ~17:19)
**Status:** mitigated. FINDINGS 8 (Incident).

- With `extras.flag` set the unit rebooted twice while the v4 script ran. The last file written was `getprop.txt`;
  the U-Boot env dump (`fw_printenv`, a raw read of the NAND env partition) was empty, so the failure was during
  or just after that step. The `nanddump` backups never ran.
- No settings were lost; the unit came back normally.
- **Rule:** do not read raw NAND/MTD from userspace on this unit. Back things up by copying files from the mounted
  `/media/flash/nvm`. `fw_printenv`, `nanddump` and `stty -F /dev/ttyS1` were removed from the script, and per-step
  `sync` plus `step … done` markers were added so a failure can be located.
