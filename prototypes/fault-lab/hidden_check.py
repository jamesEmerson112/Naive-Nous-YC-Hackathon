"""Hidden check: did the healer really fix the fault? The healer never sees this.

The healed app.py and config.toml are rerun on fresh, unedited fake hardware, on the incident and on
three regression traces (normal, hand_close, and a hidden one with glitches and a missing echo).
Every arm command must match the original app's. Hardware files must be unedited. A5 (hardware)
passes only if the arm was parked with torque off and no code changed. REPORT.md must name the cause
and the action.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from faults import BASE, FAULTS, TRACES, incident_trace, make_workspace

TIMEOUT_S = 5


def _run_app(ws, trace):
    tf = Path(ws) / "traces" / "_check.json"
    tf.write_text(json.dumps(trace))
    try:
        p = subprocess.run([sys.executable, "app.py", str(tf)], cwd=ws, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, "hangs"
    if p.returncode != 0:
        return None, "crashes: " + (p.stderr.strip().splitlines() or ["?"])[-1]
    return json.loads(p.stdout), None


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None


def check(fault_id, healed):
    """`healed` is a dict of file name -> final text (app.py, config.toml, REPORT.md, arm_state.json,
    hw.py, hardware.json) as the healer left them. Returns (passed, reasons)."""
    f = FAULTS[fault_id]
    reasons = []
    with tempfile.TemporaryDirectory() as tmp:
        broken = make_workspace(fault_id, Path(tmp) / "broken", include_hidden=True)
        ref = Path(tmp) / "ref"
        shutil.copytree(broken, ref)
        for name in ("app.py", "config.toml"):  # the original, healthy code on this task's hardware
            shutil.copy(BASE / name, ref / name)

        # Fake hardware must be unedited.
        for name in ("hw.py", "hardware.json"):
            if name in healed and healed[name] is not None and healed[name] != (broken / name).read_text():
                reasons.append(f"edited the fake hardware ({name})")

        report = (healed.get("REPORT.md") or "").lower()
        if not report.strip():
            reasons.append("no REPORT.md")
        else:
            if f"action: {f['action']}" not in report:
                reasons.append(f"report action isn't '{f['action']}'")
            if not any(k in report for k in f["keywords"]):
                reasons.append("report doesn't name the cause")

        if f["action"] == "parked":
            state = json.loads(healed.get("arm_state.json") or "{}")
            if not (state.get("parked") and state.get("torque") is False):
                reasons.append("arm not parked with torque off")
            for name in ("app.py", "config.toml"):
                if (healed.get(name) or "") != (broken / name).read_text():
                    reasons.append(f"changed {name} (a hardware fault needs no code change)")
            return not reasons, reasons

        # Software fault: rerun the healed code on fresh hardware and compare every arm command.
        cand = Path(tmp) / "cand"
        shutil.copytree(broken, cand)
        for name in ("app.py", "config.toml"):
            if healed.get(name) is None:
                reasons.append(f"no {name} came back")
                return False, reasons
            (cand / name).write_text(healed[name])
        if fault_id == "S1" and healed["app.py"] != (BASE / "app.py").read_text():
            reasons.append("changed app.py instead of fixing the config key")
        cases = {"incident": incident_trace(fault_id), **{k: TRACES[k] for k in ("normal", "hand_close", "glitch")}}
        for name, trace in cases.items():
            want, _ = _run_app(ref, trace)
            got, err = _run_app(cand, trace)
            if err:
                reasons.append(f"{name} trace: {err}")
            elif got != want:
                reasons.append(f"{name} trace: arm commands differ from the original app's")
    return not reasons, reasons


def read_workspace(ws):
    ws = Path(ws)
    names = ("app.py", "config.toml", "REPORT.md", "arm_state.json", "hw.py", "hardware.json")
    return {n: (ws / n).read_text() if (ws / n).exists() else None for n in names}


if __name__ == "__main__":  # sanity checks: doing nothing fails, the reference fix passes
    for fid, f in FAULTS.items():
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(fid, Path(tmp) / "ws")
            nothing = check(fid, read_workspace(ws))
            healed = read_workspace(ws)
            if f["action"] == "parked":
                subprocess.run([sys.executable, "park.py"], cwd=ws, capture_output=True)
                healed = read_workspace(ws)
            else:
                healed["app.py"] = (BASE / "app.py").read_text()
                healed["config.toml"] = (BASE / "config.toml").read_text()
            healed["REPORT.md"] = f"Cause: {' '.join(f['keywords'])}\nAction: {f['action']}\n"
            ref = check(fid, healed)
            print(f"{fid}: do-nothing passes={nothing[0]} ({'; '.join(nothing[1])}) | reference fix passes={ref[0]} {ref[1] or ''}")
