# Sourced by the other bin/ scripts. Loads arm.env and puts the venv on PATH.
ARM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -x "$ARM_DIR/.venv/bin/python" ]; then
  echo "No venv yet. Run: $ARM_DIR/setup.sh" >&2; exit 1
fi
export PATH="$ARM_DIR/.venv/bin:$PATH"
if [ -f "$ARM_DIR/arm.env" ]; then
  set -a; . "$ARM_DIR/arm.env"; set +a
fi

need_port() { # need_port VAR_NAME
  local val="${!1:-}"
  if [ -z "$val" ] || [[ "$val" == *XXXX* || "$val" == *YYYY* ]]; then
    echo "$1 is not set. Run bin/find-ports.sh and edit $ARM_DIR/arm.env" >&2; exit 1
  fi
  if [ ! -e "$val" ]; then
    echo "$1=$val does not exist. Is the arm plugged in and powered? Ports now:" >&2
    ls /dev/tty.usbmodem* 2>/dev/null >&2 || echo "  (no /dev/tty.usbmodem* devices)" >&2
    exit 1
  fi
}

follower_args() {
  FOLLOWER_ARGS=(--robot.type=so101_follower --robot.port="$FOLLOWER_PORT" --robot.id="${FOLLOWER_ID:-naive_follower}")
  [ -n "${CAMERAS:-}" ] && FOLLOWER_ARGS+=(--robot.cameras="$CAMERAS")
  [ -n "${MAX_RELATIVE_TARGET:-}" ] && FOLLOWER_ARGS+=(--robot.max_relative_target="$MAX_RELATIVE_TARGET")
  return 0
}

leader_args() {
  LEADER_ARGS=(--teleop.type=so101_leader --teleop.port="$LEADER_PORT" --teleop.id="${LEADER_ID:-naive_leader}")
}
