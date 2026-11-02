#!/bin/sh
# Share this repository's owned SSH boot bundle, preserving its rollback rules.
set -eu
umask 077
BASE=/Plugin/sr7410-larktun
SSH_BASE=/Plugin/sr7410-ssh
OVERRIDE=/Plugin/apps/dlspeed_rt.tar.gz
STATE="$SSH_BASE/boot-created.tar.gz"
action=status
[ "$#" -eq 0 ] || action=$1
case "$action" in
enable)
    [ "$#" -eq 2 ] || { echo "Use scripts/larktun_tool.py enable-autostart" >&2; exit 1; }
    source_dir=$2
    case "$source_dir" in /var/sr7410-larktun-setup-*) ;; *) exit 1;; esac
    [ -f "$BASE/VERSION" ] && [ -f "$SSH_BASE/autostart.enabled" ] || {
        echo "Enable SSH autostart first" >&2; exit 1;
    }
    if [ ! -f "$OVERRIDE" ] || [ -L "$OVERRIDE" ] ||
        [ ! -f "$STATE" ] || [ -L "$STATE" ] ||
        ! cmp -s "$OVERRIDE" "$STATE" ||
        ! cmp -s "$OVERRIDE" "$source_dir/previous.tar.gz"; then
        echo "Boot bundle is not owned or changed after host validation" >&2
        exit 1
    fi
    cmp -s /etc/dlspeed_rt.tar.gz "$source_dir/stock.tar.gz" || {
        echo "Stock component changed after host validation" >&2; exit 1;
    }
    free=$(df -k /Plugin | awk 'END {print $4}')
    [ "$free" -ge 3072 ] || { echo "3 MiB free flash needed to refresh boot bundle" >&2; exit 1; }
    [ ! -e "$STATE.new" ] && [ ! -L "$STATE.new" ] &&
        [ ! -e "$OVERRIDE.new" ] && [ ! -L "$OVERRIDE.new" ] || exit 1
    cp "$source_dir/override.tar.gz" "$STATE.new"
    cp "$source_dir/override.tar.gz" "$OVERRIDE.new"
    chmod 600 "$STATE.new" "$OVERRIDE.new"
    mv "$STATE.new" "$STATE"
    mv "$OVERRIDE.new" "$OVERRIDE"
    touch "$BASE/autostart.enabled"
    echo "Larktun autostart enabled through shared SSH boot entry"
    ;;
disable)
    rm -f "$BASE/autostart.enabled"
    echo "Larktun autostart disabled; SSH entry retained"
    ;;
status)
    if [ -f "$BASE/autostart.enabled" ] && [ -f "$SSH_BASE/autostart.enabled" ] &&
        [ -f "$OVERRIDE" ] && cmp -s "$OVERRIDE" "$STATE"; then
        echo "Larktun autostart enabled (reboot verification required)"
    else
        echo "Larktun autostart disabled or shared SSH entry unavailable"
    fi
    ;;
*) echo "Usage: autostart.sh enable|disable|status" >&2; exit 2;;
esac
