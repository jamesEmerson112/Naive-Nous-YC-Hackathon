#!/usr/bin/env bash
source "$(dirname "$0")/_common.sh"
cd "$ARM_DIR" && exec python -m prims.play "$@"
