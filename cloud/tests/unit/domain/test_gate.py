"""domain/gate.py: replay, canary verdict, candidate state machine, stage composition."""

import ast
import itertools
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from romulus_cloud.domain.gate import (
    ID,
    STATES,
    TERMINAL,
    CheckinObs,
    GateInputs,
    IncidentObs,
    InstallObs,
    ReplayCase,
    ReplayResult,
    Stage,
    StageResult,
    by_kind,
    canary_verdict,
    compose,
    next_state,
    passthrough,
    replay,
    replay_code_stub,
    replay_stub,
)
from romulus_contracts import FirmwareManifest

T0 = datetime(2026, 11, 7, 22, 0, 0, tzinfo=UTC)
W = 120


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


MANIFEST = {
    "version": 8,
    "device_class": "so101",
    "code_rev": "a1b2c3d",
    "rules": {
        "camera_occluded": {"signal": "brightness", "below": 0.35, "action": "safe_pose"},
        "gripper_stall": {"signal": "gripper_current_ma", "above": 900, "action": "backoff_retry"},
    },
}

CAM = ReplayCase("inc-cam", {"brightness": [0.41, 0.18, 0.12]}, "safe_pose", "camera_occluded")
STALL = ReplayCase("inc-stall", {"gripper_current_ma": [400, 950]}, "backoff_retry", "stall")


# =========================================================================== replay


def test_replay_one_case_passes_with_the_rule_that_fired():
    r = replay(MANIFEST, [CAM])
    assert r.passed and not r.stub
    assert r.why.startswith("replayed 1 incident; 1 pass")
    assert "camera_occluded fired safe_pose" in r.why
    assert r.cases[0].fired == ("camera_occluded->safe_pose",)


def test_replay_many_cases_all_pass():
    r = replay(MANIFEST, [CAM, STALL])
    assert r.passed and r.why == "replayed 2 incidents; 2 pass"
    assert [c.passed for c in r.cases] == [True, True]


def test_replay_fails_when_the_expected_action_does_not_fire():
    dim = ReplayCase("inc-dim", {"brightness": [0.5, 0.4]}, "safe_pose")
    r = replay(MANIFEST, [CAM, dim])
    assert not r.passed
    assert "1 pass, 1 fail" in r.why and "inc-dim: replay failed: safe_pose did not fire" in r.why
    assert [c.passed for c in r.cases] == [True, False]


def test_replay_mismatched_action_fails():
    r = replay(MANIFEST, [ReplayCase("i", {"brightness": [0.1]}, "backoff_retry")])
    assert not r.passed
    assert "backoff_retry did not fire (fired: camera_occluded->safe_pose)" in r.why


def test_replay_missing_signal_fails_and_says_so():
    r = replay(MANIFEST, [ReplayCase("i", {"other": [1]}, "safe_pose")])
    assert not r.passed
    assert "fired: nothing" in r.why and "brightness: 1 missing" in r.why


def test_replay_skipped_samples_are_explained_on_failure():
    case = ReplayCase("i", {"brightness": [None, "0.1", True, float("nan"), 0.9]}, "safe_pose")
    r = replay(MANIFEST, [case])
    assert not r.passed
    for reason in ("1 null", "1 string", "1 boolean", "1 NaN"):
        assert reason in r.why
    assert len(r.cases[0].skipped) >= 4


def test_replay_skipped_samples_are_mentioned_on_a_pass_but_not_missing_signals():
    case = ReplayCase("i", {"brightness": [None, 0.1]}, "safe_pose")
    r = replay(MANIFEST, [case])
    assert r.passed
    assert "ignored brightness: 1 null" in r.why
    assert "gripper_current_ma" not in r.why  # other rule's signal is just absent


def test_replay_case_without_expected_action_fails():
    r = replay(MANIFEST, [ReplayCase("i", {"brightness": [0.1]}, None)])
    assert not r.passed and "no resolving_action" in r.why


