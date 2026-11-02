#!/bin/sh
set -eu
umask 077
BASE=/Plugin/sr7410-larktun
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = aarch64 ] || {
    echo "Root aarch64 shell required" >&2; exit 1;
}
[ ! -e "$BASE" ] && [ ! -L "$BASE" ] && [ ! -e /Plugin/larktun ] || {
    echo "An existing Larktun installation was found; no overwrite or migration attempted" >&2; exit 1;
}
[ -f /Plugin/sr7410-ssh/VERSION ] || {
    echo "Install this repository's SSH runtime first" >&2; exit 1;
}
for command in gzip wc cmp tar awk tr readlink; do
    command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done
available=$(df -k /Plugin | awk 'END {print $4}')
compressed=$(($(wc -c < larktun.gz) + $(wc -c < larktund.gz) + $(wc -c < ca-certificates.crt)))
needed=$(((compressed + 1023) / 1024 + 4096))
case "$available" in ''|*[!0-9]*) exit 1;; esac
[ "$available" -ge "$needed" ] || { echo "Insufficient flash space; need runtime plus 4 MiB reserve" >&2; exit 1; }
memory=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
case "$memory" in ''|*[!0-9]*) exit 1;; esac
[ "$memory" -ge 131072 ] || { echo "At least 128 MiB available RAM required during install" >&2; exit 1; }
mkdir -m 700 "$BASE"
cp -a . "$BASE/"
chmod 700 "$BASE/"*.sh
# Free RAM staging copies before expanding the executables.
rm -f larktun.gz larktund.gz ../payload.tar.gz
"$BASE/start.sh"
echo "Larktun installed; login and autostart are not yet enabled"
