"""Fleet Doctor, device side (interfaces §10): the breaker, the triggers, the proposal
applier's checks, the mark-good timer, the outbox kind, the consult scenario and chaos."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from fake_device.arm import FakeArm
from fake_device.chaos import PROFILES, Chaos, Link, build_schedule
from fake_device.client import DeviceClient, HashMismatch, InvalidPackage
from fake_device.doctor import (
    ADVICE_MAX_TOKENS,
    CHARS_PER_TOKEN,
    HealStep,
    MarkGoodTimer,
    TriggerHook,
    base_mismatch,
    format_advice,
)
from fake_device.fake_cloud import CANNED_ADVICE, FakeCloud, canned_proposal
from fake_device.outbox import Outbox
from fake_device.scenarios import consult
from romulus_contracts import (
    DoctorAsk,
    IncidentReport,
    Overlay,
    ProposalBase,
    ProposalPointer,
    ProposalResult,
    Running,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "examples"
T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
PID = canned_proposal()["proposal_id"]
ASK_PATH = "/v1/devices/sim-1/doctor/ask"
PROPOSAL_PATH = f"/v1/devices/sim-1/proposals/{PID}"


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture
def cloud() -> FakeCloud:
    return FakeCloud.seeded()


@pytest.fixture
def client(cloud) -> DeviceClient:
    return DeviceClient(cloud.http(), "sim-1")


def booted(cloud: FakeCloud, tmp_path: Path, device_id: str = "sim-1") -> FakeArm:
    """An arm on firmware 1, harness 1, code c0de001 (the canned proposal's base)."""
    arm = FakeArm(cloud.http(), device_id, tmp_path / device_id)
    arm.tick(T0)
    cloud.requests.clear()
    return arm


def parked(cloud: FakeCloud, tmp_path: Path) -> FakeArm:
    arm = booted(cloud, tmp_path)
    arm.device.park_for_consult()
    return arm


def result_of(cloud: FakeCloud, pid: str = PID) -> dict | None:
    entry = cloud.proposal_results.get(pid)
    return entry[1] if entry else None


def on_top_of(overlay_id: str) -> dict:
    """The canned proposal, drafted to replace the active overlay `overlay_id`."""
    return canned_proposal(base={**canned_proposal()["base"], "overlay_id": overlay_id})


# --- the circuit breaker -----------------------------------------------------------------


def test_breaker_trips_after_a_timeout_and_skips_the_rest_of_the_heal(cloud, client):
    hook = TriggerHook(client)
    hook.start_heal(uuid.uuid4())
    cloud.doctor_timeout = True
    assert hook.maybe_ask([], tier=3) is None  # T6 asks and times out
    assert hook.breaker.tripped
    cloud.doctor_timeout = False
    # A different trigger in the same heal: skipped without a request.
    assert hook.maybe_ask([], verify_failures=2) is None
    assert cloud.paths().count(ASK_PATH) == 1
    # The next heal starts with a closed breaker and asks again.
    hook.start_heal(uuid.uuid4())
    assert hook.maybe_ask([], verify_failures=2) is not None
    assert cloud.paths().count(ASK_PATH) == 2


@pytest.mark.parametrize("trouble", ["offline", "503", "429", "garbage"])
def test_any_failed_ask_trips_the_breaker(cloud, client, trouble, monkeypatch):
    if trouble == "offline":
        cloud.offline = True
    elif trouble == "garbage":
        monkeypatch.setattr(cloud, "advice", {"not": "advice"})
    else:
        cloud.fail_status = int(trouble)
    hook = TriggerHook(client)
    hook.start_heal(None)
    assert hook.maybe_ask([], verify_failures=2) is None
    assert hook.breaker.tripped and hook.history[-1]["advice"] is None


def test_ask_doctor_returns_none_on_any_error_and_never_raises(cloud, client):
    good = {"trigger": "T2", "question": "why?"}
    assert client.ask_doctor(good, timeout_s=5)["hypotheses"] == CANNED_ADVICE["hypotheses"]
    assert client.ask_doctor({"question": "no trigger"}, timeout_s=5) is None  # invalid ask
    cloud.doctor_timeout = True
    assert client.ask_doctor(good, timeout_s=5) is None
    cloud.doctor_timeout = False
    for status in (404, 409, 500, 502, 503, 504):
        cloud.fail_status = status
        assert client.ask_doctor(good, timeout_s=5) is None
    cloud.fail_status = None
    cloud.offline = True
    assert client.ask_doctor(good, timeout_s=5) is None


def test_ask_doctor_sends_contract_version_and_a_valid_doctor_ask(cloud, client):
    client.ask_doctor({"trigger": "T4"}, timeout_s=5)
    ((device, sent),) = cloud.asks
    assert device == "sim-1" and sent["contract_version"] == "0.1"
    assert DoctorAsk.model_validate(sent).trigger == "T4"


# --- the triggers ------------------------------------------------------------------------

READ_A = HealStep("cat a.py", read=("a.py",))
EDIT_A = HealStep("patch a.py", changed=("a.py",))
FAIL = HealStep("pytest -q", check="fail")
PASS = HealStep("pytest -q", check="pass")


def detect(steps=(), **kw):
    hook = TriggerHook(DeviceClient(FakeCloud().http(), "sim-1"))
    return hook.detect(list(steps), **kw)


@pytest.mark.parametrize(
    "steps, kw, expected",
    [
        # T2: verify still failing after K=2 attempts, or a build/flash failure
        ([EDIT_A, FAIL], {"verify_failures": 1}, None),
        ([EDIT_A, FAIL, EDIT_A, FAIL], {"verify_failures": 2}, "T2"),
        ([HealStep("make flash", build_failed=True)], {}, "T2"),
        # T4: the same command 3 times in a row...
        ([READ_A, FAIL, FAIL, FAIL], {}, "T4"),
        ([READ_A, HealStep("ls  -la"), HealStep("ls -la "), HealStep(" ls -la")], {}, "T4"),
        ([READ_A, FAIL, FAIL], {}, None),
        ([FAIL, EDIT_A, FAIL, EDIT_A, FAIL], {}, None),  # 3 times, not in a row, edits between
        # ...or 5 steps reading nothing new and changing nothing
        (
            [READ_A] + [HealStep(f"cat a.py # {i}", read=("a.py",)) for i in range(5)],
            {},
            "T4",
        ),
        (
            [READ_A] + [HealStep(f"cat a.py # {i}", read=("a.py",)) for i in range(4)],
            {},
            None,  # only 4 steps without progress
        ),
        (
            [READ_A]
            + [HealStep(f"cat a.py # {i}", read=("a.py",)) for i in range(4)]
            + [HealStep("cat b.py", read=("b.py",))],
            {},
            None,  # the 5th step read a new file
        ),
        (
            [READ_A] + [HealStep(f"cat a.py # {i}", read=("a.py",)) for i in range(4)] + [EDIT_A],
            {},
            None,  # the 5th step changed a file
        ),
        # T5: >= 70% of the budget used with no passing check
        ([READ_A, FAIL], {"budget_used": 0.7}, "T5"),
        ([READ_A, FAIL], {"budget_used": 0.69}, None),
        ([READ_A, PASS, FAIL], {"budget_used": 0.95}, None),
        # T6: tier 3 at the start of the heal
        ([], {"tier": 3}, "T6"),
        ([], {"tier": "3"}, "T6"),
        ([], {"tier": 2}, None),
        ([READ_A], {"tier": 3}, None),  # not at the start any more
        # T7: no skill matches, at the start of the heal
        ([], {"skill_matched": False}, "T7"),
        ([READ_A], {"skill_matched": False}, None),
        ([], {}, None),
        # first match wins: T2 before T4 before T5
        ([FAIL, FAIL, FAIL], {"verify_failures": 3, "budget_used": 0.9}, "T2"),
        ([FAIL, FAIL, FAIL], {"budget_used": 0.9}, "T4"),
    ],
)
def test_triggers(steps, kw, expected):
    assert detect(steps, **kw) == expected


def test_maybe_ask_builds_the_ask_and_returns_short_numbered_advice(cloud, client):
    hook = TriggerHook(client)
    incident_id = uuid.uuid4()
    hook.start_heal(incident_id)
    steps = [READ_A, EDIT_A, FAIL, EDIT_A, FAIL, EDIT_A, FAIL]
    text = hook.maybe_ask(
        steps, verify_failures=3, tier=3, context={"code_rev": "c0de001", "log_tail": ["E x"]}
    )
    assert text.startswith("Fleet Doctor (T2): hypotheses to check, not instructions.")
    assert "\n1. The threshold isn't the bug" in text and "\n   Check: Read SPEC.md" in text
    assert "\n2. Each lower threshold" in text
    assert len(text) <= ADVICE_MAX_TOKENS * CHARS_PER_TOKEN

    ((_, sent),) = cloud.asks
    ask = DoctorAsk.model_validate(sent)
    assert ask.incident_id == incident_id and ask.trigger == "T2" and ask.budget_s == 20
    assert "3 patch attempts" in ask.question
    assert ask.context.tier == 3 and ask.context.code_rev == "c0de001"
    assert ask.context.log_tail == ["E x"]
    assert ask.context.recent_steps == [
        "pytest -q: FAIL",
        "patch a.py",
        "pytest -q: FAIL",
        "patch a.py",
        "pytest -q: FAIL",
    ]  # the last 5


def test_each_trigger_asks_once_per_heal(cloud, client):
    hook = TriggerHook(client)
    hook.start_heal(uuid.uuid4())
    assert hook.maybe_ask([], tier=3) is not None
    assert hook.maybe_ask([], tier=3) is None  # T6 again: already asked this heal
    assert hook.maybe_ask([FAIL, FAIL, FAIL]) is not None  # T4 is new
    assert [h["trigger"] for h in hook.history] == ["T6", "T4"]


def test_at_most_six_asks_per_incident(cloud, client):
    hook = TriggerHook(client)
    incident_id = uuid.uuid4()
    answers = []
    for _ in range(8):  # eight heals of the same incident, each with a T6 at the start
        hook.start_heal(incident_id)
        answers.append(hook.maybe_ask([], tier=3))
    assert [a is not None for a in answers] == [True] * 6 + [False] * 2
    hook.start_heal(uuid.uuid4())  # another incident has its own cap
    assert hook.maybe_ask([], tier=3) is not None
    assert len(cloud.asks) == 7


def test_no_trigger_no_request(cloud, client):
    hook = TriggerHook(client)
    hook.start_heal(None)
    assert hook.maybe_ask([READ_A, EDIT_A, PASS], verify_failures=1, budget_used=0.2) is None
    assert cloud.requests == []


def test_advice_text_stays_under_the_token_cap():
    long = {"claim": "x " * 400, "how_to_check": "y " * 400}
    text = format_advice({"hypotheses": [long, long]}, "T5")
    assert len(text) <= ADVICE_MAX_TOKENS * CHARS_PER_TOKEN
    assert "1. x x" in text and text.endswith("…") and "2." not in text
    short = {"claim": "c", "how_to_check": ""}
    assert format_advice({"hypotheses": [short]}, "T4").endswith("1. c")
    assert format_advice({"hypotheses": []}, "T4") is None
    assert format_advice({"hypotheses": [{"claim": "  "}]}, "T4") is None


def test_empty_advice_injects_nothing_but_does_not_trip_the_breaker(cloud, client):
    cloud.advice = {"ask_id": "ask_x", "hypotheses": []}
    hook = TriggerHook(client)
    hook.start_heal(None)
    assert hook.maybe_ask([], tier=3) is None
    assert not hook.breaker.tripped
    assert hook.maybe_ask([FAIL, FAIL, FAIL]) is None and len(cloud.asks) == 2


# --- pulling a proposal ------------------------------------------------------------------


def test_get_proposal_checks_the_header_and_the_pointer_sha(cloud, client):
    cloud.offer("sim-1")
    pointer = cloud.pointer("sim-1")
    data, sha = client.get_proposal(PID, expected_sha256=pointer["sha256"])
    assert data["proposal_id"] == PID and sha == pointer["sha256"]
    with pytest.raises(HashMismatch, match="check-in pointer"):
        client.get_proposal(PID, expected_sha256="0" * 64)
    cloud.corrupt.add(PROPOSAL_PATH)
    with pytest.raises(HashMismatch):
        client.get_proposal(PID)


def test_get_proposal_rejects_a_body_for_another_id(cloud, client):
    cloud.offer("sim-1", canned_proposal(proposal_id="prop_other"))
    cloud.proposals[PID] = cloud.proposals["prop_other"]  # served under the wrong id
    with pytest.raises(InvalidPackage):
        client.get_proposal(PID)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_proposal("prop_missing")


# --- the applier's checks ----------------------------------------------------------------


def test_happy_path_applies_watches_marks_good_and_holds(cloud, tmp_path):
    arm = parked(cloud, tmp_path)
    proposal = cloud.offer("sim-1")
    arm.tick(at(10))

    overlay = arm.device.overlay
    assert overlay.proposal_id == PID and overlay.code_patch == proposal["code_patch"]
    assert overlay.overlay_id.startswith("ov-sim-1-")
    result = result_of(cloud)
    assert result["result"] == "applied" and result["overlay_id"] == overlay.overlay_id
    assert "sha256 ok" in result["reason"] and "verify passed" in result["reason"]
    ProposalResult.model_validate(result)
    assert cloud.pointer("sim-1") is None  # the cloud has the answer
    assert arm.applier.timer.overlay_id == overlay.overlay_id
    assert arm.device.consult_parked  # still parked during the watch

    arm.tick(at(40))
    assert arm.applier.timer is not None  # 30 s in: still watching
    arm.tick(at(75))  # 65 s after applying: marked good
    assert arm.applier.timer is None and not arm.device.consult_parked
    assert arm.device.overlay.overlay_id == overlay.overlay_id
    assert cloud.overlays == []  # mark-good is not "held"

    arm.tick(at(75 + 1200))  # 20 min later with no recurrence: held
    assert cloud.overlays[-1][1] == overlay.overlay_id
    assert cloud.overlays[-1][2]["status"] == "held"
    assert arm.device.overlay_held


def test_the_same_proposal_twice_is_a_no_op(cloud, tmp_path):
    arm = parked(cloud, tmp_path)
    cloud.offer("sim-1")
    arm.tick(at(10))
    applied = arm.device.overlay
    cloud.offers["sim-1"] = PID  # the check-in points at it again (result not seen yet)
    gets = cloud.paths("GET").count(PROPOSAL_PATH)
    arm.tick(at(20))
    assert arm.device.overlay is applied and cloud.paths("GET").count(PROPOSAL_PATH) == gets
    # ...also after a restart: the handled ids are on disk.
    again = FakeArm(cloud.http(), "sim-1", tmp_path / "sim-1", device=arm.device)
    assert PID in again.applier.handled
    again.tick(at(30))
    assert arm.device.overlay is applied and cloud.paths("GET").count(PROPOSAL_PATH) == gets
    assert len(cloud.proposal_results) == 1


@pytest.mark.parametrize(
    "base, why",
    [
        ({"code_rev": "c0de002"}, "code_rev c0de001 (proposal is for c0de002)"),
        ({"firmware": 2}, "firmware 1 (proposal is for 2)"),
        ({"harness": 2}, "harness 1 (proposal is for 2)"),
        ({"overlay_id": "ov-old"}, "overlay None (proposal replaces ov-old)"),
    ],
)
def test_base_mismatch_is_stale_and_nothing_is_applied(cloud, tmp_path, base, why):
    arm = parked(cloud, tmp_path)
    full = {**canned_proposal()["base"], **base}
    cloud.offer("sim-1", canned_proposal(base=full))
    arm.tick(at(10))
    assert result_of(cloud)["result"] == "stale" and why in result_of(cloud)["reason"]
    assert arm.device.overlay is None and arm.applier.timer is None


def test_base_checks_harness_only_when_set_and_mcu_build_only_for_mcu_patches():
    running = Running(firmware=1, harness=1, code_rev="c0de001", mcu_build="stm32-a")
    py_patch = canned_proposal()["code_patch"]
    mcu_patch = py_patch.replace("control/hold.py", "firmware/src/main.c")
    base = ProposalBase(firmware=1, harness=None, code_rev="c0de001", mcu_build="stm32-b")
    assert base_mismatch(base, running, None, py_patch) == ""  # harness unset, not MCU
    assert "mcu_build stm32-a (proposal is for stm32-b)" in base_mismatch(
        base, running, None, mcu_patch
    )
    same_mcu = base.model_copy(update={"mcu_build": "stm32-a"})
    assert base_mismatch(same_mcu, running, None, mcu_patch) == ""
    # an active overlay the proposal doesn't know about would be silently dropped: stale
    assert "overlay ov-1" in base_mismatch(base, running, "ov-1", py_patch)


def test_a_proposal_that_replaces_the_active_overlay_applies_and_supersedes_it(cloud, tmp_path):
    arm = parked(cloud, tmp_path)
    old = Overlay(overlay_id="ov-old", diff={"rules": {"camera_occluded": {"below": 0.3}}})
    arm.device.apply_overlay(old)
    cloud.offer("sim-1", on_top_of("ov-old"))
    arm.tick(at(10))
    new_id = result_of(cloud)["overlay_id"]
    assert arm.device.overlay.overlay_id == new_id
    arm.tick(at(75))  # marked good: now the old one is reported superseded
    assert cloud.overlays[-1][1] == "ov-old" and cloud.overlays[-1][2]["status"] == "superseded"


@pytest.mark.parametrize("why", ["not parked", "task running"])
def test_not_parked_or_busy_is_rejected(cloud, tmp_path, why):
    arm = booted(cloud, tmp_path)
    if why == "task running":
        arm.device.park_for_consult()
        arm.device.task_running = True
    cloud.offer("sim-1")
    arm.tick(at(10))
    assert result_of(cloud)["result"] == "rejected"
    assert result_of(cloud)["reason"] == "not parked, or a task is running"
    assert arm.device.overlay is None


def test_expired_is_rejected(cloud, tmp_path):
    arm = parked(cloud, tmp_path)
    cloud.offer("sim-1", canned_proposal(expires_at="2026-11-07T22:00:05Z"))
    arm.tick(at(10))
    assert result_of(cloud)["result"] == "rejected" and "expired" in result_of(cloud)["reason"]
    assert arm.device.overlay is None


def test_sha_mismatch_is_rejected_and_nothing_is_applied(cloud, tmp_path):
    arm = parked(cloud, tmp_path)
    cloud.offer("sim-1")
    cloud.corrupt.add(PROPOSAL_PATH)
    arm.tick(at(10))
    result = result_of(cloud)
    assert result["result"] == "rejected" and "sha256 mismatch" in result["reason"]
    assert arm.device.overlay is None and arm.applier.timer is None


def test_a_pointer_sha_that_differs_from_the_body_is_rejected(cloud, tmp_path):
    arm = parked(cloud, tmp_path)
    cloud.offer("sim-1")
    pointer = ProposalPointer.model_validate({**cloud.pointer("sim-1"), "sha256": "ab" * 32})
    assert arm.applier.handle(pointer, at(10)) == "rejected"
    assert arm.device.overlay is None


def test_failed_verify_rolls_back_and_reports(cloud, tmp_path):
    arm = parked(cloud, tmp_path)
    old = Overlay(overlay_id="ov-old", diff={})
    arm.device.apply_overlay(old)
    arm.device.proposal_verify_ok = False
    cloud.offer("sim-1", on_top_of("ov-old"))
    arm.tick(at(10))
    result = result_of(cloud)
    assert result["result"] == "failed_verify" and result["overlay_id"] is None
    assert "rolled back" in result["reason"]
    assert arm.device.overlay is old and arm.applier.timer is None


def test_a_patch_touching_a_safety_path_is_rejected(cloud, tmp_path):
    arm = parked(cloud, tmp_path)
    arm.applier.safety_paths = ("control",)
    cloud.offer("sim-1")
    arm.tick(at(10))
    assert result_of(cloud)["result"] == "rejected" and "safety" in result_of(cloud)["reason"]
    assert arm.device.overlay is None


def test_frozen_takes_nothing_and_reports_nothing(cloud, tmp_path):
    arm = parked(cloud, tmp_path)
    cloud.offer("sim-1")
    pointer = ProposalPointer.model_validate(cloud.pointer("sim-1"))
    assert arm.applier.handle(pointer, at(10), frozen=True) is None
    assert arm.device.overlay is None and cloud.paths("GET") == []
    assert PID not in arm.applier.handled  # taken once unfrozen


def test_a_gone_proposal_is_dropped_and_a_network_error_retried(cloud, tmp_path):
    arm = parked(cloud, tmp_path)
    cloud.offer("sim-1")
    pointer = ProposalPointer.model_validate(cloud.pointer("sim-1"))
    cloud.offline = True
    assert arm.applier.handle(pointer, at(10)) is None and PID not in arm.applier.handled
    cloud.offline = False
    cloud.fail_status = 503
    assert arm.applier.handle(pointer, at(20)) is None and PID not in arm.applier.handled
    cloud.fail_status = 410
    assert arm.applier.handle(pointer, at(30)) is None and PID in arm.applier.handled
    assert cloud.proposal_results == {}


# --- the mark-good timer -----------------------------------------------------------------


def test_mark_good_timer():
    timer = MarkGoodTimer("ov-1", PID, T0, hold_s=60)
    assert timer.check(at(0), healthy=True) == "watching"
    assert timer.check(at(59), healthy=True) == "watching"
    assert timer.check(at(60), healthy=True) == "good"
    assert timer.check(at(30), healthy=False) == "failed"


def test_a_health_failure_in_the_window_reverts_the_overlay(cloud, tmp_path):
    arm = parked(cloud, tmp_path)
    old = Overlay(overlay_id="ov-old", diff={})
    arm.device.apply_overlay(old)
    cloud.offer("sim-1", on_top_of("ov-old"))
    arm.tick(at(10))
    applied = result_of(cloud)["overlay_id"]
    arm.device.fault_recurs = True  # the fault comes back under the new overlay
    arm.tick(at(40))
    assert arm.device.overlay is old  # the snapshot is back
    device, overlay_id, status = cloud.overlays[-1]
    assert (overlay_id, status["status"]) == (applied, "reverted")
    assert "mark-good" in status["reason"]
    assert arm.applier.timer is None and arm.device.consult_parked  # still waiting


def test_an_overlay_replaced_during_the_watch_is_no_longer_watched(cloud, tmp_path):
    arm = parked(cloud, tmp_path)
    cloud.offer("sim-1")
    arm.tick(at(10))
    arm.device.drop_overlay()  # e.g. the cloud said drop_overlay, or a new version installed
    arm.tick(at(20))
    assert arm.applier.timer is None and cloud.overlays == []


def test_one_proposal_at_a_time(cloud, tmp_path):
    arm = parked(cloud, tmp_path)
    cloud.offer("sim-1")
    arm.tick(at(10))
    first = arm.device.overlay.overlay_id
    second = canned_proposal(
        proposal_id="prop_second", base={**canned_proposal()["base"], "overlay_id": first}
    )
    cloud.offer("sim-1", second)
    arm.tick(at(20))  # still watching the first: wait
    assert "prop_second" not in cloud.proposal_results
    arm.tick(at(75))  # first marked good (arm unparked), then the second is rejected
    assert result_of(cloud, "prop_second")["result"] == "rejected"


# --- the outbox kind ---------------------------------------------------------------------


def test_proposal_result_outbox_round_trip(cloud, client, tmp_path):
    cloud.offer("sim-1")
    outbox = Outbox(tmp_path / "outbox")
    result = ProposalResult(result="applied", overlay_id="ov-1", reason="ok", occurred_at=T0)
    cloud.offline = True
    outbox.add_proposal_result(PID, result)
    assert outbox.drain(client, T0) == 0
    (entry,) = outbox.pending()
    assert (entry["kind"], entry["proposal_id"]) == ("proposal_result", PID)
    cloud.offline = False
    assert Outbox(tmp_path / "outbox").drain(client, at(60)) == 1  # after a restart
    assert result_of(cloud)["overlay_id"] == "ov-1"
    assert result_of(cloud)["contract_version"] == "0.1"
    # an identical resend is fine; a proposal the cloud doesn't know is dead-lettered
    outbox.add_proposal_result(PID, result)
    outbox.add_proposal_result("prop_unknown", result)
    assert outbox.drain(client, at(120)) == 1
    (dead,) = outbox.dead_letters()
    assert dead["proposal_id"] == "prop_unknown" and "404" in dead["error"]


# --- the consult scenario ----------------------------------------------------------------


def test_consult_scenario_asks_first_then_parks_and_asks_for_a_consult(cloud, tmp_path):
    arm = booted(cloud, tmp_path)
    fault, wait_over = consult(arm, hold_after_s=600)
    assert (fault.at_s, wait_over.at_s) == (0, 600)
    fault.run(T0)

    ((device, ask),) = cloud.asks  # the L1 ask came first
    assert ask["trigger"] == "T2" and ask["context"]["tier"] == 3
    assert ask["context"]["files"][0]["path"] == "control/hold.py"
    (incident,) = cloud.incidents.values()
    assert cloud.paths()[:2] == [ASK_PATH, "/v1/devices/sim-1/incidents"]
    assert ask["incident_id"] == incident["incident_id"]
    IncidentReport.model_validate(incident)
    assert incident["consult"] == {"wanted": True, "trigger": "T2", "attempts": 2}
    assert incident["outcome"]["resolved"] is False
    assert incident["outcome"]["suspected_cause"] == "software"
    assert incident["running"]["code_rev"] == "c0de001" and incident["overlay"] is None
    assert "Fleet Doctor (T2)" in incident["agent"]["reasoning"]
    assert "The threshold isn't the bug" in incident["agent"]["reasoning"]
    assert "ask_doctor T2" in incident["agent"]["actions"]
    assert arm.device.consult_parked and arm.device.health() == "degraded"

    # Same story as the contracts fixture F12 uses.
    example = json.loads((EXAMPLES / "incident_consult_wanted.json").read_text())
    for key in ("kind", "consult", "tier", "signals", "log_tail"):
        assert incident[key] == example[key], key
    outcome = {k: v for k, v in incident["outcome"].items() if v is not None}
    assert outcome == example["outcome"]


def test_consult_end_to_end_against_the_fake_cloud(cloud, tmp_path):
    arm = booted(cloud, tmp_path)
    fault, wait_over = consult(arm, hold_after_s=600)
    fault.run(T0)
    cloud.offer("sim-1")  # the cloud's consult offers the fix
    arm.tick(at(10))
    assert result_of(cloud)["result"] == "applied"
    wait_over.run(at(30))  # under watch: stays parked until marked good
    assert arm.device.consult_parked
    arm.tick(at(80))
    assert not arm.device.consult_parked
    arm.tick(at(80 + 600))  # the scenario's hold_after_s
    assert cloud.overlays[-1][2]["status"] == "held"


def test_consult_with_no_fix_unparks_when_the_wait_is_over(cloud, tmp_path):
    arm = booted(cloud, tmp_path)
    cloud.offline = True  # no Doctor, no cloud: the ask fails, the incident queues
    fault, wait_over = consult(arm, hold_after_s=600)
    fault.run(T0)
    assert arm.doctor.breaker.tripped and arm.device.consult_parked
    wait_over.run(at(600))
    assert not arm.device.consult_parked
    cloud.offline = False
    arm.tick(at(700))
    (incident,) = cloud.incidents.values()
    assert "ask_doctor T2: no answer" in incident["agent"]["actions"]
    assert "Fleet Doctor" not in incident["agent"]["reasoning"]


# --- chaos -------------------------------------------------------------------------------

DEVICES = ["sim-1", "sim-2", "sim-3"]


def test_doctor_chaos_is_off_overnight_and_on_in_demo_on_non_canary_arms():
    assert PROFILES["overnight"].doctor_every_s == 0 < PROFILES["demo"].doctor_every_s
    night = build_schedule(DEVICES, "sim-2", PROFILES["overnight"], 0)
    assert not [e for e in night if e.kind == "doctor"]
    demo = build_schedule(DEVICES, "sim-2", PROFILES["demo"], 0)
    doctors = [e for e in demo if e.kind == "doctor"]
    assert doctors and {e.device_id for e in doctors} <= {"sim-1", "sim-3"}


def test_demo_chaos_plays_consults(tmp_path):
    cloud = FakeCloud.seeded()
    upstream = cloud.http()
    links = {d: Link(upstream) for d in DEVICES}
    arms = [FakeArm(links[d].client(), d, tmp_path / d, canary=d == "sim-2") for d in DEVICES]
    chaos = Chaos(arms, PROFILES["demo"], seed=5, links=links)
    for i in range(int(3 * 3600 / 60) + 1):
        now = at(i * 60)
        for a in arms:
            a.tick(now)
        chaos.run_due(i * 60, now)
    assert chaos.failures == []
    assert chaos.ran["doctor"] > 0
    consults = [i for i in cloud.incidents.values() if i.get("consult")]
    assert len(consults) == chaos.ran["doctor"]
    assert {a["trigger"] for _, a in cloud.asks} == {"T2"}
