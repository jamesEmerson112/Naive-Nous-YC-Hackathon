"""F11 on the real device code (B1, B3): the repo's device/, packaged from git, with bench
fault D1 planted, is the fleet's code. sim-3 reports D1 with a `code_patch` in Ishaan's path
format (`--- a/robot/control.py`, relative to device/; also the `device/`-prefixed form).
The replay runs device/'s own suite and the held-out verify generated from bench/tasks.py
(`CODER_VERIFY_DIR`), and the code candidate waits at `awaiting_canary` (B7).

- The always-on tests play the agent with the bench's oracle (undo the planted rule) through
  `FakeDocker`, which runs the real device tests and verify scripts on the host.
- `test_f11_real_device_with_the_real_coding_agent` is the one real run: mini-swe-agent in the
  real `romulus-coder` image, the real LLM, at most 15 steps and 5 minutes. Opt-in:

      CODER_INTEGRATION=1 LLM_BASE_URL=... LLM_MODEL=... LLM_API_KEY=... \\
          uv run pytest cloud/tests/flows/test_f11_real_device.py -s -k real_coding_agent

  The LLM_* variables are read at import, because conftest unsets settings variables.
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from functools import partial
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from fake_device.scenarios import SCENARIOS
from romulus_cloud import events
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.coder import LocalAgent, Sandbox, subprocess_runner
from romulus_cloud.adapters.llm import FakeLlm
from romulus_cloud.devicecode import package, verify_from_bench
from romulus_cloud.models import CodeVersion, Device, Overlay
from romulus_cloud.services import batch, code_lockin, gate, registry
from romulus_cloud.services.jobs import InlineJobs
from romulus_cloud.services.seed import FIRMWARE_V1
from romulus_cloud.worker import Step, run_tick

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
S = timedelta(seconds=1)
KW = {"actor": "human:test", "actor_class": "human"}
KEY = "sk-test-B1-SECRET"
CLEAN = "dev0000"  # device/ as the repo has it: what the verify is generated from
BASE = "dev0d1p"  # device/ with bench fault D1 planted (child of CLEAN)
B7 = "no canary device runs real device code (B7)"
LLM = {k: os.environ.get(k, "") for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")}


@pytest.fixture(scope="module")
def bench():
    tasks = verify_from_bench.load_tasks(REPO / "bench")
    files = package.from_git(REPO)[1]
    planted = verify_from_bench.planted(tasks, "D1", files)
    return {
        "files": files,
        "planted": planted,
        "verify": verify_from_bench.generate(REPO / "bench", files, CLEAN),
        # Ishaan's format: relative to device/, `--- a/robot/control.py`.
        "patch": code_lockin.package_diff(planted, files),
        "drop_rule": tasks.DROP_RULE,
    }


def device_prefixed(patch: str) -> str:
    for side in ("a/", "b/"):
        patch = patch.replace(f"{side}robot/", f"{side}device/robot/")
    return patch


@pytest.fixture
def settings(make_settings, bench, tmp_path):
    verify_dir = tmp_path / "verify_device"
    verify_from_bench.write(verify_dir, bench["verify"])
    return make_settings().model_copy(
        update={
            "coder_provider": "local_agent",
            "coder_verify_dir": str(verify_dir),
            "llm_base_url": LLM["LLM_BASE_URL"] or "http://llm.invalid/v1",
            "llm_model": LLM["LLM_MODEL"] or "test-model",
            "llm_api_key": SecretStr(LLM["LLM_API_KEY"] or KEY),
        }
    )


@pytest.fixture
def real_fleet(seeded, settings, bench):
    """Code dev0000 (device/), its child dev0d1p (+ D1), and firmware v2 naming dev0d1p, on
    every device."""
    store = build_blobstore(settings.blob_dir)
    registry.create_code(seeded, store, registry.build_tarball(bench["files"]), rev=CLEAN, **KW)
    tarball = registry.build_tarball(bench["planted"])
    registry.create_code(seeded, store, tarball, rev=BASE, parent_rev=CLEAN, **KW)
    registry.create_firmware(seeded, FIRMWARE_V1 | {"version": 2, "code_rev": BASE}, **KW)
    for d in seeded.scalars(select(Device)):
        d.assigned_firmware = 2
    seeded.commit()
    return seeded


def rig_steps(settings, coder, sandbox):
    store = build_blobstore(settings.blob_dir)
    jobs = InlineJobs()
    tools = batch.Tools(settings, FakeLlm(), coder, store, jobs)
    code = code_lockin.ReplayTools(settings, jobs, sandbox, store)
    return [
        Step("gate.tick", partial(gate.tick, settings=settings, code=code)),
        Step("batch.maybe_run", partial(batch.maybe_run, settings=settings, tools=tools)),
    ]


def run_d1(client, arms, tick_arms, now, clock, sessions, steps, patch, monkeypatch):
    monkeypatch.setattr("fake_device.scenarios.D1_CODE_PATCH", patch)
    tick_arms(now)
    tick_arms(now + 10 * S)
    for i, step in enumerate(SCENARIOS["code"](arms["sim-3"], 0)):
        step.run(now + 20 * S + i * 10 * S)
    for t in (40, 41):
        clock.at(now + t * S)
        results = run_tick(steps, sessions, now + t * S, {})
        assert set(results.values()) == {"ok"}, results
    cands = {c["type"]: c for c in client.get("/v1/candidates").json()}
    if "code" not in cands:  # say why: the overlay's reason carries the draft's notes
        ov = arms["sim-3"].device.overlay
        row = sessions().get(Overlay, ("sim-3", ov.overlay_id)) if ov else None
        pytest.fail(f"no code candidate; overlay {row and row.status}: {row and row.status_reason}")
    return client.get(f"/v1/candidates/{cands['code']['id']}").json()


@pytest.mark.parametrize("form", ["relative", "device_prefixed"])
def test_f11_real_device_d1_patch_reaches_awaiting_canary(
    client, arms, tick_arms, now, clock, sessions, settings, real_fleet, bench, db_session,
    monkeypatch, form,
):  # fmt: skip
    def oracle(work: Path) -> None:  # the bench's reference fix: undo the planted rule
        control = work / "robot" / "control.py"
        control.write_text(control.read_text().replace(bench["drop_rule"], ""))

    patch = bench["patch"] if form == "relative" else device_prefixed(bench["patch"])
    assert "--- a/robot/control.py" in bench["patch"]
    docker = fakes.FakeDocker(agent=oracle)
    sandbox = Sandbox(image="romulus-coder:test", runner=docker)
    coder = LocalAgent(base_url="http://llm.invalid/v1", model="m", api_key=KEY, sandbox=sandbox)
    detail = run_d1(
        client, arms, tick_arms, now, clock, sessions, rig_steps(settings, coder, sandbox),
        patch, monkeypatch,
    )  # fmt: skip

    cv = db_session.get(CodeVersion, ("default", detail["code_rev"]))
    assert cv.source == "coder" and cv.parent_rev == BASE
    assert detail["status"] == "awaiting_canary", detail
    replay = detail["replay"]
    assert replay["passed"] and not replay["stub"]
    # The planted base fails device/'s suite and the bench verify; the fix passes all 6.
    assert "base: tests fail" in replay["why"] and "verify 4/6 passed" in replay["why"]
    assert "patched: tests pass, verify 6/6 passed" in replay["why"]
    waiting = [e for e in detail["events"] if e["kind"] == "candidate.awaiting_canary"]
    assert B7 in waiting[0]["why"]
    assert not detail["touches_safety"]


def _oracle_coder(bench):
    def oracle(work: Path) -> None:
        control = work / "robot" / "control.py"
        control.write_text(control.read_text().replace(bench["drop_rule"], ""))

    sandbox = Sandbox(image="romulus-coder:test", runner=fakes.FakeDocker(agent=oracle))
    coder = LocalAgent(base_url="http://llm.invalid/v1", model="m", api_key=KEY, sandbox=sandbox)
    return coder, sandbox


def test_f11_verify_from_another_code_version_fails_closed_as_stale(
    client, arms, tick_arms, now, clock, sessions, settings, real_fleet, bench, monkeypatch,
    tmp_path,
):  # fmt: skip
    """Reviewer finding 4: verify generated for code that isn't the base or its ancestor."""
    other = tmp_path / "verify_other"
    verify_from_bench.write(
        other, verify_from_bench.generate(REPO / "bench", bench["files"], "zzz9999")
    )
    stale = settings.model_copy(update={"coder_verify_dir": str(other)})
    coder, sandbox = _oracle_coder(bench)
    detail = run_d1(
        client, arms, tick_arms, now, clock, sessions, rig_steps(stale, coder, sandbox),
        bench["patch"], monkeypatch,
    )  # fmt: skip
    assert detail["status"] == "rolled_back"
    assert (
        "stale verify: CODER_VERIFY_DIR was generated from code zzz9999"
        in (detail["verdict"]["why"])
    )
    assert "regenerate with `make device-verify`" in detail["verdict"]["why"]
    assert sandbox.runner.calls and all(c["argv"][-1] == "agent" for c in sandbox.runner.calls)


