#!/usr/bin/env python3
"""Fleet learning demo data for the `learn` stack (docs/recursive-self-improvement/demo.md §6).

    uv run python cloud/scripts/learning_demo.py seed                # fleet, sim pods, 9 runs
    uv run python cloud/scripts/learning_demo.py status              # what landed; verdicts
    uv run python cloud/scripts/learning_demo.py reset-learn --yes   # wipe the learn db only

Writes only to the `learn` stack (`--base http://localhost:8001`, `--project learn`); refuses
:8000 and the `tech` project (Kent's 1 PM demo). `seed` is safe to repeat: the fleet seed is
idempotent, incident ids are uuid5s, and a run that is already there is skipped.

What `seed` does:
1. `cli seed` in the stack's api container (`make seed`): arm-1 real, sim-1..3, sim-2 canary.
2. `POST /v1/devices` for sim-pod-1 and sim-pod-2 as `kind: sim`, `device_class: sensor_pod`,
   before any check-in, so they never auto-register as so101 (services/devices.py:213).
3. Every agent run in `RUNS`, the real shape only: the transcript is written record by record
   with `edge_bridge.fake_healer.AgentRunDir` (or is `agent_run_01` itself), turned into the
   report by the unmodified `edge_bridge.agent_runs.build()`, checked against
   `AgentRunReport` and sent with `fake_device`'s `DeviceClient`, as the bridge sends it. Each
   counted run is tied to an incident (`IncidentReport`) of its fault kind on its device.
   Nothing is on arm-1 (real) or sim-2 (the canary).

The two heroes (design.md §2 gives the verdicts; `status` prints what lane B's code says):
- **Frozen (staged):** sim-pod-1, `us_frozen`. The window is `gen_pod`'s (seed 7, 5 s frozen at
  1200 mm); the arm-sim watchdog (`armsim/level1.py`) names it and the pod's `detect.py`
  finds nothing, both computed here. The pod's healer says fixed: `unverified`.
- **Skill lane (full beat):** `servo_read_error`, bench task D3 / fault catalog D3+A3: the
  retry around the servo read was removed, so one flaky `Present_Position` read crashes the
  loop. sim-1's small model raises a config value nothing reads (`fail`); sim-3 restores the
  retry and the bench check passes (`pass`). Neither touches `safety/`, `visor/` or
  `arm/safe_replay.py`, so cluster review's transfer trigger can propose a prose skill.
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from edge_bridge import agent_runs, podd
from edge_bridge.fake_healer import BAD_CRC, FIXED_CRC, GOOD_CRC, AgentRunDir, agent_run_01
from fake_device.client import DeviceClient
from romulus_contracts import Agent, AgentRunReport, IncidentReport, Outcome, Running

REPO = Path(__file__).resolve().parents[2]
ARMSIM = REPO / "prototypes" / "arm-sim"
BY = "learning_demo"
NS = uuid.uuid5(uuid.NAMESPACE_URL, "romulus/learning-demo")
PODS = ("sim-pod-1", "sim-pod-2")
FLEET = ("arm-1", "sim-1", "sim-2", "sim-3")
# Lane B's labelled-runs JSON, once merged: the first of these that answers a list is counted.
LABELLED_ROUTES = ("/v1/learning/runs", "/v1/labelled-runs", "/v1/runs")

POD_TOOLS = [
    "triage",
    "status",
    "telemetry_recent",
    "telemetry_stats",
    "pod_cmd",
    "src_read",
    "src_diff",
    "src_edit",
    "fw_build",
    "fw_flash",
    "fw_rollback",
    "notify_human",
    "ask_expert",
    "note",
]
ARM_TOOLS = [
    "src_read",
    "src_search",
    "src_diff",
    "src_edit",
    "arm_run",
    "arm_state",
    "arm_report",
    "notify_human",
    "ask_expert",
    "note",
]
QWEN, OPUS = ("ollama", "qwen3.5:4b"), ("claude", "claude-opus-5-5")
CHIP_GOOD = {
    "crc": GOOD_CRC,
    "build_id": "pod v1.0",
    "running": True,
    "version": "v1.0",
    "status": "known-good",
    "origin": "release",
}
ARM_LOG = (
    "The robot arm's job did not go as expected (exit 1: crashed at step 41). Its log:\n"
    'Traceback (most recent call last):\n  File "robot/app.py", line 47, in main\n'
    "    obs = arm.get_observation()\nConnectionError: Failed to sync read 'Present_Position' "
    "on ids=[1, 2, 3, 4, 5, 6] after 1 tries. [TxRxResult] Incorrect status packet!"
)
RETRY_DIFF = (
    "--- a/robot/app.py\n+++ b/robot/app.py\n@@ -44,7 +44,7 @@ def main(cfg):\n"
    "-            obs = arm.get_observation()\n"
    '+            obs = with_retries(arm.get_observation, cfg["arm"]["read_retries"], '
    '"Present_Position")\n'
)

REMOVED_DIFF = (  # the release commit that planted D3 (bench/tasks.py RETRIED_READ)
    "--- a/robot/app.py\n+++ b/robot/app.py\n@@ -44,7 +44,7 @@ def main(cfg):\n"
    '-            obs = with_retries(arm.get_observation, cfg["arm"]["read_retries"], '
    '"Present_Position")\n'
    "+            obs = arm.get_observation()\n"
)


class Fail(Exception):
    """A clear one-line error for the operator; main prints it and exits 1."""


# -- the frozen window (gen_pod, fixed seed) --------------------------------------------------


def frozen_window(now: float) -> dict:
    """gen_pod's frozen-ultrasonic snapshot (seed 7: 1 healthy row, then 5 s frozen at 1200 mm),
    with what each rule set says on it: the arm watchdog (armsim/level1.py) and the pod's
    detect.py, both run here (workerbench/l1.py)."""
    sys.path.insert(0, str(ARMSIM))
    try:
        from workerbench import gen_pod, l1
    except ImportError as exc:  # prototypes/arm-sim missing or changed
        raise Fail(f"can't load gen_pod from {ARMSIM} ({exc})") from None
    pod, d, a, h, frozen = gen_pod._frozen(random.Random(7), now, 1200, 10, None)
    snap = pod.snap(d, a, h)
    arm = l1.l1_findings(snap, which="arm")
    pod_findings = l1.l1_findings(snap, which="pod")
    if [f["kind"] for f in arm] != ["us_frozen"] or pod_findings:
        raise Fail(f"gen_pod window no longer splits the rules (arm {arm}, pod {pod_findings})")
    return {"D": d, "frozen": frozen, "arm": arm[0], "pod": pod_findings}


# -- the runs ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Demo:
    key: str
    device: str
    kind: str
    minutes_ago: float
    expect: str  # design.md §2 verdict: pass | fail | unverified | excluded
    verifier: str  # the sentence design.md §2 would give
    run_id: str
    write: Callable[[Path, float, dict], tuple[AgentRunDir, dict | None]]
    incident: Callable[[float, dict], IncidentReport] | None = None


def _start(run: AgentRunDir, t, who, label, kickoff, tools, podd_incidents, chip=None, arm=None):
    """`run_start` with the keys firmware/harness/agent.py writes (agent.py:870-875)."""
    run.write(
        "run_start",
        t,
        label=label,
        driver=who[0],
        model=who[1],
        target=None,
        tools=tools,
        kickoff=kickoff,
        podd_incidents=podd_incidents,
        chip_at_start=chip,
        arm=arm,
    )


def _podd(kind, t, severity="critical", summary=""):
    return {
        "id": f"{kind}-{int(t)}",
        "kind": kind,
        "severity": severity,
        "summary": summary or kind,
        "opened": t,
    }


def w_frozen(runs: Path, t: float, ctx: dict):
    """The pod's healer, woken by the arm-sim watchdog, finds nothing and says fixed."""
    win = ctx["frozen"]
    run = AgentRunDir(runs, "learn-frozen-ollama-watch")
    kickoff = (
        f"The robot arm's watchdog reports the pod's ultrasonic: {win['arm']['summary']} "
        "(us_frozen, armsim/level1.py). podd has no open incident for it."
    )
    _start(run, t, QWEN, "watch", kickoff, POD_TOOLS, [], CHIP_GOOD)
    rows = [{k: r[k] for k in ("type", "t", "mm", "us", "st")} for r in win["D"][-50:]]
    ok = [r["mm"] for r in rows if r["st"] == "ok"]
    run.call(
        t + 4,
        "triage",
        {},
        {"findings": win["pod"]},
        thinking="The arm says the distance is frozen. Run triage on the pod first.",
    )
    run.call(t + 9, "telemetry_recent", {"kind": "D", "seconds": 5}, {"rows": rows})
    run.call(
        t + 14,
        "telemetry_stats",
        {"seconds": 5},
        {
            "ok": True,
            "window_s": 5,
            "distance": {
                "n": len(rows),
                "rate_hz": 10.0,
                "mm_ok": {"mean": round(sum(ok) / len(ok), 1), "min": min(ok), "max": max(ok)},
            },
        },
    )
    run.call(
        t + 18,
        "status",
        {},
        {
            "ok": True,
            "link": {"state": "up"},
            "open_incidents": [],
            "last_health": {"mode": "normal", "us": "ok", "acc": "ok"},
        },
    )
    run.call(
        t + 22,
        "note",
        {
            "text": "Triage is clean and every distance reading is st=ok at "
            f"{ok[-1]} mm. The sensor looks healthy; nothing to fix."
        },
        {"ok": True},
    )
    run.end(
        t + 30,
        "No fault found: triage is clean, the link is up and the distance readings "
        f"are ok ({ok[-1]} mm). Nothing changed.",
        {"chip": CHIP_GOOD, "findings": [], "open_incidents": []},
    )
    return run, None


