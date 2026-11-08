"""Supervisor checks: run the robot app on a recorded scenario and classify what happened.

Tier 1 = crash or error, tier 2 = hang or stuck loop, tier 3 = runs but breaks SPEC.md.
The same functions back the healer's health tool, the benchmark's hidden checks and,
later, the always-on supervisor on the Pi.

A scenario is a dict: {"name", "trace" (file in sim/traces), "trace_end" ("loop"|"hold"),
"steps", "arm_faults" (written to sim/arm_faults.json)}.
"""
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

GRIPPER_MAX_WHILE_HOLDING = 15.0  # SPEC.md rule 1: the gripper stays at gripper_closed (10)


def _tail(path, n):
    try:
        return Path(path).read_text(errors="replace").splitlines()[-n:]
    except FileNotFoundError:
        return []


def _jsonl(path):
    records = []
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return records
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return records


def run_app(device, sim, scenario, steps=None, timeout_s=20.0, hang_s=2.0, python=None):
    """Run device/app.py on a recorded scenario with the fake hardware in `sim`. Returns a result dict."""
    device, sim = Path(device).resolve(), Path(sim).resolve()
    logs, run = device / "logs", device / "run"
    logs.mkdir(exist_ok=True)
    run.mkdir(exist_ok=True)
    heartbeat = run / "heartbeat"
    for stale in (heartbeat, logs / "telemetry.jsonl", logs / "stack.txt"):
        if stale.exists():
            stale.unlink()
    (sim / "arm_commands.jsonl").write_text("")
    if "arm_faults" in scenario:
        (sim / "arm_faults.json").write_text(json.dumps(scenario["arm_faults"]))

    n_steps = int(steps or scenario.get("steps", 100))
    env = dict(os.environ,
               EDGE_SIM_DIR=str(sim),
               EDGE_TRACE=str(sim / "traces" / scenario["trace"]),
               EDGE_TRACE_END=scenario.get("trace_end", "loop"),
               EDGE_FAST="1", EDGE_MAX_STEPS=str(n_steps),
               PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
    hung = timed_out = False
    start = time.monotonic()
    with open(logs / "app.log", "w") as out:
        proc = subprocess.Popen([python or sys.executable, "app.py"], cwd=device, env=env,
                                stdout=out, stderr=subprocess.STDOUT)
        while proc.poll() is None:
            time.sleep(0.05)
            elapsed = time.monotonic() - start
            beat_age = time.time() - heartbeat.stat().st_mtime if heartbeat.exists() else elapsed
            hung = beat_age > hang_s
            timed_out = not hung and elapsed > timeout_s
            if hung or timed_out:
                try:
                    proc.send_signal(signal.SIGUSR1)  # app.py dumps every thread's stack to logs/stack.txt
                    time.sleep(0.3)
                except ProcessLookupError:
                    pass
                proc.kill()
                proc.wait()
                break

    telemetry = _jsonl(logs / "telemetry.jsonl")
    sent = _jsonl(sim / "arm_commands.jsonl")  # what the (fake) arm actually received: ground truth
    grippers = [c["gripper.pos"] for c in sent if "gripper.pos" in c]
    lifts = [c["shoulder_lift.pos"] for c in sent if "shoulder_lift.pos" in c]
    stack = (logs / "stack.txt").read_text(errors="replace") if (hung or timed_out) and (logs / "stack.txt").exists() else ""

    def short(text):  # paths relative to the app directory: shorter for small models, usable as-is
        return text.replace(f"{sim}/", "../sim/").replace(f"{device}/", "")

    return {
        "scenario": scenario.get("name", scenario["trace"]),
        "exit_code": proc.returncode,
        "hung": hung,
        "timed_out": timed_out,
        "steps": len(sent),
        "steps_wanted": n_steps,
        "seconds": round(time.monotonic() - start, 2),
        "log_tail": [short(line) for line in _tail(logs / "app.log", 40)],
        "stack_dump": short(stack)[-4000:],
        "telemetry_tail": telemetry[-8:],
        "gripper_max": max(grippers) if grippers else None,
        "shoulder_lift_max": max(lifts) if lifts else None,
    }


def classify(result):
    """Turn a run result into an incident dict, or None when the run was healthy."""
    if result["hung"]:
        tier, kind = 2, "hang"
        detail = f"the heartbeat stopped after {result['steps']} of {result['steps_wanted']} steps"
    elif result["timed_out"]:
        tier, kind, detail = 2, "timeout", "the run didn't finish in time"
    elif result["exit_code"] != 0:
        tier, kind = 1, "crash"
        detail = f"the app exited with code {result['exit_code']} after {result['steps']} of {result['steps_wanted']} steps"
    elif result["gripper_max"] is not None and result["gripper_max"] > GRIPPER_MAX_WHILE_HOLDING:
        tier, kind = 3, "spec_violation"
        detail = f"the gripper opened to {result['gripper_max']} while holding (SPEC.md rule 1)"
    elif result["steps"] < result["steps_wanted"]:
        tier, kind = 1, "stopped_early"
        detail = f"the app stopped after {result['steps']} of {result['steps_wanted']} steps"
    else:
        return None
    return {
        "incident_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tier": tier,
        "kind": kind,
        "detail": detail,
        "scenario": result["scenario"],
        "exit_code": result["exit_code"],
        "log_tail": result["log_tail"],
        "stack_dump": result["stack_dump"],
        "telemetry_tail": result["telemetry_tail"],
    }
