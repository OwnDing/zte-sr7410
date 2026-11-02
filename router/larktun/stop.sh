#!/bin/sh
set -eu
RUN=/var/sr7410-larktun
[ -f "$RUN/larktund.pid" ] || exit 0
pid=$(cat "$RUN/larktund.pid")
case "$pid" in ''|*[!0-9]*) echo "Invalid PID file" >&2; exit 1;; esac
if kill -0 "$pid" 2>/dev/null; then
    tr '\000' ' ' < "/proc/$pid/cmdline" | grep -Fq "$RUN/larktund" || {
        echo "Refusing to stop another process" >&2; exit 1;
    }
    kill "$pid"
    attempt=0
    while kill -0 "$pid" 2>/dev/null; do
        attempt=$((attempt + 1))
        [ "$attempt" -lt 15 ] || { echo "Daemon still running" >&2; exit 1; }
        sleep 1
    done
fi
rm -f "$RUN/larktund.pid" "$RUN/larktund.sock"