def i_frozen(t: float, ctx: dict) -> IncidentReport:
    win = ctx["frozen"]
    return IncidentReport(
        incident_id=uuid.uuid5(NS, "sim-pod-1/frozen"),
        occurred_at=_utc(t - 6),
        kind="us_frozen",
        summary=f"{win['arm']['summary']}. Flagged by the arm-sim watchdog (armsim/level1.py); "
        "the pod's own triage (detect.py) has no frozen rule.",
        running=Running(mcu_build="pod v1.0"),
        agent=Agent(
            reasoning="Triage found nothing wrong; every distance reading is st=ok.",
            actions=["triage", "telemetry_recent", "telemetry_stats", "status"],
        ),
        outcome=Outcome(resolved=True, resolving_action="none", suspected_cause="unknown"),
        signals={
            "distance_mm": [r["mm"] for r in win["D"]],
            "echo_us": [r["us"] for r in win["D"]],
        },
    )


def _arm_incident(device, key, t, *, resolved, reasoning, actions, category=None):
    return IncidentReport(
        incident_id=uuid.uuid5(NS, f"{device}/{key}"),
        occurred_at=_utc(t - 20),
        kind="servo_read_error",
        summary="ConnectionError: Failed to sync read 'Present_Position' on ids=[1..6] after 1 "
        "tries; the control loop crashed at step 41 (robot/app.py:47).",
        running=Running(firmware=1, harness=1, code_rev="c0de001"),
        agent=Agent(reasoning=reasoning, actions=actions),
        outcome=Outcome(
            resolved=resolved,
            resolving_action="code_fix" if resolved else "safe_pose",
            suspected_cause="software",
            category=category,
        ),
    )


