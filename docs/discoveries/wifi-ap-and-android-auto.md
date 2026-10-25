# Wi-Fi AP, Android Auto and CarPlay
**Status:** wireless AA works; HT40 applied and verified. FINDINGS 8, 8.5, 12.

## Android Auto
- Sunplus "AAS" v2.2.20. `aa_manager_service` (state machine, TCP server on 192.168.0.1:8765, BT RFCOMM
  service) spawns `aa_accessory_service` per session (SSL, protobuf, video, audio). App side:
  `libandroidautomodule.so`, UI `libandroidauto.so`. `accessory_server` is CarLife, not AA.
- Config: `/application/etc/Androidauto/androidauto_config.xml` (1024×600, 480P and 720P). The car name
  shown on the phone is the SDK placeholder (Rolls-Royce Phantom 2016).
- Wired AA only happens when the "smartphone link" setting selects AA (`UsbMControl::setAndroidDeviceMode`).
  The EasyConnect AOA file it references is missing.
- Wireless sequence: BT RFCOMM → phone gets SSID/passphrase → phone joins the AP → TCP 8765 → session.
- **Privacy:** `libandroidautomodule` logs the hotspot passphrase. Redact it from any capture.

## Wi-Fi AP
- RTL8821CS (1×1 Wi-Fi 5 + BT), driver loaded with `rtw_vht_enable=2`.
- `networkmanager` reads `/media/flash/nvm/softap.conf` if present, else the ROM copy, and writes back **every**
  key, so extra keys survive SSID changes from the UI.
- ROM config is channel 149, `ieee80211n=1`, no `ht_capab`, so HT20 (72 Mbps PHY).
- **No 802.11ac:** hostapd 2.10 was built without `CONFIG_IEEE80211AC`; VHT keys would stop the AP from starting.
- **HT40 works:** adding `ht_capab=[HT40+][SHORT-GI-20][SHORT-GI-40]` gave hostapd `secondary_channel=1` and
  driver `cur_bwmode=2` after cold boots (boots 5eced8c3, dbce6923). Note the first run was on channel 36 (the
  stored config), not 149. *runtime*
- `wifi40.sh` removes the line again if hostapd does not come up within 45 s. Descriptor: `patches/wifi40.json`.
