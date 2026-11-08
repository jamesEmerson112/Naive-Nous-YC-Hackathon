"""Held-out verify scripts for `device/`, generated from the healer benchmark (blocker B1).

`device/` ships no `verify/`, but `bench/tasks.py` plants five software faults (D1, D2, D3,
S1, S2), each with the scenario that triggers it. Each is caught by a SPEC.md property on
the fake hardware (no arm, Ollama or LLM), so it runs in the replay's offline sandbox. A5
(missing gripper servo) is hardware: the right move is park and report with no code change,
so it has nothing for a code replay to verify.

The bench's own hidden check is exact-match on arm commands, which bench/README.md keeps
for the benchmark only: it would reject a legitimate fix. This verify checks SPEC.md's
properties instead (`verify_template.py`), on every scenario the software tasks use.

`generate` writes, for `CODER_VERIFY_DIR` (the replay copies each to verify/<path>):
- `test_bench_verify.py`: `verify_template.py`;
- `bench_reference.json`: the scenarios, SPEC thresholds frozen from the package's
  config.toml, and the package's sim/ and supervisor/ hashes;
- `bench_monitor.py` and `bench_sim/`: frozen copies of the package's supervisor and fake
  hardware. The verify runs only these, never the candidate's (a patch to
  `supervisor/monitor.py` could otherwise fake the result from inside the check);
- `MANIFEST.json`: `generated_from.code_rev`, which the gate compares with the candidate's
  base lineage, failing closed as "stale verify" (`code_lockin.stale_verify`).

Regenerate (`make device-verify`) when `device/` changes. `planted` applies a task's fault
edits to a package, for tests and demo runs (the base a healer's `code_patch` fixes).

    uv run python -m romulus_cloud.devicecode.verify_from_bench --out data/verify/device
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType

from romulus_cloud.devicecode import package

TEMPLATE = Path(__file__).with_name("verify_template.py")
TEST_FILE, REFERENCE_FILE = "test_bench_verify.py", "bench_reference.json"
MONITOR_FILE, SIM_DIR, MANIFEST_FILE = "bench_monitor.py", "bench_sim", "MANIFEST.json"
SIM_RUNTIME = ("arm_commands.jsonl", "arm_state.json", "arm_faults.json", "__pycache__")
APP_DIR = "robot"  # bench task edits name files in device/robot (its "device/" work area)
POSE_TOL = 0.5  # degrees, as device/tests/test_spec.py
LIFTED_SHARE = 0.8  # "lifted away": at least 80% of lift_max_deg, as device/tests


@contextlib.contextmanager
def _restored_imports() -> Iterator[None]:
    """bench/tasks.py puts device/supervisor on sys.path and imports `monitor`; undo that."""
    path, had = list(sys.path), sys.modules.get("monitor")
    try:
        yield
    finally:
        sys.path[:] = path
        if had is None:
            sys.modules.pop("monitor", None)
        else:
            sys.modules["monitor"] = had


def _load(name: str, path: Path) -> ModuleType:
    """Import a file by path (bench/ and device/supervisor/ aren't packages)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None or not path.is_file():
        raise FileNotFoundError(f"no {path}")
    module = importlib.util.module_from_spec(spec)
    with _restored_imports():
        spec.loader.exec_module(module)
    return module


def load_tasks(bench_dir: Path) -> ModuleType:
    return _load("romulus_bench_tasks", bench_dir / "tasks.py")


def software_scenarios(tasks: ModuleType) -> dict[str, dict]:
    """{scenario name: {"scenario", "tasks"}} over every software task's incident scenario
    plus the regressions the bench's verify adds (normal, hand_close)."""
    out: dict[str, dict] = {}
    for task in tasks.TASKS:
        if task.cause != "software":
            continue
        for scenario in (task.scenario, tasks.NORMAL, tasks.HAND_CLOSE):
            case = out.setdefault(scenario["name"], {"scenario": scenario, "tasks": []})
            if case["scenario"] != scenario:
                raise ValueError(f"two bench scenarios are both named {scenario['name']!r}")
            if task.id not in case["tasks"]:
                case["tasks"].append(task.id)
    return out


def _hashes(device: Path) -> dict[str, str]:
    """Same rule as the template's `_hashes`."""
    out = {}
    for top in ("sim", "supervisor"):
        for p in sorted((device / top).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.name not in SIM_RUNTIME:
                out[p.relative_to(device).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def spec_thresholds(device: Path, monitor: ModuleType) -> dict[str, float]:
    """SPEC.md's numbers from the package's config.toml (what device/tests mirror by hand)."""
    cfg = tomllib.loads((device / APP_DIR / "config.toml").read_text())
    sensor, arm = cfg["sensor"], cfg["arm"]
    return {
        "near_cm": sensor["near_cm"],
        "far_cm": sensor["far_cm"],
        "min_cm": sensor["min_valid_cm"],
        "max_cm": sensor["max_valid_cm"],
        "hold_lift": arm["hold_pose"]["shoulder_lift"],
        "lifted": LIFTED_SHARE * arm["lift_max_deg"],
        "pose_tol": POSE_TOL,
        "gripper_max": monitor.GRIPPER_MAX_WHILE_HOLDING,
    }


def _check_healthy(monitor: ModuleType, device: Path, scenario: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="romulus-bench-ref-") as tmp:
        robot, sim = Path(tmp) / "robot", Path(tmp) / "sim"
        shutil.copytree(device / "robot", robot, ignore=shutil.ignore_patterns("logs", "run"))
        shutil.copytree(device / "sim", sim, ignore=shutil.ignore_patterns(*SIM_RUNTIME))
        incident = monitor.classify(monitor.run_app(robot, sim, scenario))
    if incident is not None:
        raise ValueError(
            f"the canonical app fails {scenario['name']}: {incident['detail']}; "
            "generate verify from a healthy code version"
        )


def generate(bench_dir: Path, files: Mapping[str, bytes], code_rev: str = "") -> dict[str, bytes]:
    """Verify files (paths relative to verify/) for the code package `files`."""
    tasks = load_tasks(bench_dir)
    cases = software_scenarios(tasks)
    out: dict[str, bytes] = {TEST_FILE: TEMPLATE.read_bytes()}
    with tempfile.TemporaryDirectory(prefix="romulus-bench-verify-") as tmp:
        device = package.unpack(dict(files), Path(tmp))
        monitor_path = device / "supervisor" / "monitor.py"
        monitor = _load("romulus_device_monitor", monitor_path)
        for case in cases.values():
            _check_healthy(monitor, device, case["scenario"])
        out[MONITOR_FILE] = monitor_path.read_bytes()
        for p in sorted((device / "sim").rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.name not in SIM_RUNTIME:
                out[f"{SIM_DIR}/{p.relative_to(device / 'sim').as_posix()}"] = p.read_bytes()
        reference = {
            "generated_from": {
                "code_rev": code_rev,
                "bench_tasks": [t.id for t in tasks.TASKS if t.cause == "software"],
            },
            "spec": spec_thresholds(device, monitor),
            "hashes": _hashes(device),
            "scenarios": cases,
        }
    out[REFERENCE_FILE] = (json.dumps(reference, indent=1, sort_keys=True) + "\n").encode()
    manifest = {"generated_from": {"code_rev": code_rev, "by": "verify_from_bench"}}
    out[MANIFEST_FILE] = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode()
    return out


def planted(tasks: ModuleType, task_id: str, files: Mapping[str, bytes]) -> dict[str, bytes]:
    """`files` with bench task `task_id`'s fault planted (each edit must match exactly once)."""
    out = dict(files)
    for name, old, new in tasks.BY_ID[task_id].edits:
        key = f"{package.CODE_ROOT}/{APP_DIR}/{name}"
        text = out[key].decode()
        if text.count(old) != 1:
            raise ValueError(f"{task_id}: {key} has {text.count(old)} matches for its edit")
        out[key] = text.replace(old, new).encode()
    return out


def write(out_dir: Path, verify_files: Mapping[str, bytes]) -> None:
    """Replace `out_dir` with the verify files, so a stale bench_sim/ file can't linger.
    Refuses a non-empty dir this generator didn't write (no MANIFEST.json)."""
    if out_dir.is_dir() and any(out_dir.iterdir()):
        first_layout = {p.name for p in out_dir.iterdir()} <= {TEST_FILE, REFERENCE_FILE}
        if not (out_dir / MANIFEST_FILE).is_file() and not first_layout:
            raise FileExistsError(f"{out_dir} has files this generator didn't write")
        shutil.rmtree(out_dir)
    for name, data in verify_files.items():
        path = out_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="romulus_cloud.devicecode.verify_from_bench")
    parser.add_argument("--repo", default=".", help="the repo (device/ is packaged from git)")
    parser.add_argument("--rev", default=None, help="commit (default: last to touch device/)")
    parser.add_argument("--bench", default=None, help="bench dir (default: <repo>/bench)")
    parser.add_argument("--out", default="data/verify/device", help="CODER_VERIFY_DIR to write")
    args = parser.parse_args(argv)
    repo = package.repo_root(Path(args.repo))
    rev, files = package.from_git(repo, args.rev)
    verify = generate(Path(args.bench) if args.bench else repo / "bench", files, rev)
    write(Path(args.out), verify)
    cases = json.loads(verify[REFERENCE_FILE])["scenarios"]
    print(
        f"verify: wrote {len(verify)} files to {args.out} for device/ at {rev}: "
        + "; ".join(f"{n} ({', '.join(c['tasks'])})" for n, c in sorted(cases.items()))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