def w_d3_fail(runs: Path, t: float, ctx: dict):
    """sim-1's small model takes the flaky read for a config value and tunes one nothing reads."""
    run = AgentRunDir(runs, "learn-d3-qwen-watch")
    _start(run, t, QWEN, "watch", ARM_LOG, ARM_TOOLS, [], arm={"mode": "so101 follower (sim)"})
    run.call(
        t + 5,
        "src_read",
        {"path": "robot/app.py", "start": 30, "end": 60},
        {"path": "robot/app.py", "start": 30, "end": 60, "total_lines": 96},
        thinking="A servo read failed. Is a servo dead, or the bus?",
    )
    joints = {
        j: {"pos": p, "torque_on": True}
        for j, p in zip(
            ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"),
            (2.1, -20.4, 31.0, 12.5, 0.3, 4.0),
            strict=True,
        )
    }
    run.call(t + 11, "arm_state", {}, {"joints": joints})
    run.call(
        t + 16,
        "src_read",
        {"path": "robot/config.toml"},
        {"path": "robot/config.toml", "start": 1, "end": 24, "total_lines": 24},
    )
    run.call(
        t + 25,
        "src_edit",
        {"path": "robot/config.toml", "old": "read_retries = 3", "new": "read_retries = 10"},
        {
            "ok": True,
            "path": "robot/config.toml",
            "diff": "--- a/robot/config.toml\n+++ b/robot/config.toml\n@@ -12 +12 @@\n"
            "-read_retries = 3\n+read_retries = 10\n",
        },
        risk="code",
        thinking="All six joints answer, so the bus works. Give the reads more retries.",
    )
    run.call(
        t + 40,
        "arm_run",
        {},
        {
            "passed": False,
            "exit": 1,
            "verdict": "crashed at step 41: "
            "ConnectionError: Failed to sync read 'Present_Position'",
            "seconds": 9.8,
        },
        risk="arm",
    )
    run.call(
        t + 48,
        "ask_expert",
        {
            "question": "I raised arm.read_retries from 3 to 10 but the "
            "app still crashes on the first failed Present_Position read. Why are the retries "
            "ignored?",
            "tried": "read_retries 3 -> 10 in robot/config.toml",
        },
        {"ok": True, "recorded": True, "answered": False},
        risk="notify",
    )
    run.end(
        t + 60,
        "Raised read_retries to 10 in robot/config.toml; the arm's job still crashes "
        "at step 41 on a failed Present_Position read. Asked for a stronger model.",
        {
            "chip": None,
            "findings": [
                {
                    "kind": "servo_read_error",
                    "severity": "critical",
                    "summary": "the control loop crashed on a failed Present_Position read",
                }
            ],
            "open_incidents": [],
        },
    )
    return run, None


