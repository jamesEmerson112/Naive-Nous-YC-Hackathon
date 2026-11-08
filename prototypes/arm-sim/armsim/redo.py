"""Category 2 of bugs, a wrong action (docs/healer-data-levels.md:21-23; docs/worker-manager-healer.md §5
step 9): the arm did the wrong thing -> it is told -> it reverses the action -> the manager corrects the
command -> the arm redoes it -> plain code's check decides. At most 2 redos, then a person.

    python -m armsim.stories redo --manager fake                      # the story, through the dispatcher (sim)
    python -m armsim.dispatch --manager fake --wrong-action redo      # the dispatcher path
    PYTHONPATH=. .venv/bin/python tests/test_redo.py

One wrong action, in order. ScriptedExecutor._step (armsim/dispatch.py) calls Redo.wrong_action() when its
missed-grasp check fires, only with `--wrong-action redo` (which needs `--manager fake|hermes`); with the
default `off` the card is blocked as before.
1. Detect (plain code): after grip or lift the gripper isn't holding the block. A wrong_action incident
   opens in the worker (id wa-<n>, kind grasp_missed, src dispatcher), not in the host's watchdog: nothing
   is broken, and a watchdog incident would block the very moves the redo needs (host.py `skill` op).
2. Reverse (plain code): release -> back off 30 mm (the lift skill, jaws open) -> above_block (reach),
   one `reverse` event a step. The loop goes on only if the arm got back over the block, jaws open.
3. Escalate: an arm-escalation/v1 packet, category wrong_action (allowed: redo_step, park,
   handoff_human, no_action), with the grip's evidence, the 6 s of distance rows up to the miss, and
   state.corrections: what plain code can change about the approach (relocate and offset need the arm's
   kinematics; none always).
4. Plan: the manager answers redo_step with from_step and at most one bounded correction (escalation.py:
   none, relocate, or offset dx_mm/dy_mm within 40 mm), and check_plan checks it. No angles: plain
   code's IK turns a block position into a pose.
5. Redo: the correction first (relocate: the host's `locate` scan, a bounded search with the wrist
   sensor), then the job's steps from from_step through the failed one, the approach moved by the
   correction; one `redo` event a step.
6. Verify (plain code): the same missed-grasp check on the redone grip (and lift): holding. Passed: the
   job goes on from the next step. Failed: back to 2 with the new evidence, at most MAX_REDOS (2) times.
Only on the sim (the host's window op says arm sim) is anything reversed or redone. On the real arm, or
when the host doesn't say (the window op failed, an older host), the card is blocked as with
`--wrong-action off`: none of this has run on hardware yet, so it fails closed.
Anything else (no plan, a manager that wasn't asked because its tools can act, a refused plan, park,
handoff_human, no_action, a fault during the redo, two failed redos, a bug in the redo) ends the same
way: reverse to a safe pose, park, the card is blocked, and a person takes it, and that sticks: the
arm host holds an incident for the person (ScriptedExecutor.hand_to_person: the open fault's incident,
or one the host opens, kind grasp_missed, already handed off), so the dispatcher starts no job until a
person's check at home closes it (Mission Control's "a person fixed it": clear, then `repair verify`).
There is no scripted fallback for a wrong action.

Host side (sim only; armsim/host.py calls these): shift_block() plants the wrong action (`inject
block_shift`, `clear block_shift`), locate() is the scan (`skill locate`), approach_at() is the approach
over a shifted block (`skill approach` with `offset_mm`). check_request() refuses them on the real arm,
without kinematics, or with an offset past MAX_OFFSET_MM: the host re-checks the bound itself.

Events (armsim/events.py, with the job's card/order/job fields): src commander incident (with
category wrong_action) and heal; src worker reverse, escalation, plan_check, redo, plan_action, handoff;
src manager fix_plan.
"""
import json
import math
import time

from .escalation import ESCALATION_SCHEMA, MAX_OFFSET_MM, MAX_REDOS, SAFETY, check_plan, parse_plan, validate
from .worker import job_key

