#!/bin/sh
set -eu
umask 077
BASE=/Plugin/sr7410-larktun
RUN=/var/sr7410-larktun
mkdir -p "$RUN"
chmod 700 "$BASE" "$RUN"
if [ -f "$RUN/larktund.pid" ]; then
    pid=$(cat "$RUN/larktund.pid")
    case "$pid" in ''|*[!0-9]*) echo "Invalid PID file" >&2; exit 1;; esac
    if kill -0 "$pid" 2>/dev/null; then
        tr '\000' ' ' < "/proc/$pid/cmdline" | grep -Fq "$RUN/larktund" || {
            echo "PID belongs to another process" >&2; exit 1;
        }
        exit 0
    fi
    rm -f "$RUN/larktund.pid"
fi
mkdir "$RUN/start.lock" 2>/dev/null || {
    echo "Startup already running or stale start.lock exists" >&2; exit 1;
}
trap 'rmdir "$RUN/start.lock"' EXIT
# shellcheck source=/dev/null
. "$BASE/sizes.conf"
unpack_binary() {
    name=$1
    expected=$2
    if [ ! -x "$RUN/$name" ]; then
        gzip -dc "$BASE/$name.gz" > "$RUN/$name.new"
        [ "$(wc -c < "$RUN/$name.new")" -eq "$expected" ] || {
            rm -f "$RUN/$name.new"; echo "Unexpected binary size" >&2; exit 1;
        }
        chmod 700 "$RUN/$name.new"
        mv "$RUN/$name.new" "$RUN/$name"
    fi
}
unpack_binary larktun "$LARKTUN_BYTES"
unpack_binary larktund "$LARKTUND_BYTES"
export SSL_CERT_FILE="$BASE/ca-certificates.crt"
export TS_LOGS_DIR="$RUN"
export GOMEMLIMIT=128MiB
"$RUN/larktund" --tun=userspace-networking --state="$BASE/larktund.state" \
    --socket="$RUN/larktund.sock" --port=41641 \
    </dev/null > "$RUN/daemon.log" 2>&1 &
pid=$!
echo "$pid" > "$RUN/larktund.pid"
attempt=0
until [ -S "$RUN/larktund.sock" ]; do
    kill -0 "$pid" || { echo "Daemon failed; inspect /var/sr7410-larktun/daemon.log" >&2; exit 1; }
    attempt=$((attempt + 1))
    [ "$attempt" -lt 30 ] || { echo "Daemon socket not ready" >&2; exit 1; }
    sleep 1
done
