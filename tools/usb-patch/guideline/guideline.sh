#!/bin/sh
# Reverse guide-line position test for the Eanon X3, called from gemn_auto.sh on USB insert.
# Background: docs/discoveries/reverse-camera-guide-lines.md.
#
# ParklineCtrl (libapis) draws the "Reversing Guidance Line" bitmap at a fixed y. The payload is
# the stock libapis with that y changed (tools/build_guideline.py). The loader looks for
# libapis.so.1 first in /media/flash/nvm (init.gui.rc:4), so a copy there replaces the stock
# one on the next boot. The stock file is never touched; removing our copy restores stock.
# NOTE: libapis is used by almost every app, and this override is untested for it. If the UI
# does not come up, plug the stick in with mode=uninstall; the USB hook runs independently.
#
# Controlled by <usb>/guideline/mode (one word):
#   install    copy payload/libapis.so.1 into nvm (if checks pass)
#   uninstall  remove our copy from nvm
#   status     change nothing, just report
# Every run appends to <usb>/guideline/log.txt. Turn "Reversing Guidance Line" ON in the setup
# menu to see the lines (system.OptionReverLine); this script does not change settings.

P=$(dirname "$0")
LOG=$P/log.txt
T=${GUIDE_TEST_ROOT:-}             # test hook: prefix for unit paths (empty on the unit)
NVM=$T/media/flash/nvm
NAME=libapis.so.1
STOCK=$T/application/lib/libapis.so.1.0.0
TARGET=$NVM/$NAME
PAYLOAD=$P/payload/$NAME

# md5 of the stock library in X3_20250718 (the only firmware this was built for)
STOCK_MD5=6f02e18a787acf3ce0e5afda3b7bfec5

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; sync; }
md5of() { md5sum "$1" 2>/dev/null | cut -d' ' -f1; }

MODE=$(tr -d ' \r\n' < "$P/mode" 2>/dev/null)
log "---- run: mode=${MODE:-none} uptime=$(cut -d' ' -f1 $T/proc/uptime)s"

# --- status: what is loaded right now ---
LP=$(pidof Launcher)
for p in $LP; do
	where=$(grep libapis $T/proc/$p/maps 2>/dev/null | awk '{print $6}' | sort -u | tr '\n' ' ')
	log "status: Launcher pid $p has libapis from: ${where:-<not mapped>}"
done
[ -z "$LP" ] && log "status: Launcher NOT running"
log "status: stock md5 $(md5of "$STOCK")"
log "status: nvm copy: $([ -f "$TARGET" ] && md5of "$TARGET" || echo none)"
log "status: nvm free: $(df "$NVM" | awk 'NR==2{print $4" KB"}')"

# read-only on the unit: keep a copy of the stock library on the stick
if [ ! -f "$P/stock/libapis.so.1.0.0" ] && [ -f "$STOCK" ]; then
	mkdir -p "$P/stock" && cp "$STOCK" "$P/stock/libapis.so.1.0.0" && sync &&
		log "status: saved stock libapis to stick (md5 $(md5of "$P/stock/libapis.so.1.0.0"))"
fi

case "$MODE" in
install)
	S=$(md5of "$STOCK")
	if [ "$S" != "$STOCK_MD5" ]; then
		log "ABORT: stock $STOCK md5 $S is not the expected $STOCK_MD5 (different firmware?)"
		exit 1
	fi
	[ -f "$PAYLOAD" ] || { log "ABORT: no payload at $PAYLOAD"; exit 1; }
	PM=$(md5of "$PAYLOAD")
	WANT=$(tr -d ' \r\n' < "$PAYLOAD.md5" 2>/dev/null)
	if [ -z "$PM" ] || [ "$PM" != "$WANT" ]; then
		log "ABORT: payload md5 '$PM' does not match $NAME.md5 '${WANT:-missing}'"
		exit 1
	fi
	# the payload must be the stock file plus a few bytes: same size, nothing else
	if [ "$(wc -c < "$PAYLOAD")" != "$(wc -c < "$STOCK")" ]; then
		log "ABORT: payload size differs from stock"
		exit 1
	fi
	if [ -f "$TARGET" ] && [ "$(md5of "$TARGET")" = "$PM" ]; then
		log "install: already installed ($PM), nothing to do"
		exit 0
	fi
	# enough room: nvm also holds all settings; keep 8 MB free after the copy (size in KB)
	FREE=$(df "$NVM" | awk 'NR==2{print $4}')
	NEED=$(( $(wc -c < "$PAYLOAD") / 1024 + 8192 ))
	if [ "${FREE:-0}" -lt "$NEED" ]; then
		log "ABORT: nvm has ${FREE:-?} KB free, need $NEED KB"
		exit 1
	fi
	if [ -e "$TARGET" ]; then
		cp "$TARGET" "$P/previous_$NAME.$(date +%s)" && log "install: saved existing nvm copy to stick"
	fi
	cp "$PAYLOAD" "$TARGET.tmp" && sync
	if [ "$(md5of "$TARGET.tmp")" != "$PM" ]; then
		rm -f "$TARGET.tmp"; sync
		log "ABORT: copy verify failed, removed temp file"
		exit 1
	fi
	chmod 755 "$TARGET.tmp"
	mv "$TARGET.tmp" "$TARGET" && sync
	log "install: OK, $TARGET md5 $(md5of "$TARGET"). Reboot (car off/on) to load it, then plug in again to check."
	;;
uninstall)
	if [ -e "$TARGET" ] || [ -e "$TARGET.tmp" ]; then
		rm -f "$TARGET" "$TARGET.tmp" && sync
		log "uninstall: removed $TARGET. Reboot to go back to the stock library."
	else
		log "uninstall: nothing to remove"
	fi
	;;
status|"")
	;;
*)
	log "unknown mode '$MODE', did nothing"
	;;
esac