COARSE_MM = 15         # the scan's grid: a 30 mm block always covers at least one point
FINE_MM = 2            # each edge is bisected to this
SURFACE_MM = 12        # a reading sees the block when the surface it hits is within this of the block's top
WIDTH_MM = (20, 50)    # what the scan accepts as the block's width, per axis (the sim's block is 30 mm)
STALL_DEG = 8.0        # a probe move short by less than this is out of reach (near the base the shoulder
                       # touches the camera mount, 3 deg short); more, or 3 short moves in a row, is a stall
LIFT_STEPS = ("grip", "lift")     # the steps whose grasp check is the verify


def short(text, n=160):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


# ---- host side (sim only) -----------------------------------------------------------------------------
def sim_only(arm, *, kinematics=True):
    """Why the arm host can't run a wrong-action op here, or None."""
    if getattr(arm, "kind", "sim") != "sim" or getattr(arm, "m", None) is None:
        return "sim only: the real arm has no simulated block to move or scan for"
    if kinematics and not getattr(arm, "use_ik", True):
        return "needs the arm's kinematics, and they are off"
    return None


def check_request(arm, name, req):
    """None, or why the host refuses `skill locate` or `skill approach` with offset_mm."""
    why = sim_only(arm)
    if why:
        return why
    off = req.get("offset_mm")
    if off is None:
        return None if name == "locate" else "offset_mm is missing"
    if name != "approach":
        return "offset_mm moves the approach only"
    if not (isinstance(off, (list, tuple)) and len(off) == 2 and all(isinstance(v, (int, float)) and
                                                                        not isinstance(v, bool) for v in off)):
        return f"offset_mm must be [dx, dy] in mm, not {off!r}"
    if math.hypot(*off) > MAX_OFFSET_MM:
        return f"offset_mm {list(off)} is more than {MAX_OFFSET_MM} mm from where the poses expect the block"
    return None


def shift_block(arm, dx_mm=0.0, dy_mm=0.0):
    """Plant the wrong action: the block sits dx/dy mm from where the poses expect it (the sim's
    BLOCK_POS), square on the table; (0, 0) puts it back. Someone bumped it, say."""
    import mujoco
    from .model import BLOCK_POS
    dx_mm, dy_mm = float(dx_mm), float(dy_mm)
    if math.hypot(dx_mm, dy_mm) > 150:
        raise ValueError("block_shift is for a bumped block: keep it within 150 mm")
    m, d = arm.m, arm.d
    j = m.body("box").jntadr[0]
    q, v = m.jnt_qposadr[j], m.jnt_dofadr[j]
    d.qpos[q:q + 3] = [BLOCK_POS[0] + dx_mm / 1000, BLOCK_POS[1] + dy_mm / 1000, BLOCK_POS[2]]
    d.qpos[q + 3:q + 7] = [1.0, 0.0, 0.0, 0.0]
    d.qvel[v:v + 6] = 0.0
    mujoco.mj_forward(m, d)
    return {"dx_mm": dx_mm, "dy_mm": dy_mm, "block_mm": [round(float(x) * 1000, 1) for x in d.qpos[q:q + 3]]}


class _Stop(Exception):
    """A probe's move was halted by the watchdog or stalled: the scan ends with that move's result."""

    def __init__(self, result):
        super().__init__("scan stopped")
        self.result = result


