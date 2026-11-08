#!/usr/bin/env bash
# Calibrate the arms (once per arm; stored by id). Usage: bin/calibrate.sh [follower|leader|both]
source "$(dirname "$0")/_common.sh"
which="${1:-both}"
if [ "$which" = follower ] || [ "$which" = both ]; then
  need_port FOLLOWER_PORT
  echo "== Calibrating FOLLOWER ($FOLLOWER_PORT, id=${FOLLOWER_ID})"
  lerobot-calibrate --robot.type=so101_follower --robot.port="$FOLLOWER_PORT" --robot.id="$FOLLOWER_ID"
fi
if [ "$which" = leader ] || [ "$which" = both ]; then
  need_port LEADER_PORT
  echo "== Calibrating LEADER ($LEADER_PORT, id=${LEADER_ID})"
  lerobot-calibrate --teleop.type=so101_leader --teleop.port="$LEADER_PORT" --teleop.id="$LEADER_ID"
fi
