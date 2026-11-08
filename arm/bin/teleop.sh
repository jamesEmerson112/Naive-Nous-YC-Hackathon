#!/usr/bin/env bash
# Leader -> follower teleoperation, no recording. Ctrl-C to stop.
# Extra args pass through to lerobot-teleoperate, e.g. --display_data=true --fps=30
source "$(dirname "$0")/_common.sh"
need_port FOLLOWER_PORT; need_port LEADER_PORT
follower_args; leader_args
exec lerobot-teleoperate "${FOLLOWER_ARGS[@]}" "${LEADER_ARGS[@]}" "$@"