def locate(arm, *, read, radius_mm=MAX_OFFSET_MM):
    """Find the block with the wrist sensor, plain code, no model: the fingertips at the block's top
    + 45 mm pointing down, grid points COARSE_MM apart within radius_mm of where the poses expect it,
    nearest first, until one sees the block's top; then bisect out to each of its four edges (FINE_MM).
    `read` is the host's `_median_reading` (3 fresh rows). Returns plain JSON types:
      {"found": True, "offset_mm": [dx, dy], "width_mm": [wx, wy], "probes": n, "seconds": sim s}
      {"found": False, "reason": ..., "probes": n}, or a stopped move's result (halted / a stall)."""
    from .model import BLOCK_HALF, BLOCK_POS, BLOCK_TOP
    from .prims import OPEN
    sid = arm.m.site("ultrasonic").id
    z = BLOCK_TOP + 0.045
    t0, n, short_moves = arm.t, [0], [0]

    def probe(ox, oy):
        """True: the sensor sees the block's top here; False: something lower (the table); None: can't
        get there pointing down."""
        n[0] += 1
        try:
            pose = arm.solve((BLOCK_POS[0] + ox / 1000, BLOCK_POS[1] + oy / 1000, z), seed=list(arm.read().values()))
        except ValueError:
            return None
        pose[5] = OPEN
        r = arm.goto(pose, duration=0.3)
        if r.get("halted"):
            raise _Stop(r)
        if r.get("arrived") is False and r.get("worst_joint"):
            short_moves[0] += 1
            if r.get("max_err_deg", 0) >= STALL_DEG or short_moves[0] >= 3:
                raise _Stop(r)                    # the host's arrival check opens joint_stall
            return None
        short_moves[0] = 0
        mm, _ = read(arm)
        if mm is None:
            return False
        sensor_z = float(arm._belief_data().site_xpos[sid][2]) * 1000      # forward kinematics
        return bool(sensor_z - mm >= BLOCK_TOP * 1000 - SURFACE_MM)

    def edge(seed, axis, sign):
        inside, outside = 0.0, 2 * BLOCK_HALF[axis] * 1000 + FINE_MM    # the far edge is within one width
        while outside - inside > FINE_MM:
            mid = (inside + outside) / 2
            p = list(seed)
            p[axis] += sign * mid
            if probe(*p):
                inside = mid
            else:
                outside = mid                     # unreachable counts as outside
        return seed[axis] + sign * (inside + outside) / 2

    reach = radius_mm + COARSE_MM / 2
    k = int(reach // COARSE_MM)
    grid = sorted(((i * COARSE_MM, j * COARSE_MM) for i in range(-k, k + 1) for j in range(-k, k + 1)
                   if math.hypot(i, j) * COARSE_MM <= reach), key=lambda p: (math.hypot(*p), p))
    try:
        seed = next((p for p in grid if probe(*p)), None)
        if seed is None:
            return {"found": False, "reason": f"no block within {radius_mm} mm of where the poses expect it",
                    "probes": n[0], "seconds": round(arm.t - t0, 1)}
        x0, x1 = edge(seed, 0, -1), edge(seed, 0, 1)
        cx = (x0 + x1) / 2
        y0, y1 = edge((cx, seed[1]), 1, -1), edge((cx, seed[1]), 1, 1)
    except _Stop as stop:
        return {**stop.result, "found": False, "reason": "the scan stopped: a move was halted or stalled",
                "probes": n[0]}
    off, width = [round(cx), round((y0 + y1) / 2)], [round(x1 - x0, 1), round(y1 - y0, 1)]
    out = {"probes": n[0], "seconds": round(arm.t - t0, 1), "offset_mm": off, "width_mm": width}
    if not all(WIDTH_MM[0] <= w <= WIDTH_MM[1] for w in width):
        return {**out, "found": False, "reason": f"what the scan saw is {width[0]:g} x {width[1]:g} mm, not the block"}
    if math.hypot(*off) > radius_mm:
        return {**out, "found": False, "reason": f"the block is {math.hypot(*off):.0f} mm away, past the "
                                                 f"{radius_mm} mm a correction may move the approach"}
    return {**out, "found": True}


def approach_at(arm, poses, dx_mm, dy_mm, *, approach):
    """The host's approach (host.approach, passed in), over a block dx/dy mm from where the poses expect
    it: start 45 mm over its top, step down over it, then the final move solved for it, jaws open."""
    from .model import BLOCK_POS, BLOCK_TOP
    from .prims import OPEN
    x, y = BLOCK_POS[0] + dx_mm / 1000, BLOCK_POS[1] + dy_mm / 1000
    try:
        above = arm.solve((x, y, BLOCK_TOP + 0.045), seed=list(poses["above_block"]))
        pick = arm.solve((x, y, BLOCK_TOP - 0.015), seed=list(poses["pick"]))
    except ValueError as exc:
        return {"arrived": False, "aborted": f"can't reach the corrected approach: {exc}", "offset_mm": [dx_mm, dy_mm]}
    above[5] = pick[5] = OPEN
    r = approach(arm, {**poses, "above_block": above, "pick": pick}, xy=(x, y))
    return {**r, "offset_mm": [dx_mm, dy_mm]}


def run_skill(arm, skills, name, req, *, approach, read):
    """The host's `skill` op for `locate` and for `approach` with offset_mm (check_request passed)."""
    if name == "locate":
        return locate(arm, read=read)
    dx, dy = req["offset_mm"]
    return approach_at(arm, skills.p, float(dx), float(dy), approach=approach)


# ---- worker side: the loop -----------------------------------------------------------------------------
def corrections(esc):
    """What plain code can change about the approach for this packet (state.corrections)."""
    return ["relocate", "offset", "none"] if (esc.get("state") or {}).get("kinematics") else ["none"]


def _skill_text(step, reply):
    from .dispatch import skill_text
    return skill_text(step, reply)


def _locate_text(reply):
    if not reply.get("ok"):
        return f"refused ({short(reply.get('error'), 100)})"
    res = reply.get("result") or {}
    if res.get("found"):
        return (f"found the block {res['offset_mm'][0]:+d}, {res['offset_mm'][1]:+d} mm from where the poses expect "
                f"it ({res['width_mm'][0]:g} x {res['width_mm'][1]:g} mm, {res['probes']} probes, {res['seconds']:g} s)")
    return f"no block found: {res.get('reason')} ({res.get('probes')} probes)"


def _correction_text(args):
    """The correction in words; tolerant of a plan that hasn't been checked yet."""
    corr = (args or {}).get("correction") or "none"
    if corr == "offset":
        return f"offset dx {args.get('dx_mm')} mm, dy {args.get('dy_mm')} mm"
    return {"relocate": "relocate the block", "none": "no correction"}.get(corr, f"correction {corr!r}")


class Redo:
    """The wrong-action loop around the Worker (armsim/worker.py): its manager client, packet saving,
    incident cards and job-card comments. wrong_action() returns (ok, why) for ScriptedExecutor._step."""

    def __init__(self, worker, *, max_redos=MAX_REDOS):
        self.worker, self.max_redos = worker, max_redos
        self.n = 0
        self.redos = {}                       # job run (worker.job_key) -> redo_steps run (check_plan's redos_this_job)

    def _ev(self, ex, kind, text, src="worker", **fields):
        return self.worker.emit(src, kind, text, **ex.ctx, **fields)

    def wrong_action(self, ex, step, reply):
        t0 = time.time()
        self.n += 1
        try:
            return self._run(ex, step, reply, t0)
        except Exception as exc:  # a bug here must not leave the arm loose: park, and a person takes it
            ex.host("skill", name="park")
            why = f"the wrong-action redo failed ({type(exc).__name__}: {exc})"
            ex.hand_to_person(None, why, kind="grasp_missed", summary=f"{step}: the gripper isn't holding the block; {why}")
            try:
                self._ev(ex, "error", f"{why}; the arm parks for a person")
            except Exception:  # noqa: BLE001 (the event log may be what failed)
                pass
            return False, f"{step}: the gripper isn't holding the block, and the redo failed ({type(exc).__name__})"

    def _run(self, ex, step, reply, t0):
        inc = self._incident(ex, step, reply, (getattr(ex, "replies", None) or {}).get("approach"))
        if inc.get("arm") != "sim":               # never run on hardware yet, and fail closed when the host doesn't say
            where = "on the real arm" if inc.get("arm") == "real" else (
                f"here: the arm host didn't say this arm is the sim (arm {inc.get('arm')!r})")
            self._ev(ex, "handoff", f"the wrong-action redo is sim only tonight: nothing is reversed or redone {where}, "
                                    "the card is blocked as before", ok=False, incident_id=inc["id"], step=step)
            return False, f"{step}: the gripper isn't holding the block"
        tried, moved = [], True
        for attempt in range(1, self.max_redos + 1):
            ok, why = self._reverse(ex, inc, step, full=moved)
            if not ok:
                return self._give_up(ex, inc, step, t0, why, reverse=False)
            plan, why = self._ask(ex, inc, step, tried)      # the arm stands reversed over the block meanwhile
            if plan is None:
                return self._give_up(ex, inc, step, t0, why, reverse=False)
            if plan["action"] != "redo_step":
                return self._give_up(ex, inc, step, t0, self._not_redo(plan), reverse=False, action=plan["action"])
            out = self._redo(ex, inc, step, plan)
            key = job_key(ex)
            self.redos[key] = self.redos.get(key, 0) + 1
            tried.append({"action": "redo_step", "verified": out["ok"], "args": plan["args"]})
            detail = (f"the manager ({self.worker.label}) planned redo_step from {plan['args']['from_step']} "
                      f"({_correction_text(plan['args'])}), plain code checked and ran it: {out['detail']}")
            self._ev(ex, "plan_action", f"redo_step: {out['detail']}" + (" (verified)" if out["ok"] else ""),
                     ok=out["ok"], action="redo_step", verified=out["ok"], escalation=self.esc_id, attempt=attempt,
                     incident_id=inc["id"])
            if out["ok"]:
                self._close_inc(ex, True, f"RESULT: healed. {detail}", note=f"Redid {step} ({_correction_text(plan['args'])}).")
                return self._healed(ex, inc, step, t0, detail)
            again = attempt < self.max_redos and not out.get("fault")
            self._close_inc(ex, False, f"BLOCKED: {detail}" + ("; the worker asks the manager again" if again else ""))
            if out.get("fault"):
                return self._give_up(ex, inc, step, t0, out["detail"], reverse=False)
            inc = self._incident(ex, step, out.get("reply") or {}, out.get("approach"), prior=inc, why=out["detail"])
            moved = out.get("moved", True)
        return self._give_up(ex, inc, step, t0, f"the grasp still missed after {self.max_redos} redos", full=moved)

    # ---- 1. detect -------------------------------------------------------------------------------------
    def _incident(self, ex, step, reply, approach_reply, prior=None, why=None):
        """The wrong-action incident (the same id across its redos), with the evidence of this miss."""
        res = reply.get("result") or {}
        g = res.get("grasp")
        failed = next((s for s in LIFT_STEPS if s == reply.get("skill")), step)
        bits = ([f"{failed} closed on nothing: the jaws shut to {abs(g.get('gripper') or 0.0):.1f} (0 = closed) at "
                 f"{g.get('load_nm')} N*m"] if g else [f"the redo failed: {why}"] if why else [f"{step} missed"])
        appr = (approach_reply or {}).get("result") or {}
        readings = appr.get("readings") or []
        if readings:
            bits.append(f"the approach read {readings[0]} -> {readings[-1]} mm over {len(readings)} steps"
                        + (f", shifted {appr['offset_mm'][0]:+g}, {appr['offset_mm'][1]:+g} mm" if appr.get("offset_mm") else ""))
        clock = ex.host("window", seconds=0.1)     # the host's clock: the window's `until` (wall in pod mode)
        inc = {"id": prior["id"] if prior else f"wa-{self.n}", "kind": "grasp_missed", "severity": "warn",
               "src": "dispatcher", "summary": short("; ".join(bits), 300), "category": "wrong_action",
               "opened_at": clock.get("now") if clock.get("ok") else None, "arm": clock.get("arm")}
        again = " again, after the redo" if prior else ""
        ex._ev("incident", f"incident during {step}{again}: grasp_missed (a wrong action): {inc['summary']}",
               step=step, category="wrong_action",
               incident={"id": inc["id"], "kind": inc["kind"], "summary": inc["summary"]})
        return inc

    # ---- 2. reverse -------------------------------------------------------------------------------------
    def _reverse(self, ex, inc, step, full=True):
        """release -> back off 30 mm -> above_block (full=False: only the last, after a redo that never went
        below the scan). (ok, why-not)."""
        last = None
        undo = (("release", "release"), ("back off 30 mm", "lift"), ("go over the block", "reach"))
        for what, name in undo if full else undo[2:]:
            if ex.stop.is_set():
                return False, f"the dispatcher was stopped while reversing {step}"
            r = last = ex.host("skill", name=name)
            fault = r.get("incident") or (None if r.get("ok") else (r.get("incidents") or [None])[0])
            self._ev(ex, "reverse", f"reverse {step}, {what}: {_skill_text(name, r)}", step=step, undo=name,
                     ok=bool(r.get("ok")) and not fault, incident_id=inc["id"])
            if r.get("unreachable"):
                return False, f"the arm host stopped answering while reversing {step}: {r['error']}"
            if fault:
                return False, f"reversing {step} hit a fault ({fault.get('kind')}: {fault.get('summary')}); the arm is parked"
            if not r.get("ok"):
                return False, f"the arm host refused {name} while reversing {step}: {r.get('error')}"
        if (last.get("result") or {}).get("arrived") is False:
            return False, f"reversing {step}: the arm didn't get back over the block ({_skill_text('reach', last)})"
        return True, None

    # ---- 3-4. escalate, plan, check ---------------------------------------------------------------------
    def _ask(self, ex, inc, step, tried):
        """Escalate this incident and check the answer: (plan, said) or (None, why there is none)."""
        from .worker import build_escalation, job_key, worker_answer
        w = self.worker
        self.esc_id = esc_id = f"inc-{w.token}-{inc['id']}-{len(tried) + 1}"
        w.cur = {"iid": inc["id"], "source": "plan", "note": None, "plan": None, "inc_card": None}
        why_not = w.tools_problem()                   # the startup preflight's rule, again, before each ask
        if why_not:
            self._ev(ex, "error", f"the worker didn't ask the manager ({w.label}): {why_not}; there is no scripted "
                                  "redo, so the arm parks for a person", tools=why_not, step=step)
            return None, f"no plan from the manager (it wasn't asked: {why_not})"
        job = {"card_id": ex.ctx.get("card") or "", "steps": list(ex.job_steps), "failed_step": step}
        esc, meta = build_escalation(inc, ex.host, esc_id=esc_id, job=job, tried=tried, device_id=w.device_id,
                                     deadline_s=w.deadline_s, category="wrong_action", status=ex.host("status"))
        esc["state"]["parked"] = False                # the arm stands over the block, jaws open, job paused
        esc["state"]["corrections"] = corrections(esc)
        lm_why = "off"
        if w.lm:
            esc["worker"], lm_why = worker_answer(meta["text"], endpoint=w.lm["endpoint"], model=w.lm["model"],
                                                  idmap=meta["idmap"], reasoning_effort=w.lm.get("reasoning_effort"))
        errs = validate(esc, ESCALATION_SCHEMA)
        if errs:
            self._ev(ex, "error", f"the escalation packet for {inc['id']} is invalid: {'; '.join(errs[:3])}")
            return None, "the worker couldn't build a valid packet"
        path = w._save(esc)
        self._ev(ex, "escalation", f"escalated grasp_missed (a wrong action) during {step} to the manager ({w.label}) "
                                   f"as {esc_id}: allowed {', '.join(esc['allowed_actions'])}; corrections "
                                   f"{', '.join(esc['state']['corrections'])}; worker model: "
                                   + (short(esc["worker"]["message"], 100) if esc["worker"] else lm_why),
                 step=step, escalation=esc_id, category="wrong_action", allowed=esc["allowed_actions"],
                 corrections=esc["state"]["corrections"], tried=tried, worker=esc["worker"], packet=str(path),
                 incident={"id": inc["id"], "kind": inc["kind"], "summary": inc["summary"]})
        w._incident_card(ex, esc, inc, step)
        t_ask = time.time()
        text, why = w.client.ask(esc)
        if text is None:
            self._ev(ex, "fix_plan", f"no plan from the manager ({w.label}): {why}; there is no scripted redo, so the "
                                     "arm parks for a person", src="manager", ok=False, escalation=esc_id,
                     seconds=round(time.time() - t_ask, 1))
            return None, f"no plan from the manager ({why})"
        obj, _ = parse_plan(text)
        if isinstance(obj, dict) and obj.get("action"):
            args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
            redo = f" from {args.get('from_step')} ({_correction_text(args)})" if obj["action"] == "redo_step" else ""
            said = f"{obj['action']}{redo} (confidence {obj.get('confidence')}): {short(obj.get('why'), 160)}"
        else:
            said = f"an answer that isn't a plan: {short(text, 160)}"
        self._ev(ex, "fix_plan", f"the manager ({w.label}) answers {said}", src="manager", ok=True, escalation=esc_id,
                 plan=obj if isinstance(obj, dict) else None, raw=None if isinstance(obj, dict) else short(text, 400),
                 seconds=round(time.time() - t_ask, 1), run=getattr(w.client, "last", {}).get("run_id"))
        # What the host says now, not assumed: the wrong action is the worker's own incident (no host incident),
        # open until the redo ends or the dispatcher stops; the job is still paused where the reverse left it only
        # if the host answers and no watchdog incident parked the arm while the manager was thinking.
        st = ex.host("status")
        state = {"incident_open": not ex.stop.is_set(),
                 "halted": bool(st.get("ok")) and not st.get("incidents"), "now": time.time(), "asked_at": t_ask,
                 "heals_this_job": len(ex.healed), "redos_this_job": self.redos.get(job_key(ex), 0),
                 "tried": tried}
        ok, reason = check_plan(text, esc, state)
        self._ev(ex, "plan_check", (f"plain code checked the plan: ok, {obj['action']} is allowed, bounded and within "
                                    "budget") if ok else f"plain code refused the plan: {reason}",
                 ok=ok, reason=reason, escalation=esc_id)
        if not ok:
            if reason.startswith(SAFETY):
                return None, f"the manager's plan was refused ({reason})"
            w._comment_inc(ex, f"PLAN: refused, {reason}.")
            return None, f"the manager's plan was refused ({reason})"
        w.cur.update(plan=obj)
        w._comment_job(ex, f"PLAN: grasp_missed during {step} (a wrong action) went to the manager ({w.label}) as "
                           f"{esc_id}: it chose {said}; plain code checked it and runs it now.")
        w._comment_inc(ex, f"PLAN: {said}. Plain code's check: ok.")
        return obj, said

    @staticmethod
    def _not_redo(plan):
        why = short(plan.get("why"), 160)
        if plan["action"] == "handoff_human":
            return f"the manager handed it to a person ({why})"
        if plan["action"] == "park":
            return f"the manager chose to keep the arm parked ({why})"
        return f"the manager chose {plan['action']}, which runs as a check, and the grasp still isn't holding ({why})"

    # ---- 5-6. redo, verify ------------------------------------------------------------------------------
    def _redo(self, ex, inc, step, plan):
        """The correction, then the steps from from_step through `step`. {ok, detail, fault?, reply, approach}."""
        args = plan["args"]
        corr, start = args.get("correction") or "none", args["from_step"]
        steps = list(ex.job_steps)
        todo = steps[steps.index(start):steps.index(step) + 1]
        offset, found = None, ""
        if corr == "relocate":
            r = ex.host("skill", name="locate")
            res = r.get("result") or {}
            fault = r.get("incident")
            self._ev(ex, "redo", f"relocate: {_locate_text(r)}", step="locate", ok=bool(res.get("found")) and not fault,
                     result={k: res.get(k) for k in ("found", "offset_mm", "width_mm", "probes", "seconds", "reason")},
                     incident_id=inc["id"])
            if r.get("unreachable") or fault or not r.get("ok"):
                why = r.get("error") or (fault or {}).get("summary") or "refused"
                return {"ok": False, "fault": True, "detail": f"the relocate scan stopped ({short(why, 160)})"}
            if not res.get("found"):
                return {"ok": False, "moved": False, "detail": f"the relocate scan found no block: {res.get('reason')}"}
            offset = res["offset_mm"]
            found = f"the scan found the block {offset[0]:+d}, {offset[1]:+d} mm off; "
        elif corr == "offset":
            offset = [args["dx_mm"], args["dy_mm"]]
        replies = {}

        def did(done):
            return f"reversed {step} (release, back off, back over the block), {found}redid {', '.join(done)}"

        for s in todo:
            if ex.stop.is_set():
                return {"ok": False, "fault": True, "detail": f"the dispatcher was stopped during the redo, before {s}"}
            fields = {"offset_mm": offset} if s == "approach" and offset else {}
            r = replies[s] = ex.host("skill", name=s, **fields)
            fault = r.get("incident") or (None if r.get("ok") else (r.get("incidents") or [None])[0])
            res = r.get("result") or {}
            self._ev(ex, "redo", f"redo {s}" + (f" shifted {offset[0]:+g}, {offset[1]:+g} mm" if fields else "")
                     + f": {_skill_text(s, r)}", step=s, ok=bool(r.get("ok")) and not fault and res.get("arrived") is not False,
                     incident_id=inc["id"], **fields)
            if r.get("unreachable"):
                return {"ok": False, "fault": True, "detail": f"the arm host stopped answering during the redo of {s}"}
            if fault:
                return {"ok": False, "fault": True,
                        "detail": f"the redo of {s} hit a fault ({fault.get('kind')}: {fault.get('summary')}); the arm is parked"}
            if not r.get("ok"):
                return {"ok": False, "fault": True, "detail": f"the arm host refused the redo of {s}: {r.get('error')}"}
            if res.get("arrived") is False:
                return {"ok": False, "detail": f"the redo of {s} didn't arrive ({res.get('aborted') or 'no reason given'})",
                        "reply": r, "approach": replies.get("approach")}
            if s in LIFT_STEPS and res.get("grasp") and not res["grasp"].get("holding"):     # the verify
                return {"ok": False, "detail": f"{did(todo[:todo.index(s) + 1])}, and the redone {s} still closed on nothing",
                        "reply": r, "approach": replies.get("approach")}
        return {"ok": True, "detail": f"{did(todo)}: the check passed, the gripper holds the block"}

    # ---- the ends --------------------------------------------------------------------------------------
    def _healed(self, ex, inc, step, t0, detail):
        secs = round(time.time() - t0, 1)
        ex._ev("heal", f"healed grasp_missed (a wrong action): {detail} ({secs:.0f} s)", step=step, ok=True,
               action="redo_step", verified=True, seconds=secs, category="wrong_action",
               incident={"id": inc["id"], "kind": inc["kind"], "summary": inc["summary"]})
        ex.healed.append({"kind": inc["kind"], "summary": inc["summary"], "step": step, "action": "redo_step",
                          "detail": detail, "seconds": secs, "pod": []})
        return True, None

    def _give_up(self, ex, inc, step, t0, why, *, reverse=True, full=False, action="handoff_human"):
        """Reverse to a safe pose (unless a fault already parked the arm), park, and hand to a person: the
        arm host holds an incident for them (hand_to_person), so the next order waits until a person's check
        at home closes it."""
        if reverse:
            ok, rev_why = self._reverse(ex, inc, step, full=full)
            if not ok:
                why = f"{why}; {rev_why}"
        park = ex.host("skill", name="park")
        held = ex.hand_to_person(None, f"the wrong-action redo gave up: {why}", kind=inc["kind"],
                                 summary=f"{inc['summary']} (a wrong action the redo couldn't fix)")
        secs = round(time.time() - t0, 1)
        self._ev(ex, "handoff", f"handed to a person: {why}; " + ("the arm is parked" if park.get("ok") else
                                                                    f"park refused: {short(park.get('error'), 80)}")
                 + (f"; the arm host holds {held.get('id')} for them" if held.get("ok") else
                    f"; the arm host didn't take the hand-off ({short(held.get('error'), 80)}): no job starts until it does"),
                 ok=False, action=action, incident_id=inc["id"], step=step, held=held.get("id"))
        ex._ev("heal", f"couldn't heal grasp_missed (a wrong action): {why} ({secs:.0f} s)", step=step, ok=False,
               action=action, verified=False, seconds=secs, category="wrong_action",
               incident={"id": inc["id"], "kind": inc["kind"], "summary": inc["summary"]})
        self._close_inc(ex, False, f"BLOCKED: {why}; the arm is parked for a person")
        return False, f"grasp_missed during {step} ({inc['summary']}): {why}; it needs a person"

    def _close_inc(self, ex, ok, text, note=None):
        """RESULT: and done, or BLOCKED: and blocked, on this escalation's INC card (once)."""
        w, cur = self.worker, self.worker.cur or {}
        card = cur.get("inc_card")
        w.cur = None
        if not card:
            return
        w._board(ex, "comment on the incident card", ex.board.comment, card, text)
        if ok:
            w._board(ex, "close the incident card", ex.board.done, card, note=note or "Redid the step.")
        else:
            w._board(ex, "block the incident card", ex.board.move, card, status="blocked")


def make(args, worker):
    """The Redo for dispatch.py's flags, or None (--wrong-action off, or no worker: --manager off)."""
    if getattr(args, "wrong_action", "off") != "redo" or worker is None:
        return None
    return Redo(worker)


if __name__ == "__main__":
    print(json.dumps({"max_redos": MAX_REDOS, "max_offset_mm": MAX_OFFSET_MM, "coarse_mm": COARSE_MM,
                      "fine_mm": FINE_MM}, indent=1))
