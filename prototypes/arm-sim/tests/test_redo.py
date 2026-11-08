"""Category 2, a wrong action (armsim/redo.py): a missed grasp is reversed, the manager corrects the
command, the arm redoes it, plain code's check decides; at most 2 redos, then a person. The loop runs
against a fake arm host (test_worker's Host, plus a block that can sit off where the poses expect it)
and the fake Hermes API server (tests/fake_manager.py); the host-side scan and the shifted approach run
on a real SimArm (MuJoCo, no host process, no pod). Run from prototypes/arm-sim:

    PYTHONPATH=. .venv/bin/python tests/test_redo.py      (or: python -m pytest tests/)
"""
import json
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import fake_manager  # noqa: E402
from armsim import dispatch, events, orders  # noqa: E402
from armsim.escalation import ESCALATION_SCHEMA, validate  # noqa: E402
from armsim.manager import ManagerClient  # noqa: E402
from armsim.redo import Redo  # noqa: E402
from armsim.worker import Worker  # noqa: E402
from test_dispatch import TMP, fresh, texts  # noqa: E402
from test_worker import Host  # noqa: E402

PICK = ["go_home", "reach", "approach", "grip", "lift", "release", "go_home"]


class Bench(Host):
    """A fake arm host with a block on the table, dx/dy mm from where the poses expect it.
      block=(30, 0)        where the block is; the jaws hold it if the approach lands within 10 mm of it
      finds=[(15, 0), ...] what successive `skill locate` scans report (None in the list: not found);
                           default: the block itself if it is within 40 mm
      fault_on="approach"  the corrected (redo) approach opens a us_stuck incident and halts
    Every skill reply is the host's shape ({ok, skill, result}); self.skills records (name, offset_mm)."""

    def __init__(self, *, block=(30, 0), finds=None, fault_on=None, arm="sim", window_ok=True, **kw):
        super().__init__(**kw)
        self.block, self.finds, self.fault_on, self.arm = block, None if finds is None else list(finds), fault_on, arm
        self.window_ok = window_ok              # False: an older host without the window op
        self.at, self.holding, self.skills = (0, 0), False, []

    def __call__(self, op, **f):
        if op == "window":
            if not self.window_ok:
                return {"ok": False, "error": "unknown op 'window'"}
            return {**super().__call__(op, **f), "arm": self.arm, "kinematics": self.arm == "sim"}
        if op != "skill" or self.open():
            return super().__call__(op, **f)
        name, off = f["name"], f.get("offset_mm")
        self.calls.append((op, name))
        self.skills.append((name, off))
        ok = {"ok": True, "skill": name}
        if name == self.fault_on and off is not None:
            self.fault_on = None
            inc = self._open("us_stuck", "Distance stuck at 95 mm while the arm moved 25 mm")
            return {**ok, "result": {"arrived": False, "halted": "us_stuck"}, "incident": inc}
        if name == "locate":
            got = self.finds.pop(0) if self.finds else (self.block if math.hypot(*self.block) <= 40 else None)
            if got is None:
                return {**ok, "result": {"found": False, "reason": "no block within 40 mm of where the poses expect it",
                                         "probes": 37, "seconds": 22.2}}
            return {**ok, "result": {"found": True, "offset_mm": list(got), "width_mm": [30.0, 30.0], "probes": 29,
                                     "seconds": 17.4}}
        if name == "approach":
            self.at = tuple(off or (0, 0))
            over = math.dist(self.at, self.block) < 15
            return {**ok, "result": {"arrived": True, "switched_at_mm": 75, "steps": 4 if off else (7 if over else 13),
                                     "readings": [95, 85, 75] if off else ([110, 95, 75] if over else [140, 110, 75]),
                                     **({"offset_mm": list(off)} if off else {})}}
        if name == "grip":
            self.holding = math.dist(self.at, self.block) < 10
            return {**ok, "result": {"grasp": {"holding": self.holding, "gripper": 17.9 if self.holding else 0.0,
                                               "load_nm": 0.41 if self.holding else 0.004}}}
        if name == "lift":
            return {**ok, "result": {"arrived": True, "grasp": {"holding": self.holding}}}
        if name in ("release", "park"):
            self.holding = False
        if name in ("reach", "go_home", "park"):
            self.at = (0, 0)
        return {**ok, "result": {"arrived": True}}


