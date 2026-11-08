"""Healer level 2 on one Runpod GPU, through Runpod's REST API (no SSH, no runpodctl needed here).

    python level2/runpod_l2.py launch      # create the pod; the test ships inside its start command
    python level2/runpod_l2.py status      # the pod, its cost per hour, and the job's progress
    python level2/runpod_l2.py fetch       # copy results + logs into level2/results/
    python level2/runpod_l2.py terminate   # delete the pod (disk included): nothing keeps billing

The key: $RUNPOD_API_KEY, sent as a Bearer header. In a Claude cloud session the network proxy injects
the saved key, so run with RUNPOD_API_KEY=proxy-injected.

Caps, so a hang can't drain the account (docs/healer-data-levels.md):
- the job runs under `timeout 7200` (2 h);
- 20 min after the job ends, the pod deletes itself (`runpodctl remove pod`, if the image has it);
- `terminate` from here after `fetch`, which is the normal way it ends.
Results are served read-only on the pod's port 8000 (https://<pod-id>-8000.proxy.runpod.net/): only
the test's answers and logs, nothing secret.
"""
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

API = "https://rest.runpod.io/v1"
HERE = Path(__file__).resolve().parent
STATE = HERE / "results" / "pod.json"
GPUS = ["NVIDIA RTX A6000", "NVIDIA A40", "NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation"]   # 48 GB cards
IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"

START = r"""
set -u
mkdir -p /workspace/out && cd /workspace
echo "$CHATTS_B64" | base64 -d > chatts_test.py
(cd /workspace/out && nohup python -m http.server 8000 > /dev/null 2>&1 &)
export HF_HOME=/workspace/hf HF_HUB_ENABLE_HF_TRANSFER=1
echo "START $(date -u +%H:%M:%S)" > out/status.txt
timeout 7200 bash -c 'pip install -q "transformers==4.52.4" accelerate hf_transfer > out/pip.log 2>&1 \
  && python chatts_test.py > out/chatts.log 2>&1; echo "EXIT $?" >> out/chatts.log'
cp -f /workspace/chatts_results.jsonl out/ 2>/dev/null
nvidia-smi > out/nvidia-smi.txt 2>&1
echo "JOB DONE $(date -u +%H:%M:%S)" >> out/status.txt
sleep 1200
runpodctl remove pod "$RUNPOD_POD_ID" || runpodctl stop pod "$RUNPOD_POD_ID"
sleep infinity
"""


def api(method, path, body=None):
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        raise SystemExit("set RUNPOD_API_KEY (in a Claude cloud session: RUNPOD_API_KEY=proxy-injected)")
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "armsim-level2/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode()
    return json.loads(text) if text.strip() else {}


def saved():
    if not STATE.exists():
        raise SystemExit("no pod launched from here (level2/results/pod.json is missing)")
    return json.loads(STATE.read_text())


def launch():
    if STATE.exists() and saved().get("id"):
        raise SystemExit(f"a pod is already recorded: {saved()['id']}; terminate it first")
    test = (HERE / "chatts_test.py").read_bytes()
    body = {"name": "healer-level2-chatts", "imageName": IMAGE, "computeType": "GPU", "cloudType": "COMMUNITY",
            "gpuTypeIds": GPUS, "gpuCount": 1, "containerDiskInGb": 30, "volumeInGb": 60,
            "volumeMountPath": "/workspace", "ports": ["8000/http"],
            "env": {"CHATTS_B64": base64.b64encode(test).decode()},
            "dockerStartCmd": ["bash", "-c", START], "interruptible": False}
    pod = api("POST", "/pods", body)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"id": pod.get("id"), "launched": time.time(), "gpu": pod.get("gpu"),
                                 "costPerHr": pod.get("costPerHr") or pod.get("adjustedCostPerHr")}, indent=1))
    print(json.dumps({k: pod.get(k) for k in ("id", "desiredStatus", "costPerHr", "adjustedCostPerHr", "gpu",
                                              "machineId")}, indent=1))


def proxy(name):
    url = f"https://{saved()['id']}-8000.proxy.runpod.net/{name}"
    req = urllib.request.Request(url, headers={"User-Agent": "armsim-level2/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def status():
    s = saved()
    pod = api("GET", f"/pods/{s['id']}")
    up_h = (time.time() - s["launched"]) / 3600
    rate = pod.get("costPerHr") or pod.get("adjustedCostPerHr") or s.get("costPerHr") or 0
    print(f"pod {s['id']}: {pod.get('desiredStatus')}, ${rate}/h, up {up_h:.2f} h, about ${float(rate) * up_h:.2f} so far")
    for name in ("status.txt", "chatts.log"):
        try:
            print(f"--- {name}\n" + proxy(name).decode(errors="replace")[-1500:])
        except OSError as exc:
            print(f"--- {name}: not reachable yet ({exc})")


def fetch():
    out = HERE / "results"
    for name in ("status.txt", "chatts.log", "chatts_results.jsonl", "pip.log", "nvidia-smi.txt"):
        try:
            (out / name).write_bytes(proxy(name))
            print("fetched", name)
        except OSError as exc:
            print(f"{name}: {exc}")


def terminate():
    s = saved()
    api("DELETE", f"/pods/{s['id']}")
    up_h = (time.time() - s["launched"]) / 3600
    print(f"terminated {s['id']} after {up_h:.2f} h")
    STATE.write_text(json.dumps({**s, "id": None, "terminated": time.time(), "last_id": s["id"]}, indent=1))


if __name__ == "__main__":
    {"launch": launch, "status": status, "fetch": fetch, "terminate": terminate}[sys.argv[1] if len(sys.argv) > 1 else "status"]()
