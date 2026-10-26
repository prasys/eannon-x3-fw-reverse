#!/bin/sh
# Read-only diagnostics for the Eanon X3, run by the stock USB autorun hook
# (/etc/usb_action_8368-U runs <usb root>/gemn_auto.sh as root on stick insert).
# Changes nothing on flash; writes only to the USB stick and to /tmp (RAM).
#
# Each plug-in = one snapshot in diag/<boot_id>_<n>/:
#   changes.txt     settings diff vs the previous snapshot (what your last action saved)
#   audio_events.txt  audio/DSP log lines since the previous snapshot (what got applied)
#   window.log      every log line since the previous snapshot (unfiltered)
# On the first plug-in after boot it also starts a background logcat recorder in /tmp,
# so events between plug-ins are kept (logcat's own ring buffer is small).
# ResumeInfo.ini (Loudness / DSP switch / legacy Emperor blob) is only written at
# shutdown, so its changes show up in the first snapshot after the next boot.
# Same boot_id across a car off/on => warm resume; new boot_id => cold boot.

USB=$(dirname "$0")

# library override installer / status check (tools/usb-patch), runs first
[ -f "$USB/x3patch/patch.sh" ] && sh "$USB/x3patch/patch.sh"
# Wi-Fi AP 40 MHz patch / health check (tools/usb-patch/wifi40), before the wifi/ capture
[ -f "$USB/wifi40/wifi40.sh" ] && sh "$USB/wifi40/wifi40.sh"
# Home date DD-MM-YYYY nvm-override test (tools/usb-patch/datefmt)
[ -f "$USB/datefmt/datefmt.sh" ] && sh "$USB/datefmt/datefmt.sh"
# reverse guide-line position test (tools/usb-patch/guideline), nvm libapis override
[ -f "$USB/guideline/guideline.sh" ] && sh "$USB/guideline/guideline.sh"
OUT=$USB/diag
RAM=/tmp/x3diag
NVM=/media/flash/nvm/Sunplus
mkdir -p "$OUT" "$RAM"

BOOT_ID=$(cut -c1-8 /proc/sys/kernel/random/boot_id 2>/dev/null)
[ -z "$BOOT_ID" ] && BOOT_ID=unknown
N=1
while [ -d "$OUT/${BOOT_ID}_$N" ]; do N=$((N + 1)); done
SNAP=$OUT/${BOOT_ID}_$N
mkdir -p "$SNAP"

# --- background logcat recorder (RAM only, max 4 x 2 MB) ---
REC=started
if [ -f "$RAM/rec.pid" ] && kill -0 "$(cat "$RAM/rec.pid")" 2>/dev/null; then
	REC=running
else
	nohup /usr/local/bin/logcat -v time -f "$RAM/live.log" -r2048 -n4 >/dev/null 2>&1 &
	echo $! > "$RAM/rec.pid"
fi

UPTIME=$(cut -d' ' -f1 /proc/uptime)
echo "$(date '+%Y-%m-%d %H:%M:%S') boot_id=$BOOT_ID uptime=${UPTIME}s snap=${BOOT_ID}_$N recorder=$REC" >> "$OUT/runs.txt"

{
	echo "== date";        date
	echo "== uptime";      cat /proc/uptime
	echo "== boot_id";     cat /proc/sys/kernel/random/boot_id
	echo "== uname";       uname -a
	echo "== mtd";         cat /proc/mtd
	echo "== mounts";      cat /proc/mounts
	echo "== df";          df
	echo "== ps";          ps
	echo "== nvm listing"; ls -laR /media/flash/nvm
} > "$SNAP/system.txt" 2>&1

