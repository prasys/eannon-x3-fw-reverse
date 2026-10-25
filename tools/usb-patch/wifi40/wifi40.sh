#!/bin/sh
# 40 MHz (HT40) for the X3's Wi-Fi AP (wireless Android Auto / CarPlay), called from
# gemn_auto.sh on USB insert. Background: docs/FINDINGS.md 8.5, docs/ROADMAP.md.
#
# hostapd runs from /media/flash/nvm/softap.conf, which has ieee80211n=1 but no
# ht_capab, so the AP is 20 MHz. This adds one line:
#   ht_capab=[HT40+][SHORT-GI-20][SHORT-GI-40]     ([HT40-] for ch 40/48/56/64/153/161)
# hostapd only reads its config at start, so the change takes effect on the next
# cold boot (a new boot_id; a warm resume keeps the old hostapd running).
#
# Controlled by <usb>/wifi40/mode (one word):
#   apply   add the line (if checks pass). On later boots, check hostapd came up; if it
#           did not within 45 s, remove the line again (auto-undo) and write
#           wifi40/auto_undone so it is not re-applied. Reboot to get the AP back.
#   undo    remove the ht_capab line from the current file (keeps any other change
#           networkmanager made since, e.g. a new SSID). Reboot to take effect.
#   status  change nothing, just report
# Every run appends to <usb>/wifi40/log.txt (no SSID/passphrase is logged). Before the
# first change the original file is copied to /media/flash/nvm/softap.conf.pre-wifi40
# (on the unit only, since it holds the AP passphrase).

P=$(dirname "$0")
LOG=$P/log.txt
T=${WIFI40_TEST_ROOT:-}             # test hook: prefix for unit paths (empty on the unit)
CONF=$T/media/flash/nvm/softap.conf
BAK=$CONF.pre-wifi40
HCLI=${WIFI40_HCLI:-/usr/local/bin/hostapd_cli -p /var/run/hostapd -i wlan0}
RTW=$T/proc/net/rtl8821cs/wlan0
CAPS='[SHORT-GI-20][SHORT-GI-40]'

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; sync; }
md5of() { md5sum "$1" 2>/dev/null | cut -d' ' -f1; }
key() { sed -n "s/^$1=//p" "$CONF" | tr -d '\r' | head -n 1; }
st() { $HCLI status 2>/dev/null | tr -d '\r'; }
summary() {
	S=$(st)
	if [ -z "$S" ]; then
		printf 'hostapd: no answer on /var/run/hostapd (pids: %s)' "$(pidof hostapd)"
	else
		printf 'hostapd: %s' "$(echo "$S" | grep -E '^(state|freq|channel|secondary_channel|ht_caps_info|num_sta\[0\])=' | tr '\n' ' ')"
	fi
	[ -r "$RTW/ap_info" ] && printf '; driver: %s' "$(grep -o 'cur_bwmode=[^,]*' "$RTW/ap_info")"
	echo
}
# remove every ht_capab line (the stock and pre-patch configs have none)
strip_line() {
	awk '!/^ht_capab=/' "$CONF" > "$CONF.wifi40.tmp" && sync
	if grep -q '^interface=' "$CONF.wifi40.tmp" && ! grep -q '^ht_capab=' "$CONF.wifi40.tmp"; then
		chmod 600 "$CONF.wifi40.tmp"; mv "$CONF.wifi40.tmp" "$CONF" && sync
		log "$1: removed ht_capab, $CONF md5 now $(md5of "$CONF")$( [ "$(md5of "$CONF")" = "$(md5of "$BAK")" ] && echo ' (same as backup)')"
		return 0
	fi
	rm -f "$CONF.wifi40.tmp"; sync
	log "$1: ABORT, stripped file failed verification; $CONF unchanged"
	return 1
}

BOOT=$(cut -c1-8 $T/proc/sys/kernel/random/boot_id 2>/dev/null)
MODE=$(tr -d ' \r\n' < "$P/mode" 2>/dev/null)
log "---- run: mode=${MODE:-none} boot_id=$BOOT uptime=$(cut -d' ' -f1 $T/proc/uptime)s"
[ -f "$CONF" ] || { log "ABORT: $CONF not found (AP runs from the ROM copy?), did nothing"; exit 1; }
CUR=$(key ht_capab)
log "status: channel=$(key channel) hw_mode=$(key hw_mode) ht_capab=${CUR:-<none>} md5=$(md5of "$CONF")"
log "status: $(summary)"

