"""Held-out verify generated from the real bench/tasks.py (B1).

Runs the generated scripts the way the coder image's `verify` mode does (`run_verify`:
isolated Python, no conftest, clean config), on the host Python, against the real device/
package: the canonical code passes, each software task's planted fault fails, tampering
with the candidate's supervisor or sim can't rescue a planted fault, and a change SPEC.md
allows still passes.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from romulus_cloud.devicecode import package, verify_from_bench
from romulus_cloud.devicecode.verify_from_bench import (
    MANIFEST_FILE,
    MONITOR_FILE,
    REFERENCE_FILE,
    TEST_FILE,
)

REPO = Path(__file__).resolve().parents[4]
_spec = importlib.util.spec_from_file_location(
    "coder_run_agent_b1", REPO / "cloud" / "coder" / "run_agent.py"
)
run_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_agent)
N_TESTS = 6  # the policy check + one SPEC-property test per scenario (5)

# The reviewer's tamper (PR #38): a tail appended to the candidate's supervisor/monitor.py
# that restores the file's bytes at import (so a hash check passes), then fakes the run.
TAMPER = """

# --- appended by a "fix" ---
import pathlib as _p
_me = _p.Path(__file__)
_src = _me.read_text()
_me.write_text(_src[: _src.index("\\n\\n# --- appended by a")] + "\\n")
classify = lambda result: None
_real_run = run_app
def run_app(device, sim, scenario, *a, **k):
    r = _real_run(device, sim, scenario, *a, **k)
    r["gripper_max"] = 10.0
    return r