# --- settings: copy, checksum, mtime, diff vs previous snapshot ---
mkdir -p "$SNAP/nvm"
cp -a "$NVM" "$SNAP/nvm/" 2>/dev/null
for f in "$NVM"/*.ini; do
	stat -c '%y %s %n' "$f"
	md5sum "$f"
done > "$SNAP/ini_stat.txt" 2>&1
grep -n -E '^audio\.|^system\.Eq' "$NVM/SETUPMENU.ini" > "$SNAP/audio_keys.txt" 2>&1

PREV=$OUT/last
{
	if [ -d "$PREV" ]; then
		echo "== vs previous snapshot $(cat "$PREV/name" 2>/dev/null)"
		for f in SETUPMENU.ini ResumeInfo.ini; do
			diff -u "$PREV/$f" "$NVM/$f" || true
		done
	else
		echo "== first snapshot, nothing to compare"
	fi
} > "$SNAP/changes.txt" 2>&1
rm -rf "$PREV"; mkdir -p "$PREV"
cp "$NVM"/*.ini "$PREV/" 2>/dev/null
echo "${BOOT_ID}_$N" > "$PREV/name"

# --- logs ---
dmesg > "$SNAP/dmesg.txt" 2>&1
/usr/local/bin/logcat -d -v time > "$SNAP/logcat.txt" 2>&1
cp -a /tmp/sp/log "$SNAP/sp_log" 2>/dev/null

# full recorder log, oldest first
for i in 4 3 2 1; do [ -f "$RAM/live.log.$i" ] && cat "$RAM/live.log.$i"; done > "$SNAP/live.log"
[ -f "$RAM/live.log" ] && cat "$RAM/live.log" >> "$SNAP/live.log"

# redaction for anything copied to the stick: Wi-Fi secrets, phone serials, and MACs cut
# to their OUI (the Android Auto stack logs "retpassphrase:<psk>" and "serial[..]" /
# "serail[..]")
RED='s/^((wpa_)?passphrase|(wpa_)?psk|wep_key[0-9]*|wpa_password)=.*/\1=<redacted>/
s/([Pp]assphrase|[Pp]assword|psk)([:=] ?)[^,[:space:]]*/\1\2<redacted>/g
s/(ser(ia|ai)l\[)[^]]*\]/\1<redacted>]/g
s/(([0-9a-fA-F]{2}:){3})([0-9a-fA-F]{2}:){2}[0-9a-fA-F]{2}/\1xx:xx:xx/g'
sed -i -r "$RED" "$SNAP/logcat.txt" "$SNAP/live.log" 2>/dev/null
[ -d "$SNAP/sp_log" ] && find "$SNAP/sp_log" -type f -exec sed -i -r "$RED" {} \; 2>/dev/null

# lines new since the previous snapshot, by logcat timestamp ("MM-DD HH:MM:SS.mmm")
PAT='setSpeakerDelay|[Dd]elay|[Ee]mperor|[Ff]ilter|[Ll]ou?ndness|[Vv][Bb]ass|[Bb]alance|[Ff]ader|APP_CMD|[Dd]ps|[Bb]ongiovi|[Ss]tereo|[Ss]ub[Ww]oofer|DSP|dsp|set eq failed'
SINCE=$(cat "$RAM/since.$BOOT_ID" 2>/dev/null)
[ "$REC" = started ] && SINCE=""
{
	[ "$REC" = started ] && cat "$SNAP/logcat.txt"
	cat "$SNAP/live.log"
} | grep -E '^[0-9][0-9]-[0-9][0-9] ' | sort -u | awk -v s="$SINCE" 'substr($0,1,18) > s' > "$SNAP/window.log"
{
	echo "== log lines after '${SINCE:-boot}' (recorder=$REC)"
	grep -E "$PAT" "$SNAP/window.log" | grep -v 'control 766\]'
} > "$SNAP/audio_events.txt" 2>&1
NEWEST=$(tail -n 1 "$SNAP/window.log" | cut -c1-18)
[ -n "$NEWEST" ] && echo "$NEWEST" > "$RAM/since.$BOOT_ID"

sync

# --- Wi-Fi AP / Android Auto (read-only; secrets and MACs redacted) ---
# networkmanager reads /media/flash/nvm/softap.conf if present, else the ROM copy,
# and starts /usr/local/bin/hostapd with ctrl_interface=/var/run/hostapd.
W=$SNAP/wifi
mkdir -p "$W"
SOFTAP_NVM=/media/flash/nvm/softap.conf
SOFTAP_ROM=/usr/local/etc/wifimanager/softap.conf
HCLI="/usr/local/bin/hostapd_cli -p /var/run/hostapd -i wlan0"
{
	echo "== processes"; ps | grep -E 'hostapd|networkmanager|udhcpd|wpa_supplicant|aa_|wifi' | grep -v grep
	for p in $(pidof hostapd); do
		echo "== hostapd $p cmdline"; tr '\0' ' ' < /proc/$p/cmdline; echo
	done
	echo "== /var/run/hostapd"; ls -la /var/run/hostapd
	echo "== softap.conf files"
	for f in "$SOFTAP_NVM" "$SOFTAP_ROM"; do
		[ -f "$f" ] && { stat -c '%y %s %n' "$f"; md5sum "$f"; } || echo "missing: $f"
	done
} > "$W/ap.txt" 2>&1
for f in "$SOFTAP_NVM" "$SOFTAP_ROM"; do
	[ -f "$f" ] && sed -r "$RED" "$f" > "$W/$(echo "$f" | cut -d/ -f2-3 | tr / _)_softap.conf"
