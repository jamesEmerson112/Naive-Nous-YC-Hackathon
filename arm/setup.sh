#!/usr/bin/env bash
# One-time setup: Python 3.12 venv in arm/.venv with LeRobot 0.7.0.
set -euo pipefail
cd "$(dirname "$0")"
uv venv .venv --python 3.12
VIRTUAL_ENV=.venv uv pip install -r requirements.txt
[ -f arm.env ] || cp arm.env.example arm.env
echo "Done. Next: plug in both arms, run bin/find-ports.sh, and put the ports in arm/arm.env."