case "$MODE" in
apply)
	if [ -f "$P/auto_undone" ]; then
		log "apply: skipped, auto-undo happened before ($(cat "$P/auto_undone")). Delete wifi40/auto_undone to retry."
		exit 0
	fi
	if [ -z "$CUR" ]; then
		# --- first run: add the line ---
		CH=$(key channel)
		case "$CH" in
		36|44|52|60|100|108|116|124|132|140|149|157) HT='[HT40+]' ;;
		40|48|56|64|104|112|120|128|136|144|153|161) HT='[HT40-]' ;;
		*) log "apply: ABORT, channel '$CH' has no 40 MHz pair (or is 2.4 GHz/auto), did nothing"; exit 1 ;;
		esac
		[ "$(key hw_mode)" = a ] || { log "apply: ABORT, hw_mode is not 'a' (5 GHz), did nothing"; exit 1; }
		[ "$(key ieee80211n)" = 1 ] || { log "apply: ABORT, ieee80211n is not 1, did nothing"; exit 1; }
		st | grep -q '^state=ENABLED' || { log "apply: ABORT, hostapd is not ENABLED right now, so a later failure couldn't be blamed on this; did nothing"; exit 1; }
		if [ ! -f "$BAK" ]; then
			cp "$CONF" "$BAK" && sync
			[ "$(md5of "$BAK")" = "$(md5of "$CONF")" ] || { rm -f "$BAK"; sync; log "apply: ABORT, backup verify failed"; exit 1; }
			chmod 600 "$BAK"
			log "apply: backup $BAK md5 $(md5of "$BAK")"
		fi
		LINE="ht_capab=$HT$CAPS"
		# awk ends every line with \n, so a last line without one can't run into ours
		awk -v l="$LINE" '{ print } END { print l }' "$CONF" > "$CONF.wifi40.tmp" && sync
		# verify: exactly one ht_capab line, and everything else unchanged
		if [ "$(grep -c '^ht_capab=' "$CONF.wifi40.tmp")" != 1 ] ||
			[ "$(awk '!/^ht_capab=/' "$CONF.wifi40.tmp" | md5sum)" != "$(awk '{ print }' "$CONF" | md5sum)" ]; then
			rm -f "$CONF.wifi40.tmp"; sync
			log "apply: ABORT, new file failed verification; $CONF unchanged"
			exit 1
		fi
		chmod 600 "$CONF.wifi40.tmp"
		mv "$CONF.wifi40.tmp" "$CONF" && sync
		echo "$BOOT" > "$P/applied_boot"; sync
		log "apply: OK, added '$LINE' (channel $CH), $CONF md5 now $(md5of "$CONF")."
		log "apply: takes effect after a COLD boot (car off long enough that the next run shows a new boot_id). Plug in again then to check it."
	elif [ "$CUR" = "${CUR#\[HT40}" ] || [ "${CUR#*\]}" != "$CAPS" ]; then
		log "apply: ht_capab is '$CUR', which this script did not write; did nothing"
	elif [ "$BOOT" = "$(cat "$P/applied_boot" 2>/dev/null)" ]; then
		log "apply: pending, written during this boot; hostapd still runs the old config. Cold boot, then plug in again."
	else
		# --- later boot: health check ---
		i=0
		while [ $i -lt 45 ] && ! st | grep -q '^state=ENABLED'; do sleep 1; i=$((i + 1)); done
		if st | grep -q '^state=ENABLED'; then
			S=$(st)
			if echo "$S" | grep -qE '^secondary_channel=-?1$'; then
				log "check: OK, AP is up at 40 MHz. $(summary)"
			else
				log "check: AP is up but still 20 MHz (hostapd may have fallen back, e.g. a neighbour AP on the secondary channel). Harmless. $(summary)"
			fi
		else
			log "check: FAIL, hostapd not ENABLED after 45 s. $(summary)"
			LC=$T/usr/local/bin/logcat
			[ -x "$LC" ] && "$LC" -d -v time 2>/dev/null | grep -E 'Hostapd start fail|SoftAP|HT capab|HT40|20/40|ht_capab' | tail -n 8 |
				while read -r l; do log "check: log: $l"; done
			if strip_line auto-undo; then
				echo "$(date '+%Y-%m-%d %H:%M:%S') boot_id=$BOOT" > "$P/auto_undone"; sync
				log "auto-undo: done. Reboot (cold) to bring the AP back at 20 MHz. Won't re-apply while wifi40/auto_undone exists."
			fi
		fi
	fi
	;;
undo)
	if [ -n "$CUR" ]; then
		strip_line undo && log "undo: reboot (cold) to run the AP without it."
	else
		log "undo: no ht_capab line, nothing to do"
	fi
	rm -f "$P/applied_boot"; sync
	;;
status|"")
	;;
*)
	log "unknown mode '$MODE', did nothing"
	;;
esac