def bench(name, mode="good", *, client=None, **host_kw):
    _, board, log, emit = fresh(name)
    _, card = orders.place(board, "pick", source="test")
    host = Bench(**host_kw)
    srv = None
    if client is None:
        srv, url = fake_manager.start(mode, delay=0.05)
        client = ManagerClient(url, label="fake" if mode == "good" else f"fake-{mode}", poll_s=0.05)
    worker = Worker(client, emit=emit, deadline_s=10, packet_dir=TMP / f"{name}-packets")
    ex = dispatch.ScriptedExecutor(board, call=host, emit=emit, poll_s=0.01, pod_clear_s=0.05, hold_s=0.05,
                                   worker=worker, redo=Redo(worker))
    d = dispatch.Dispatcher(board, ex, call=host, emit=emit, every=0.01, sleep=lambda s: None)
    jobs = d.run(once=True)
    if srv:
        srv.shutdown()
    return board, card, host, log, jobs


def evs(log, kind=None):
    return [e for e in events.read(log) if kind is None or e["kind"] == kind]


def flow(log):
    keep = ("incident", "reverse", "escalation", "fix_plan", "plan_check", "redo", "plan_action", "handoff", "heal",
            "job_result")
    return [e["kind"] for e in evs(log) if e["kind"] in keep]


def inc_cards(board):
    return [c for c in board.show() if c["title"].startswith("INC ")]


def test_a_missed_grasp_is_reversed_relocated_and_redone():
    board, card, host, log, jobs = bench("relocate", block=(30, 0))
    c = board.card(card["id"])
    assert c["status"] == "done" and "healed grasp_missed" in c["note"], c
    assert flow(log) == ["incident", "reverse", "reverse", "reverse", "escalation", "fix_plan", "plan_check",
                         "redo", "redo", "redo", "plan_action", "heal", "job_result"], flow(log)
    assert host.skills == [("go_home", None), ("reach", None), ("approach", None), ("grip", None),       # the miss
                           ("release", None), ("lift", None), ("reach", None),                          # reversed
                           ("locate", None), ("approach", [30, 0]), ("grip", None),                     # corrected
                           ("lift", None), ("release", None), ("go_home", None)], host.skills           # job goes on
    inc = evs(log, "incident")[0]
    assert inc["src"] == "commander" and inc["category"] == "wrong_action" and inc["incident"]["kind"] == "grasp_missed"
    assert "grip closed on nothing" in inc["text"] and "140 -> 75 mm over 3 steps" in inc["text"], inc["text"]
    packet = json.loads(Path(evs(log, "escalation")[0]["packet"]).read_text())
    assert validate(packet, ESCALATION_SCHEMA) == [], validate(packet, ESCALATION_SCHEMA)
    assert packet["category"] == "wrong_action" and packet["findings"][0]["src"] == "dispatcher"
    assert packet["allowed_actions"] == ["redo_step", "park", "handoff_human", "no_action"]
    assert packet["state"]["corrections"] == ["relocate", "offset", "none"] and packet["state"]["job"]["failed_step"] == "grip"
    plan = evs(log, "fix_plan")[0]["plan"]
    assert plan["action"] == "redo_step" and plan["args"] == {"from_step": "approach", "correction": "relocate"}, plan
    assert evs(log, "plan_check")[0]["ok"] and evs(log, "plan_action")[0]["verified"] is True
    said = texts(board, card["id"])
    assert any(t.startswith("PLAN: grasp_missed during grip (a wrong action) went to the manager (fake)") for t in said)
    assert said[-1].startswith("RESULT:") and "planned redo_step from approach (relocate the block)" in said[-1] and \
        "the scan found the block +30, +0 mm off" in said[-1], said[-1]
    (ic,) = inc_cards(board)
    assert ic["status"] == "done" and [t.split(":")[0] for t in texts(board, ic["id"])] == ["ESCALATION", "PLAN", "RESULT"]
    assert jobs[0]["outcome"] == "done" and jobs[0]["healed"] == ["grasp_missed"]


def test_the_offset_correction_shifts_the_approach_without_a_scan():
    board, card, host, log, jobs = bench("offset", "redo_offset", block=(30, 0))
    assert board.card(card["id"])["status"] == "done"
    assert ("locate", None) not in host.skills and ("approach", [30, 0]) in host.skills, host.skills
    assert "offset dx 30 mm, dy 0 mm" in texts(board, card["id"])[-1]


