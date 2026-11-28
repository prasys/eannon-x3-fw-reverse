#!/bin/sh
# Library override installer for the Eanon X3, called from gemn_auto.sh on USB insert.
#
# The UI loads libinfomodule.so by name, and /media/flash/nvm is first in
# LD_LIBRARY_PATH (rootfs init.gui.rc:4), so a copy placed there is loaded instead of
# /application/lib/module/default/libinfomodule.so. Takes effect on the next boot.
# The stock file is never touched; deleting our copy restores stock behaviour.
#
# Controlled by <usb>/x3patch/mode (one word):
#   install    copy payload/libinfomodule.so into nvm (if checks pass)
#   uninstall  remove our copy from nvm
#   status     change nothing, just report
# Every run appends to <usb>/x3patch/log.txt, and (once) copies the stock library to
# <usb>/x3patch/stock/ so a payload can be built without the firmware zip.
# The payload must be built for the exact stock firmware on the unit: its target stock
# md5 (payload/libinfomodule.so.stock_md5, or X3_20250718 for legacy sticks) must match
# the unit's stock library, and its md5 must match payload/libinfomodule.so.md5 or the
# per-firmware KNOWN_MD5 list below.

P=$(dirname "$0")
LOG=$P/log.txt
T=${X3PATCH_TEST_ROOT:-}           # test hook: prefix for unit paths (empty on the unit)
NVM=$T/media/flash/nvm
NAME=libinfomodule.so
STOCK=$T/application/lib/module/default/$NAME
TARGET=$NVM/$NAME
PAYLOAD=$P/payload/$NAME

# md5s of stock libinfomodule.so in supported firmwares:
#   f00b52a7... = X3_20250718 (Jul 2025)
#   6a5ce057... = X3_20250812 (Aug 12 2025 / 7047-XBB_UI02)
STOCK_MD5_20250718=f00b52a739bb333307ee9a841bd02369
STOCK_MD5_20250812=6a5ce057b32f19576dfd8b60645d3923
SUPPORTED_STOCK_MD5="$STOCK_MD5_20250718 $STOCK_MD5_20250812"

# payloads we know about per firmware:
# X3_20250718: stock copy, patch B (FINDINGS 9), string fixes (FINDINGS 12), patch C builds
KNOWN_MD5_20250718="f00b52a739bb333307ee9a841bd02369 fae35255cfc28d530fa100af5cab4c4d c8fceef18c51ab983634744470663672 573843500395e13431fde51d2d5f1422 c749a7ef61fd4f1b61bbcb6f5884e00e 8379496265baa6a790f6f1998e6cc051 cf3cb416b72f7fb4d576e95095c2b7a7"
# X3_20250812 (FINDINGS 18.2): stock copy, patch B, B+strings, B+strings+C
KNOWN_MD5_20250812="6a5ce057b32f19576dfd8b60645d3923 b6e16e0272070e2bbe0cbd4f2ee8cc9d e86ad71d591090a4bed9becb41f9f3ca e55d9270791f0402fa85975c6776dca0"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; sync; }
md5of() { md5sum "$1" 2>/dev/null | cut -d' ' -f1; }

MODE=$(tr -d ' \r\n' < "$P/mode" 2>/dev/null)
log "---- run: mode=${MODE:-none} uptime=$(cut -d' ' -f1 "$T/proc/uptime")s"

# --- status: what is loaded right now ---
LP=$(pidof Launcher)
for p in $LP; do
	where=$(grep "$NAME" "$T/proc/$p/maps" 2>/dev/null | awk '{print $6}' | sort -u | tr '\n' ' ')
	log "status: Launcher pid $p has $NAME from: ${where:-<not mapped>}"
done
[ -z "$LP" ] && log "status: Launcher NOT running"
log "status: nvm copy: $([ -f "$TARGET" ] && md5of "$TARGET" || echo none)"
log "status: nvm free: $(df "$NVM" | awk 'NR==2{print $4" KB"}')"

# read-only on the unit: keep a copy of the stock library on the stick for building
if [ ! -f "$P/stock/$NAME" ] && [ -f "$STOCK" ]; then
	mkdir -p "$P/stock" && cp "$STOCK" "$P/stock/$NAME" && sync &&
		log "status: saved stock $NAME to stick (md5 $(md5of "$P/stock/$NAME"))"
fi

case "$MODE" in
install)
	S=$(md5of "$STOCK")
	case " $SUPPORTED_STOCK_MD5 " in
	*" $S "*) ;;
	*)
		log "ABORT: stock $STOCK md5 ${S:-missing} is not a supported firmware ($SUPPORTED_STOCK_MD5)"
		exit 1
		;;
	esac

	FOR_STOCK=$(tr -d ' \r\n' < "$PAYLOAD.stock_md5" 2>/dev/null)
	[ -z "$FOR_STOCK" ] && FOR_STOCK=$STOCK_MD5_20250718
	if [ "$S" != "$FOR_STOCK" ]; then
		log "ABORT: payload was built for stock md5 $FOR_STOCK, but unit has $S"
		exit 1
	fi

	case "$S" in
	"$STOCK_MD5_20250718") KNOWN_MD5=$KNOWN_MD5_20250718 ;;
	"$STOCK_MD5_20250812") KNOWN_MD5=$KNOWN_MD5_20250812 ;;
	*) KNOWN_MD5="" ;;
	esac

	[ -f "$PAYLOAD" ] || { log "ABORT: no payload at $PAYLOAD"; exit 1; }
	PM=$(md5of "$PAYLOAD")
	[ -n "$PM" ] || { log "ABORT: could not md5 $PAYLOAD"; exit 1; }
	WANT=$(tr -d ' \r\n' < "$PAYLOAD.md5" 2>/dev/null)
	case " $KNOWN_MD5 $WANT " in
	*" $PM "*) ;;
	*) log "ABORT: payload md5 $PM matches neither $NAME.md5 (${WANT:-missing}) nor a known build for stock $S"; exit 1 ;;
	esac
	if [ -f "$TARGET" ] && [ "$(md5of "$TARGET")" = "$PM" ]; then
		log "install: already installed ($PM), nothing to do"
		exit 0
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
	log "install: OK, $TARGET md5 $(md5of "$TARGET"). Reboot (car off/on) to load it."
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
