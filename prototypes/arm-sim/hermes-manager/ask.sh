#!/usr/bin/env bash
# One escalation by hand, the curl of docs/worker-manager-healer.md §2.3: POST a saved packet to the
# manager, poll its run every 2 s (up to 120 s), print the status, the output and the latency.
#   ./ask.sh samples/esc-us_stuck.json          (reads ./manager.key.env, written by setup.sh)
# The packet gets a fresh incident id first: an Idempotency-Key is kept 24 h and the same key with another
# body answers 409 (H:L39538). Then check the answer the way the worker does:
#   cd .. && PYTHONPATH=. .venv/bin/python -m armsim.manager ask hermes-manager/samples/esc-us_stuck.json
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source "$HERE/manager.key.env"
PKT=${1:?usage: ./ask.sh <packet.json>}
INC=inc-manual-$(uuidgen | tr 'A-Z' 'a-z' | cut -c1-12)
ESC=$(jq -c --arg inc "$INC" '.incident_id = $inc' "$PKT")
T0=$(date +%s)
RUN=$(curl -s "$ARMSIM_MANAGER_URL/v1/runs" -H "Authorization: Bearer $ARMSIM_MANAGER_KEY" \
  -H "Idempotency-Key: $INC" -H "Content-Type: application/json" \
  -d "$(jq -n --arg input "$ESC" --arg inc "$INC" \
        '{input: $input, session_id: $inc,
          instructions: "Use skill arm-incident-manager. Reply with ONLY one arm-fixplan/v1 JSON object."}')" | jq -r .run_id)
echo "run $RUN for $INC"
for _ in $(seq 60); do
  OUT=$(curl -s "$ARMSIM_MANAGER_URL/v1/runs/$RUN" -H "Authorization: Bearer $ARMSIM_MANAGER_KEY")
  ST=$(jq -r .status <<<"$OUT")
  [[ "$ST" == running || "$ST" == started || "$ST" == queued ]] || break
  sleep 2
done
echo "status: $ST after $(( $(date +%s) - T0 )) s"
jq -r '.output // .error' <<<"$OUT"
jq -c '{usage, runtime}' <<<"$OUT"