def test_a_second_relocate_after_a_bad_first_one_succeeds():
    board, card, host, log, jobs = bench("twice", block=(30, 0), finds=[(15, 0), (30, 0)])
    assert board.card(card["id"])["status"] == "done", texts(board, card["id"])
    assert [o for n, o in host.skills if n == "approach"] == [None, [15, 0], [30, 0]], host.skills
    second = json.loads(Path(evs(log, "escalation")[1]["packet"]).read_text())
    assert second["state"]["tried"] == [{"action": "redo_step", "verified": False,
                                         "args": {"from_step": "approach", "correction": "relocate"}}], second["state"]
    assert "again, after the redo" in evs(log, "incident")[1]["text"]
    assert [c["status"] for c in sorted(inc_cards(board), key=lambda c: c["title"])] == ["blocked", "done"]


def test_two_failed_redos_go_to_a_person_and_the_arm_parks():
    board, card, host, log, jobs = bench("far", block=(60, 0))
    assert board.card(card["id"])["status"] == "blocked" and jobs[0]["outcome"] == "blocked"
    assert [n for n, _ in host.skills].count("locate") == 2 and len(evs(log, "escalation")) == 2
    assert host.skills[-3:] == [("locate", None), ("reach", None), ("park", None)], host.skills[-3:]  # back, park
    assert evs(log, "handoff")[0]["ok"] is False and "after 2 redos" in evs(log, "handoff")[0]["text"]
    blocked = texts(board, card["id"])[-1]
    assert blocked.startswith("BLOCKED: grasp_missed during grip") and "it needs a person" in blocked, blocked
    assert all(c["status"] == "blocked" for c in inc_cards(board)) and len(inc_cards(board)) == 2
    assert evs(log, "heal")[-1]["ok"] is False


def test_no_plan_means_no_redo_the_arm_parks_for_a_person():
    board, card, host, log, jobs = bench("refused", "refused", block=(30, 0))
    assert board.card(card["id"])["status"] == "blocked"
    assert "locate" not in [n for n, _ in host.skills] and host.skills[-1] == ("park", None), host.skills
    assert "no plan from the manager" in texts(board, card["id"])[-1]


class Stub:
    """A manager client that answers one fixed plan (no HTTP): for plans the fake server doesn't make."""
    label = "stub"

    def __init__(self, **args):
        self.args, self.asked = args, []

    def ask(self, esc):
        self.asked.append(esc)
        return json.dumps({"schema": "arm-fixplan/v1", "incident_id": esc["incident_id"], "action": "redo_step",
                           "args": self.args, "kind": "grasp_missed", "confidence": 0.7, "touches_safety": False,
                           "why": "stub"}), "ok"


def test_unbounded_or_raw_corrections_are_refused_and_nothing_moves():
    for name, args, want in (("far-offset", {"from_step": "approach", "correction": "offset", "dx_mm": 60, "dy_mm": 0},
                              "60 is above 40"),
                             ("angle", {"from_step": "approach", "shoulder_pan": 12}, "unexpected key 'shoulder_pan'"),
                             ("from-grip", {"from_step": "grip", "correction": "relocate"}, "redo from reach or approach")):
        board, card, host, log, jobs = bench(name, client=Stub(**args), block=(30, 0))
        check = evs(log, "plan_check")[0]
        assert check["ok"] is False and want in check["reason"], (name, check)
        assert board.card(card["id"])["status"] == "blocked"
        assert not [s for s in host.skills[7:] if s[0] in ("locate", "approach", "grip")], (name, host.skills)


def test_a_redo_whose_grip_misses_again_is_fully_reversed_before_the_person():
    board, card, host, log, jobs = bench("miss-twice", block=(30, 0), finds=[(15, 0), (15, 0)])
    assert board.card(card["id"])["status"] == "blocked"
    assert host.skills[-5:] == [("grip", None), ("release", None), ("lift", None), ("reach", None), ("park", None)]
    assert [e["undo"] for e in evs(log, "reverse")] == ["release", "lift", "reach"] * 3, evs(log, "reverse")


def test_a_fault_during_the_redo_stops_it():
    board, card, host, log, jobs = bench("fault", block=(30, 0), fault_on="approach")
    assert board.card(card["id"])["status"] == "blocked"
    assert len(evs(log, "escalation")) == 1                         # no second redo after a real fault
    assert "the redo of approach hit a fault (us_stuck" in texts(board, card["id"])[-1]


