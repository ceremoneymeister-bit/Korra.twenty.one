#!/usr/bin/env bash
# Host-owned durable updater. Install alongside up.sh; keep this directory root-owned.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$HERE/updater.py" "$@"