def test_replay_signals_not_a_mapping_fails_that_case_only():
    r = replay(MANIFEST, [CAM, ReplayCase("bad", [0.1], "safe_pose")])
    assert not r.passed and r.cases[0].passed and not r.cases[1].passed
    assert "signals must be a mapping" in r.cases[1].why


def test_replay_with_no_cases_fails():
    r = replay(MANIFEST, [])
    assert not r.passed and "no incidents to replay" in r.why


def test_replay_malformed_rules_fail_instead_of_raising():
    bad = {**MANIFEST, "rules": {"x": {"signal": "s", "action": "a"}}}
    r = replay(bad, [CAM])
    assert not r.passed and "malformed" in r.why


def test_replay_manifest_without_rules():
    r = replay({**MANIFEST, "rules": {}}, [CAM])
    assert not r.passed


def test_replay_accepts_a_firmware_manifest_and_a_generator():
    r = replay(FirmwareManifest.model_validate(MANIFEST), (c for c in [CAM, STALL]))
    assert r.passed


def test_replay_to_json():
    j = replay(MANIFEST, [CAM, ReplayCase("x", {}, "safe_pose")]).to_json()
    assert j["checked"] == 2 and j["passed_count"] == 1 and j["passed"] is False
    assert j["cases"][0]["case_id"] == "inc-cam" and j["stub"] is False


def test_code_stub_passes_and_announces_itself():
    r = replay_code_stub()
    assert r.passed and r.stub and r.cases == ()
    assert r.why == "replay: stub, passed by default; device tests not wired (M8)"
    assert r.to_json()["stub"] is True


def test_generic_stub():
    r = replay_stub("harness has no rules to replay")
    assert r.passed and r.stub and r.why.startswith("replay: stub, passed by default; ")


# =========================================================================== canary


def healthy(n: int = 6, step: float = 20, start: float = 5) -> list:
    return [InstallObs(at(1), "installed")] + [
        CheckinObs(at(start + i * step), "ok") for i in range(n)
    ]


def verdict(obs, now_s, **kw):
    return canary_verdict(obs, T0, W, at(now_s), **kw)


def test_pending_inside_the_window():
    v = verdict(healthy(), 60)
    assert v.status == "pending" and "60 s of 120 s left" in v.why


def test_pass_exactly_at_window_end():
    assert verdict(healthy(), W).status == "pass"


def test_pending_just_before_window_end():
    assert verdict(healthy(), W - 0.001).status == "pending"


def test_pass_after_window_end():
    v = verdict(healthy(), W + 500)
    assert v.status == "pass" and "installed, 6 ok check-ins" in v.why


def test_now_before_started_at_is_pending():
    assert verdict(healthy(), -30).status == "pending"


@pytest.mark.parametrize(
    ("bad", "needle"),
    [
        (CheckinObs(at(30), "failing"), "health failing 30 s after"),
        (InstallObs(at(3), "rolled_back", "health failed"), "install rolled_back 3 s after"),
        (InstallObs(at(3), "hash_mismatch"), "install hash_mismatch"),
        (InstallObs(at(3), "invalid", "rule shape"), "install invalid 3 s after the canary"),
        (IncidentObs(at(40), "camera_occluded"), "repeated camera_occluded 40 s after"),
    ],
)
@pytest.mark.parametrize("now_s", [10, 45, W, W + 1000])
def test_fail_fast_at_any_time(bad, needle, now_s):
    # An observation stamped after `now` (clock skew) still counts: failing is the safe side.
    v = verdict([*healthy(), bad], now_s, repeat_kinds={"camera_occluded"})
    assert v.status == "fail" and needle in v.why


def test_failure_after_the_window_end_still_fails():
    v = verdict([*healthy(), CheckinObs(at(W + 5), "failing")], W + 10)
    assert v.status == "fail"


def test_install_detail_is_in_the_why():
    v = verdict([InstallObs(at(3), "rolled_back", "health failed after 5 s")], 10)
    assert "(health failed after 5 s)" in v.why


