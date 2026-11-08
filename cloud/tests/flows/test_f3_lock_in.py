"""F3: lock-in → fleet (flows doc F3). Plus the batch's other paths: the code pass-through
candidate that stops at awaiting_canary, the harness skill, and the LLM fallback.

Unmodified fake arms (`tools/fake_device`) talk to the real app through `device_http`;
worker steps run with an injected `now`, and the routes' clock follows it (`clock`).
"""

import io
import json
import tarfile
from datetime import timedelta

from fake_device.scenarios import SCENARIOS
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.coder import FakeCoder
from romulus_cloud.adapters.llm import FakeLlm, LlmError
from romulus_cloud.models import Candidate, HarnessVersion, Overlay
from romulus_cloud.services.batch import Tools

S = timedelta(seconds=1)


def _start(tick_arms, now):
    """F1: every arm installs firmware v1, code c0de001 and harness v1."""
    tick_arms(now)
    tick_arms(now + 10 * S)


def _held(arms, scenario, name, at):
    """Run a scenario's fault, then its `held` step 10 s later."""
    steps = SCENARIOS[scenario](arms[name], 0)
    for i, step in enumerate(steps):
        step.run(at + i * 10 * S)


def _story(client, device_id):
    (inc,) = [i for i in client.get("/v1/incidents").json() if i["device_id"] == device_id]
    return client.get(f"/v1/incidents/{inc['incident_id']}/story").json()


def _devices(client):
    return {d["id"]: d for d in client.get("/v1/devices").json()}


def test_f3_rules_patch_is_locked_in_canaried_and_promoted(
    client, arms, tick_arms, work, now, db_session, count_events
):
    _start(tick_arms, now)
    _held(arms, "rules", "sim-1", now + 20 * S)
    tick_arms(now + 35 * S)

    # 1. The batch job runs on request: firmware v2 (v1 + the diff) and harness v2 (a skill).
    resp = client.post("/v1/batch/run", json={"by": "kent"})
    assert resp.status_code == 200 and resp.json()["why"].startswith("human:kent asked")
    work(now + 40 * S)
    cands = {c["type"]: c for c in client.get("/v1/candidates").json()}
    assert set(cands) == {"firmware", "harness"}
    assert cands["firmware"]["version"] == 2 and cands["harness"]["version"] == 2
    assert "templated by FakeLlm" in cands["firmware"]["why"]
    assert "changes rule camera_occluded" in cands["firmware"]["why"]
    fw = client.get("/v1/firmware").json()[-1]
    assert fw["version"] == 2 and fw["source"] == "batch"
    overlay_id = arms["sim-1"].device.overlay.overlay_id
    overlay = db_session.get(Overlay, ("sim-1", overlay_id))
    assert overlay.status == "locked_in" and overlay.candidate_id == cands["firmware"]["id"]

    # 2-3. The gate replays and assigns both to the canary only.
    work(now + 41 * S)
    devs = _devices(client)
    assert devs["sim-2"]["assigned"] == {"firmware": 2, "harness": 2}
    for other in ("sim-1", "sim-3", "arm-1"):
        assert devs[other]["assigned"] == {"firmware": 1, "harness": 1}
    fw_cand = client.get(f"/v1/candidates/{cands['firmware']['id']}").json()
    assert fw_cand["status"] == "canary" and fw_cand["canary_device"] == "sim-2"
    assert fw_cand["replay"]["passed"] and not fw_cand["replay"]["stub"]
    assert [e["kind"] for e in fw_cand["events"]] == [
        "overlay.locked_in",
        "candidate.created",
        "candidate.replayed",
        "candidate.canary_started",
    ]
    assert all(e["why"] for e in fw_cand["events"])

    # The canary installs and stays healthy for the window (120 s from now + 41 s).
    assert ("firmware", 2, "installed") in tick_arms(now + 50 * S)["sim-2"]
    assert arms["sim-1"].device.overlay is not None  # the fix stays until v2 replaces it
    for t in (90, 130, 155):
        tick_arms(now + t * S)
        work(now + t * S + S)
    assert db_session.get(Candidate, cands["firmware"]["id"]).status == "canary"  # pending
    assert count_events("candidate.promoted") == 0

    # 4. Promote: every so101 device is assigned v2 and the fleet converges.
    work(now + 162 * S)
    for c in client.get("/v1/candidates").json():
        assert c["status"] == "promoted", c
    devs = _devices(client)
    assert all(d["assigned"] == {"firmware": 2, "harness": 2} for d in devs.values())
    tick_arms(now + 170 * S)
    tick_arms(now + 180 * S)
    devs = _devices(client)
    for name in arms:
        assert devs[name]["running"]["firmware"] == devs[name]["running"]["harness"] == 2
        assert devs[name]["overlay_id"] is None  # no device kept the old overlay
    # sim-1 reported `superseded` when v2 replaced its overlay; it stays locked in.
    assert count_events("overlay.superseded", {"device": "sim-1", "overlay": overlay_id}) == 1
    db_session.expire_all()
    assert db_session.get(Overlay, ("sim-1", overlay_id)).status == "locked_in"

    story = _story(client, "sim-1")
    # Evidence uploads (M4) interleave as the outbox drains; the gate's steps read in order.
    chain = story["chain"]
    assert "→ locked in → firmware v2 → harness v2 → replay ✓ → canary sim-2 →" in chain, chain
    assert chain.index("→ held →") < chain.index("→ locked in →")
    assert chain.endswith("→ fleet"), chain
    promoted = [s for s in story["steps"] if s["kind"] == "candidate.promoted"]
    assert len(promoted) == 2 and "canary healthy for 120 s" in promoted[0]["why"]


