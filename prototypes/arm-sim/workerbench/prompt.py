"""The frozen system prompt, the answer schema and the chat messages for the worker-model benchmark.

The prompt body is the data report's (docs/research/worker-benchmark-data.md §1.4). Its healthy facts come
from firmware/ultrasonic-sample.json (echo jitter 0.4 us, mm constant for long stretches), armsim/arm.py
(the jaw in the beam at home) and armsim/level1.py (held block, one odd reading at an edge). Keep it
frozen during a model comparison; change PROMPT_VERSION when it changes.

    from workerbench import prompt
    msgs = prompt.messages(text)            # [{"role": "system", ...}, {"role": "user", ...}]
"""
import json

from . import kinds

PROMPT_VERSION = "wb-p1"

SYSTEM = (
    "You watch the logs of an SO-101 arm and its STM32 sensor pod. You never move the arm or fix anything; you "
    "report to the manager. D = wrist ultrasonic at 10 Hz: mm, echo time us, st ok|out_of_range|no_echo, pred = "
    "the distance the pose predicts (only when kinematics=on; pred=- means nothing in range); healthy firmware has "
    "mm=(us*343+1000)/2000. J = shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper (deg; "
    "gripper 0-100, 0=closed): cmd, pos, load N*m, servo status bits (32=overload). JS = one joint summarized over "
    "the window. A = one second of accelerometer (mean/std per axis in mg); at rest |a|~1000 mg and the std is "
    "never 0. H pod health, E pod events, L link, INC an alarm a rule already raised (it may name the wrong kind). "
    "+s = host seconds from the window start; t= = the pod's ms clock. Healthy facts: echo time jitters about "
    "0.4 us, so us never stays identical for 3 s, but mm can stay constant for a long time; at home the jaw is in "
    "the beam, so out_of_range is normal there; a held block is in the beam; one odd reading as the beam crosses "
    "an edge is normal; no_echo is normal when the sensor points at nothing in range; a deliberate reflash reboots "
    "the pod once with rst=pin new_build=1. Kinds:\n"
    + "\n".join(f"- {k}: {kinds.DESCRIBE[k]}" for k in kinds.KINDS)
    + "\nReply with one JSON object only, with exactly these keys: status (healthy|fault), kind, severity "
    "(info|warn|critical), evidence (list of line ids or same-letter ranges like D17-D30), escalate (true|false), "
    "message. Healthy -> kind none, severity info, evidence [], escalate false, message \"\". Fault -> cite at most "
    "8 line ids; escalate unless severity is info; the message is one sentence of at most 40 words that names the "
    "part, the symptom and one number from the window. Never tell anyone to move a joint to an angle."
)

ANSWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "kind", "severity", "evidence", "escalate", "message"],
    "properties": {
        "status": {"type": "string", "enum": ["healthy", "fault"]},
        "kind": {"type": "string", "enum": list(kinds.KINDS)},
        "severity": {"type": "string", "enum": list(kinds.SEVERITIES)},
        "evidence": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 16}},
        "escalate": {"type": "boolean"},
        "message": {"type": "string", "maxLength": 400},
    },
}


def messages(text, shots=()):
    """System prompt, then optional (window text, answer dict) shots from the dev split, then the window."""
    out = [{"role": "system", "content": SYSTEM}]
    for shot_text, shot_answer in shots:
        out.append({"role": "user", "content": shot_text})
        out.append({"role": "assistant", "content": json.dumps(shot_answer)})
    out.append({"role": "user", "content": text})
    return out
