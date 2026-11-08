#!/usr/bin/env bash
# Teleoperate AND record a LeRobot dataset (v3) at the same time.
#   bin/record.sh "Wave at the audience" [num_episodes] [episode_seconds] [extra lerobot-record args...]
# Keys while recording: Right arrow = end episode early, Left arrow = redo episode, Esc = stop.
# Data lands in arm/data/<repo_id>_<timestamp>/ (pushed to the Hub only if PUSH_TO_HUB=true).
source "$(dirname "$0")/_common.sh"
need_port FOLLOWER_PORT; need_port LEADER_PORT
task="${1:?usage: bin/record.sh \"task description\" [episodes] [seconds] [extra args]}"
episodes="${2:-5}"; seconds="${3:-20}"; shift $(( $# < 3 ? $# : 3 ))
follower_args; leader_args
repo="${DATASET_REPO_ID:-naive-nous/so101_demo}"
mkdir -p "$ARM_DIR/data"
exec lerobot-record "${FOLLOWER_ARGS[@]}" "${LEADER_ARGS[@]}" \
  --dataset.repo_id="$repo" \
  --dataset.root="$ARM_DIR/data/${repo//\//__}_$(date +%Y%m%d_%H%M%S)" \
  --dataset.no_stamp=true \
  --dataset.single_task="$task" \
  --dataset.num_episodes="$episodes" \
  --dataset.episode_time_s="$seconds" \
  --dataset.reset_time_s="${RESET_SECONDS:-10}" \
  --dataset.push_to_hub="${PUSH_TO_HUB:-false}" \
  --dataset.streaming_encoding=true \
  --dataset.encoder_threads=2 \
  --display_data="${DISPLAY_DATA:-true}" \
  "$@"
