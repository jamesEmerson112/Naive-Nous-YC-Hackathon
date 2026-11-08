#!/usr/bin/env bash
# Blocker B8's check: build a scratch copy of firmware/ inside the coder image, offline
# (--network none), with the same limits the worker's sandbox uses. Run from the repo root:
# `make coder-firmware-check` (builds the image first). Exits 1 if any build fails.
#
# CODER_IMAGE picks another image tag (default romulus-coder).

set -euo pipefail
IMAGE="${CODER_IMAGE:-romulus-coder}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/romulus-fw-check.XXXXXX")"
trap 'rm -rf "$SCRATCH"' EXIT

# The tracked tree only: host-installed tools, downloads and build output stay behind.
tar -C "$ROOT" --exclude=firmware/toolkit/opt --exclude=firmware/toolkit/downloads \
  --exclude=firmware/toolkit/dist --exclude=firmware/healer/var --exclude=build \
  --exclude=backups -cf - firmware | tar -C "$SCRATCH" -xf -

docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
  --memory 2g --memory-swap 2g --cpus 2 --pids-limit 512 \
  --user "$(id -u):$(id -g)" -v "$SCRATCH:/work" \
  --entrypoint python "$IMAGE" /opt/romulus/firmware_check.py /work/firmware