def test_earliest_failure_is_the_reason():
    obs = [CheckinObs(at(50), "failing"), IncidentObs(at(20), "camera_occluded")]
    v = verdict(obs, 60, repeat_kinds=["camera_occluded"])
    assert "repeated camera_occluded 20 s" in v.why


def test_observations_before_start_are_ignored():
    stale = [CheckinObs(at(-10), "failing"), InstallObs(at(-5), "rolled_back")]
    assert verdict([*stale, *healthy()], W).status == "pass"
    assert verdict(stale, W).status == "fail"  # but they don't count as an install either
    assert "no successful install" in verdict(stale, W).why


def test_observation_exactly_at_start_counts():
    v = verdict([*healthy(), CheckinObs(T0, "failing")], 10)
    assert v.status == "fail" and "0 s after" in v.why


def test_other_incident_kinds_do_not_fail_but_are_mentioned():
    v = verdict(
        [*healthy(), IncidentObs(at(30), "gripper_stall")], W, repeat_kinds={"camera_occluded"}
    )
    assert v.status == "pass" and "unrelated incidents: gripper_stall" in v.why


def test_no_install_report_fails_at_window_end():
    obs = [CheckinObs(at(10 * i), "ok") for i in range(1, 10)]
    assert verdict(obs, 60).status == "pending"
    v = verdict(obs, W)
    assert v.status == "fail" and "no successful install" in v.why


def test_install_after_window_end_does_not_count():
    obs = [*healthy()[1:], InstallObs(at(W + 1), "installed")]
    assert verdict(obs, W + 5).status == "fail"


def test_too_few_healthy_checkins_fails():
    v = verdict(healthy(n=1), W)
    assert v.status == "fail" and "only 1 healthy check-ins" in v.why and "need 2" in v.why


def test_min_checkins_is_configurable():
    assert verdict(healthy(n=1), W, min_checkins=1).status == "pass"
    assert verdict(healthy(n=3), W, min_checkins=4).status == "fail"
    assert verdict([InstallObs(at(1), "installed")], W, min_checkins=0).status == "pass"


def test_checkins_after_window_end_do_not_count_toward_pass():
    obs = [InstallObs(at(1), "installed"), CheckinObs(at(W + 1), "ok"), CheckinObs(at(W + 2), "ok")]
    assert verdict(obs, W + 10).status == "fail"


def test_degraded_that_recovers_passes():
    obs = [*healthy(n=3, start=5), CheckinObs(at(70), "degraded"), CheckinObs(at(100), "ok")]
    v = verdict(obs, W)
    assert v.status == "pass" and "1 non-ok check-ins that recovered" in v.why


def test_degraded_last_checkin_fails_at_window_end_but_not_before():
    obs = [*healthy(n=3, start=5), CheckinObs(at(110), "degraded")]
    assert verdict(obs, 115).status == "pending"
    v = verdict(obs, W)
    assert v.status == "fail" and "last check-in in the window was degraded" in v.why


def test_zero_window():
    obs = [InstallObs(T0, "installed"), CheckinObs(T0, "ok"), CheckinObs(T0, "ok")]
    assert canary_verdict(obs, T0, 0, T0).status == "pass"


def test_negative_window_is_an_error():
    with pytest.raises(ValueError):
        canary_verdict([], T0, -1, T0)


def test_order_of_observations_does_not_matter():
    obs = [
        *healthy(n=4, start=10),
        CheckinObs(at(55), "degraded"),
        IncidentObs(at(60), "gripper_stall"),
    ]
    rng = random.Random(7)
    for now_s in (30, W, W + 60):
        expected = verdict(obs, now_s)
        for _ in range(20):
            shuffled = obs[:]
            rng.shuffle(shuffled)
            assert verdict(shuffled, now_s) == expected
    # and with a failure in the mix, the earliest one wins whatever the order
    bad = [*obs, CheckinObs(at(90), "failing"), InstallObs(at(80), "rolled_back")]
    for perm in itertools.islice(itertools.permutations(bad[-3:]), 6):
        assert "install rolled_back 80 s" in verdict([*bad[:-3], *perm], W).why