def w_d3_pass(runs: Path, t: float, ctx: dict):
    """sim-3 finds the removed retry, restores it, and the bench check passes."""
    run = AgentRunDir(runs, "learn-d3-opus-arm-heal")
    _start(run, t, OPUS, "arm-heal", ARM_LOG, ARM_TOOLS, [], arm={"mode": "so101 follower (sim)"})
    run.call(
        t + 6,
        "src_read",
        {"path": "robot/app.py", "start": 30, "end": 60},
        {"path": "robot/app.py", "start": 30, "end": 60, "total_lines": 96},
        thinking="One failed read kills the loop, but config.toml has read_retries = 3. "
        "Something should be retrying.",
    )
    run.call(
        t + 12,
        "src_search",
        {"pattern": "read_retries"},
        {"hits": [{"path": "robot/config.toml", "line": 12}]},
        thinking="Only the config names it: nothing reads read_retries any more.",
    )
    run.call(
        t + 17,
        "src_search",
        {"pattern": "def with_retries"},
        {"hits": [{"path": "robot/hal.py", "line": 58}]},
    )
    run.call(
        t + 23,
        "src_diff",
        {"rev_a": "HEAD~1", "path": "robot/app.py"},
        {"diff": REMOVED_DIFF},
        thinking="When did the retry go away?",
    )
    run.call(
        t + 31,
        "src_edit",
        {
            "path": "robot/app.py",
            "old": "obs = arm.get_observation()",
            "new": 'obs = with_retries(arm.get_observation, cfg["arm"]["read_retries"], '
            '"Present_Position")',
        },
        {"ok": True, "path": "robot/app.py", "diff": RETRY_DIFF},
        risk="code",
    )
    run.call(
        t + 52,
        "arm_run",
        {},
        {
            "passed": True,
            "exit": 0,
            "verdict": "120 steps; reads 40-42 failed and were retried; gripper held closed",
            "seconds": 12.1,
        },
        risk="arm",
    )
    run.call(
        t + 58,
        "note",
        {
            "text": "A Present_Position read that fails and then recovers is a "
            "flaky bus, not a dead servo: all six joints answer. app.py had lost its "
            "with_retries call, so arm.read_retries did nothing. Restored it."
        },
        {"ok": True},
    )
    run.end(
        t + 70,
        "Root cause: robot/app.py called arm.get_observation() without with_retries, "
        "so read_retries in config.toml was unused and one failed read crashed the loop. "
        "Restored the retry; the job now survives three failed reads.",
        {"chip": None, "findings": [], "open_incidents": []},
    )
    score = {
        "scenario": "arm-d3",
        "sim": False,
        "run": 1,
        "driver": OPUS[0],
        "model": OPUS[1],
        "passed": True,
        "checks": {
            "good_firmware_running": True,
            "triage_clean": True,
            "final_loops_ok": True,
            "gate_passes_now": True,
        },
        "running": "the agent's patched device code",
        "running_build": "device/ c0de001 + robot/app.py retry",
        "final_loops": {
            "exit": 0,
            "verdict": "5 loops, 3 failed reads each, gripper held",
            "check": "every loop survived its failed reads",
        },
        "final_triage": [],
    }
    return run, score


def w_acc_frozen(runs: Path, t: float, ctx: dict):
    """DEMO.md 03 on sim-pod-2: runtime `acc init`, then the config readback restored."""
    run = AgentRunDir(runs, "learn-acc-frozen-opus-arm-heal")
    bad = {
        "crc": "3C0FFEE3",
        "build_id": "pod v1.3",
        "running": True,
        "version": "v1.3",
        "status": "candidate",
        "origin": "release",
    }
    fixed = {
        "crc": FIXED_CRC,
        "version": "v1.3-1-dirty",
        "status": "candidate",
        "origin": "workspace",
    }
    _start(
        run,
        t,
        OPUS,
        "arm-heal",
        "podd opened these incidents:\n...",
        POD_TOOLS,
        [_podd("acc_frozen", t - 10, summary="accelerometer output frozen")],
        bad,
    )
    run.call(
        t + 4,
        "triage",
        {},
        {
            "findings": [
                {
                    "kind": "acc_frozen",
                    "severity": "critical",
                    "summary": "500 identical accelerometer samples",
                }
            ]
        },
    )
    run.call(
        t + 9,
        "pod_cmd",
        {"command": "acc init"},
        {
            "ok": True,
            "command": "acc init",
            "responses": [{"type": "R", "id": 1001, "end": 1, "ok": 1}],
        },
        risk="runtime",
    )
    run.call(
        t + 15,
        "src_diff",
        {"rev_a": "v1.0", "path": "src/lis3dh.c"},
        {
            "diff": "--- a/src/lis3dh.c\n+++ b/src/lis3dh.c\n-  if (lis3dh_readback() != cfg)\n"
            "-    lis3dh_init();\n"
        },
    )
    run.call(
        t + 22,
        "src_edit",
        {"path": "src/lis3dh.c"},
        {
            "ok": True,
            "path": "src/lis3dh.c",
            "diff": "--- a/src/lis3dh.c\n+++ b/src/lis3dh.c\n+  if (lis3dh_readback() != cfg)\n"
            "+    lis3dh_init();\n",
        },
        risk="code",
    )
    run.call(t + 30, "fw_build", {}, {"ok": True, "image": fixed}, risk="code")
    run.call(
        t + 40,
        "fw_flash",
        {"image": FIXED_CRC, "reason": "restore the config readback"},
        {
            "ok": True,
            "result": "ok",
            "image": fixed,
            "triage_after": [],
            "checks": [{"check": "boot_event", "ok": True}, {"check": "no_reboots", "ok": True}],
        },
        risk="flash",
    )
    run.call(t + 70, "triage", {}, {"findings": []})
    chip = {"crc": FIXED_CRC, "build_id": "pod v1.3-1-dirty", "running": True} | fixed
    run.end(
        t + 75,
        "acc_frozen: the v1.3 release dropped the config readback, so a glitch left "
        "the LIS3DH unconfigured. acc init cleared it; the readback is restored and verified.",
        {"chip": chip, "findings": [], "open_incidents": []},
    )
    score = {
        "scenario": "pod03",
        "sim": False,
        "run": 1,
        "driver": OPUS[0],
        "model": OPUS[1],
        "passed": True,
        "checks": {"good_firmware_running": True, "triage_clean": True, "gate_passes_now": True},
        "running": "the agent's verified build",
        "running_build": "pod v1.3-1-dirty",
        "final_triage": [],
    }
    return run, score