def _docker_image(name: str) -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "image", "inspect", name], capture_output=True).returncode == 0


@pytest.mark.skipif(
    os.environ.get("CODER_INTEGRATION") != "1" or not all(LLM.values()),
    reason="the one real run: CODER_INTEGRATION=1 and LLM_BASE_URL/LLM_MODEL/LLM_API_KEY",
)
def test_f11_real_device_with_the_real_coding_agent(
    client, arms, tick_arms, now, clock, sessions, settings, real_fleet, bench, monkeypatch
):
    image = os.environ.get("CODER_IMAGE") or "romulus-coder"
    if not _docker_image(image):
        pytest.skip(f"build the coder image first (make coder-image): {image}")
    sandbox = Sandbox(image=image, runner=subprocess_runner)
    agent = LocalAgent(
        base_url=LLM["LLM_BASE_URL"],
        model=LLM["LLM_MODEL"],
        api_key=LLM["LLM_API_KEY"],
        sandbox=sandbox,
        step_limit=15,
        time_limit_s=300,
        max_tokens=2000,
    )
    drafts = []

    class Recording:  # keeps the (already redacted) draft, which the DB only summarizes
        def draft(self, *args, **kw):
            drafts.append(agent.draft(*args, **kw))
            return drafts[-1]

    diag: dict = {"model": LLM["LLM_MODEL"], "caps": "15 steps, 300 s, max_tokens 2000"}
    detail = None
    try:
        detail = run_d1(
            client, arms, tick_arms, now, clock, sessions,
            rig_steps(settings, Recording(), sandbox), bench["patch"], monkeypatch,
        )  # fmt: skip
    finally:
        ov = arms["sim-3"].device.overlay
        row = sessions().get(Overlay, ("sim-3", ov.overlay_id)) if ov else None
        diag["overlay"] = {"status": row and row.status, "status_reason": row and row.status_reason}
        diag["drafts"] = [
            {"passed": d.passed, "notes": d.notes, "flags": list(d.flags), "diff": d.diff,
             "test_output": d.test_output[-3000:]}
            for d in drafts
        ]  # fmt: skip
        if detail is not None:
            diag["candidate"] = {k: detail.get(k) for k in ("id", "status", "code_rev", "why")}
            diag["replay"] = detail.get("replay")
            diag["verdict"] = detail.get("verdict")
            replayed = events.find(
                sessions(), {"candidate": detail["id"]}, kinds=["candidate.replayed"]
            )
            diag["replay_output"] = [e.data.get("replay_output") for e in replayed]
            diag["events"] = [
                {"kind": e["kind"], "why": e["why"]} for e in detail.get("events", [])
            ]
        out = os.environ.get("B1_DIAG_FILE")
        if out:
            text = json.dumps(diag, indent=2, default=str)
            Path(out).write_text(text.replace(LLM["LLM_API_KEY"], "[redacted]"))
    print("\ncandidate:", detail["status"], "\nwhy:", detail["why"])
    print("replay:", detail["replay"]["why"])
    assert LLM["LLM_API_KEY"] not in str(detail)
    assert detail["status"] == "awaiting_canary"
    assert detail["replay"]["passed"] and not detail["replay"]["stub"]
