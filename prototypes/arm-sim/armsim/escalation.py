"""The worker/manager healer's two messages, and the plain-code check of a manager's plan
(docs/worker-manager-healer.md §1). Standard library only: no jsonschema in the venv.

    from armsim.escalation import ESCALATION_SCHEMA, FIXPLAN_SCHEMA, allowed_actions, validate, check_plan
    errors = validate(packet, ESCALATION_SCHEMA)        # [] when the packet is valid
    ok, reason = check_plan(run_output, packet, state)  # the manager's answer, checked before anything moves

    PYTHONPATH=. .venv/bin/python tests/test_escalation.py

The two messages:
- `arm-escalation/v1`, worker -> manager: the incident, the level-1 findings, the small model's six-key
  answer (or null), a lite render of the last 6 s, the arm's state, and the actions plain code allows
  for this incident (`allowed_actions`). Built by `armsim.worker.build_escalation`.
- `arm-fixplan/v1`, manager -> worker: one action from that list. It can carry no angles and no
  free-form commands; only `redo_step` takes arguments: the skill to redo from, and for a wrong action
  (category 2, armsim/redo.py) one bounded correction from the packet's `state.corrections`:
  `none` (redo as before), `relocate` (plain code scans for the block and re-approaches over it) or
  `offset` with `dx_mm`/`dy_mm`, each within +-40 mm and at most 40 mm in all, applied to the approach.

`check_plan(plan, esc, state)` runs the design's checks in order, and the first failure stops the plan:
  1 exactly one JSON object (one code fence tolerated) that passes the schema;
  2 the right incident, and that incident is still open on the host;
  3 an action from esc.allowed_actions;
  4 valid arguments (only redo_step has any: from_step in the job's steps, at or before the failed one;
    a correction must be offered in state.corrections, needs the arm's kinematics and a from_step of
    reach or approach, and dx_mm/dy_mm come only with `offset`, within MAX_OFFSET_MM);
  5 no safety changes (touches_safety: true is refused; the worker parks and hands off, K17);
  6 within budget: 2 heals a job, 1 pod_rollback plan an incident (carried out as a wait for the pod's own
    healer: plain code can't flash the pod, armsim/worker.py execute), 2 redo_steps a job, never a fix
    again after its check failed (a redo_step counts as the same only with the same from_step and
    correction, and `relocate` may run again: it measures the block afresh each time (inferred)). park
    and handoff_human are not fixes: they hand the arm to a person, so the repeat rule skips them;
  7 still current: before the deadline, and the arm still halted.
`state` is plain data the worker reads from the host just before the check (keys under check_plan).
A refusal under rule 6 for repeating a fix contains AGAIN: the scripted fallback then skips that fix too
(`repeat_refused`, armsim/dispatch.py ScriptedExecutor._heal).
"""
import json
import re
from datetime import datetime

ACTIONS = ("restart_sensor", "verify", "pod_rollback", "redo_step", "park", "handoff_human", "no_action")
STEPS = ("reach", "approach", "grip", "lift", "release")      # the skills redo_step may restart from
MAX_HEALS = 2               # a job's heal budget: the same as armsim/dispatch.py MAX_HEALS
MAX_POD_ROLLBACKS = 1       # per incident, inside the pod gate's FLASH_CAP = 3 (firmware/harness/agent.py:38)
MAX_REDOS = 2               # per job: a wrong action is redone at most twice, then a person (armsim/redo.py)
CORRECTIONS = ("none", "relocate", "offset")    # what a redo_step may change about the approach (category 2)
MAX_OFFSET_MM = 40          # the bound on a correction's shift of the approach, per axis and in all (inferred)
CORRECT_FROM = ("reach", "approach")            # a correction moves the approach, so the redo starts there or earlier
SAFETY = "touches_safety"   # a reason that starts with this: park and hand off, no fallback
MAYBE_POD_KINDS = ("us_offset", "us_scale", "us_residual")   # the same as dispatch.py: podd may explain them
NOT_FIXES = ("park", "handoff_human")   # they hand the arm to a person: never a "failed fix" for the repeat rule
AGAIN = "it is not tried again"         # in a refusal for repeating a fix (rule 6): the fallback skips it too

_FINDING = {"type": "object", "additionalProperties": False, "required": ["src", "kind", "severity", "summary"],
            "properties": {"src": {"enum": ["watchdog", "podd", "l3a", "dispatcher"]},
                           "kind": {"type": "string", "maxLength": 40},
                           "severity": {"enum": ["info", "warn", "critical"]},
                           "summary": {"type": "string", "maxLength": 300}}}

