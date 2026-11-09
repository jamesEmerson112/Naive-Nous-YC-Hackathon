# Sourced by the other bin/ scripts. Loads arm.env and puts the venv on PATH.
ARM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -x "$ARM_DIR/.venv/bin/python" ]; then
  echo "No venv yet. Run: $ARM_DIR/setup.sh" >&2; exit 1
fi
export PATH="$ARM_DIR/.venv/bin:$PATH"
if [ -f "$ARM_DIR/arm.env" ]; then
  set -a; . "$ARM_DIR/arm.env"; set +a
fi
