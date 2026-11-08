"""Held-out verify scripts for `device/`, generated from the healer benchmark (blocker B1).

`device/` ships no `verify/`, but `bench/tasks.py` has a hidden check per planted fault. For
a software task (D1, D2, D3, S1, S2) it reruns the healed app on a fresh copy of the canonical
sim and requires every arm command to match the canonical app's on the task's scenario and
on the normal and hand_close regressions, with sim/ untouched. That needs only the fake
hardware in `device/sim` (no arm, Ollama or LLM), so it runs in the replay's offline sandbox.
A5 (missing gripper servo) is hardware: the right move is park and report with no code change,
so it has nothing for a code replay to verify.

`generate` writes two files for `CODER_VERIFY_DIR` (the replay copies each to verify/<path>):
- `test_bench_verify.py`: `verify_template.py`, one test per scenario plus a sim/supervisor
  hash check;
- `bench_reference.json`: the canonical app's arm commands per scenario, computed here by
  running the package's own app on its own sim, and the sim/ and supervisor/ hashes.

The reference is the code version you generate from, so regenerate when `device/` changes.
The exact-match check is the benchmark's, deliberately strict: a fix that changes the arm's
behaviour on these scenarios fails it even if SPEC.md allows it (bench/README.md); the
property tests in `device/tests` are the lenient half of the replay.

`planted` applies a task's fault edits to a package, for tests and demo runs (the base a
healer's `code_patch` fixes).

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
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType

from romulus_cloud.devicecode import package

TEMPLATE = Path(__file__).with_name("verify_template.py")
TEST_FILE, REFERENCE_FILE = "test_bench_verify.py", "bench_reference.json"
SIM_RUNTIME = ("arm_commands.jsonl", "arm_state.json", "arm_faults.json", "__pycache__")
APP_DIR = "robot"  # bench task edits name files in device/robot (its "device/" work area)


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


def _run(monitor: ModuleType, device: Path, scenario: dict) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="romulus-bench-ref-") as tmp:
        robot, sim = Path(tmp) / "robot", Path(tmp) / "sim"
        shutil.copytree(device / "robot", robot, ignore=shutil.ignore_patterns("logs", "run"))
        shutil.copytree(device / "sim", sim, ignore=shutil.ignore_patterns(*SIM_RUNTIME))
        result = monitor.run_app(robot, sim, scenario)
        incident = monitor.classify(result)
        if incident is not None:
            raise ValueError(
                f"the canonical app fails {scenario['name']}: {incident['detail']}; "
                "generate verify from a healthy code version"
            )
        return monitor._jsonl(sim / "arm_commands.jsonl")


def generate(bench_dir: Path, files: Mapping[str, bytes], code_rev: str = "") -> dict[str, bytes]:
    """Verify files (paths relative to verify/) for the code package `files`."""
    tasks = load_tasks(bench_dir)
    cases = software_scenarios(tasks)
    with tempfile.TemporaryDirectory(prefix="romulus-bench-verify-") as tmp:
        device = package.unpack(dict(files), Path(tmp))
        monitor = _load("romulus_device_monitor", device / "supervisor" / "monitor.py")
        for case in cases.values():
            case["commands"] = _run(monitor, device, case["scenario"])
        hashes = _hashes(device)
    reference = {
        "generated_from": {
            "code_rev": code_rev,
            "bench_tasks": [t.id for t in tasks.TASKS if t.cause == "software"],
        },
        "hashes": hashes,
        "scenarios": cases,
    }
    return {
        TEST_FILE: TEMPLATE.read_bytes(),
        REFERENCE_FILE: (json.dumps(reference, indent=1, sort_keys=True) + "\n").encode(),
    }


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
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in verify_files.items():
        (out_dir / name).write_bytes(data)


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
        f"verify: wrote {', '.join(sorted(verify))} to {args.out} for device/ at {rev}: "
        + "; ".join(f"{n} ({', '.join(c['tasks'])})" for n, c in sorted(cases.items()))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
