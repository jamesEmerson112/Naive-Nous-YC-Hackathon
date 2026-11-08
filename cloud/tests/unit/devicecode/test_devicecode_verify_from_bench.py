"""Held-out verify generated from the real bench/tasks.py (B1).

Runs the generated scripts the way the coder image's `verify` mode does (`run_verify`:
isolated Python, no conftest, clean config), on the host Python, against the real device/
package: the canonical code passes every test, and each software task's planted fault fails.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from romulus_cloud.devicecode import package, verify_from_bench
from romulus_cloud.devicecode.verify_from_bench import REFERENCE_FILE, TEST_FILE

REPO = Path(__file__).resolve().parents[4]
_spec = importlib.util.spec_from_file_location(
    "coder_run_agent_b1", REPO / "cloud" / "coder" / "run_agent.py"
)
run_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_agent)


@pytest.fixture(scope="module")
def bench():
    return verify_from_bench.load_tasks(REPO / "bench")


@pytest.fixture(scope="module")
def device_files():
    return package.from_git(REPO)[1]


@pytest.fixture(scope="module")
def verify_files(device_files):
    return verify_from_bench.generate(REPO / "bench", device_files, "test")


def _verify(tmp_path, files, verify_files):
    work = package.unpack(dict(files), tmp_path)
    verify_from_bench.write(work / "verify", verify_files)
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
    assert all(case["commands"] for case in ref["scenarios"].values())
    assert set(ref["hashes"]) >= {"sim/fake_arm.py", "supervisor/monitor.py"}
    assert sorted(verify_files) == [REFERENCE_FILE, TEST_FILE]


def test_generation_is_deterministic(device_files, verify_files):
    assert verify_from_bench.generate(REPO / "bench", device_files, "test") == verify_files


def test_canonical_device_code_passes_every_verify_test(tmp_path, device_files, verify_files):
    result = _verify(tmp_path, device_files, verify_files)
    assert result["exit_code"] == 0, result["output"]
    assert (result["tests"], result["passed"], result["skipped"]) == (6, 6, 0)


@pytest.mark.parametrize("task_id", ["D1", "D2", "D3", "S1", "S2"])
def test_each_planted_fault_fails_verify(tmp_path, bench, device_files, verify_files, task_id):
    planted = verify_from_bench.planted(bench, task_id, device_files)
    assert planted != device_files
    result = _verify(tmp_path, planted, verify_files)
    assert result["exit_code"] != 0
    assert result["passed"] < result["tests"] == 6


def test_a_changed_sim_fails_verify(tmp_path, device_files, verify_files):
    files = dict(device_files)
    files["device/sim/fake_arm.py"] += b"\n# healer was here\n"
    result = _verify(tmp_path, files, verify_files)
    assert result["exit_code"] != 0
    assert "test_sim_and_supervisor_are_canonical" in result["output"]


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


def test_cli_writes_the_verify_dir(tmp_path, capsys):
    assert verify_from_bench.main(["--repo", str(REPO), "--out", str(tmp_path / "v")]) == 0
    assert sorted(p.name for p in (tmp_path / "v").iterdir()) == [REFERENCE_FILE, TEST_FILE]
    assert "D1" in capsys.readouterr().out
