#!/usr/bin/env bash
# Preflight for the overnight run (flows F10, build plan M7 and K4/K13). Run from the repo
# root: `make overnight-check`. Prints one PASS / WARN / FAIL line per check and exits 1
# if anything FAILed. Read only: it changes nothing.
#
# Respects COMPOSE_PROJECT_NAME and API_PORT like the Makefile does.

set -u
COMPOSE="${COMPOSE:-docker compose}"
API_PORT="${API_PORT:-8000}"
MIN_FREE_GB="${MIN_FREE_GB:-20}"
fails=0

pass() { printf 'PASS  %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }

# --- Docker and the stack ------------------------------------------------------------------
if docker info >/dev/null 2>&1; then
  pass "Docker is running"
else
  fail "Docker is not running (start Docker Desktop)"
  echo "Overnight check: $fails failure(s)."
  exit 1
fi

container_state() {  # <service> -> "status health restart-policy", or empty
  local id
  id="$($COMPOSE --profile sim ps -q "$1" 2>/dev/null | head -n1)"
  [ -n "$id" ] || return 0
  docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}} {{.HostConfig.RestartPolicy.Name}}' "$id"
}

for svc in db api worker fake-devices; do
  read -r status health policy <<<"$(container_state "$svc")"
  if [ -z "${status:-}" ]; then
    fail "$svc: not running (make up; make sim for fake-devices)"
  elif [ "$status" != "running" ]; then
    fail "$svc: $status"
  elif [ "$health" != "-" ] && [ "$health" != "healthy" ]; then
    fail "$svc: running but $health"
  elif [ "$policy" != "unless-stopped" ] && [ "$policy" != "always" ]; then
    warn "$svc: running, but restart policy is '$policy' (want unless-stopped; rebuild with make up)"
  else
    pass "$svc: running${health:+ ($health)}, restart $policy"
  fi
  unset status health policy
done

if curl -fsS "http://localhost:${API_PORT}/healthz" >/dev/null 2>&1; then
  pass "api answers on http://localhost:${API_PORT}/healthz"
else
  fail "api does not answer on http://localhost:${API_PORT}/healthz"
fi

if $COMPOSE logs --since 2m worker 2>/dev/null | grep -q '"worker.step"'; then
  pass "worker ran steps in the last 2 minutes"
else
  warn "worker logged no steps in the last 2 minutes (make logs)"
fi

fd_args="$(docker inspect -f '{{join .Args " "}}' "$($COMPOSE --profile sim ps -q fake-devices 2>/dev/null | head -n1)" 2>/dev/null || true)"
case "$fd_args" in
  *--chaos=overnight*|*"--chaos overnight"*) pass "fake-devices runs the overnight chaos schedule ($fd_args)" ;;
  "") ;;  # already reported above
  *) warn "fake-devices args are '$fd_args' (want --chaos=overnight --speed=1)" ;;
esac

# --- What the app itself resolves (not printenv: env names match case-insensitively) -----------
setting() {  # <service> <name>: the value the app's own Settings resolve, or empty
  $COMPOSE exec -T "$1" python -m romulus_cloud.cli setting "$2" 2>/dev/null | tr -d '\r'
}

for svc in api worker; do
  naive="$(setting "$svc" naive_mode)"
  if [ -z "$naive" ]; then
    fail "can't read the effective NAIVE_MODE in $svc (is it running?)"
  elif [ "$naive" = "fake" ]; then
    pass "NAIVE_MODE resolves to fake in $svc: no real business gets called (K13)"
  else
    fail "NAIVE_MODE resolves to '$naive' in $svc. Overnight it MUST be fake (K13): cp .env.overnight.example .env.overnight; make up"
  fi
done

check_value() {  # <setting> <wanted>
  local got
  got="$(setting api "$1")"
  if [ "$got" = "$2" ]; then pass "$1=$got"; else warn "$1=${got:-<unreadable>} (overnight wants $2; see .env.overnight.example)"; fi
}
check_value canary_window_s 1200
check_value batch_interval_s 3600
check_value overnight_started True
pass "llm_mode=$(setting api llm_mode) (informational)"

flag="$($COMPOSE exec -T api python -m romulus_cloud.cli overnight status 2>/dev/null | tr -d '\r')"
case "$flag" in
  *"started at"*) pass "database overnight flag set ($flag)" ;;
  *) warn "database overnight flag not set yet (make overnight-start once launched)" ;;
esac

# --- The laptop --------------------------------------------------------------------------------
free_gb="$(df -Pk . | awk 'NR==2 {printf "%d", $4 / 1024 / 1024}')"
if [ "${free_gb:-0}" -gt "$MIN_FREE_GB" ]; then
  pass "disk free: ${free_gb} GB (> ${MIN_FREE_GB} GB)"
else
  fail "disk free: ${free_gb:-?} GB (need > ${MIN_FREE_GB} GB; BLOB_BUDGET_GB is 20 by default)"
fi

if command -v pmset >/dev/null 2>&1; then
  if pmset -g batt | grep -q "AC Power"; then
    pass "power adapter connected"
  else
    fail "running on battery: plug in the power adapter"
  fi
else
  warn "pmset not found; check power by hand"
fi

if pgrep -x caffeinate >/dev/null 2>&1; then
  pass "caffeinate is running (pid $(pgrep -x caffeinate | tr '\n' ' '| sed 's/ $//'))"
else
  fail "caffeinate is not running: start 'caffeinate -dims' in its own terminal"
fi

# Docker Desktop keeps this in its group container; macOS may block reading it (privacy
# prompt), so give up after 3 seconds rather than hang.
ds="$HOME/Library/Group Containers/group.com.docker"
autostart="$(perl -e 'alarm 3; exec @ARGV' cat "$ds/settings-store.json" "$ds/settings.json" 2>/dev/null \
  | grep -oiE '"autoStart" *: *(true|false)' | head -n1)"
case "$autostart" in
  *true) pass "Docker Desktop starts on login" ;;
  *false) warn "Docker Desktop does not start on login (Settings > General > Start Docker Desktop when you sign in)" ;;
  *) warn "Docker Desktop start-on-login not detectable; check Settings > General" ;;
esac

echo
if [ "$fails" -eq 0 ]; then
  echo "Overnight check: all required checks passed."
else
  echo "Overnight check: $fails failure(s). Fix them before leaving it overnight."
  exit 1
fi