def test_iterables_are_accepted():
    assert canary_verdict(iter(healthy()), T0, W, at(W)).status == "pass"


# =========================================================================== state machine

PASS = ReplayResult(True, "replayed 1 incident; 1 pass")
FAIL = ReplayResult(False, "replayed 1 incident; 0 pass, 1 fail (x)")
NOW = at(W + 1)


def inputs(**kw) -> GateInputs:
    defaults = dict(canary_started_at=T0, canary_observations=tuple(healthy()), canary_window_s=W)
    return GateInputs(**{**defaults, **kw})


# (state, inputs, expected new state, substring of why)
TABLE = [
    # replaying
    ("replaying", inputs(replay=None), "replaying", "waiting for replay"),
    ("replaying", inputs(replay=FAIL), "rolled_back", "never assigned to a device"),
    ("replaying", inputs(replay=PASS), "canary", "starting canary"),
    ("replaying", inputs(replay=PASS, kind="harness"), "canary", "starting canary"),
    (
        "replaying",
        inputs(replay=replay_code_stub(), kind="code", real_code_canary=True),
        "canary",
        "replay: stub, passed by default",
    ),
    ("replaying", inputs(replay=PASS, kind="code"), "awaiting_canary", "B7"),
    (
        "replaying",
        inputs(replay=PASS, kind="code", passthrough=True, real_code_canary=True),
        "awaiting_canary",
        "ALLOW_PASSTHROUGH_CODE_PROMOTION is off",
    ),
    (
        "replaying",
        inputs(
            replay=PASS,
            kind="code",
            passthrough=True,
            real_code_canary=True,
            allow_passthrough_code_promotion=True,
        ),
        "canary",
        "starting canary",
    ),
    (
        "replaying",
        inputs(replay=PASS, kind="code", passthrough=True, allow_passthrough_code_promotion=True),
        "awaiting_canary",
        "B7",
    ),
    ("replaying", inputs(replay=FAIL, kind="code"), "rolled_back", "replay"),
    # awaiting_canary
    ("awaiting_canary", inputs(kind="code"), "awaiting_canary", "B7"),
    (
        "awaiting_canary",
        inputs(kind="code", passthrough=True, real_code_canary=True),
        "awaiting_canary",
        "pass-through",
    ),
    ("awaiting_canary", inputs(kind="code", real_code_canary=True), "canary", "starting canary"),
    ("awaiting_canary", inputs(kind="firmware"), "canary", "starting canary"),
    # canary
    ("canary", inputs(canary_started_at=None), "canary", "not started"),
    ("canary", inputs(canary_observations=()), "rolled_back", "no successful install"),
    (
        "canary",
        inputs(canary_observations=(CheckinObs(at(5), "failing"),)),
        "rolled_back",
        "health failing",
    ),
    (
        "canary",
        inputs(
            canary_observations=(*healthy(), IncidentObs(at(40), "camera_occluded")),
            repeat_kinds=frozenset({"camera_occluded"}),
        ),
        "rolled_back",
        "repeated camera_occluded",
    ),
    ("canary", inputs(), "promoted", "promoting to the fleet"),
    ("canary", inputs(kind="code", real_code_canary=True), "promoted", "promoting"),
    ("canary", inputs(kind="code"), "awaiting_canary", "not promoting"),
    (
        "canary",
        inputs(kind="code", passthrough=True, real_code_canary=True),
        "awaiting_canary",
        "not promoting: pass-through",
    ),
    (
        "canary",
        inputs(kind="code", passthrough=True, real_code_canary=True, canary_observations=()),
        "rolled_back",
        "no successful install",
    ),
    # frozen (fleet unfrozen) restarts from replay (F5)
    ("frozen", inputs(), "replaying", "restarting from replay"),
    # terminal
    ("promoted", inputs(replay=FAIL), "promoted", "final"),
    ("rolled_back", inputs(replay=PASS), "rolled_back", "final"),
]


