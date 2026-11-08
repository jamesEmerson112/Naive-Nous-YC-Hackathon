#!/usr/bin/env bash
# Fetch MuJoCo Menagerie's SO-101 model (robotstudio_so101, Apache-2.0) into vendor/. Run once.
set -euo pipefail
cd "$(dirname "$0")"
if [ -d vendor/menagerie/robotstudio_so101 ]; then
  echo "vendor/menagerie/robotstudio_so101 is already there"
  exit 0
fi
mkdir -p vendor
git clone --depth 1 --filter=blob:none --sparse https://github.com/google-deepmind/mujoco_menagerie vendor/menagerie
git -C vendor/menagerie sparse-checkout set robotstudio_so101
echo "Done. Next: uv run --python 3.12 --with-requirements requirements.txt python -m armsim poses"
