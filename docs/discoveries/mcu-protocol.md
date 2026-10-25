# SoC to MCU protocol (mcusrv)
**Status:** recovered statically, **never seen on the wire**. Full tables: `docs/MCU_PROTOCOL.md`.

## What we know
- Bridge: `/application/bin/mcusrv 1 115200`, so `/dev/ttyS1` at 115200 baud. `libmcusrv.so` holds the Binder
  interfaces (`IMCUStatus`, `BnMCUPower`). *strings, decompiler*
- Frame: `FF AA LEN payload CHK CRC_hi CRC_lo`; `CHK = -(LEN+ΣP) & 0xFF`; CRC16 poly 0x8432, MSB-first, init 0,
  no final XOR. The exact span the CRC covers is not verified. *decompiler*
- The flag that causes the Emperor and EQ bugs is MCU to SoC command **0x39** (`mcu_parse_rst_memory`).
- MCU firmware (`mcu8368u.bin`, JieLi `JL_690X.BIN`) is encrypted; we cannot see why or when the MCU sends it.

## Rules
- Never write to `ttyS1` while mcusrv runs. Never send `0x75` (`send_mcu_upgrade`).
- Do not kill or restart mcusrv in the car: it carries the power handshake (0x00 ↔ `send_mcu_power_status`).
- Safe observation only: `/proc/interrupts` and `/proc/tty/driver/*` counters, evdev capture (both in the extras
  section of `gemn_auto.sh`).

## Next
Confirm the CRC span by re-implementing it against a captured frame. Find what makes the MCU report RstMemory.
Other sub-modules seen in strings: MCUKey (injects keys into `/dev/sp_virt_kb`), MCUSpin, MCUDisc, MCUVBus, MCUBeep.
