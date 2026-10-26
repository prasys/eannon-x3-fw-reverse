#!/bin/sh
# Home date DD-MM-YYYY test for the Eanon X3, called from gemn_auto.sh on USB insert.
# Background: docs/discoveries/date-format-ddmmyyyy.md.
#
# The Home date comes from libhomelauncher.so (an activity plugin), format "yyyy-MM-dd" at
# file offset 0x1846b. This puts a copy in /media/flash/nvm and reports whether the
# Launcher actually maps it. It is NOT known whether the plugin loader honours
# LD_LIBRARY_PATH (it likely dlopen()s /application/lib/activity/default/... by full path),
# so the log after the next boot is the result of the test. Stock file is never touched.
#
# <usb>/datefmt/mode (one word):
#   probe      copy the unit's own unmodified libhomelauncher.so to nvm (safe: tests loading only)
#   install    copy payload/libhomelauncher.so (patched, dd-MM-yyyy) to nvm
#   uninstall  remove our copy from nvm
#   status     change nothing, just report
# Takes effect on the next cold boot. Every run appends to <usb>/datefmt/log.txt.
# Payload: python3 tools/usb-patch/datefmt/build_payload.py <stock .so> <usb>/datefmt/payload

P=$(dirname "$0")
LOG=$P/log.txt
T=${DATEFMT_TEST_ROOT:-}            # test hook: prefix for unit paths (empty on the unit)
NVM=$T/media/flash/nvm
NAME=libhomelauncher.so
STOCK=$T/application/lib/activity/default/$NAME
TARGET=$NVM/$NAME
PAYLOAD=$P/payload/$NAME
STOCK_MD5=a29d979a7540d312bea3bf1d8f0c3891
PATCHED_MD5=c08c872fe8426fc196ba0abe126c2d8e

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; sync; }
md5of() { md5sum "$1" 2>/dev/null | cut -d' ' -f1; }

MODE=$(tr -d ' \r\n' < "$P/mode" 2>/dev/null)
log "---- run: mode=${MODE:-none} uptime=$(cut -d' ' -f1 $T/proc/uptime)s"

# --- what is loaded right now (this is the test result) ---
LP=$(pidof Launcher)
[ -z "$LP" ] && log "status: Launcher NOT running"
for p in $LP; do
	where=$(grep "$NAME" $T/proc/$p/maps 2>/dev/null | awk '{print $6}' | sort -u | tr '\n' ' ')
	log "status: Launcher pid $p maps $NAME from: ${where:-<not mapped>}"
done
log "status: stock md5 $(md5of "$STOCK") (expect $STOCK_MD5)"
log "status: nvm copy: $([ -f "$TARGET" ] && md5of "$TARGET" || echo none)"
log "status: nvm free: $(df "$NVM" 2>/dev/null | awk 'NR==2{print $4" KB"}')"

check_stock() {
	S=$(md5of "$STOCK")
	if [ "$S" != "$STOCK_MD5" ]; then
		log "ABORT: stock $STOCK md5 ${S:-missing} is not the expected $STOCK_MD5 (different firmware?)"
		return 1
	fi
}
# copy $1 to nvm atomically; $2 = expected md5
put() {
	if [ -e "$TARGET" ]; then
		cp "$TARGET" "$P/previous_$NAME.$(date +%s)" && log "$MODE: saved existing nvm copy to stick"
	fi
	cp "$1" "$TARGET.tmp" && sync
	if [ "$(md5of "$TARGET.tmp")" != "$2" ]; then
		rm -f "$TARGET.tmp"; sync
		log "ABORT: copy verify failed, removed temp file"
		return 1
	fi
	chmod 755 "$TARGET.tmp"
	mv "$TARGET.tmp" "$TARGET" && sync
	log "$MODE: OK, $TARGET md5 $(md5of "$TARGET"). Cold boot (car off/on), plug in again, read the 'maps' line."
}

case "$MODE" in
probe)
	check_stock || exit 1
	[ "$(md5of "$TARGET")" = "$STOCK_MD5" ] && { log "probe: already installed, nothing to do"; exit 0; }
	put "$STOCK" "$STOCK_MD5"
	;;
install)
	check_stock || exit 1
	[ -f "$PAYLOAD" ] || { log "ABORT: no payload at $PAYLOAD"; exit 1; }
	PM=$(md5of "$PAYLOAD")
	[ "$PM" = "$PATCHED_MD5" ] || { log "ABORT: payload md5 ${PM:-none} is not the known patched build $PATCHED_MD5"; exit 1; }
	[ "$(md5of "$TARGET")" = "$PM" ] && { log "install: already installed, nothing to do"; exit 0; }
	put "$PAYLOAD" "$PM"
	;;
uninstall)
	if [ -e "$TARGET" ] || [ -e "$TARGET.tmp" ]; then
		rm -f "$TARGET" "$TARGET.tmp" && sync
		log "uninstall: removed $TARGET. Cold boot to go back to stock."
	else
		log "uninstall: nothing to remove"
	fi
	;;
status|"") ;;
*) log "unknown mode '$MODE', did nothing" ;;
esac
