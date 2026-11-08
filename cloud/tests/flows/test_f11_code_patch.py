"""F11: code patch → coding agent → candidate (flows doc F11, D21, M8).

The fleet runs the toy device repo (`tests/fixtures/toy_device`, fault D1 planted) as code
`toy0001`. sim-3 reports fault D1 with the contracts example `code_patch` and later `held`.
The coder is a real `local_agent` that runs `cloud/coder/runner.py` on the host with no
agent and no LLM (`host_coder`): git applies the patch, the toy suite and D1's verify script
run for real. The gate's code replay runs the same checks on the new code version.

This module turns the coder on on purpose (`make_settings` forces fake adapters); nothing
here touches the network or Docker.
"""

import io
import tarfile
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from fake_device.scenarios import SCENARIOS
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.coder import build_coder
from romulus_cloud.adapters.llm import FakeLlm
from romulus_cloud.models import Device, Overlay
from romulus_cloud.services import registry
from romulus_cloud.services.batch import Tools
from romulus_cloud.services.seed import FIRMWARE_V1

S = timedelta(seconds=1)
KW = {"actor": "human:test", "actor_class": "human"}
B7 = "no canary device runs real device code (B7)"

BAD_PATCH = '''\
--- a/control/hold.py
+++ b/control/hold.py
@@ -18,7 +18,4 @@ def step(state, dist_mm, cmd):
     """One control step: distance in, gripper command out (see SPEC.md)."""
     if state == State.HOLDING:
-        if dist_mm is not None and dist_mm < RELEASE_THRESHOLD_MM:
-            log_state(State.HOLDING, State.RELEASE, reason="dist_lt_threshold", dist_mm=dist_mm)
-            return State.RELEASE, cmd.open_gripper()
-        return State.HOLDING, cmd.hold()
+        return State.RELEASE, cmd.open_gripper()
     if state == State.RELEASE:
'''


@pytest.fixture
def settings(make_settings, host_coder):
    return make_settings(**host_coder).model_copy(update={"coder_provider": "local_agent"})


@pytest.fixture
def toy_fleet(seeded, settings, toy_package):
    """Code toy0001 (the toy repo) and firmware v2 naming it, assigned to every device."""
    store = build_blobstore(settings.blob_dir)
    registry.create_code(seeded, store, toy_package, rev="toy0001", **KW)
    registry.create_firmware(seeded, FIRMWARE_V1 | {"version": 2, "code_rev": "toy0001"}, **KW)
    for d in seeded.scalars(select(Device)):
        d.assigned_firmware = 2
    seeded.commit()
    return seeded


class _Reviewed:
    """Stands in for an agent that reviewed the patch and kept it: the real sandbox still
    applies it and runs the tests; only the `agent` label differs (no LLM in tests)."""

    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def draft(self, patch, incident, code_rev, package=None):
        self.calls += 1
        d = self.inner.draft(patch, incident, code_rev, package)
        return replace(d, agent="scripted-reviewer") if d.passed else d


def _tools(settings, coder) -> Tools:
    return Tools(settings, FakeLlm(), coder, build_blobstore(settings.blob_dir))


def _held(arms, name, at):
    for i, step in enumerate(SCENARIOS["code"](arms[name], 0)):
        step.run(at + i * 10 * S)


def _start(tick_arms, now):
    """F1: every arm installs firmware v2 with code toy0001."""
    tick_arms(now)
    tick_arms(now + 10 * S)


def _cands(client):
    return {c["type"]: c for c in client.get("/v1/candidates").json()}