@pytest.mark.parametrize(("state", "gi", "expected", "needle"), TABLE)
def test_transition_table(state, gi, expected, needle):
    t = next_state(state, gi, NOW)
    assert t.state == expected
    assert needle in t.why, t.why


def test_canary_pending_stays_in_canary():
    t = next_state("canary", inputs(), at(30))
    assert t.state == "canary" and "window open" in t.why


def test_canary_promotes_exactly_at_the_window_end():
    assert next_state("canary", inputs(), at(W)).state == "promoted"
    assert next_state("canary", inputs(), at(W - 1)).state == "canary"


def test_canary_uses_the_inputs_window_and_min_checkins():
    gi = inputs(canary_window_s=600, min_checkins=10)
    assert next_state("canary", gi, at(300)).state == "canary"
    assert next_state("canary", gi, at(600)).state == "rolled_back"


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("now_s", [-50, 0, 60, W, W + 1000])
def test_frozen_nothing_moves(state, now_s):
    """D11: while frozen, every live candidate goes to (or stays) frozen; finals stay final."""
    for gi in (
        inputs(frozen=True),
        inputs(frozen=True, replay=PASS),
        inputs(frozen=True, replay=FAIL),
    ):
        t = next_state(state, gi, at(now_s))
        if state in TERMINAL:
            assert t.state == state
        else:
            assert t.state == "frozen"
            assert ("kill switch" in t.why) if state != "frozen" else ("still frozen" in t.why)


def test_freeze_then_unfreeze_restarts_from_replay():
    t = next_state("canary", inputs(frozen=True), NOW)
    assert t.state == "frozen"
    t = next_state(t.state, inputs(frozen=True), NOW)
    assert t.state == "frozen"
    t = next_state(t.state, inputs(frozen=False), NOW)
    assert t.state == "replaying"


@pytest.mark.parametrize(("state", "gi", "expected", "needle"), TABLE)
def test_touches_safety_never_changes_the_path(state, gi, expected, needle):
    flagged = GateInputs(**{**gi.__dict__, "touches_safety": True})
    plain, marked = next_state(state, gi, NOW), next_state(state, flagged, NOW)
    assert marked.state == plain.state
    if marked.state != state:
        assert marked.why == plain.why + " [touches safety layer or visor; K17]"
    else:
        assert marked.why == plain.why


def test_unknown_state_raises():
    with pytest.raises(ValueError, match="unknown candidate state"):
        next_state("shipping", inputs(), NOW)


def test_full_happy_path_through_the_machine():
    state = "replaying"
    state, _ = next_state(state, inputs(replay=None), NOW)
    assert state == "replaying"
    state, _ = next_state(state, inputs(replay=PASS), NOW)
    assert state == "canary"
    state, _ = next_state(state, inputs(), at(30))
    assert state == "canary"
    state, why = next_state(state, inputs(), at(W))
    assert state == "promoted" and "canary healthy" in why


def test_pass_through_code_never_reaches_canary_or_promoted_without_the_setting():
    """BUILD §3: a pass-through code candidate never passes awaiting_canary unless allowed."""
    for state in STATES:
        for real in (True, False):
            for replay_result in (PASS, replay_code_stub(), None):
                for obs in ((), tuple(healthy())):
                    gi = inputs(
                        kind="code",
                        passthrough=True,
                        real_code_canary=real,
                        replay=replay_result,
                        canary_observations=obs,
                    )
                    t = next_state(state, gi, NOW)
                    if state != "promoted":
                        assert t.state != "promoted", (state, gi)
                    if state != "canary":
                        assert t.state != "canary", (state, gi)


# =========================================================================== composition


def add(n: int, name: str | None = None) -> Stage:
    return Stage(name or f"add{n}", lambda ctx: StageResult(True, ctx + n, (f"+{n}",)))


def double() -> Stage:
    return Stage("double", lambda ctx: StageResult(True, ctx * 2, ("*2",)))