def test_without_the_flag_a_missed_grasp_blocks_the_card_as_before():
    _, board, log, emit = fresh("off")
    _, card = orders.place(board, "pick", source="test")
    host = Bench(block=(30, 0))
    ex = dispatch.ScriptedExecutor(board, call=host, emit=emit, poll_s=0.01, pod_clear_s=0.05, hold_s=0.05)
    dispatch.Dispatcher(board, ex, call=host, emit=emit, every=0.01, sleep=lambda s: None).run(once=True)
    assert board.card(card["id"])["status"] == "blocked"
    assert texts(board, card["id"])[-1] == "BLOCKED: grip: the gripper isn't holding the block"
    assert [n for n, _ in host.skills] == ["go_home", "reach", "approach", "grip"] and not evs(log, "reverse")


def test_on_the_real_arm_nothing_is_reversed_or_redone():
    board, card, host, log, jobs = bench("real", block=(30, 0), arm="real")
    assert board.card(card["id"])["status"] == "blocked"
    assert texts(board, card["id"])[-1] == "BLOCKED: grip: the gripper isn't holding the block"
    assert [n for n, _ in host.skills] == ["go_home", "reach", "approach", "grip"], host.skills    # no move after
    assert not evs(log, "reverse") and not evs(log, "escalation") and "sim only" in evs(log, "handoff")[0]["text"]


def test_a_host_that_doesnt_say_sim_gets_no_redo():
    # finding 9: fail closed. The window op failed (an older host, a host error): arm is unknown, so nothing
    # is reversed or redone, as on the real arm
    board, card, host, log, jobs = bench("no-window", block=(30, 0), window_ok=False)
    assert board.card(card["id"])["status"] == "blocked"
    assert texts(board, card["id"])[-1] == "BLOCKED: grip: the gripper isn't holding the block"
    assert [n for n, _ in host.skills] == ["go_home", "reach", "approach", "grip"], host.skills
    assert not evs(log, "reverse") and not evs(log, "escalation")
    assert "didn't say this arm is the sim" in evs(log, "handoff")[0]["text"], evs(log, "handoff")[0]["text"]


def test_a_fault_that_opens_while_the_manager_thinks_makes_the_redo_plan_stale():
    # nit 12: the check reads the host's real state instead of assuming the arm still waits where it was left
    host_ref = {}

    class Faulty(Stub):
        def ask(self, esc):
            host_ref["host"]._open("us_stuck", "Distance stuck at 95 mm")      # the watchdog parks the arm meanwhile
            return super().ask(esc)
    _, board, log, emit = fresh("stale-redo")
    _, card = orders.place(board, "pick", source="test")
    host = host_ref["host"] = Bench(block=(30, 0))
    worker = Worker(Faulty(from_step="approach", correction="relocate"), emit=emit, deadline_s=10,
                    packet_dir=TMP / "stale-redo-packets")
    ex = dispatch.ScriptedExecutor(board, call=host, emit=emit, poll_s=0.01, pod_clear_s=0.05, hold_s=0.05,
                                   worker=worker, redo=Redo(worker))
    dispatch.Dispatcher(board, ex, call=host, emit=emit, every=0.01, sleep=lambda s: None).run(once=True)
    check = evs(log, "plan_check")[0]
    assert check["ok"] is False and "no longer halted" in check["reason"], check
    assert "locate" not in [n for n, _ in host.skills], host.skills                  # nothing was redone
    assert board.card(card["id"])["status"] == "blocked"


def test_a_card_run_again_gets_a_fresh_redo_budget():
    # nit 12: the budget was keyed by card and never reset, so a card a person moved back to todo had none left
    _, board, log, emit = fresh("rerun")
    _, card = orders.place(board, "pick", source="test")
    srv, url = fake_manager.start("good", delay=0.05)
    worker = Worker(ManagerClient(url, label="fake", poll_s=0.05), emit=emit, deadline_s=10,
                    packet_dir=TMP / "rerun-packets")
    host = Bench(block=(60, 0))                                   # past the scan: 2 failed redos, then a person
    ex = dispatch.ScriptedExecutor(board, call=host, emit=emit, poll_s=0.01, pod_clear_s=0.05, hold_s=0.05,
                                   worker=worker, redo=Redo(worker))
    d = dispatch.Dispatcher(board, ex, call=host, emit=emit, every=0.01, sleep=lambda s: None)
    d.run(once=True)
    assert board.card(card["id"])["status"] == "blocked"
    board.move(card["id"], status="todo")                         # a person checks the arm and runs it again
    d.run(once=True)
    srv.shutdown()
    checks = evs(log, "plan_check")
    assert len(checks) == 4 and all(c["ok"] for c in checks), [(c["ok"], c["reason"]) for c in checks]
    assert [n for n, _ in host.skills].count("locate") == 4, host.skills


