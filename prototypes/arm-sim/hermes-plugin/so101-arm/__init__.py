"""so101-arm: the commander's skill menu as Hermes tools (approach A in James's plan).

Each tool asks the arm host (prototypes/arm-sim/armsim/host.py tonight, the real arm's host
tomorrow) to run one skill. The LLM picks skills; it never sends joint angles, so no model sits
in the real-time loop. A pre_tool_call guard refuses motion while a fault is open: the healer
fixes the cause first, then the commander retries.
"""
import json
import os
import socket
import tempfile

TOOLSET = "so101_arm"
# Same default as the arm host (armsim/client.py): macOS caps Unix socket paths at 104 bytes.
SOCK = os.environ.get("ARMSIM_SOCK") or os.path.join(tempfile.gettempdir(), "armsim.sock")

SKILLS = {
    "arm_go_home": ("go_home", "Move the arm to its ready pose over the work area. Use it to reset before retrying a task."),
    "arm_reach": ("reach", "Move the open gripper over the block, ready to approach it."),
    "arm_approach": ("approach", "Move over the block with the jaws open, then step down 5 mm at a time using the wrist "
                                 "distance sensor until close, and make the final move so the jaws straddle the block. "
                                 "To pick up the block: arm_approach, then arm_grip, then arm_lift."),
    "arm_grip": ("grip", "Close the gripper. The result says whether it is holding something (gripper stopped short of closed, with load)."),
    "arm_lift": ("lift", "Lift the gripper 30 mm straight up and check it is still holding."),
    "arm_release": ("release", "Open the gripper."),
    "arm_wave": ("wave", "Wave hello: raise the arm and wave the wrist."),
    "arm_park": ("park", "Fold the arm to its rest pose and switch torque off. Use after a fault."),
}
MOTION = set(SKILLS) - {"arm_park"}


def _call(op, **fields):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(120)
            s.connect(SOCK)
            s.sendall((json.dumps({"op": op, **fields}) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"arm host not reachable at {SOCK}: {exc}. Start it with "
                                      "`python -m armsim.host` in prototypes/arm-sim."}


def _schema(name, description):
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": {}, "required": []}}


def register(ctx):
    for tool, (skill, description) in SKILLS.items():
        def handler(params, _skill=skill, **kwargs):
            del params, kwargs
            return json.dumps(_call("skill", name=_skill))
        ctx.register_tool(name=tool, toolset=TOOLSET, schema=_schema(tool, description), handler=handler)

    def status(params, **kwargs):
        del params, kwargs
        return json.dumps({"status": _call("status"), "distance": _call("distance"), "joints": _call("read")})

    ctx.register_tool(
        name="arm_status", toolset=TOOLSET, handler=status,
        schema=_schema("arm_status", "Read the arm: joint positions, servo status bits, grasp state, "
                                     "open incidents and the wrist distance sensor."),
    )

    def restart_sensor(params, **kwargs):
        del params, kwargs
        return json.dumps(_call("repair", action="restart_sensor"))

    ctx.register_tool(
        name="arm_restart_sensor", toolset=TOOLSET, handler=restart_sensor,
        schema=_schema("arm_restart_sensor", "Healer repair: restart the wrist distance sensor, then verify on "
                                             "fresh readings. Fixes a frozen or stuck distance. Never moves the arm."),
    )

    def guard(tool_name, args, task_id="", **kwargs):
        del args, task_id, kwargs
        if tool_name not in MOTION:
            return None
        st = _call("status")
        if not st.get("ok"):
            return None                          # the tool itself will report the host problem
        if st.get("incidents"):
            said = "; ".join(i["summary"] for i in st["incidents"])
            return {"action": "block", "message": f"The arm is parked: {said}. Fix the cause first "
                                                  "(for a stuck distance, arm_restart_sensor), then retry."}
        if any(st.get("status", [])):
            return {"action": "block", "message": f"A servo reports an alarm (status bits {st['status']}); "
                                                  "park the arm and report it instead of moving."}
        return None

    ctx.register_hook("pre_tool_call", guard)
