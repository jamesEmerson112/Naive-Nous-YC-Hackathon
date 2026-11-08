#!/usr/bin/env bash
# Set up the `manager` Hermes profile on the Mac that runs Hermes v0.23.0 (docs/worker-manager-healer.md §2.1-2.2).
# H:L<n> = a line in docs/raw/hermes-llms-full.txt.
#
#   hermes gateway status                  # pick ONE branch first (both bind 8642, so never run both)
#   ./setup.sh a <mac-ip>                  # (a) a default gateway is already running: serve /p/manager on it
#   ./setup.sh b <mac-ip>                  # (b) no gateway running: the manager profile runs its own
#
# <mac-ip> is the address the arm host (the Pi, or this Mac in sim) reaches; 127.0.0.1 for sim on this Mac.
# What it does: creates the profile (--clone keeps the ChatGPT-plan login, H:L9050), gives a fresh clone a
# blank memory and its own SOUL.md (--clone copies the default profile's, H:L9028), installs the skill,
# writes a fresh API_SERVER_KEY into the profile's .env, writes ./manager.key.env (chmod 600) for the worker,
# and applies config.manager.yaml with `hermes -p manager config set` (H:L54590, H:L3858-3872; lists go as
# inline literals, H:L7581-7585), and locks the skill (`curator lock`, H:L13186-13190). It does NOT touch the
# default profile's .env (branch a prints what it must hold). The worker checks the result itself:
# `--manager hermes` won't start, or ask, unless /v1/toolsets shows delegation on and nothing but memory and
# skills beside it (armsim/worker.py preflight; check.sh applies the same rule). Needs python3.
set -euo pipefail
branch=${1:-}; ip=${2:-}; port=${PORT:-8642}
if [[ "$branch" != a && "$branch" != b || -z "$ip" ]]; then
  sed -n '2,17p' "$0"; exit 2
fi
HERE=$(cd "$(dirname "$0")" && pwd)
HOME_H=${HERMES_HOME:-$HOME/.hermes}
P=$HOME_H/profiles/manager

command -v hermes >/dev/null || { echo "hermes is not on PATH"; exit 1; }
command -v python3 >/dev/null || { echo "python3 is not on PATH (it reads config.manager.yaml)"; exit 1; }
echo "== $(hermes --version 2>&1 | head -1)   (the design was written for v0.23.0)"

echo "== 1. the profile"
if [[ -d "$P" ]]; then
  echo "   $P already exists: keeping it, and its memories/ and SOUL.md as they are (README step 2)"
else
  hermes profile create manager --clone \
    --description "Arm-company incident manager: reads one arm-escalation/v1 packet, answers with one arm-fixplan/v1 JSON."
  # --clone copies SOUL.md and memories/MEMORY.md, memories/USER.md from the default profile (H:L9028): the
  # arm commander's. "For a blank memory ... delete the two files afterwards; the agent never falls back to
  # another profile's memory when they are absent" (H:L9028). The cloned copies are kept beside them.
  for f in memories/MEMORY.md memories/USER.md; do
    if [[ -f "$P/$f" ]]; then mv "$P/$f" "$P/$f.cloned-from-default"; echo "   moved the cloned $f aside (blank memory)"; fi
  done
  if [[ -f "$P/SOUL.md" ]]; then mv "$P/SOUL.md" "$P/SOUL.md.cloned-from-default"; fi
  cat > "$P/SOUL.md" <<'SOUL'
You are the arm company's incident manager. You never act on anything yourself: you read one
arm-escalation/v1 packet and answer with exactly one arm-fixplan/v1 JSON object, using the skill
arm-incident-manager. Your only tools are delegate_task (your analysts and the critic), memory, and the
skill tools, to read your skill with skill_view; never change a skill. Plain code checks your answer and
carries it out; if you are unsure, answer park or handoff_human.
SOUL
  echo "   wrote the manager's own SOUL.md (the cloned one is SOUL.md.cloned-from-default)"
fi

