#!/usr/bin/env bash
# Set up the `manager` Hermes profile on the Mac that runs Hermes v0.23.0 (docs/worker-manager-healer.md §2.1-2.2).
# H:L<n> = a line in docs/raw/hermes-llms-full.txt.
#
#   hermes gateway status                  # pick ONE branch first (both bind 8642, so never run both)
#   ./setup.sh a <mac-ip>                  # (a) a default gateway is already running: serve /p/manager on it
#   ./setup.sh b <mac-ip>                  # (b) no gateway running: the manager profile runs its own
#
# <mac-ip> is the address the arm host (the Pi, or this Mac in sim) reaches; 127.0.0.1 for sim on this Mac.
# What it does: creates the profile (--clone keeps the ChatGPT-plan login, H:L9050), installs the skill,
# writes a fresh API_SERVER_KEY into the profile's .env, and writes ./manager.key.env (chmod 600) for the
# worker. It does NOT edit YAML: merge config.manager.yaml into the profile's config.yaml by hand (step 4
# below), and it does NOT touch the default profile's .env (branch a prints what it must hold).
set -euo pipefail
branch=${1:-}; ip=${2:-}; port=${PORT:-8642}
if [[ "$branch" != a && "$branch" != b || -z "$ip" ]]; then
  sed -n '2,12p' "$0"; exit 2
fi
HERE=$(cd "$(dirname "$0")" && pwd)
HOME_H=${HERMES_HOME:-$HOME/.hermes}
P=$HOME_H/profiles/manager

command -v hermes >/dev/null || { echo "hermes is not on PATH"; exit 1; }
echo "== $(hermes --version 2>&1 | head -1)   (the design was written for v0.23.0)"

echo "== 1. the profile"
if [[ -d "$P" ]]; then
  echo "   $P already exists: keeping it"
else
  hermes profile create manager --clone \
    --description "Arm-company incident manager: reads one arm-escalation/v1 packet, answers with one arm-fixplan/v1 JSON."
fi

echo "== 2. the skill"
mkdir -p "$P/skills/arm-incident-manager"
cp "$HERE/skills/arm-incident-manager/SKILL.md" "$P/skills/arm-incident-manager/SKILL.md"
echo "   $P/skills/arm-incident-manager/SKILL.md"

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

cat <<EOF

== 4. by hand: merge $HERE/config.manager.yaml into $P/config.yaml
   For each key there: if the cloned file already has it, change its value; don't add a second copy.
   Keep the cloned model: block (gpt-6-sol on the ChatGPT plan). Then check:
     hermes -p manager config show | grep -E 'gateway_mode|disabled_toolsets|max_concurrent'

EOF
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
== 7. one real run on a saved packet:  ./ask.sh samples/esc-us_stuck.json
EOF