def test_wrong_action_redo_needs_the_manager():
    try:
        dispatch.main(["--wrong-action", "redo"])
        raise AssertionError("--wrong-action redo without --manager was accepted")
    except SystemExit as exc:
        assert "needs --manager" in str(exc), exc


# ---- the host side, on a real SimArm (no host process) ------------------------------------------------
def test_the_scan_finds_a_shifted_block_and_the_corrected_approach_grasps_it():
    from armsim import prims, redo
    from armsim.arm import SimArm
    from armsim.host import Skills, _median_reading, approach
    arm = SimArm(start=prims.REST)
    sk = Skills(arm)
    assert redo.shift_block(arm, 30, 0)["block_mm"][:2] == [240.0, 0.0]
    sk.go_home()
    sk.reach()
    sk.approach()
    assert sk.grip()["grasp"]["holding"] is False                 # the wrong action: the jaws close on nothing
    sk.release()
    sk.lift()
    sk.reach()
    found = redo.locate(arm, read=_median_reading)
    assert found["found"] is True and math.dist(found["offset_mm"], (30, 0)) <= 3, found
    assert json.loads(json.dumps(found)) == found                 # plain JSON types: the host replies with it
    r = redo.approach_at(arm, sk.p, *found["offset_mm"], approach=approach)
    assert r["arrived"] is True, r
    assert sk.grip()["grasp"]["holding"] is True and sk.lift()["grasp"]["holding"] is True
    for shift in ((-30, 0), (0, 35)):                            # toward the base, and to the side
        arm = SimArm(start=prims.REST)
        sk = Skills(arm)
        redo.shift_block(arm, *shift)
        sk.go_home()
        sk.reach()
        found = redo.locate(arm, read=_median_reading)
        assert found["found"] is True and math.dist(found["offset_mm"], shift) <= 4, (shift, found)


def test_a_stuck_joint_during_the_scan_stops_it_as_a_stall():
    from armsim import level1, prims, redo
    from armsim.arm import SimArm
    from armsim.host import Skills, _median_reading
    arm = SimArm(start=prims.REST)
    sk = Skills(arm)
    redo.shift_block(arm, 30, 0)
    sk.go_home()
    sk.reach()
    arm.inject("joint_stuck", joint=2)
    out = redo.locate(arm, read=_median_reading)
    assert out["found"] is False and out["worst_joint"] == "elbow_flex" and out["probes"] == 1, out
    assert level1.check_arrival(out)[0]["kind"] == "joint_stall"      # what the host's arrival check opens


def test_the_scan_reports_a_block_out_of_range_and_the_host_checks_its_bounds():
    from armsim import prims, redo
    from armsim.arm import SimArm
    from armsim.host import Skills, _median_reading
    arm = SimArm(start=prims.REST)
    sk = Skills(arm)
    redo.shift_block(arm, 70, 0)
    sk.go_home()
    sk.reach()
    out = redo.locate(arm, read=_median_reading)
    assert out["found"] is False and ("past the 40 mm" in out["reason"] or "no block within 40 mm" in out["reason"]), out
    assert redo.check_request(arm, "approach", {"offset_mm": [30, 35]}).startswith("offset_mm [30, 35] is more than 40")
    assert redo.check_request(arm, "reach", {"offset_mm": [5, 0]}) == "offset_mm moves the approach only"
    assert redo.check_request(arm, "approach", {"offset_mm": [True, 0]}).startswith("offset_mm must be")
    assert redo.check_request(arm, "approach", {"offset_mm": [30, 0]}) is None
    arm.use_ik = False
    assert "kinematics" in redo.check_request(arm, "locate", {})
    arm.kind = "real"
    assert redo.check_request(arm, "locate", {}).startswith("sim only")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            t0 = time.time()
            fn()
            print(f"ok  {name}  ({time.time() - t0:.1f} s)")
