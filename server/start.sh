#!/usr/bin/env bash
# Serve qwen3.5:4b from this laptop to the Pi over Ethernet, and keep the laptop awake while it runs.
# Any flags go through to serve.py (see `python3 server/serve.py --help`).
set -euo pipefail
cd "$(dirname "$0")"
exec caffeinate -i python3 serve.py "$@"