def test_f11_a_passing_patch_becomes_a_tested_code_candidate_that_waits_for_b7(
    client, arms, tick_arms, work, now, settings, toy_fleet, count_events
):
    _start(tick_arms, now)
    assert arms["sim-3"].device.running().code_rev == "toy0001"
    _held(arms, "sim-3", now + 20 * S)
    coder = _Reviewed(build_coder(settings))

    # 1-3. The batch sends the patch, incident and code to the coder; the tested tree becomes
    # a new code version, and a new firmware manifest names it (the coder never edits it).
    work(now + 40 * S, tools=_tools(settings, coder))
    assert coder.calls == 1
    code = _cands(client)["code"]
    assert code["code_rev"] not in (None, "toy0001") and code["stub_stages"] == []
    assert "Coder (drafted by scripted-reviewer)" in code["why"]
    assert "Device tests passed" in code["why"] and "5 passed" in code["why"]
    assert "--- a/control/hold.py" in code["why"]  # the diff, readable on the timeline
    fw = client.get(f"/v1/firmware/{code['version']}").json()["manifest"]
    assert fw["code_rev"] == code["code_rev"] and fw["rules"] == FIRMWARE_V1["rules"]
    pkg = client.get(f"/v1/code/{code['code_rev']}/package")
    tar = tarfile.open(fileobj=io.BytesIO(pkg.content), mode="r:gz")
    assert b"dist_lt_threshold" not in tar.extractfile("device/control/hold.py").read()
    assert "device/tests/test_hold_proximity.py" in tar.getnames()

    # 4. The gate runs the device tests on the new code in the sandbox, then stops: no canary
    # runs real code (B7). The harness sibling goes on without it.
    work(now + 41 * S)
    detail = client.get(f"/v1/candidates/{code['id']}").json()
    assert detail["status"] == "awaiting_canary"
    assert detail["replay"]["passed"] and not detail["replay"]["stub"]
    assert f"the device tests passed on code {code['code_rev']}" in detail["replay"]["why"]
    (waiting,) = [e for e in detail["events"] if e["kind"] == "candidate.awaiting_canary"]
    assert B7 in waiting["why"]
    for t in (50, 90, 130, 165, 400):
        tick_arms(now + t * S)
        work(now + t * S + S)
    assert client.get(f"/v1/candidates/{code['id']}").json()["status"] == "awaiting_canary"
    assert all(d["assigned"]["firmware"] == 2 for d in client.get("/v1/devices").json())
    assert count_events("candidate.awaiting_canary", {"candidate": code["id"]}) == 1

    # The coder's diff, test output and summary are in the incident's story.
    (inc,) = [i for i in client.get("/v1/incidents").json() if i["device_id"] == "sim-3"]
    story = client.get(f"/v1/incidents/{inc['incident_id']}/story").json()
    created = [s for s in story["steps"] if s["kind"] == "candidate.created"]
    assert any("5 passed" in s["why"] and "control/hold.py" in s["why"] for s in created)


def test_f11_without_an_agent_the_tested_patch_is_a_labeled_passthrough(
    client, arms, tick_arms, work, now, toy_fleet
):
    _start(tick_arms, now)
    _held(arms, "sim-3", now + 20 * S)
    work(now + 40 * S)  # the settings' own coder: local_agent with CODER_AGENT=none
    work(now + 41 * S)
    code = client.get(f"/v1/candidates/{_cands(client)['code']['id']}").json()
    assert code["stub_stages"] == ["draft_code"]
    assert "the device patch, no coding agent" in code["why"]
    assert code["status"] == "awaiting_canary" and not code["replay"]["stub"]
    (waiting,) = [e for e in code["events"] if e["kind"] == "candidate.awaiting_canary"]
    assert "pass-through code candidate" in waiting["why"]


def test_f11_a_failing_patch_gets_no_candidate_and_is_not_retried(
    client, arms, tick_arms, work, now, settings, toy_fleet, db_session, count_events, monkeypatch
):
    monkeypatch.setattr("fake_device.scenarios.D1_CODE_PATCH", BAD_PATCH)
    _start(tick_arms, now)
    _held(arms, "sim-3", now + 20 * S)
    coder = _Reviewed(build_coder(settings))
    tools = _tools(settings, coder)
    work(now + 40 * S, tools=tools)

    assert client.get("/v1/candidates").json() == []  # nothing built, not even the skill
    overlay_id = arms["sim-3"].device.overlay.overlay_id
    ov = db_session.get(Overlay, ("sim-3", overlay_id))
    assert ov.status == "lockin_error" and "did not pass the device tests" in ov.status_reason
    (inc,) = [i for i in client.get("/v1/incidents").json() if i["device_id"] == "sim-3"]
    story = client.get(f"/v1/incidents/{inc['incident_id']}/story").json()
    (error,) = [s for s in story["steps"] if s["kind"] == "batch.error"]
    assert "2 failed" in error["why"] and "FAIL: step 0 with a hand at 30 mm" in error["why"]
    assert "not retried; the device keeps its local fix" in error["why"]

    # No rebuild loop: later runs (timer or button) never call the coder for it again.
    for t in (200, 400):
        client.post("/v1/batch/run", json={"by": "kent"})
        work(now + t * S, tools=tools)
    assert coder.calls == 1
    assert count_events("batch.error", {"overlay": overlay_id}) == 1
    assert arms["sim-3"].device.overlay is not None  # the device keeps its local fix