done
[ -f "$SOFTAP_NVM" ] && diff -u "$W/usr_local_softap.conf" "$W/media_flash_softap.conf" > "$W/softap_nvm_vs_rom.diff" 2>&1
sync; echo "step wifi files done" >> "$SNAP/system.txt"; sync
RTW=/proc/net/rtl8821cs/wlan0
# live AP/driver state into dir $1, AA/AP log lines from log file $2
wifi_live() {
	mkdir -p "$1"
	{
		for c in status get_config all_sta; do
			echo "== hostapd_cli $c"; $HCLI $c
		done
	} 2>&1 | sed -r "$RED" > "$1/hostapd_cli.txt"
	{
		echo "== ifconfig wlan0"; ifconfig wlan0
		echo "== ip addr"; ip addr
		echo "== /proc/net/wireless"; cat /proc/net/wireless
		# rtw driver status dumps only (no register dumps)
		for e in ap_info all_sta_info bw_mode ht_option chan_plan country_code; do
			echo "== $RTW/$e"; cat "$RTW/$e"
		done
		echo "== rtw proc entries"; ls "$RTW"
	} 2>&1 | sed -r "$RED" > "$1/iface.txt"
	grep -E 'hostapd|[Ss]oft[Aa][Pp]|[Ww]ifi[Aa]p|wlan0|[Aa]ndroid[Aa]uto|aa_manager|aa_accessory|AAS|RFCOMM|8765|[Cc]ar[Pp]lay' \
		"$2" > "$1/aa_events.txt" 2>&1
}
wifi_live "$W" "$SNAP/window.log"
grep -iE 'rtw|rtl8821|wlan0|80211' "$SNAP/dmesg.txt" | sed -r "$RED" > "$W/dmesg_wifi.txt" 2>&1
sync; echo "step wifi done" >> "$SNAP/system.txt"; sync

# --- phone watch: the hook runs once, at plug-in. With the stick left in that's at
# boot, before the phone joins the AP, so if no station is connected yet, poll hostapd
# every 5 s for up to 15 min. When one joins, wait 30 s for projection to start, then
# capture wifi_sta/ (same files as wifi/ plus the log since boot) and sample the link
# rate/RSSI every 5 s for 2 min. Stops quietly if the stick is pulled.
num_sta() { $HCLI status 2>/dev/null | sed -n 's/^num_sta\[0\]=//p'; }
if [ "$(num_sta)" = 0 ] && ! { [ -f "$RAM/watch.pid" ] && kill -0 "$(cat "$RAM/watch.pid")" 2>/dev/null; }; then
	(
		trap '' HUP
		i=0
		while [ $i -lt 180 ]; do
			sleep 5
			[ -d "$SNAP" ] || exit 0
			[ "$(num_sta)" -gt 0 ] 2>/dev/null && break
			i=$((i + 1))
		done
		if [ $i -ge 180 ]; then
			echo "sta watch: no phone joined within 15 min" >> "$SNAP/system.txt"; sync; exit 0
		fi
		echo "sta watch: phone joined $(date '+%H:%M:%S')" >> "$SNAP/system.txt"
		sleep 30
		[ -d "$SNAP" ] || exit 0
		WS=$SNAP/wifi_sta
		mkdir -p "$WS"
		{
			for i in 4 3 2 1; do [ -f "$RAM/live.log.$i" ] && cat "$RAM/live.log.$i"; done
			[ -f "$RAM/live.log" ] && cat "$RAM/live.log"
		} | sed -r "$RED" > "$WS/live.log"
		wifi_live "$WS" "$WS/live.log"
		sync; echo "sta watch: snapshot done $(date '+%H:%M:%S')" >> "$SNAP/system.txt"; sync
		j=0
		while [ $j -lt 24 ] && [ -d "$WS" ]; do
			echo "== t=$((j * 5))s $(date '+%H:%M:%S')"
			grep -E 'macaddr|bwmode|ht_bitrate|vht_bitrate|_tp |bitrate_100kbps|rssi' "$RTW/all_sta_info"
			$HCLI all_sta | grep -E '^[0-9a-f]{2}:|rate|signal|connected_time|_bytes'
			sleep 5
			j=$((j + 1))
		done 2>&1 | sed -r "$RED" > "$WS/link_samples.txt"
		sync; echo "sta watch: link samples done $(date '+%H:%M:%S')" >> "$SNAP/system.txt"; sync
	) > /dev/null 2>&1 &
	echo $! > "$RAM/watch.pid"
