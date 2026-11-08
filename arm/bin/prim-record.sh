#!/usr/bin/env bash
# Record movements by moving the arm by hand. Usage: bin/prim-record.sh <name> [<name> ...]
#                                                    bin/prim-record.sh --pose <name> [...]
source "$(dirname "$0")/_common.sh"
cd "$ARM_DIR" && exec python -m prims.record "$@"