"""


@pytest.fixture(scope="module")
def bench():
    return verify_from_bench.load_tasks(REPO / "bench")


@pytest.fixture(scope="module")
def device_files():
    return package.from_git(REPO)[1]


@pytest.fixture(scope="module")
def verify_files(device_files):
    return verify_from_bench.generate(REPO / "bench", device_files, "test")


def _verify(tmp_path, files, verify_files, tamper=None):
    work = package.unpack(dict(files), tmp_path)
    verify_from_bench.write(work / "verify", verify_files)
    if tamper:
        tamper(work)
    return run_agent.run_verify(120, work=str(work), python=sys.executable)


def test_scenarios_cover_every_software_task_and_skip_the_hardware_one(bench, verify_files):
    ref = json.loads(verify_files[REFERENCE_FILE])
    software = sorted(t.id for t in bench.TASKS if t.cause == "software")
    assert software == ["D1", "D2", "D3", "S1", "S2"]  # A5 is hardware: park, no code change
    covered = {task for case in ref["scenarios"].values() for task in case["tasks"]}
    assert sorted(covered) == software
    assert set(ref["scenarios"]) == {
        "normal",
        "hand_close",
        "blind_zone",
        "flaky_bus",
        "sensor_dead",
    }
    assert ref["spec"] == {
        "near_cm": 15.0, "far_cm": 60.0, "min_cm": 2.0, "max_cm": 400.0, "hold_lift": -20.0,
        "lifted": 28.0, "pose_tol": 0.5, "gripper_max": 15.0,
    }  # fmt: skip
    assert "commands" not in ref["scenarios"]["normal"]  # no exact-match reference
    assert verify_files[MONITOR_FILE] == (REPO / "device/supervisor/monitor.py").read_bytes()
    assert "bench_sim/fake_arm.py" in verify_files and "bench_sim/traces/normal.csv" in verify_files
    assert json.loads(verify_files[MANIFEST_FILE])["generated_from"]["code_rev"] == "test"


def test_generation_is_deterministic(device_files, verify_files):
    assert verify_from_bench.generate(REPO / "bench", device_files, "test") == verify_files


def test_canonical_device_code_passes_every_verify_test(tmp_path, device_files, verify_files):
    result = _verify(tmp_path, device_files, verify_files)
    assert result["exit_code"] == 0, result["output"]
    assert (result["tests"], result["passed"], result["skipped"]) == (N_TESTS, N_TESTS, 0)


@pytest.mark.parametrize("task_id", ["D1", "D2", "D3", "S1", "S2"])
def test_each_planted_fault_fails_verify(tmp_path, bench, device_files, verify_files, task_id):
    planted = verify_from_bench.planted(bench, task_id, device_files)
    assert planted != device_files
    result = _verify(tmp_path, planted, verify_files)
    assert result["exit_code"] != 0
    assert result["passed"] < result["tests"] == N_TESTS


def test_a_tampered_monitor_cannot_rescue_planted_d1(tmp_path, bench, device_files, verify_files):
    """Reviewer finding 2: verify imports only its own frozen supervisor."""

    def tamper(work):
        mon = work / "supervisor" / "monitor.py"
        mon.write_text(mon.read_text().rstrip("\n") + TAMPER)

    planted = verify_from_bench.planted(bench, "D1", device_files)
    result = _verify(tmp_path, planted, verify_files, tamper=tamper)
    assert result["exit_code"] != 0
    assert "test_spec_properties_hold[hand_close]" in result["output"]
    assert "FAILED" in result["output"] and "gripper opened" in result["output"]


def test_a_tampered_sim_cannot_rescue_planted_d1(tmp_path, bench, device_files, verify_files):
    def tamper(work):  # a fake arm that logs every gripper command as closed
        arm = work / "sim" / "fake_arm.py"
        text = arm.read_text()
        old = '        self.log.write(json.dumps(action) + "\\n")\n'
        assert old in text
        new = '        self.log.write(json.dumps({**action, "gripper.pos": 10.0}) + "\\n")\n'
        arm.write_text(text.replace(old, new))

    planted = verify_from_bench.planted(bench, "D1", device_files)
    result = _verify(tmp_path, planted, verify_files, tamper=tamper)
    assert "test_spec_properties_hold[hand_close]" in result["output"]
    assert result["exit_code"] != 0


def test_a_changed_sim_fails_the_policy_check(tmp_path, device_files, verify_files):
    files = dict(device_files)
    files["device/sim/fake_arm.py"] += b"\n# healer was here\n"
    result = _verify(tmp_path, files, verify_files)
    assert result["exit_code"] != 0 and result["passed"] == N_TESTS - 1
    assert "test_policy_sim_and_supervisor_untouched" in result["output"]


def test_a_change_spec_allows_still_passes(tmp_path, device_files, verify_files):
    """Different arm commands (exact-match would reject it), same SPEC properties."""
    files = dict(device_files)
    cfg = files["device/robot/config.toml"].decode()
    files["device/robot/config.toml"] = cfg.replace("wrist_roll = 0.0", "wrist_roll = 5.0").encode()
    assert files != device_files
    result = _verify(tmp_path, files, verify_files)
    assert result["exit_code"] == 0, result["output"]


def test_generate_refuses_a_code_version_that_already_fails(bench, device_files):
    with pytest.raises(ValueError, match="canonical app fails"):
        verify_from_bench.generate(
            REPO / "bench", verify_from_bench.planted(bench, "S1", device_files)
        )


def test_loading_the_bench_leaves_sys_path_and_modules_alone():
    path, had = list(sys.path), "monitor" in sys.modules
    verify_from_bench.load_tasks(REPO / "bench")
    assert sys.path == path
    assert ("monitor" in sys.modules) == had


def test_cli_writes_the_verify_dir_and_refuses_a_foreign_one(tmp_path, capsys):
    out = tmp_path / "v"
    assert verify_from_bench.main(["--repo", str(REPO), "--out", str(out)]) == 0
    assert {p.name for p in out.iterdir()} >= {
        REFERENCE_FILE,
        TEST_FILE,
        MONITOR_FILE,
        MANIFEST_FILE,
    }
    assert "D1" in capsys.readouterr().out
    assert verify_from_bench.main(["--repo", str(REPO), "--out", str(out)]) == 0  # rewrite ok
    foreign = tmp_path / "mine"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("keep me")
    with pytest.raises(FileExistsError):
        verify_from_bench.main(["--repo", str(REPO), "--out", str(foreign)])
    assert (foreign / "notes.txt").exists()
