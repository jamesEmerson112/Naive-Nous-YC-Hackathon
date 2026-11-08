#!/usr/bin/env bash
# Start the Romulus cloud on this laptop so the Pi's edge bridge can report to it over Ethernet.
#   - db, api and worker via Compose, the API on 127.0.0.1:8000 and <ethernet-ip>:8000 (not Wi-Fi)
#   - firmware v1, harness v1 and the demo devices (make seed; harmless to repeat)
#   - pod-1 registered as a sensor pod, so its check-ins don't make it an arm
# Safe to rerun. Env: PI_IP (default 192.168.50.2), ETH_IP (default: found from PI_IP), API_PORT.
set -euo pipefail
cd "$(dirname "$0")/.."

PI_IP="${PI_IP:-192.168.50.2}"
API_PORT="${API_PORT:-8000}"
if [ -z "${ETH_IP:-}" ]; then
  ETH_IP="$(python3 -c 'import socket,sys; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect((sys.argv[1],9)); print(s.getsockname()[0])' "$PI_IP")"
fi
case "$ETH_IP" in 127.*|"") echo "no route to the Pi at $PI_IP; is the Ethernet cable in? Or set ETH_IP." >&2; exit 1;; esac
export ETH_IP API_PORT

holder="$(lsof -nP -iTCP:"$API_PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $1" (pid "$2")"}' | sort -u | tr '\n' ' ' || true)"
if [ -n "$holder" ] && ! docker compose ps --status running api 2>/dev/null | grep -q api; then
  echo "port $API_PORT is taken by: $holder" >&2
  echo "find the container with: docker ps --filter publish=$API_PORT  (then docker stop it)" >&2
  exit 1
fi

COMPOSE=(docker compose -f compose.yaml -f server/compose.ethernet.yaml)
"${COMPOSE[@]}" up --build -d db api worker

printf "waiting for the API"
for _ in $(seq 120); do
  curl -fs "http://127.0.0.1:$API_PORT/healthz" >/dev/null && break
  printf "."; sleep 1
done
echo
curl -fs "http://127.0.0.1:$API_PORT/healthz" >/dev/null || { echo "API never came up; see: docker compose logs api" >&2; exit 1; }

"${COMPOSE[@]}" exec -T api python -m romulus_cloud.cli seed

code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$API_PORT/v1/devices" \
  -H 'Content-Type: application/json' \
  -d '{"id": "pod-1", "kind": "real", "device_class": "sensor_pod", "by": "olir", "why": "STM32 sensor pod on the bench Pi"}')"
case "$code" in
  201) echo "registered pod-1 as a sensor pod" ;;
  *)   curl -fs "http://127.0.0.1:$API_PORT/v1/devices/pod-1" >/dev/null \
         && echo "pod-1 already registered" \
         || { echo "registering pod-1 failed (HTTP $code)" >&2; exit 1; } ;;
esac

curl -fs "http://$ETH_IP:$API_PORT/healthz" >/dev/null \
  && echo "cloud reachable at http://$ETH_IP:$API_PORT (the Pi's CLOUD_URL); dashboard http://localhost:$API_PORT/" \
  || { echo "API answers on localhost but not on $ETH_IP:$API_PORT" >&2; exit 1; }