ESCALATION_SCHEMA = {
    "$id": "arm-escalation/v1", "type": "object", "additionalProperties": False,
    "required": ["schema", "incident_id", "device_id", "category", "opened_at", "findings", "worker", "window",
                 "state", "allowed_actions", "deadline_s"],
    "properties": {
        "schema": {"const": "arm-escalation/v1"},
        "incident_id": {"type": "string", "pattern": "^inc-[A-Za-z0-9-]{4,64}$"},
        "device_id": {"type": "string"},
        "category": {"enum": ["fault", "wrong_action"]},
        "opened_at": {"type": "string", "format": "date-time"},
        "findings": {"type": "array", "minItems": 1, "maxItems": 8, "items": _FINDING},
        "worker": {"type": ["object", "null"]},
        "window": {"type": "string", "maxLength": 12000},
        "state": {"type": "object", "required": ["mode", "kinematics", "parked", "tried"],
                  "properties": {
                      "mode": {"enum": ["sim", "real"]}, "kinematics": {"type": "boolean"},
                      "parked": {"type": "boolean"}, "pod_image": {"type": ["string", "null"]},
                      "job": {"type": ["object", "null"],
                              "properties": {"card_id": {"type": "string"},
                                             "steps": {"type": "array", "items": {"type": "string"}},
                                             "failed_step": {"type": "string"}}},
                      "corrections": {"type": "array", "uniqueItems": True, "items": {"enum": list(CORRECTIONS)}},
                      "tried": {"type": "array", "items": {"type": "object", "required": ["action", "verified"],
                                                           "properties": {"action": {"type": "string"},
                                                                          "verified": {"type": "boolean"}}}}}},
        "allowed_actions": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": list(ACTIONS)}},
        "deadline_s": {"type": "number", "minimum": 3, "maximum": 120}}}

FIXPLAN_SCHEMA = {
    "$id": "arm-fixplan/v1", "type": "object", "additionalProperties": False,
    "required": ["schema", "incident_id", "action", "args", "kind", "confidence", "touches_safety", "why"],
    "properties": {
        "schema": {"const": "arm-fixplan/v1"},
        "incident_id": {"type": "string"},
        "action": {"enum": list(ACTIONS)},
        "args": {"type": "object", "additionalProperties": False,
                 "properties": {"from_step": {"enum": list(STEPS)},
                                "correction": {"enum": list(CORRECTIONS)},
                                "dx_mm": {"type": "integer", "minimum": -MAX_OFFSET_MM, "maximum": MAX_OFFSET_MM},
                                "dy_mm": {"type": "integer", "minimum": -MAX_OFFSET_MM, "maximum": MAX_OFFSET_MM}}},
        "kind": {"type": "string", "maxLength": 40},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "touches_safety": {"type": "boolean"},
        "why": {"type": "string", "maxLength": 400},
        "expect": {"type": "string", "maxLength": 200}}}


# ---- a small JSON Schema validator: the keywords the two schemas above (and the worker's) use ----------
_TYPES = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
          "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
          "null": lambda v: v is None,
          "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
          "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)}


def _is_datetime(s):
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return "T" in s
    except ValueError:
        return False


