"""The two deterministic checks that see the fault before any model does: podd's us_mismatch triage rule and
the arm's start gate (arm/safe_replay.py), on the v1.7 image and on v1.0."""
import pytest

from simpod import FakeSensor, SimPod, mm_v10, mm_v17
import safe_replay
from healer import detect


def kinds(pod):
    return {f["kind"]: f["severity"] for f in pod.findings()}


@pytest.mark.parametrize("distance_mm", [250, 900, 2500, 3900])
def test_v17_opens_a_critical_us_mismatch_at_any_distance(distance_mm):
    pod = SimPod("v1.7", distance_mm)
    f = [x for x in pod.findings() if x["kind"] == "us_mismatch"]
    assert f and f[0]["severity"] == "critical"
    ev = f[0]["evidence"]
    assert ev["mismatched"] == ev["n"] and ev["mm_reported_range"][1] <= 32
    assert abs(ev["samples"][-1]["mm_from_echo"] - distance_mm) < 10      # the echo still knows the truth
    assert "us_blocked" not in kinds(pod)          # wrong mm with a long echo is not "something covers it"


@pytest.mark.parametrize("distance_mm", [250, 900, 2500, 3900])
def test_v10_is_clean(distance_mm):
    assert kinds(SimPod("v1.0", distance_mm)) == {}


def test_a_really_covered_sensor_is_not_a_firmware_fault():
    """Short echo AND short distance, consistent with each other: us_blocked (info), never us_mismatch."""
    rows = [{"ts": 100 - 0.1 * i, "us": us, "mm": mm_v10(us), "st": "ok"} for i, us in enumerate([150, 151, 149] * 6)]
    k = {f["kind"] for f in detect.check_ultrasonic({"D": rows, "A": [], "now": 100})}
    assert "us_blocked" in k and "us_mismatch" not in k


def test_out_of_range_far_echoes_are_checked_too():
    """Nothing within 4 m: v1.0 reports the long echo consistently, v1.7 wraps it like any other."""
    rows = lambda conv: [{"ts": 100 - 0.1 * i, "us": 25000 + i, "mm": conv(25000 + i), "st": "out_of_range"}
                         for i in range(15)]
    assert not detect.check_ultrasonic({"D": rows(mm_v10), "A": [], "now": 100})
    assert [f["kind"] for f in detect.check_ultrasonic({"D": rows(mm_v17), "A": [], "now": 100})] == ["us_mismatch"]


def test_gate_refuses_on_v17_and_passes_on_v10():
    bad = FakeSensor(SimPod("v1.7", 900).d_rows(16))
    ok, msg = safe_replay.check_clear(bad, 200, 1.5, 6)
    assert not ok and msg.startswith("object at") and int(msg.split()[2]) <= 32
    good = FakeSensor(SimPod("v1.0", 900).d_rows(16))
    ok, msg = safe_replay.check_clear(good, 200, 1.5, 6)
    assert ok and "nearest" in msg


def test_check_only_runs_the_gate_and_shows_the_samples():
    """safe_replay.py --check-only: exit 2 with the offending samples on v1.7, exit 0 and no motion on v1.0."""
    pod = SimPod("v1.7")
    code, out, _, _ = pod.sh("cd .. && python3 -u arm/safe_replay.py --min-mm 200 --check-only")
    assert code == 2 and "NOT MOVING" in out and "last samples: mm=" in out and "us=5" in out
    pod.firmware = "v1.0"
    code, out, _, _ = pod.sh("cd .. && python3 -u arm/safe_replay.py --min-mm 200 --check-only")
    assert code == 0 and "start gate passed (--check-only: no motion)" in out and "arm:" not in out