echo "== 2. the skill"
mkdir -p "$P/skills/arm-incident-manager"
cp "$HERE/skills/arm-incident-manager/SKILL.md" "$P/skills/arm-incident-manager/SKILL.md"
echo "   $P/skills/arm-incident-manager/SKILL.md"
# The manager has the skills toolset (to read the skill), and with it skill_manage: lock this skill so no
# session can change it ("A locked skill stays readable and auto-loadable, but skill_manage, write_file, patch
# and terminal all refuse to change it", H:L13186-13190). config.manager.yaml also stages every skill write.
if hermes -p manager curator lock arm-incident-manager >/dev/null 2>&1; then
  echo "   locked arm-incident-manager (hermes -p manager curator lock)"
else
  echo "   WARNING: couldn't lock the skill (hermes -p manager curator lock arm-incident-manager); skills.write_approval"
  echo "   still stages every skill write. Lock it by hand (or in Desktop) once the profile works."
fi

echo "== 3. the API server key in $P/.env"
# --clone copies the default profile's .env (H:L9028), so drop any API_SERVER_* it brought along: a named
# prefix must have its own key, and the default's key is refused there (H:L39844-39860).
touch "$P/.env"
grep -v '^API_SERVER_' "$P/.env" > "$P/.env.new" || true
mv "$P/.env.new" "$P/.env"
key=$(openssl rand -hex 24)
echo "API_SERVER_KEY=$key" >> "$P/.env"
if [[ "$branch" == b ]]; then
  printf 'API_SERVER_ENABLED=true\nAPI_SERVER_HOST=%s\nAPI_SERVER_PORT=%s\n' "$ip" "$port" >> "$P/.env"
  url="http://$ip:$port"
else
  url="http://$ip:$port/p/manager"
fi
chmod 600 "$P/.env"
printf 'ARMSIM_MANAGER_URL=%s\nARMSIM_MANAGER_KEY=%s\n' "$url" "$key" > "$HERE/manager.key.env"
chmod 600 "$HERE/manager.key.env"
echo "   wrote API_SERVER_KEY (not shown) and $HERE/manager.key.env (the worker's URL and key; never commit it)"

echo "== 4. config.manager.yaml into $P/config.yaml (the cloned model: block is kept)"
# One `config set` per key: dotted settings go to config.yaml (H:L3872), a list as an inline JSON literal
# (H:L7581-7585). An existing key's value is replaced, so there is no second copy to merge by hand.
pairs=$(python3 "$HERE/config_pairs.py" "$HERE/config.manager.yaml") || {
  echo "   couldn't read config.manager.yaml: merge it by hand (README step 3)"; exit 1; }
while IFS=$'\t' read -r k v; do
  [[ -n "$k" ]] || continue
  if hermes -p manager config set "$k" "$v" >/dev/null; then
    printf '   %-40s %s\n' "$k" "$v"
  else
    echo "   FAILED: hermes -p manager config set $k '$v'. Merge config.manager.yaml by hand (README step 3), then ./check.sh"
    exit 1
  fi
done <<<"$pairs"
echo "   check:  hermes -p manager config show | grep -E 'gateway_mode|disabled_toolsets|max_concurrent'"
echo

if [[ "$branch" == a ]]; then
  cat <<EOF
== 5a. the default profile's .env ($HOME_H/.env) must hold (H:L39870-39884; inferred: the listener is shared):
     API_SERVER_ENABLED=true
     API_SERVER_HOST=$ip
     API_SERVER_PORT=$port
   then:  hermes gateway restart          (H:L1271)
   URL:   $url/v1/runs
EOF
else
  cat <<EOF
== 5b. start the manager's own gateway (H:L995, H:L4211), in its own terminal:
     hermes -p manager gateway run
   URL:   $url/v1/runs
EOF
fi
cat <<EOF

== 6. check it (no model call):  ./check.sh
== 7. one real run on a saved packet:  ./ask.sh samples/esc-us_stuck.json   (its output must be one JSON object)
EOF
