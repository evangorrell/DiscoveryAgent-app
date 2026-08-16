#!/bin/sh
# Run the DiscoveryAgent frontend against this checkout.
#
#   tools/dev_server.sh [port]        # default 8765
#
# Frontend and backend code both come from the working tree; the virtualenvs
# come from a development install (default ~/da-dev, override with
# DISCOVERYAGENT_DEV_ROOT). Create one first if you have not:
#
#   python3 tools/bootstrap_development_install.py --install-root ~/da-dev \
#     --data-root "$HOME/Library/Application Support/DiscoveryAgent"
#
# See docs/development_server_launch.md.
set -eu

repository=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
install_root=${DISCOVERYAGENT_DEV_ROOT:-$HOME/da-dev}
port=${1:-8765}

release=$(ls -d "$install_root"/releases/*/ 2>/dev/null | head -1 || true)
if [ -z "$release" ]; then
    printf '%s\n' "No development install under $install_root." >&2
    printf '%s\n' "Run tools/bootstrap_development_install.py first (see docs/development_server_launch.md)." >&2
    exit 1
fi
release=${release%/}

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    printf '%s\n' "Warning: ANTHROPIC_API_KEY is unset — the UI will load but rounds cannot start." >&2
fi

printf '%s\n' "DiscoveryAgent (source) on http://127.0.0.1:$port — Ctrl+C to stop"

# PYTHONDONTWRITEBYTECODE keeps __pycache__ out of application/, which the
# snapshot byte-identity checks in the bootstrap and artifact builds reject.
exec env \
    DISCOVERYAGENT_BACKEND_ROOT="$repository/application/backend" \
    DISCOVERYAGENT_BACKEND_PYTHON="$release/envs/backend/bin/python" \
    PYTHONPATH="$repository/application/frontend" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    "$release/envs/frontend/bin/python" -m da_selection_ui --port "$port"