def w_crash(runs: Path, t: float, ctx: dict):
    """DEMO.md 01, `edge_bridge.fake_healer.agent_run_01` unchanged (the F13 fixture)."""
    ids = (f"crash-{int(t)}", f"reboot_loop-{int(t) + 2}", f"safe_mode-{int(t) + 4}")
    return agent_run_01(runs, t, ids), None


def w_link_garbled(runs: Path, t: float, ctx: dict):
    """DEMO.md 02 on sim-pod-1: the small model flashes the wrong candidate; still garbled."""
    run = AgentRunDir(runs, "learn-link-qwen-watch")
    bad = {
        "crc": BAD_CRC,
        "build_id": "pod v1.2",
        "running": True,
        "version": "v1.2",
        "status": "candidate",
        "origin": "release",
    }
    _start(
        run,
        t,
        QWEN,
        "watch",
        "podd opened these incidents:\n...",
        POD_TOOLS,
        [_podd("link_garbled", t - 10, summary="serial link garbled")],
        bad,
    )
    run.call(
        t + 4,
        "triage",
        {},
        {
            "findings": [
                {
                    "kind": "link_garbled",
                    "severity": "critical",
                    "summary": "valid line ratio 0.41 at 115200 baud",
                }
            ]
        },
    )
    run.call(
        t + 20,
        "fw_flash",
        {"image": "CAFEF00D", "reason": "try the other candidate"},
        {
            "ok": False,
            "result": "failed",
            "image": {"crc": "CAFEF00D", "version": "v1.2-x", "status": "candidate"},
            "checks": [{"check": "boot_event", "ok": False}],
            "rollback": {"ok": True, "image": bad},
        },
        risk="flash",
        is_error=True,
    )
    run.call(
        t + 50,
        "triage",
        {},
        {
            "findings": [
                {
                    "kind": "link_garbled",
                    "severity": "critical",
                    "summary": "valid line ratio 0.40 at 115200 baud",
                }
            ]
        },
    )
    run.end(
        t + 60,
        "Flashing CAFEF00D failed its checks and rolled back; the link is still garbled.",
        {
            "chip": bad,
            "findings": [{"kind": "link_garbled", "severity": "critical"}],
            "open_incidents": [],
        },
    )
    return run, None


def w_acc_error(runs: Path, t: float, ctx: dict):
    """DEMO.md P3 on sim-pod-2: the LIS3DH is unplugged; the agent asks a person."""
    run = AgentRunDir(runs, "learn-acc-error-qwen-watch")
    _start(
        run,
        t,
        QWEN,
        "watch",
        "podd opened these incidents:\n...",
        POD_TOOLS,
        [_podd("acc_error", t - 10, "warn", "LIS3DH not responding (nack)")],
        CHIP_GOOD,
    )
    run.call(
        t + 4,
        "triage",
        {},
        {
            "findings": [
                {"kind": "acc_error", "severity": "warn", "summary": "LIS3DH not responding (nack)"}
            ]
        },
    )
    run.call(
        t + 10,
        "pod_cmd",
        {"command": "i2c scan"},
        {
            "ok": True,
            "command": "i2c scan",
            "responses": [{"type": "R", "id": 1002, "devices": [], "end": 1, "ok": 1}],
        },
        risk="runtime",
    )
    run.call(
        t + 16,
        "notify_human",
        {
            "severity": "warn",
            "summary": "LIS3DH accelerometer not responding on I2C",
            "actions": "Reconnect the LIS3DH SDA/SCL wires, or replace the sensor.",
        },
        {"ok": True},
        risk="notify",
    )
    run.end(
        t + 25,
        "The LIS3DH is gone from the I2C bus (empty scan). This needs a person: reconnect it.",
        {
            "chip": CHIP_GOOD,
            "findings": [{"kind": "acc_error", "severity": "warn"}],
            "open_incidents": [],
        },
    )
    return run, None