def test_harness_candidate_adds_the_skill_to_the_package(
    client, arms, tick_arms, work, now, db_session, settings
):
    _start(tick_arms, now)
    _held(arms, "rules", "sim-3", now + 20 * S)
    work(now + 40 * S)
    (cand,) = [c for c in client.get("/v1/candidates").json() if c["type"] == "harness"]
    row = db_session.get(HarnessVersion, ("default", cand["version"]))
    with build_blobstore(settings.blob_dir).get(row.sha256) as f:
        tar = tarfile.open(fileobj=io.BytesIO(f.read()), mode="r:gz")
    names = tar.getnames()
    skill = next(n for n in names if n.startswith("skills/camera_occluded-"))
    assert "skills/README.md" in names  # the base harness is kept
    assert b"# Skill: handle camera_occluded" in tar.extractfile(skill).read()
    assert json.loads(tar.extractfile("harness.json").read())["version"] == cand["version"]
    assert row.meta["version"] == cand["version"] and row.source == "batch"


def test_code_patch_candidate_is_a_labeled_passthrough_that_waits(
    client, arms, tick_arms, work, now, count_events
):
    _start(tick_arms, now)
    _held(arms, "code", "sim-3", now + 20 * S)
    work(now + 40 * S)
    work(now + 41 * S)
    cands = {c["type"]: c for c in client.get("/v1/candidates").json()}
    code = cands["code"]
    assert "pass-through: coding agent not wired" in code["why"]
    assert code["stub_stages"] == ["draft_code", "replay"]
    assert code["status"] == "awaiting_canary"
    events = client.get(f"/v1/candidates/{code['id']}").json()["events"]
    waiting = [e for e in events if e["kind"] == "candidate.awaiting_canary"]
    assert "replay: stub, passed by default; device tests not wired (M8)" in waiting[0]["why"]
    assert "ALLOW_PASSTHROUGH_CODE_PROMOTION is off" in waiting[0]["why"]
    # It never reaches a device, however long it waits; its harness sibling goes on.
    for t in (50, 90, 130, 165, 400):
        tick_arms(now + t * S)
        work(now + t * S + S)
    assert client.get(f"/v1/candidates/{code['id']}").json()["status"] == "awaiting_canary"
    assert client.get(f"/v1/candidates/{cands['harness']['id']}").json()["status"] == "promoted"
    assert all(d["assigned"]["firmware"] == 1 for d in _devices(client).values())
    assert count_events("candidate.awaiting_canary", {"candidate": code["id"]}) == 1


class _BrokenLlm(FakeLlm):
    model = "broken-model"

    def explain(self, incident):
        raise LlmError("proxy returned 502")

    def write_skill(self, incident):
        raise LlmError("proxy returned 502")


def test_llm_error_falls_back_to_templated_text_and_says_so(
    client, arms, tick_arms, work, now, settings
):
    _start(tick_arms, now)
    _held(arms, "rules", "sim-1", now + 20 * S)
    tools = Tools(settings, _BrokenLlm(), FakeCoder(), build_blobstore(settings.blob_dir))
    work(now + 40 * S, tools=tools)
    cands = client.get("/v1/candidates").json()
    assert len(cands) == 2
    for c in cands:
        assert "templated by FakeLlm" in c["why"]
        events = client.get(f"/v1/candidates/{c['id']}").json()["events"]
        (created,) = [e for e in events if e["kind"] == "candidate.created"]
        assert "LLM unavailable (proxy returned 502); used templated text instead" in created["why"]
