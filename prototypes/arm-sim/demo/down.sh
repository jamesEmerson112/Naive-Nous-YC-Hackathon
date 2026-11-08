#!/usr/bin/env bash
# Stop the arm host (it stops its virtual pod and podd) and the edge bridge. Romulus keeps running
# (make down from the repo root stops it).
SIM="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$SIM/runs/logs"
(cd "$SIM" && PYTHONPATH="$SIM" "$SIM/.venv/bin/python" -c "from armsim.client import call; call('shutdown')" \
  >/dev/null 2>&1) || true
for name in bridge host; do
  if [ -f "$LOGS/$name.pid" ]; then
    kill "$(cat "$LOGS/$name.pid")" 2>/dev/null || true
    rm -f "$LOGS/$name.pid"
  fi
done
[ "${1:-}" = "--quiet" ] || echo "arm host and edge bridge stopped (Romulus still up: make down to stop it)"