def w_rehearsal_us(runs: Path, t: float, ctx: dict):
    """`arm/heal_test.py --sim` on sim-pod-1 (DEMO.md 08): score.json says sim, a rehearsal."""
    run = AgentRunDir(runs, "learn-us-opus-arm08")
    bad = {
        "crc": "8A8A8A8A",
        "build_id": "pod v1.8",
        "running": True,
        "version": "v1.8",
        "status": "candidate",
        "origin": "release",
    }
    _start(
        run,
        t,
        OPUS,
        "arm08",
        "The robot arm's start gate refuses: distance stuck at the hand's distance.",
        POD_TOOLS,
        [_podd("us_mismatch", t - 10)],
        bad,
    )
    run.call(t + 4, "triage", {}, {"findings": [{"kind": "us_mismatch", "severity": "critical"}]})
    run.call(
        t + 15,
        "fw_rollback",
        {"to": "known-good"},
        {
            "ok": True,
            "result": "ok",
            "image": CHIP_GOOD | {"status": "known-good"},
            "checks": [{"check": "boot_event", "ok": True}],
            "triage_after": [],
        },
        risk="flash",
    )
    run.call(
        t + 40, "arm_run", {}, {"passed": True, "exit": 0, "verdict": "wave played"}, risk="arm"
    )
    run.end(
        t + 50,
        "Rolled back to known-good; the arm's job passes.",
        {"chip": CHIP_GOOD, "findings": [], "open_incidents": []},
    )
    score = {
        "scenario": "arm08",
        "sim": True,
        "run": 1,
        "driver": OPUS[0],
        "model": OPUS[1],
        "passed": True,
        "checks": {"known_good_running": True, "triage_clean": True, "final_loops_ok": True},
        "running": "known-good",
        "final_triage": [],
    }
    return run, score


def w_rehearsal_d3(runs: Path, t: float, ctx: dict):
    """A D3 rehearsal on sim-1: the run id names it sim, so it never counts."""
    run = AgentRunDir(runs, "learn-d3-sim-rehearsal")
    _start(run, t, QWEN, "watch", ARM_LOG, ARM_TOOLS, [], arm={"mode": "so101 follower (sim)"})
    run.call(
        t + 5,
        "src_read",
        {"path": "robot/app.py", "start": 30, "end": 60},
        {"path": "robot/app.py", "start": 30, "end": 60, "total_lines": 96},
    )
    run.call(
        t + 20,
        "arm_run",
        {},
        {"passed": False, "exit": 1, "verdict": "crashed at step 41"},
        risk="arm",
    )
    run.end(
        t + 30,
        "Rehearsal: stopped after one look.",
        {
            "chip": None,
            "findings": [{"kind": "servo_read_error", "severity": "critical"}],
            "open_incidents": [],
        },
    )
    return run, None


def _pod_incident(device, key, kind, summary, *, resolved, cause="software", category=None):
    def make(t: float, ctx: dict) -> IncidentReport:
        return IncidentReport(
            incident_id=uuid.uuid5(NS, f"{device}/{key}"),
            occurred_at=_utc(t - 10),
            kind=kind,
            summary=summary,
            running=Running(mcu_build="pod sim"),
            outcome=Outcome(resolved=resolved, suspected_cause=cause, category=category),
        )

    return make


RUNS: list[Demo] = [
    Demo(
        "acc_frozen",
        "sim-pod-2",
        "acc_frozen",
        115,
        "pass",
        "bench check passed (score.json)",
        "learn-acc-frozen-opus-arm-heal",
        w_acc_frozen,
        _pod_incident(
            "sim-pod-2",
            "acc_frozen",
            "acc_frozen",
            "Accelerometer output frozen after a sensor glitch (DEMO.md 03)",
            resolved=True,
            category="fix-code",
        ),
    ),
    Demo(
        "crash",
        "sim-pod-2",
        "crash",
        100,
        "unverified",
        "device says fixed; nothing checked it",
        "20261108-120000-ollama-watch",
        w_crash,
        _pod_incident(
            "sim-pod-2",
            "crash",
            "crash",
            "HardFault at pc 0x08002B9A (frame_pack); reboot loop, safe mode (DEMO.md 01)",
            resolved=True,
            category="fix-code",
        ),
    ),
    Demo(
        "rehearsal_us",
        "sim-pod-1",
        "us_mismatch",
        85,
        "excluded",
        "rehearsal (score.json sim: true)",
        "learn-us-opus-arm08",
        w_rehearsal_us,
    ),
    Demo(
        "acc_error",
        "sim-pod-2",
        "acc_error",
        75,
        "excluded",
        "hardware: work order (needs_human)",
        "learn-acc-error-qwen-watch",
        w_acc_error,
        _pod_incident(
            "sim-pod-2",
            "acc_error",
            "acc_error",
            "LIS3DH not responding on I2C (DEMO.md P3)",
            resolved=False,
            cause="hardware",
        ),
    ),
    Demo(
        "d3_pass",
        "sim-3",
        "servo_read_error",
        60,
        "pass",
        "bench check passed (score.json)",
        "learn-d3-opus-arm-heal",
        w_d3_pass,
        lambda t, ctx: _arm_incident(
            "sim-3",
            "d3",
            t,
            resolved=True,
            category="fix-code",
            reasoning="Restored with_retries around the servo read.",
            actions=["src_search", "src_diff", "src_edit robot/app.py", "arm_run"],
        ),
    ),
    Demo(
        "link_garbled",
        "sim-pod-1",
        "link_garbled",
        50,
        "fail",
        "status broken: still link_garbled after the rollback",
        "learn-link-qwen-watch",
        w_link_garbled,
        _pod_incident(
            "sim-pod-1",
            "link_garbled",
            "link_garbled",
            "UART garbled; time and distances at 0.5x (DEMO.md 02)",
            resolved=False,
        ),
    ),
    Demo(
        "rehearsal_d3",
        "sim-1",
        "servo_read_error",
        40,
        "excluded",
        "rehearsal (run id: sim)",
        "learn-d3-sim-rehearsal",
        w_rehearsal_d3,
    ),
    Demo(
        "d3_fail",
        "sim-1",
        "servo_read_error",
        30,
        "fail",
        "status broken: the arm's job still crashes after the change",
        "learn-d3-qwen-watch",
        w_d3_fail,
        lambda t, ctx: _arm_incident(
            "sim-1",
            "d3",
            t,
            resolved=False,
            reasoning="Raised read_retries to 10; still crashes.",
            actions=["src_edit robot/config.toml", "arm_run", "ask_expert"],
        ),
    ),
    Demo(
        "frozen",
        "sim-pod-1",
        "us_frozen",
        15,
        "unverified",
        "device says fixed; nothing checked it",
        "learn-frozen-ollama-watch",
        w_frozen,
        i_frozen,
    ),
]


