#!/usr/bin/env bash
# Run ON THE PI, from anywhere in the repo checkout. Installs the edge bridge as a systemd service
# (edge-bridge) that reports the pod healer to the laptop's cloud at CLOUD_URL.
#
#   bash server/pi/install_bridge.sh                                   # CLOUD_URL=http://192.168.50.1:8000
#   CLOUD_URL=http://192.168.50.1:8000 POD_DEVICE_ID=pod-1 bash server/pi/install_bridge.sh
#
# Needs internet once (uv fetches Python 3.12 and the packages). After that the service runs from
# .venv and never downloads anything. It starts at boot, restarts if it dies, and keeps running
# while the laptop is unreachable: incidents queue in data/edge_bridge/<id>/outbox and go out
# when the cloud answers again (fake_device/outbox.py).
set -euo pipefail

REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
CLOUD_URL="${CLOUD_URL:-http://192.168.50.1:8000}"
POD_DEVICE_ID="${POD_DEVICE_ID:-pod-1}"
RUN_USER="${SUDO_USER:-$USER}"
UNIT=/etc/systemd/system/edge-bridge.service
cd "$REPO"

if ! command -v uv >/dev/null; then
  echo "uv is missing. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh  (then open a new shell)" >&2
  exit 1
fi

echo "== installing Python 3.12 and the edge bridge into $REPO/.venv"
uv python install 3.12
# Only the bridge and what it imports (contracts, fake_device's client and outbox, httpx): none
# of the cloud's Postgres, FastAPI or LLM dependencies.
uv sync --package edge-bridge --no-dev --frozen
.venv/bin/python -c "import edge_bridge, fake_device, romulus_contracts" \
  || { echo "edge_bridge didn't import from .venv" >&2; exit 1; }

echo "== checking the cloud at $CLOUD_URL"
if curl -fs -m 5 "$CLOUD_URL/healthz" >/dev/null; then
  echo "ok: the cloud answers"
else
  echo "warning: no answer from $CLOUD_URL/healthz. Installing anyway: the bridge queues until it answers."
  echo "         On the laptop: ./server/cloud_up.sh"
fi

echo "== writing $UNIT (runs as $RUN_USER)"
sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=Romulus edge bridge: pod healer records -> cloud ($CLOUD_URL)
# No hard dependency on the network: with no link it keeps queueing and retries.
After=network.target

[Service]
User=$RUN_USER
WorkingDirectory=$REPO
Environment=CLOUD_URL=$CLOUD_URL
Environment=POD_DEVICE_ID=$POD_DEVICE_ID
Environment=PYTHONUNBUFFERED=1
ExecStart=$REPO/.venv/bin/python -m edge_bridge
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now edge-bridge
sleep 3
systemctl --no-pager --lines=8 status edge-bridge || true
cat <<EOF

Installed. Useful commands:
  journalctl -u edge-bridge -f          # live log: check-ins, queued incidents, retries
  sudo systemctl restart edge-bridge    # after a git pull
  ls data/edge_bridge/$POD_DEVICE_ID/outbox   # reports still waiting for the cloud
  sudo systemctl disable --now edge-bridge    # stop it for good
The bridge needs podd running (firmware/healer/bin/podd); until then it checks in as failing.
EOF
