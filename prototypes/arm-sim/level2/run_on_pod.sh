#!/usr/bin/env bash
# Runs on the pod: install, run the test, then stop this pod 20 min later (time to fetch results).
# A 2-hour hard cap means nothing keeps billing if the test hangs.
cd /workspace
export HF_HOME=/workspace/hf
timeout 7200 bash -c 'pip install -q "transformers==4.52.4" accelerate > pip.log 2>&1 && python chatts_test.py > chatts.log 2>&1; echo "EXIT $?" >> chatts.log'
echo "JOB DONE $(date -u +%H:%M:%S)" >> chatts.log
sleep 1200
runpodctl stop pod "$RUNPOD_POD_ID"