def fail(note: str = "nope") -> Stage:
    return Stage("fail", lambda ctx: StageResult(False, ctx, (note,)))


STAGES = [add(1), double(), fail(), passthrough("draft_code"), ID]


@pytest.mark.parametrize("s", STAGES)
@pytest.mark.parametrize("ctx", [0, 3, -2])
def test_identity_laws(s, ctx):
    assert (ID >> s)(ctx) == s(ctx)
    assert (s >> ID)(ctx) == s(ctx)


@pytest.mark.parametrize(("a", "b", "c"), list(itertools.product(STAGES, repeat=3)))
def test_associativity(a, b, c):
    for ctx in (0, 5):
        assert ((a >> b) >> c)(ctx) == (a >> (b >> c))(ctx) == compose(a, b, c)(ctx)


def test_compose_runs_left_to_right_and_accumulates_notes():
    r = (add(1) >> double())(3)
    assert r == StageResult(True, 8, ("+1", "*2"))


def test_compose_stops_at_the_first_failure():
    r = (add(1) >> fail("replay failed") >> double())(3)
    assert r == StageResult(False, 4, ("+1", "replay failed"))


def test_compose_of_nothing_is_identity():
    assert compose()(5) == StageResult(True, 5)


def test_stage_names():
    assert (add(1) >> double()).name == "add1 >> double"
    assert ID.name == "id"


def test_passthrough_labels_itself_as_a_stub():
    r = (passthrough("draft_code") >> add(2))(1)
    assert r.ok and r.ctx == 3
    assert r.notes == ("draft_code: stub, pass-through", "+2")
    assert r.stubs == ("draft_code",)


def test_by_kind_dispatches_and_fails_on_unknown_kinds():
    replay_stage = by_kind(
        lambda ctx: ctx["kind"],
        rules=Stage("rules_replay", lambda ctx: StageResult(True, ctx, ("rules",))),
        code=passthrough("replay"),
    )
    assert replay_stage({"kind": "rules"}).notes == ("rules",)
    assert replay_stage({"kind": "code"}).stubs == ("replay",)
    r = replay_stage({"kind": "policy"})
    assert not r.ok and r.notes == ("no stage for kind 'policy'",)
    assert replay_stage.name == "by_kind(rules=rules_replay, code=replay)"


def test_gate_pipeline_shape_from_build_section_3():
    """`gate = replay >> canary >> promote_or_rollback` composes from real replay results."""

    def rules_replay(ctx):
        r = replay(ctx["manifest"], ctx["cases"])
        return StageResult(r.passed, {**ctx, "replay": r}, (r.why,), ("replay",) if r.stub else ())

    def code_replay(ctx):
        r = replay_code_stub()
        return StageResult(True, {**ctx, "replay": r}, (r.why,), ("replay",))

    replay_stage = by_kind(
        lambda ctx: ctx["kind"],
        firmware=Stage("rules_replay", rules_replay),
        code=Stage("code_replay", code_replay),
    )
    gate = replay_stage >> passthrough("canary") >> ID
    ok = gate({"kind": "firmware", "manifest": MANIFEST, "cases": [CAM]})
    assert ok.ok and ok.notes[0].startswith("replayed 1 incident; 1 pass")
    bad = gate({"kind": "firmware", "manifest": MANIFEST, "cases": []})
    assert not bad.ok and len(bad.notes) == 1
    code = gate({"kind": "code"})
    assert code.ok and code.stubs == ("replay", "canary")


# =========================================================================== purity


@pytest.mark.parametrize("module", ["gate.py", "merge.py"])
def test_domain_module_imports_only_contracts_and_stdlib(module):
    """Architecture §9 rule 3: domain/ imports only romulus_contracts and the stdlib."""
    path = Path(__file__).parents[3] / "src" / "romulus_cloud" / "domain" / module
    roots = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    assert roots - {"romulus_contracts"} <= sys.stdlib_module_names, roots
