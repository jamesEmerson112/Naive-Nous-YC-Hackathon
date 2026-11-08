#!/usr/bin/env bash
# The manager's no-model checks (docs/worker-manager-healer.md §5 step 6): health, capabilities, toolsets
# (no terminal, file or so101_arm), the skill is listed, and a wrong key gets 401. No run, no model call.
#   ./check.sh            (reads ./manager.key.env, written by setup.sh)
# H:L<n> = a line in docs/raw/hermes-llms-full.txt.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source "$HERE/manager.key.env"
U=$ARMSIM_MANAGER_URL; K=$ARMSIM_MANAGER_KEY; fail=0
say() { printf '%-44s %s\n' "$1" "$2"; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$@"; }

say "GET $U/health (H:L39505)" "$(curl -s --max-time 5 "$U/health" || echo unreachable)"
c=$(code "$U/v1/capabilities" -H "Authorization: Bearer wrong-key")
say "a wrong key on /v1/capabilities" "HTTP $c (want 401, H:L39844-39860)"; [[ $c == 401 ]] || fail=1
caps=$(curl -s --max-time 5 "$U/v1/capabilities" -H "Authorization: Bearer $K")
say "/v1/capabilities run_submission (H:L39406)" "$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("features",{}).get("run_submission"))' <<<"$caps" 2>/dev/null || echo "not JSON: ${caps:0:80}")"
ts=$(curl -s --max-time 5 "$U/v1/toolsets" -H "Authorization: Bearer $K")
python3 - "$ts" <<'PY' || fail=1
import json, sys
try:
    sets = json.loads(sys.argv[1])
except ValueError:
    sys.exit(print("/v1/toolsets: not JSON:", sys.argv[1][:120]) or 1)
sets = sets.get("data", sets) if isinstance(sets, dict) else sets
on = {s["name"]: s.get("tools", []) for s in sets if s.get("enabled")}
tools = {t for ts in on.values() for t in ts}
bad = [n for n in ("terminal", "code_execution", "file", "browser", "so101_arm", "mcp-naive_board") if n in on]
bad += [t for t in tools if t.startswith("arm_") or t in ("terminal", "write_file", "patch")]
print(f"{'/v1/toolsets enabled (H:L39796)':44s} {', '.join(sorted(on)) or '(none)'}")
print(f"{'delegate_task available':44s} {'delegate_task' in tools}")
if bad:
    sys.exit(print(f"{'NOT DISABLED (fix agent.disabled_toolsets)':44s} {', '.join(sorted(set(bad)))}") or 1)
PY
skills=$(curl -s --max-time 5 "$U/v1/skills" -H "Authorization: Bearer $K")
grep -q arm-incident-manager <<<"$skills" && say "/v1/skills lists arm-incident-manager" yes || { say "/v1/skills lists arm-incident-manager" NO; fail=1; }
[[ $fail == 0 ]] && echo "all checks passed" || { echo "some checks failed"; exit 1; }
