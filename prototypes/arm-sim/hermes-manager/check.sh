#!/usr/bin/env bash
# The manager's no-model checks (docs/worker-manager-healer.md §5 step 6): health, capabilities, toolsets
# (the worker's own rule: delegation on, nothing but memory and skills beside it), the skill is listed, and a
# wrong key gets 401. No run, no model call.
#   ./check.sh            (reads ./manager.key.env, written by setup.sh)
# H:L<n> = a line in docs/raw/hermes-llms-full.txt.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source "$HERE/manager.key.env"
U=$ARMSIM_MANAGER_URL; K=$ARMSIM_MANAGER_KEY; fail=0
say() { printf '%-44s %s\n' "$1" "$2"; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$@"; }

# /health is shown unprefixed (H:L39505); with branch (a) U ends in /p/manager, so if the prefixed probe
# doesn't answer 200, probe the shared listener's own /health (inferred: the prefix may not route /health).
h=$(code "$U/health"); hu="$U/health"
if [[ $h != 200 && "$U" == */p/* ]]; then hu="${U%/p/*}/health"; h=$(code "$hu"); fi
say "GET $hu (H:L39505)" "HTTP $h $(curl -s --max-time 5 "$hu" 2>/dev/null | head -c 80)"
[[ $h == 200 ]] || fail=1
c=$(code "$U/v1/capabilities" -H "Authorization: Bearer wrong-key")
say "a wrong key on /v1/capabilities" "HTTP $c (want 401, H:L39844-39860)"; [[ $c == 401 ]] || fail=1
caps=$(curl -s --max-time 5 "$U/v1/capabilities" -H "Authorization: Bearer $K")
say "/v1/capabilities run_submission (H:L39406)" "$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("features",{}).get("run_submission"))' <<<"$caps" 2>/dev/null || echo "not JSON: ${caps:0:80}")"
# The toolsets go through the worker's own check (armsim/manager.py toolset_problems, the rule its startup
# preflight and every ask apply), loaded by file path: armsim/__init__.py imports MuJoCo, which the Mac's
# python3 doesn't have, and manager.py's top level is standard library only.
ts=$(curl -s --max-time 5 "$U/v1/toolsets" -H "Authorization: Bearer $K")
RULE="$HERE/../armsim/manager.py"
if [[ ! -f "$RULE" ]]; then
  say "the worker's toolset rule" "NOT FOUND: $RULE (run check.sh from the arm-sim checkout)"; fail=1
else
python3 - "$ts" "$RULE" <<'PY' || fail=1
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("armsim_manager_rule", sys.argv[2])
rule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rule)
try:
    data = json.loads(sys.argv[1])
except ValueError:
    sys.exit(print("/v1/toolsets: not JSON:", sys.argv[1][:120]) or 1)
sets = data.get("data", data) if isinstance(data, dict) else data
on = sorted(str(s.get("name")) for s in sets if isinstance(s, dict) and s.get("enabled") is not False) \
    if isinstance(sets, list) else []
print("%-44s %s" % ("/v1/toolsets on (H:L39796-39809)", ", ".join(on) or "(none)"))
bad = rule.toolset_problems(data)
if bad:
    print("%-44s %s" % ("NOT ALLOWED (the worker won't ask it)", bad[0]))
    for b in bad[1:]:
        print("%-44s %s" % ("", b))
    sys.exit(print("%-44s %s" % ("fix", "agent.disabled_toolsets / platform_toolsets.api_server (README step 3)")) or 1)
print("%-44s %s" % ("the worker's toolset rule", "passes: only " + rule.ALLOWED_WORDS + " (armsim/manager.py)"))
PY
fi
skills=$(curl -s --max-time 5 "$U/v1/skills" -H "Authorization: Bearer $K")
grep -q arm-incident-manager <<<"$skills" && say "/v1/skills lists arm-incident-manager" yes || { say "/v1/skills lists arm-incident-manager" NO; fail=1; }
[[ $fail == 0 ]] && echo "all checks passed" || { echo "some checks failed"; exit 1; }
