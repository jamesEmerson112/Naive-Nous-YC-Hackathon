#!/usr/bin/env bash
# One escalation by hand, the curl of docs/worker-manager-healer.md §2.3: POST a saved packet to the
# manager, poll its run every 2 s (up to 120 s), print the status, the output and the latency, and say
# whether the output is one JSON object (the fix plan) or text (a "working on it" status line, say).
#   ./ask.sh samples/esc-us_stuck.json          (reads ./manager.key.env, written by setup.sh)
# Needs curl and python3 (no jq). The POST body is the worker's own (armsim/manager.py): input,
# session_id, instructions and "conversation_history": [] (caller-supplied history runs delegation
# synchronously, H:L39622-39628). The packet gets a fresh incident id first: an Idempotency-Key is kept
# 24 h and the same key with another body answers 409 (H:L39538). Then check the answer the way the worker does:
#   cd .. && PYTHONPATH=. .venv/bin/python -m armsim.manager ask hermes-manager/samples/esc-us_stuck.json
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source "$HERE/manager.key.env"
PKT=${1:?usage: ./ask.sh <packet.json>}
command -v python3 >/dev/null || { echo "ask.sh needs python3"; exit 1; }
INC=inc-manual-$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')
BODY=$(python3 - "$PKT" "$INC" <<'PY'
import json, sys
esc = json.load(open(sys.argv[1]))
esc["incident_id"] = sys.argv[2]
print(json.dumps({"input": json.dumps(esc), "session_id": sys.argv[2], "conversation_history": [],
                  "instructions": "Use skill arm-incident-manager. The input is one arm-escalation/v1 packet. Reply "
                                  "with ONLY one arm-fixplan/v1 JSON object whose action is in the packet's allowed_actions."}))
PY
)
field() { python3 -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); v=d.get(sys.argv[1]) if isinstance(d, dict) else None; print(v if isinstance(v, str) else json.dumps(v))' "$1"; }
T0=$(date +%s)
RESP=$(curl -s "$ARMSIM_MANAGER_URL/v1/runs" -H "Authorization: Bearer $ARMSIM_MANAGER_KEY" \
  -H "Idempotency-Key: $INC" -H "Content-Type: application/json" -d "$BODY")
RUN=$(field run_id <<<"$RESP")
[[ -n "$RUN" && "$RUN" != null ]] || { echo "no run_id: ${RESP:0:200}"; exit 1; }
echo "run $RUN for $INC"
ST=unknown; OUT='{}'
for _ in $(seq 60); do
  OUT=$(curl -s "$ARMSIM_MANAGER_URL/v1/runs/$RUN" -H "Authorization: Bearer $ARMSIM_MANAGER_KEY")
  ST=$(field status <<<"$OUT")
  [[ "$ST" == running || "$ST" == started || "$ST" == queued ]] || break
  sleep 2
done
echo "status: $ST after $(( $(date +%s) - T0 )) s"
python3 - "$OUT" <<'PY'
import json, sys
d = json.loads(sys.argv[1] or "{}")
out = d.get("output")
print(out if out is not None else d.get("error"))
print(json.dumps({"usage": d.get("usage"), "runtime": d.get("runtime")}))
text = (out or "").strip()
if text.startswith("```") and text.endswith("```"):           # one code fence is tolerated, as check_plan does
    text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
try:
    obj = json.loads(text)
    print("output is one JSON object" if isinstance(obj, dict) else f"output is JSON but a {type(obj).__name__}, not one object")
except ValueError:
    print("output is NOT one JSON object (a status line? README: 'If the run answers \"working on it\"')")
PY
