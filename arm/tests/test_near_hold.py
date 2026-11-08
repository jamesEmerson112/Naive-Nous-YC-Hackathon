"""The v1.8 bad image and the looped, watched job it's demonstrated with.

v1.8 adds a near-object hold to the HC-SR04 driver, and its keep-alive tests the held distance instead of the new
reading: right until something comes within 250 mm, right while it's there, and stuck at it after it leaves.
These tests compile near_hold() from the patch with gcc and check it (and the fix) against the simulator's port,
then run the arm's job (safe_replay.py --loops --watch) on the simulated pod and arm with a simulated hand.

  ~/lerobot-venv/bin/python -m pytest arm/tests -q
"""
import re
import shutil
import subprocess

import pytest

import agent
import heal_test
import safe_replay
from simpod import JOINTS, REST, RELEASE_DIRS, REPO, NearHold, SimPod

V18 = RELEASE_DIRS["v1.8"]
HELD = [JOINTS.index(j) for j in ("shoulder_lift", "elbow_flex", "wrist_flex")]
JOB = "cd .. && python3 -u arm/safe_replay.py --min-mm 200 --on-blocked reflex --arm-port sim-arm "


def added_lines():
    return [ln[1:] for ln in (V18 / "bug.patch").read_text().splitlines() if ln.startswith("+") and not ln.startswith("+++")]


