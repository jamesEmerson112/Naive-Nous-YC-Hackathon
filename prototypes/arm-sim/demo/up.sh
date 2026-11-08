#!/usr/bin/env bash
# Start everything the stories need on this Mac (sim), or on the Pi with --real:
#   Romulus (Docker, on the Mac), pod-1 registered as a sensor pod, the arm host (sim: with a virtual
#   pod and Olir's podd; real: the SO-101 through Andre's prims, podd already running on the pod), and
#   Kent's edge bridge from podd to Romulus.
#
#   demo/up.sh [--gif]                                   sim, on the Mac
#   ARMSIM_ROMULUS=http://<mac-ip>:8000 demo/up.sh --real   on the Pi (podd must already be up)
set -euo pipefail
SIM="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$SIM/../.." && pwd)"
LOGS="$SIM/runs/logs"; mkdir -p "$LOGS"
URL="${ARMSIM_ROMULUS:-http://localhost:8000}"
VAR="${ARMSIM_POD_VAR:-${TMPDIR:-/tmp}/armsim-pod}"
MODE=sim; EXTRA=()
for a in "$@"; do
  case "$a" in
    --real) MODE=real ;;
    --gif) EXTRA+=(--gif) ;;
  esac
done
PY="$SIM/.venv/bin/python"

if [ "$MODE" = sim ] && ! curl -sf "$URL/" >/dev/null 2>&1; then
  echo "starting Romulus (make up + seed)…"; (cd "$ROOT" && make up && make seed)
fi
curl -sf -X POST "$URL/v1/devices" -H 'Content-Type: application/json' \
  -d '{"id": "pod-1", "kind": "real", "device_class": "sensor_pod", "by": "armsim"}' >/dev/null 2>&1 || true

"$SIM/demo/down.sh" --quiet || true
if [ "$MODE" = real ]; then
  ARGS=(--arm real --pod real --var "${HEALER_VAR:-$ROOT/firmware/healer/var}")
  VAR="${HEALER_VAR:-$ROOT/firmware/healer/var}"
else
  ARGS=(--pod sim --var "$VAR")
fi
(cd "$SIM" && PYTHONPATH="$SIM" nohup "$PY" -u -m armsim.host "${ARGS[@]}" --romulus "$URL" ${EXTRA[@]+"${EXTRA[@]}"} \
  > "$LOGS/host.log" 2>&1 & echo $! > "$LOGS/host.pid")
for _ in $(seq 1 60); do
  (cd "$SIM" && PYTHONPATH="$SIM" "$PY" -c "from armsim.client import call; call('ping')" >/dev/null 2>&1) && break
  sleep 0.5
done
(cd "$ROOT" && nohup "$ROOT/.venv/bin/python" -u -m edge_bridge --healer firmware/healer --var "$VAR" \
  --url "$URL" --device-id pod-1 --quiet 30 > "$LOGS/bridge.log" 2>&1 & echo $! > "$LOGS/bridge.pid")
sleep 5
(cd "$SIM" && PYTHONPATH="$SIM" "$PY" -c "
from armsim.client import call
s = call('status'); print('arm host up:', {'pod': s.get('pod'), 'romulus': (s.get('romulus') or {}).get('device')})")
echo "logs in $LOGS · Romulus $URL · stories: PYTHONPATH=. .venv/bin/python -m armsim.stories a|b|stall"