fi

# --- extras: only when <usb root>/extras.flag exists (all read-only) ---
if [ -f "$USB/extras.flag" ]; then
	X=$SNAP/extras
	mkdir -p "$X"
	echo "extras start $(date '+%H:%M:%S')" >> "$SNAP/system.txt"

	# system / hardware inventory
	{
		echo "== cpuinfo";  cat /proc/cpuinfo
		echo "== meminfo";  cat /proc/meminfo
		echo "== cmdline";  cat /proc/cmdline
		echo "== lsmod";    lsmod
		echo "== input devices"; cat /proc/bus/input/devices
		echo "== /dev/input"; ls -la /dev/input
		echo "== tty drivers"; cat /proc/tty/drivers
		echo "== i2c buses (list only, no scan)"; i2cdetect -l
		echo "== gpio"; ls /sys/class/gpio
		echo "== interrupts"; cat /proc/interrupts
	} > "$X/hw.txt" 2>&1
	sync; echo "step hw done" >> "$SNAP/system.txt"; sync
	/usr/local/bin/getprop > "$X/getprop.txt" 2>&1
	# fw_printenv removed: unit rebooted during/just after this step (2026-10-25)
	sync; echo "step getprop done" >> "$SNAP/system.txt"; sync

	# mcusrv process + its serial port
	MP=$(pidof mcusrv)
	{
		echo "== pid $MP"
		for p in $MP; do
			echo "== cmdline"; tr '\0' ' ' < /proc/$p/cmdline; echo
			echo "== fds";     ls -l /proc/$p/fd
			echo "== status";  cat /proc/$p/status
			echo "== maps";    cat /proc/$p/maps
		done
	} > "$X/mcusrv.txt" 2>&1
	for f in /proc/tty/driver/*; do echo "== $f"; cat "$f"; done > "$X/tty_driver.txt" 2>&1

	# raw nanddump backups removed: raw NAND reads are suspected of the 2026-10-25 reboot
	sync; echo "step mcusrv/tty done" >> "$SNAP/system.txt"; sync

	# 30 s capture window: press steering-wheel / panel keys, toggle things.
	# Records raw input events (evdev allows several readers, Launcher still gets them)
	# and samples the MCU UART counters once per second.
	echo "capture window start $(date '+%H:%M:%S')" >> "$SNAP/system.txt"
	mkdir -p "$X/input"
	PIDS=""
	for ev in /dev/input/event*; do
		cat "$ev" > "$X/input/$(basename "$ev").raw" 2>/dev/null &
		PIDS="$PIDS $!"
	done
	i=0
	while [ $i -lt 30 ]; do
		echo "== t=$i $(date '+%H:%M:%S')"
		grep -iE 'uart|ttyS|serial' /proc/interrupts
		cat /proc/tty/driver/* 2>/dev/null | grep -E '^[0-9]+:'
		sleep 1
		i=$((i + 1))
	done > "$X/uart_counters.txt" 2>&1
	kill $PIDS 2>/dev/null
	for f in "$X"/input/*.raw; do
		[ -s "$f" ] && hexdump -v -e '4/4 "%08x " "\n"' "$f" > "${f%.raw}.txt"
	done
	echo "capture window end $(date '+%H:%M:%S')" >> "$SNAP/system.txt"
	sync
fi

echo "done" >> "$SNAP/system.txt"