def validate(obj, schema, path="$"):
    """Errors as 'path: what' strings; [] when obj fits the schema."""
    errs = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](obj) for t in types):
            return [f"{path}: expected {'/'.join(types)}, got {type(obj).__name__}"]
    if "const" in schema and obj != schema["const"]:
        errs.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: {obj!r} is not one of {schema['enum']}")
    if isinstance(obj, str):
        if "maxLength" in schema and len(obj) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']} characters ({len(obj)})")
        if "minLength" in schema and len(obj) < schema["minLength"]:
            errs.append(f"{path}: shorter than {schema['minLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], obj):
            errs.append(f"{path}: {obj!r} doesn't match {schema['pattern']}")
        if schema.get("format") == "date-time" and not _is_datetime(obj):
            errs.append(f"{path}: {obj!r} is not an ISO date-time")
    if _TYPES["number"](obj):
        if "minimum" in schema and obj < schema["minimum"]:
            errs.append(f"{path}: {obj} is below {schema['minimum']}")
        if "maximum" in schema and obj > schema["maximum"]:
            errs.append(f"{path}: {obj} is above {schema['maximum']}")
    if isinstance(obj, list):
        if "minItems" in schema and len(obj) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(obj) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems") and len({json.dumps(x, sort_keys=True) for x in obj}) != len(obj):
            errs.append(f"{path}: items are not unique")
        if "items" in schema:
            for i, x in enumerate(obj):
                errs += validate(x, schema["items"], f"{path}[{i}]")
    if isinstance(obj, dict):
        for k in schema.get("required", []):
            if k not in obj:
                errs.append(f"{path}: missing {k!r}")
        props = schema.get("properties", {})
        for k, v in obj.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected key {k!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errs += validate(v, schema["additionalProperties"], f"{path}.{k}")
    return errs


# ---- what plain code allows, per incident -------------------------------------------------------------
def allowed_actions(inc, state=None):
    """The allowlist for one incident (design §1, "The allowlist"), best action first.
    inc: the host's incident ({kind, pod_incidents?}); state: {"category", "pod_side"} as the worker sees it.
    Every row also allows no_action, which runs as verify: nothing resumes without verify()."""
    state = state or {}
    kind = inc.get("kind") or ""
    if state.get("category") == "wrong_action":
        acts = ["redo_step", "park", "handoff_human"]
    elif kind.startswith("pod_") or inc.get("pod_incidents") or state.get("pod_side"):
        acts = ["pod_rollback", "verify", "park", "handoff_human"]     # podd is behind it (image 07's us_offset too)
    elif kind.startswith("us_"):
        acts = ["restart_sensor", "verify", "park", "handoff_human"]
    elif kind == "joint_stall":
        acts = ["verify", "park", "handoff_human"]                     # a joint that won't move goes to a person
    else:
        acts = ["verify", "park", "handoff_human"]                     # an unknown kind: check, or hand off (inferred)
    return acts + ["no_action"]


# ---- the manager's answer, checked -------------------------------------------------------------------
_FENCE = re.compile(r"```[A-Za-z]*[ \t]*\n?(.*?)\n?[ \t]*```", re.S)


def parse_plan(text):
    """(object, None) when `text` is exactly one JSON object, bare or in one code fence; else (None, why).
    A run's output is unchecked text: prose around the JSON, a second object or a second fence is refused."""
    if isinstance(text, dict):
        return text, None
    if not isinstance(text, str) or not text.strip():
        return None, "the manager's answer is empty"
    s = text.strip()
    m = _FENCE.fullmatch(s)
    if m:
        s = m.group(1).strip()
    if "```" in s:
        return None, "the answer has more than one code fence, or text around its fence"
    try:
        obj, end = json.JSONDecoder().raw_decode(s)
    except json.JSONDecodeError as exc:
        return None, f"the answer is not one JSON object ({exc.msg} at character {exc.pos})"
    if s[end:].strip():
        return None, "the answer has text or a second JSON value after the first object"
    if not isinstance(obj, dict):
        return None, f"the answer is a JSON {type(obj).__name__}, not one object"
    return obj, None


def _same(action):
    return "verify" if action == "no_action" else action       # no_action runs as verify


def same_action(action):
    """The fix an action runs as: no_action runs as verify."""
    return _same(action)


def repeat_refused(plan, reason):
    """The fix a refusal says must not run again (rule 6: it already ran and failed its check, or the
    pod_rollback budget is spent), or None. The scripted fallback skips it as well."""
    if AGAIN not in (reason or ""):
        return None
    obj, _ = parse_plan(plan)
    return _same(obj.get("action")) if isinstance(obj, dict) else None


def _redo_key(args):
    args = args or {}
    return (args.get("from_step"), args.get("correction") or "none", args.get("dx_mm"), args.get("dy_mm"))


def _repeats(t, action, args):
    """Is the tried entry `t` the same fix as (action, args)? A redo_step is the same only with the same
    from_step and correction; a relocate never is, since it measures the block afresh (inferred)."""
    if action in NOT_FIXES or _same(t.get("action")) != _same(action):
        return False
    if action != "redo_step":
        return True
    return (args or {}).get("correction") != "relocate" and _redo_key(t.get("args")) == _redo_key(args)


def check_plan(plan, esc, state):
    """(True, "ok") or (False, reason); the checks in the design's order, the first failure stops the plan.

    plan:  the run's output text (or an already-parsed dict).
    esc:   the arm-escalation/v1 packet the plan answers.
    state: what the worker read from the host just before checking (missing keys pass):
      incident_open    the packet's incident is still open on the host
      halted           the arm is still halted (an incident is open, so motion is blocked)
      now, asked_at    wall-clock times; the plan is late after asked_at + esc.deadline_s
      heals_this_job   heals already done in this job (budget MAX_HEALS)
      redos_this_job   redo_step plans already run in this job (budget MAX_REDOS)
      tried            [{action, verified}] for this incident: the packet's, plus what ran since
    A reason that starts with SAFETY ("touches_safety") means: park and hand off, don't fall back."""
    obj, why = parse_plan(plan)
    if obj is None:
        return False, why
    errs = validate(obj, FIXPLAN_SCHEMA)
    if errs:
        return False, "the plan fails the arm-fixplan/v1 schema: " + "; ".join(errs[:4])
    # 2 the right incident, still open
    if obj["incident_id"] != esc.get("incident_id"):
        return False, f"the plan is for incident {obj['incident_id']!r}, not {esc.get('incident_id')!r}"
    if state.get("incident_open") is False:
        return False, f"incident {esc.get('incident_id')} is no longer open"
    # 3 an allowed action
    action = obj["action"]
    allowed = esc.get("allowed_actions") or []
    if action not in allowed:
        return False, f"{action} is not on this incident's list ({', '.join(allowed)})"
    # 4 valid arguments
    args = obj.get("args") or {}
    if action != "redo_step" and args:
        return False, f"{action} takes no arguments (got {sorted(args)})"
    if action == "redo_step":
        job = (esc.get("state") or {}).get("job") or {}
        steps, failed, start = job.get("steps") or [], job.get("failed_step"), args.get("from_step")
        if not steps:
            return False, "redo_step needs a job, and this incident has none"
        if not start:
            return False, "redo_step needs args.from_step"
        if start not in steps:
            return False, f"from_step {start!r} is not a step of this job ({', '.join(steps)})"
        if failed not in steps or steps.index(start) > steps.index(failed):
            return False, f"from_step {start!r} comes after the failed step {failed!r}"
        corr = args.get("correction") or "none"
        offered = (esc.get("state") or {}).get("corrections")
        if corr != "none" and corr not in (offered or ()):     # a packet that offers none allows none (fail closed)
            return False, f"the correction {corr!r} is not offered for this incident ({', '.join(offered or []) or 'none offered'})"
        if corr != "none" and not (esc.get("state") or {}).get("kinematics"):
            return False, f"the correction {corr!r} needs the arm's kinematics, and they are off"
        if corr != "none" and start not in CORRECT_FROM:
            return False, f"a correction moves the approach, so redo from {' or '.join(CORRECT_FROM)}, not {start!r}"
        has_xy = "dx_mm" in args or "dy_mm" in args
        if corr == "offset":
            if "dx_mm" not in args or "dy_mm" not in args:
                return False, "the offset correction needs both dx_mm and dy_mm"
            if (args["dx_mm"] ** 2 + args["dy_mm"] ** 2) ** 0.5 > MAX_OFFSET_MM:
                return False, f"the offset ({args['dx_mm']}, {args['dy_mm']}) mm is more than {MAX_OFFSET_MM} mm in all"
        elif has_xy:
            return False, f"dx_mm and dy_mm come only with the offset correction, not {corr!r}"
    # 5 no safety changes
    if obj.get("touches_safety"):
        return False, (f"{SAFETY}: the plan says it touches the safety layer; that goes through the Fleet "
                       "Doctor's simulated canary (K17), so the arm stays parked for a person")
    # 6 within budget
    if state.get("heals_this_job", 0) >= MAX_HEALS:
        return False, f"this job already used its {MAX_HEALS} heals"
    tried = list(state.get("tried") or (esc.get("state") or {}).get("tried") or [])
    if action == "pod_rollback" and sum(t.get("action") == "pod_rollback" for t in tried) >= MAX_POD_ROLLBACKS:
        return False, f"pod_rollback already ran {MAX_POD_ROLLBACKS} time for this incident; {AGAIN}"
    if action == "redo_step" and state.get("redos_this_job", 0) >= MAX_REDOS:
        return False, f"redo_step already ran {MAX_REDOS} time{'s' if MAX_REDOS > 1 else ''} in this job"
    if any(_repeats(t, action, args) and t.get("verified") is False for t in tried):
        return False, f"{_same(action)} already ran for this incident and its check failed; {AGAIN}"
    # 7 still current
    now, asked = state.get("now"), state.get("asked_at")
    if now is not None and asked is not None and now > asked + float(esc.get("deadline_s", 0)):
        return False, f"the plan came {now - asked:.0f} s after the ask, past the {esc.get('deadline_s')} s deadline"
    if state.get("halted") is False:
        return False, "the arm is no longer halted, so the plan is out of date"
    return True, "ok"


if __name__ == "__main__":
    # python -m armsim.escalation schemas DIR: write both schemas as JSON files (hermes-manager/ ships them).
    import sys
    from pathlib import Path
    if len(sys.argv) != 3 or sys.argv[1] != "schemas":
        raise SystemExit("usage: python -m armsim.escalation schemas DIR")
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for name, schema in (("arm-escalation.v1.schema.json", ESCALATION_SCHEMA), ("arm-fixplan.v1.schema.json", FIXPLAN_SCHEMA)):
        (out / name).write_text(json.dumps(schema, indent=1) + "\n")
        print(out / name)
