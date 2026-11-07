#!/usr/bin/env bash
# Refresh the raw documentation dumps that docs/*/terminology.md are built from.
# Usage: bash docs/raw/fetch.sh
set -euo pipefail
cd "$(dirname "$0")"
trap 'rm -f ./*.tmp' EXIT

fetch() {
  curl -fsSL --max-time 180 -o "$1.tmp" "$2"
  mv "$1.tmp" "$1"
  printf '%9s bytes  %s\n' "$(wc -c <"$1" | tr -d ' ')" "$1"
}

fetch naive-llms.txt       https://usenaive.ai/docs/llms.txt
fetch naive-llms-full.txt  https://usenaive.ai/docs/llms-full.txt
fetch hermes-llms.txt      https://hermes-agent.nousresearch.com/docs/llms.txt
fetch hermes-llms-full.txt https://hermes-agent.nousresearch.com/docs/llms-full.txt
