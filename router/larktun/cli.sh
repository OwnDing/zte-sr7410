#!/bin/sh
set -eu
BASE=/Plugin/sr7410-larktun
RUN=/var/sr7410-larktun
[ -x "$RUN/larktun" ] || { echo "Run /Plugin/sr7410-larktun/start.sh first" >&2; exit 1; }
export SSL_CERT_FILE="$BASE/ca-certificates.crt"
export TS_LOGS_DIR="$RUN"
exec "$RUN/larktun" --socket="$RUN/larktund.sock" "$@"
