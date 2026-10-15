#!/bin/sh
# Launch the unchanged Studio flow on an isolated copy of the gw_cd4 trace.
# Usage: sh tools/gw_cd4_demo.sh [port] [--reset]
set -eu
repository=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
port=${1:-8767}
release=
for candidate in ${DISCOVERYAGENT_DEV_ROOT:-} "$HOME/da-dev" "$HOME/.local/share/discoveryagent"; do
    [ -n "$candidate" ] || continue
    found=$(ls -d "$candidate"/releases/*/ 2>/dev/null | head -1 || true)
    if [ -n "$found" ]; then release=${found%/}; break; fi
done
if [ -z "$release" ]; then
    printf '%s\n' 'No development install found. See tools/dev_server.sh.' >&2
    exit 1
fi
export DISCOVERYAGENT_DATA_DIR="$HOME/DiscoveryAgent-gw-cd4-interactive-demo"
"$release/envs/frontend/bin/python" "$repository/tools/prepare_gw_cd4_demo.py" ${2:+"$2"}
printf '\nOpen http://127.0.0.1:%s/projects/gw-cd4-demo/hypotheses\n\n' "$port"
exec sh "$repository/tools/dev_server.sh" "$port"
