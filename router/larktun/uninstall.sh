#!/bin/sh
set -eu
BASE=/Plugin/sr7410-larktun
RUN=/var/sr7410-larktun
[ "$(id -u)" = 0 ] && [ -f "$BASE/VERSION" ] && [ ! -L "$BASE" ] || exit 1
"$BASE/autostart.sh" disable
"$BASE/stop.sh"
rm -rf "$BASE" "$RUN"
echo "Larktun removed, including node identity. SSH and its boot entry remain."