def test_patch_applies_to_v10_and_adds_the_hold_to_the_distance(tmp_path):
    shutil.copytree(REPO / "firmware" / "pod" / "src", tmp_path / "src")
    r = subprocess.run(["git", "apply", "--check", str(V18 / "bug.patch")], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    patch = (V18 / "bug.patch").read_text()
    assert re.findall(r"^\+\+\+ b/(\S+)", patch, re.M) == ["src/hcsr04.c", "src/hcsr04.h"]
    code = "\n".join(added_lines())
    assert "out->dist_mm = near_hold(" in code and "if (held_mm < HCSR04_HOLD_NEAR_MM)" in code
    assert "echo_us" not in code                       # the echo time stays the raw measurement


def hold_program(fixed):
    """near_hold() and its constants, exactly as the patch adds them (fixed: the keep-alive tests mm)."""
    lines = added_lines()
    text = "\n".join(lines)
    defines = "\n".join(ln for ln in lines if ln.startswith("#define HCSR04_"))
    statics = re.search(r"static uint32_t held_mm[^\n]*\nstatic uint32_t held_ms;", text).group(0)
    func = re.search(r"static uint32_t near_hold\(.*?\n\}", text, re.S).group(0)
    if fixed:
        assert func.count("if (held_mm < HCSR04_HOLD_NEAR_MM)") == 1
        func = func.replace("if (held_mm < HCSR04_HOLD_NEAR_MM)", "if (mm < HCSR04_HOLD_NEAR_MM)")
    return """#include <stdint.h>
#include <stdio.h>
%s
%s
%s
int main(void) { unsigned mm, t; while (scanf("%%u %%u", &mm, &t) == 2) printf("%%u\\n", near_hold(mm, t)); return 0; }
""" % (defines, statics, func)


def pings():
    """(true mm, uptime ms) at 10 Hz: open space at 371 mm with the arm passing at 338 mm, a hand at 80 mm for 1 s
    at 30 s, then 60 s of nothing near."""
    out, t = [], 300000
    for i in range(900):
        t += 100
        s = i / 10.0
        mm = 80 + (i % 5) - 2 if 30 <= s < 31 else 338 if 12 <= s < 13 else 371 + (i % 3) - 1
        out.append((mm, t))
    return out


@pytest.mark.skipif(not shutil.which("gcc"), reason="needs a host C compiler")
@pytest.mark.parametrize("fixed", [False, True])
def test_compiled_hold_matches_the_simulator_and_latches_only_when_buggy(tmp_path, fixed):
    (tmp_path / "h.c").write_text(hold_program(fixed))
    subprocess.run(["gcc", "-O1", "-Wall", "-Werror", "-o", str(tmp_path / "h"), str(tmp_path / "h.c")], check=True)
    seq = pings()
    run = subprocess.run([str(tmp_path / "h")], input="".join("%d %d\n" % p for p in seq), capture_output=True,
                         text=True, check=True)
    got = list(map(int, run.stdout.split()))
    sim = NearHold(fixed=fixed)
    assert got == [sim(mm, t) for mm, t in seq]                    # the simulator is the C, ping for ping
    true = [mm for mm, _ in seq]
    assert got[:300] == true[:300]                                 # before the hand: every distance right (arm at 338 too)
    hand = range(300, 310)
    assert all(abs(got[i] - true[i]) <= 4 for i in hand)            # the hand: seen right (the nearest of its pings)
    after = got[316:]                                              # from 0.6 s after the hand left
    if fixed:
        assert after == true[316:]                                 # the hold let go after 500 ms
    else:
        assert set(after) == {min(true[i] for i in hand)}          # stuck at the hand's distance for the last 59 s


def test_v18_reads_right_until_a_hand_comes_then_sticks():
    pod = SimPod("v1.0")
    pod.scenario("start", "08")
    assert pod.findings() == []
    rows = pod.d_rows(20)
    assert all(abs(r["mm"] - r["us"] * 0.1715) <= 2 for r in rows)
    pod.objects.append((pod.t, pod.t + 1.0, 80))
    during = pod.d_rows(10)
    assert all(abs(r["mm"] - r["us"] * 0.1715) <= 4 and r["mm"] < 100 for r in during)    # the hand, seen right
    after = pod.d_rows(30)
    assert len({r["mm"] for r in after}) == 1 and after[0]["mm"] < 100 and all(r["us"] > 5000 for r in after)
    f = {x["kind"]: x for x in pod.findings()}
    assert f["us_mismatch"]["severity"] == "critical" and abs(f["us_mismatch"]["evidence"]["samples"][-1]
                                                              ["mm_from_echo"] - 900) < 10
    ok, msg = safe_replay.check_clear(safe_replay_sensor(pod), 200, 1.5, 6)
    assert not ok and msg.startswith("object at")
    pod.pod_cmd("reset")                                          # a reboot clears it, until the next hand
    assert pod.findings() == []


def safe_replay_sensor(pod):
    from simpod import FakeSensor
    return FakeSensor(pod.d_rows(16))


def job(pod, extra):
    code, out, _, _ = pod.sh(JOB + extra)
    return code, out, agent.arm_report(pod, agent.ArmConfig())


def test_watch_pauses_for_a_passing_hand_and_carries_on_where_it_was(tmp_path):
    pod = SimPod("v1.0", runs_dir=tmp_path)
    pod.plan_object(30.0, 1.0, 80)                     # during loop 2's playback
    code, out, rep = job(pod, "--loops 2 --watch")
    pb, p = rep["playback"], rep["watch"]["pauses"]
    assert code == 0 and rep["loops"] == {"planned": 2, "done": 2, "frames_per_loop": 663}
    assert pb["complete"] and pb["frames_played"] == 2 * 663 and not rep["obstacle_reflex"]["ran"]
    assert len(p) == 1 and p[0]["outcome"] == "cleared" and p[0]["loop"] == 2 and p[0]["loops_done"] == 1
    assert 1.0 <= p[0]["held_s"] < 3.0 and "carrying on" in out
    ok, why = heal_test.seen_right(p[0], 200)
    assert ok and not heal_test.stuck(p[0], 200)[0], why
    assert heal_test.loops_ok(rep, 2, True)[0]


def test_watch_backs_off_and_flails_while_the_hand_stays_then_goes_back(tmp_path):
    pod = SimPod("v1.0", runs_dir=tmp_path)
    pod.plan_object(30.0, 7.0, 80)                     # longer than --hold-s: the reflex runs, then it clears
    code, out, rep = job(pod, "--loops 2 --watch")
    rx, p = rep["obstacle_reflex"], rep["watch"]["pauses"]
    assert code == 0 and rep["playback"]["complete"] and rx["ran"] and rx["cleared"] and rx["episodes"] == 1
    assert p[0]["outcome"] == "reflex cleared" and "backing off" in out and "going back to loop 2" in out
    assert heal_test.loops_ok(rep, 2, True)[0] and not heal_test.motion_ok(rep, True)[0]


def test_watch_on_v18_flails_after_the_hand_left_and_gives_up_at_rest(tmp_path):
    pod = SimPod("v1.0", runs_dir=tmp_path)
    pod.scenario("start", "08")
    pod.plan_object(30.0, 1.0, 80)
    code, out, rep = job(pod, "--loops 5 --watch")
    rx, p = rep["obstacle_reflex"], rep["watch"]["pauses"][0]
    assert code == 4 and "REFLEX GAVE UP" in out and rep["loops"]["done"] == 1
    assert rx["ran"] and not rx["cleared"] and rx["swings"] >= 5 and rx["direction_changes"] >= 3
    assert p["outcome"] == "reflex gave up" and heal_test.seen_right(p, 200)[0] and heal_test.stuck(p, 200)[0]
    track = [t for t in safe_replay_record(tmp_path)["track"] if t[1] == "reflex"]
    assert track and all(abs(t[2][i] - REST[i]) < 1e-9 for t in track for i in HELD)   # flails at rest, folded
    assert all(abs(a - b) < 1e-6 for a, b in zip(pod.arm.pose, REST))                  # and ends there
    assert not heal_test.loops_ok(rep, 5, True)[0]


def safe_replay_record(runs):
    import json
    return json.loads(sorted(runs.glob("*.json"))[-1].read_text())


def test_without_an_arm_the_looped_job_runs_dry(tmp_path):
    """heal_test --no-arm: the same loop, pause and give-up logic, timing and sensor only."""
    pod = SimPod("v1.0", runs_dir=tmp_path)
    pod.scenario("start", "08")
    pod.plan_object(30.0, 1.0, 80)
    cfg = heal_test.loop_job(agent.ArmConfig(runs_dir=str(tmp_path)), heal_test.parser().parse_args([]), 4, False)
    r = agent.run_arm(pod, cfg)
    assert "--dry-run" in cfg.command() and r["exit"] == 4 and pod.arm.moves == 0