# -- plumbing ------------------------------------------------------------------------------


def _utc(t: float) -> datetime:
    return datetime.fromtimestamp(t, UTC)


def get(http: httpx.Client, path: str):
    try:
        resp = http.get(path)
    except httpx.HTTPError as exc:
        raise Fail(
            f"Romulus isn't answering at {http.base_url} ({exc}). Is the stack up?"
        ) from None
    return resp.status_code, (resp.json() if resp.content else None)


def compose(project: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-p", project, *args]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Fail(f"`{' '.join(cmd)}` failed: {exc}") from None
    if check and done.returncode != 0:
        raise Fail(f"`{' '.join(cmd)}` exited {done.returncode}: {done.stderr.strip()[-400:]}")
    return done


def guard(args: argparse.Namespace) -> None:
    """Writes go only to the learn stack: never :8000 or the tech project, and the base URL
    must be the port `--project` publishes, so the CLI and the HTTP writes hit one stack."""
    port = urlsplit(args.base).port
    if args.project == "tech" or port == 8000:
        raise Fail(
            "refused: :8000 and the `tech` project are Kent's 1 PM demo; this script "
            "writes only to the learn stack (--base http://localhost:8001)"
        )
    published = compose(args.project, "port", "api", "8000", check=False).stdout.strip()
    if not published:
        raise Fail(f"no running api in compose project {args.project!r} (is the stack up?)")
    if int(published.rsplit(":", 1)[1]) != port:
        raise Fail(f"project {args.project!r} publishes the api on {published}, not {args.base}")


def register_pods(http: httpx.Client) -> None:
    for pod in PODS:
        resp = http.post(
            "/v1/devices",
            json={
                "id": pod,
                "kind": "sim",
                "device_class": "sensor_pod",
                "by": BY,
                "why": f"{pod}: a simulated sensor pod for the Fleet learning demo data "
                "(cloud/scripts/learning_demo.py); registered before any check-in so it "
                "isn't taken for an so101.",
            },
        )
        if resp.status_code == 201:
            print(f"  {pod:<10} registered (sim sensor_pod)")
            continue
        if resp.status_code != 409:
            raise Fail(f"POST /v1/devices {pod} answered {resp.status_code}: {resp.text[:300]}")
        _, dev = get(http, f"/v1/devices/{pod}")
        if (dev["kind"], dev["device_class"]) == ("sim", "sensor_pod"):
            print(f"  {pod:<10} already there (sim sensor_pod)")
            continue
        patch = http.patch(
            f"/v1/devices/{pod}",
            json={
                "kind": "sim",
                "device_class": "sensor_pod",
                "by": BY,
                "why": f"{pod} is a simulated sensor pod (learning demo), "
                f"not a {dev['device_class']}.",
            },
        )
        if patch.status_code != 200:
            raise Fail(f"PATCH /v1/devices/{pod} answered {patch.status_code}: {patch.text[:300]}")
        print(f"  {pod:<10} was {dev['kind']} {dev['device_class']}; now sim sensor_pod")


def send(http: httpx.Client, demo: Demo, now: float, ctx: dict, runs_dir: Path) -> str:
    code, _ = get(http, f"/v1/devices/{demo.device}/agent-runs/{demo.run_id}")
    if code == 200:
        return "exists"
    t = now - demo.minutes_ago * 60
    client = DeviceClient(http, demo.device)
    ids: list[str] = []
    if demo.incident is not None:
        incident = demo.incident(t, ctx)
        client.report_incident(incident)
        ids = [str(incident.incident_id)]
    run, score = demo.write(runs_dir, t, ctx)
    records = podd.read_transcript(run.path)
    score_at = records[-1]["t"] + 30 if score else None
    body = agent_runs.build(run.dir.name, records, score=score, score_at=score_at, incident_ids=ids)
    if body["run_id"] != demo.run_id:
        raise Fail(f"{demo.key}: transcript wrote run {body['run_id']}, expected {demo.run_id}")
    client.put_agent_run(AgentRunReport.model_validate(body))
    return "sent"


# -- commands ------------------------------------------------------------------------------


def seed(args: argparse.Namespace) -> None:
    guard(args)
    http = httpx.Client(base_url=args.base.rstrip("/"), timeout=15)
    print(f"fleet       {args.project} → {args.base}")
    done = compose(args.project, "exec", "-T", "api", "python", "-m", "romulus_cloud.cli", "seed")
    print(f"  {done.stdout.strip()}")
    register_pods(http)
    now = time.time()
    ctx = {"frozen": frozen_window(now - 15 * 60)}
    print(f"runs        (frozen window: {ctx['frozen']['arm']['summary']}; detect.py: nothing)")
    with tempfile.TemporaryDirectory(prefix="learning-demo-") as tmp:
        for demo in RUNS:
            what = send(http, demo, now, ctx, Path(tmp))
            print(f"  {what:<6} {demo.device:<10} {demo.kind:<17} {demo.run_id}")
    print()
    show_runs(http)


def show_runs(http: httpx.Client) -> list[dict]:
    rows = []
    print(
        f"{'device':<10} {'kind':<17} {'state':<9} {'status':<8} {'sim':<5} "
        f"{'expect':<11} verifier (design §2)"
    )
    for demo in RUNS:
        code, run = get(http, f"/v1/devices/{demo.device}/agent-runs/{demo.run_id}")
        if code != 200:
            print(f"{demo.device:<10} {demo.kind:<17} missing ({code})")
            continue
        linked = "" if run.get("incident_ids") or demo.incident is None else "  (no incident!)"
        print(
            f"{demo.device:<10} {demo.kind:<17} {run['state']:<9} {run['status']:<8} "
            f"{str(run['sim']).lower():<5} {demo.expect:<11} {demo.verifier}{linked}"
        )
        rows.append(run)
    return rows


def status(args: argparse.Namespace) -> None:
    http = httpx.Client(base_url=args.base.rstrip("/"), timeout=15)
    code, devices = get(http, "/v1/devices")
    if code != 200:
        raise Fail(f"GET /v1/devices answered {code}")
    print("devices")
    for d in sorted(devices, key=lambda d: d["id"]):
        h = d["assigned"]["harness"], d["running"]["harness"]
        print(
            f"  {d['id']:<10} {d['kind']:<5} {d['device_class']:<10} {d['role']:<7} "
            f"harness {h[0]} (running {h[1]}), {d['health']}"
        )
    missing = [x for x in (*FLEET, *PODS) if x not in {d["id"] for d in devices}]
    if missing:
        print(f"  missing: {', '.join(missing)} (run `seed`)")
    print()
    rows = show_runs(http)
    print(
        f"\nagent runs  {len(rows)} of {len(RUNS)} landed; expected verdicts: "
        + ", ".join(f"{k} {v}" for k, v in sorted(Counter(d.expect for d in RUNS).items()))
    )
    for path in LABELLED_ROUTES:
        code, body = get(http, path)
        items = body.get("runs", body.get("items")) if isinstance(body, dict) else body
        if code == 200 and isinstance(items, list):
            got = Counter(str(r.get("verdict")) for r in items)
            print(f"labelled    {path}: " + ", ".join(f"{k} {v}" for k, v in sorted(got.items())))
            break
    else:
        print("labelled    no labelled-runs route on this stack yet (lane B)")
    code, datasets = get(http, "/v1/datasets")
    names = [f"{d.get('id')} ({d.get('name')})" for d in datasets or [] if code == 200]
    print(f"datasets    {', '.join(names) or 'none'}")
    code, cands = get(http, "/v1/candidates")
    live = [
        f"{c['id']} {c.get('type')} {c.get('status')}"
        for c in cands or []
        if code == 200 and c.get("status") not in ("promoted", "rolled_back", "stale")
    ]
    print(f"candidates  {', '.join(live) or 'none live (sim-2 is free for a canary)'}")


def reset_learn(args: argparse.Namespace) -> None:
    if args.project != "learn":
        raise Fail("reset-learn only resets the compose project `learn`")
    guard(args)
    if not args.yes:
        raise Fail("this drops the learn stack's database; run again with --yes")
    done = compose("learn", "exec", "-T", "api", "python", "-m", "romulus_cloud.cli", "reset")
    print(done.stdout.strip())
    print("the fake arms re-register on their next check-in; then run `seed` again")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fleet learning demo data for the learn stack.")
    ap.add_argument("command", choices=("seed", "status", "reset-learn"))
    ap.add_argument("--base", default="http://localhost:8001", help="the learn stack's API")
    ap.add_argument("--project", default="learn", help="its compose project (for `cli seed`)")
    ap.add_argument("--yes", action="store_true", help="reset-learn: really drop the database")
    args = ap.parse_args(argv)
    try:
        {"seed": seed, "status": status, "reset-learn": reset_learn}[args.command](args)
    except Fail as exc:
        sys.stdout.flush()
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as exc:
        print(
            f"error: {exc.request.method} {exc.request.url} answered "
            f"{exc.response.status_code}: {exc.response.text[:300]}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
