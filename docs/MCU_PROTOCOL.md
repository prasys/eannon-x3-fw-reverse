# SoC ↔ MCU UART protocol (mcusrv)

Recovered statically from `spapp/bin/mcusrv` (ARM/Thumb mixed, non-PIE, base 0x8000)
and `spapp/lib/libmcusrv.so` (Binder interfaces, named symbols). Decompile:
Ghidra export annotated with `tools/annotate_strings.py`. Nothing here has been
observed on the wire yet (we can't sniff `ttyS1` without stealing bytes from mcusrv).
Evidence: *decompiler* unless marked.

## Link
- `/dev/ttyS%d` with port argument `1` → **`/dev/ttyS1`, 115200 baud**
  (`appinfo.rc`: `mcusrv 1 115200`; strings `"/dev/ttyS%d"`, `"------->uartBaud=%d"`).
- Threads: `mcusrv::Reader::_thread` (RX, `FUN_0000f09c`), `Writer::_thread` (TX),
  `Dispatcher::_thread` (routes by command byte to sub-modules).
- Diagnostics go to **stdout via `printf`/`puts`** (service runs with `console`), so
  `--->mcu_parse_*` traces appear only on the internal serial console, not in logcat.

## Frame format
```
FF AA LEN  P[0] … P[LEN-1]  CHK  CRC_hi CRC_lo
            └ P[0] = command byte, P[1..] = arguments
```
- **TX** builder `FUN_0000f9e8`: writes `FF`, `AA`, `LEN`, payload, then
  `CHK = -(LEN + ΣP) & 0xFF` (`FUN_0000f934`), then CRC16 appended big-endian
  (`FUN_0000f7f0`) over the frame bytes.
- **RX** state machine `FUN_0000ec04`: state 4 waits for `FF` → 5 expects `AA` →
  6 takes `LEN`, expects `LEN+3` more bytes → 7 collects (frame max 0x7F bytes,
  else `"packet pool leak"` path) → 8 verifies CRC (`FUN_0000e8ac`, error string
  `MCU_REV_STATE_CHECK CRC ERROR!`) then CHK (`FUN_0000e96c`,
  `MCU_REV_STATE_CHECK SUM ERROR!`).
- **CRC16:** table built in `FUN_0000f644`: for each byte `c = i<<8`, 8× `{msb=c&0x8000;
  c<<=1; if msb: c = ~(c ^ 0x7bcd)}`, which is the same as **poly 0x8432**, MSB-first,
  init 0x0000, no final XOR (`FUN_0000f728`). Non-standard polynomial. *Not yet
  verified* exactly which bytes the CRC covers (TX passes the buffer at frame
  start, length = LEN+4?). Confirm with a captured frame or by re-implementing
  and checking against the MCU's replies.
- Internal packet types (byte +0x40 of the packet object): `0xD0` = valid frame
  received from MCU, `0xE0` / `0xB0` = handed to the Dispatcher (`0xB0` = ack-related:
  a received `0xD0` with flag +0x41==1 is turned into `0xB0`). Ack logging:
  `"Ack id %x send id %x"`. **Ack/ID semantics are open.**

## MCU → SoC messages (dispatch `FUN_00014554`, `switch (P[0])`)
Handlers forward through `IMCUStatusCallback` (slot names from `libmcusrv`'s
`_ZTV19BpMCUStatusCallback`; the vtable has a virtual-inheritance header, so call
offset `+N` = named slot `+N+4`). On the app side these land in `McuCallback::*`
(`libinfomodule.so`).

| Cmd | Handler | Payload | Callback |
| --- | --- | --- | --- |
| 0x00 | `FUN_000135e4` "mcu_parse_power" | 1 B power state | `updateSystemPowerState(u8)`: app side saves radio + DSP state, acks with `send_mcu_power_status` (FINDINGS 5.5) |
| 0x0a | `FUN_00013738` "vr_sub" | | volume knob − |
| 0x0b | `FUN_000136b4` "vr_add" | | volume knob + |
| 0x12 | `FUN_00013d94` "ir_order:0x%x" | u16 | `updateSystemIrOrder` (IR remote code) |
| 0x18 | `FUN_000137bc` "vr2_add" | | 2nd knob + |
| 0x19 | `FUN_00013840` "vr2_sub" | | 2nd knob − |
| 0x35 | `FUN_0001367c` "mcu_parse_sync" | | sync / heartbeat? |
| 0x36 | `FUN_000138c4` | u16 `P1<<8 | P2` | `updateSystemTime` |
| 0x37 | `FUN_000139fc` "mcu_parse_detect" | | `updateSystemDetectState(u16)` (ACC/ILL/brake/reverse inputs?) |
| **0x39** | `FUN_00013ab4` "mcu_parse_rst_memory" | 1 B | `updateSystemRstMemory(u8)`: the flag that gates the EQ restore (FINDINGS 5.2) |
| 0x3a | `FUN_00013b4c` "mcu_parse_ver" | string | `updateMcuVer` |
| 0x3d | `FUN_0001394c` "mcu_parse_date" | | `updateSystemDate` |
| 0x41 | `FUN_00013c0c` "bt_ctrl cmd:%d len:%d" | buffer | `updateBtStatus` (MCU-side BT, the JieLi BT chip) |
| 0x68 | `FUN_00013f1c` "canbus cmd:%d len:%d" | buffer | `updateSystemCanbus` |
| 0x6a | `FUN_00013e58` "mcu_parse_wheel:0x%x" | u16 `P1<<8 | P2` | `updateSystemWheelStatus` (steering-wheel keys) |
| 0x7e | `FUN_00013d10` | 1 B | `updateSystemBacklightLevel` |

All other `P[0]` values 0x01–0x7d fall through as no-ops.

## SoC → MCU commands (`IMCUStatus` server in mcusrv)
Sender helper `FUN_00011a30(writer, cmd, len, buf)` with `buf[0] = cmd`. Function →
method names by aligning mcusrv's table at file 0x139ac with `_ZTV11BpMCUStatus`
(offset +2 slots; 5 entries confirmed by their own printf strings, marked ✓).

| Cmd | Function | Method | Len |
| --- | --- | --- | --- |
| 0x00 (?) | 0x128c4 | `send_mcu_power_status(u8)`: ack to power message | 2 |
| 0x17 | 0x12924 | `send_mcu_system_power(u8)` ✓ | 2 |
| 0x7a | 0x129a0 | `send_mcu_camera_power` | 2 |
| — | 0x12a00 | `send_mcu_dtv_power` (no UART write seen) | |
| 0x11 | 0x12a34 | `send_mcu_tft_a_power` | 2 |
| 0x78 | 0x12a94 | `send_mcu_subwoffer_power` | 2 |
| 0x7b | 0x12af4 | `send_mcu_vbus_power` (also USB VBUS thread, "MCU USB %d Power ON/OFF") | 2 |
| 0x10 | 0x12b54 | `send_mcu_backlight_switch` | 2 |
| 0x1a | 0x12bb4 | `send_mcu_amp_switch` | 2 |
| 0x58 | 0x12c14 | `send_mcu_led_color` | 3 |
| 0x79 | 0x12ce4 | `send_mcu_ill` | 2 |
| 0x7d | 0x12d44 | `send_mcu_vcom_pwm` | 2 |
| 0x36 | 0x12db4 | `send_mcu_time(h,m,s)` (same id as RX time) | 4 |
| 0x3d | 0x12e34 | `send_mcu_date(y,m,d)` (same id as RX date) | 4 |
| 0x35 | 0x12ebc | `send_mcu_sync()` (same id as RX sync) | 2 |
| 0x3e | 0x12f14 | `send_mcu_decode_reset()` | 1 |
| 0x42 | 0x12f64 | `send_mcu_decode_default()` | 1 |
| 0x76 | 0x12fb4 | `send_mcu_vr_type` | 2 |
| 0x01 | 0x13014 | `send_mcu_mute(u16)` | 2 |
| 0x7c | 0x13088 | `send_mcu_radio_int` | 2 |
| 0x75 | 0x130e8 | `send_mcu_upgrade` (**MCU firmware update; never send**) | 2 |
| 0x41 | 0x13238 | `send_mcu_bt_cmd(u8)` ✓ | 2 |
| 0x41 | 0x132b4 | `send_mcu_bt_cmd_buf(buf)` ✓ | n+3 |
| — | 0x133b4 | `send_mcu_read_enable` (toggles the Reader locally) | |
| 0x6a | 0x13410 | `send_mcu_wheel_ctrl(u8)` ✓ | 2 |
| 0x68 | 0x1348c | `send_mcu_canbus_buf(buf)` ✓ | n+1 |
| 0x67 | 0x13540 | `send_mcu_audio_channel(ch, gain)` ✓ (gain via lookup table) | 3 |

Cmd bytes come from the first constant assigned in each function. Ones without a
✓ are *decompiler-inferred*; check the two `cmd~1` cases (0x13014 / 0x13148) and
`power_status` (constant 0) against the assembly.

## Other sub-modules (strings)
- `MCUKey`: keys arrive and are injected into `/dev/sp_virt_kb`
  ("MCU Key new key %d event %d presse").
- `MCUSpin`: rotary encoder ("MCUSpin:%d old_spin %d d_spin %d").
- `MCUDisc`: disc mechanism status ("MCU Disc status:%d").
- `MCUVBus`: USB VBUS power per port, waits on `ro.usb.init_uphy`.
- `MCUBeep`: beep thread. `MCUTest`: "MCU SW info:%s" / "MCU HW info:%s".
- `FMModule`: tuner commands ("FMModule cmd size %d").

## Safety notes
- Never write to `/dev/ttyS1` while mcusrv runs, and never send `0x75` (upgrade).
- Killing mcusrv stops the power handshake (0x00 ↔ `send_mcu_power_status`). The MCU
  may then force power-off or skip the shutdown save. Don't restart it in the car.
- Passive observation options: UART IRQ/byte counters (`/proc/interrupts`,
  `/proc/tty/driver/*`), evdev input capture (both in the v4 USB extras).
