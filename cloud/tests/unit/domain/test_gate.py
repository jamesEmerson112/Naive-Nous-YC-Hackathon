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

V, OLD = 8, 7  # the candidate's version, and what the canary ran before


def healthy(n: int = 6, step: float = 20, start: float = 5, installed: float = 1) -> list:
    """Install at `installed` s, then `n` ok check-ins on V: 5, 25, ..., 105 s by default."""
    return [InstallObs(at(installed), "installed", V)] + [
        CheckinObs(at(start + i * step), "ok", V) for i in range(n)
    ]


def verdict(obs, now_s, window=W, **kw):
    kw = {"version": V, "install_type": "firmware", **kw}
    return canary_verdict(obs, T0, window, at(now_s), **kw)


def test_pending_inside_the_window():
    v = verdict(healthy(), 60)
    assert v.status == "pending" and "60 s of 120 s left" in v.why


def test_pass_exactly_at_window_end():
    assert verdict(healthy(), W).status == "pass"


def test_pending_just_before_window_end():
    assert verdict(healthy(), W - 0.001).status == "pending"


def test_pass_after_window_end():
    v = verdict(healthy(), W + 500)
    assert v.status == "pass" and "installed 8, 6 ok check-ins" in v.why


def test_now_before_started_at_is_pending():
    assert verdict(healthy(), -30).status == "pending"


@pytest.mark.parametrize(
    ("bad", "needle"),
    [
        (CheckinObs(at(30), "failing", V), "health failing 30 s after"),
        (CheckinObs(at(30), "failing"), "health failing 30 s after"),  # unknown version counts
        (InstallObs(at(3), "rolled_back", V, detail="health failed"), "install rolled_back 3 s"),
        (InstallObs(at(3), "hash_mismatch", V), "install hash_mismatch"),
        (InstallObs(at(3), "invalid", V, detail="rule shape"), "install invalid 3 s after the"),
        (IncidentObs(at(40), "camera_occluded"), "repeated camera_occluded 40 s after"),
        (IncidentObs(at(40), "camera_occluded", V), "repeated camera_occluded 40 s after"),
        (CheckinObs(at(30), "ok", V, overlay_id="ov-9"), "needed a local overlay (ov-9) 30 s"),
    ],
)
@pytest.mark.parametrize("now_s", [10, 45, W, W + 1000])
def test_fail_fast_at_any_time(bad, needle, now_s):
    # An observation stamped after `now` (clock skew) still counts: failing is the safe side.
    v = verdict([*healthy(), bad], now_s, repeat_kinds={"camera_occluded"})
    assert v.status == "fail" and needle in v.why


@pytest.mark.parametrize(
    "ignored",
    [
        CheckinObs(at(30), "failing", OLD),  # the old version was failing: not the candidate
        IncidentObs(at(30), "camera_occluded", OLD),  # the fault the candidate fixes, on OLD
        InstallObs(at(30), "rolled_back", OLD),  # another version's install
        InstallObs(at(30), "rolled_back", V, type="harness"),  # another type's install
        CheckinObs(at(0.5), "ok", V, overlay_id="ov-1"),  # overlay before the install
        CheckinObs(at(30), "ok", OLD, overlay_id="ov-1"),  # overlay on another version
    ],
)
def test_observations_for_another_version_count_for_nothing(ignored):
    v = verdict([*healthy(), ignored], W, repeat_kinds={"camera_occluded"})
    assert v.status == "pass", v.why


def test_reviewer_case_old_version_checkins_before_a_late_install_do_not_pass():
    """Review finding 1: ok at 10 s and 20 s, installed at 115 s, now 120 s -> not a pass."""
    for version in (OLD, V, None):
        obs = [
            CheckinObs(at(10), "ok", version),
            CheckinObs(at(20), "ok", version),
            InstallObs(at(115), "installed", V),
        ]
        v = verdict(obs, W)
        assert v.status == "fail" and "only 0 healthy check-ins" in v.why


def test_old_version_checkins_after_the_install_do_not_count():
    obs = [InstallObs(at(1), "installed", V)] + [
        CheckinObs(at(10 * i), "ok", OLD) for i in range(1, 12)
    ]
    assert verdict(obs, W).status == "fail"


def test_install_of_the_wrong_version_or_type_does_not_count():
    checkins = healthy()[1:]
    for install in (InstallObs(at(1), "installed", 9), InstallObs(at(1), "installed", "8")):
        v = verdict([install, *checkins], W)
        assert v.status == "fail" and "no successful firmware 8 install" in v.why
    harness = InstallObs(at(1), "installed", V, type="harness")
    assert verdict([harness, *checkins], W).status == "fail"
    assert verdict([harness, *checkins], W, install_type="harness").status == "pass"


def test_code_rev_versions():
    obs = [InstallObs(at(1), "installed", "c0de002", type="code")] + [
        CheckinObs(at(5 + 20 * i), "ok", "c0de002") for i in range(6)
    ]
    assert verdict(obs, W, version="c0de002", install_type="code").status == "pass"
    assert verdict(obs, W, version="c0de001", install_type="code").status == "fail"


def test_checkins_count_only_from_the_first_matching_install():
    obs = healthy(installed=50)  # check-ins at 5, 25 and 45 s came before the install
    v = verdict(obs, W)
    assert v.status == "pass" and "3 ok check-ins" in v.why


def test_failure_after_the_window_end_still_fails():
    v = verdict([*healthy(), CheckinObs(at(W + 5), "failing", V)], W + 10)
    assert v.status == "fail"


def test_earliest_failure_is_the_reason():
    obs = [CheckinObs(at(50), "failing", V), IncidentObs(at(20), "camera_occluded")]
    v = verdict(obs, 60, repeat_kinds=["camera_occluded"])
    assert "repeated camera_occluded 20 s" in v.why


def test_observations_before_start_are_ignored():
    stale = [CheckinObs(at(-10), "failing", V), InstallObs(at(-5), "rolled_back", V)]
    assert verdict([*stale, *healthy()], W).status == "pass"
    v = verdict([InstallObs(at(-5), "installed", V), *healthy()[1:]], W)
    assert v.status == "fail" and "no successful firmware 8 install" in v.why


def test_observation_exactly_at_start_counts():
    v = verdict([*healthy(), CheckinObs(T0, "failing", V)], 10)
    assert v.status == "fail" and "0 s after" in v.why


def test_other_incident_kinds_do_not_fail_but_are_mentioned():
    v = verdict(
        [*healthy(), IncidentObs(at(30), "gripper_stall")], W, repeat_kinds={"camera_occluded"}
    )
    assert v.status == "pass" and "unrelated incidents: gripper_stall" in v.why


def test_no_install_report_fails_at_window_end():
    obs = healthy()[1:]
    assert verdict(obs, 60).status == "pending"
    v = verdict(obs, W)
    assert v.status == "fail" and "no successful firmware 8 install" in v.why


def test_install_after_window_end_does_not_count():
    obs = [*healthy()[1:], InstallObs(at(W + 1), "installed", V)]
    assert verdict(obs, W + 5).status == "fail"


def test_too_few_healthy_checkins_fails():
    v = verdict(healthy(n=1, start=100), W)
    assert v.status == "fail" and "only 1 healthy check-ins" in v.why and "need 2" in v.why


def test_min_checkins_is_configurable():
    assert verdict(healthy(n=1, start=100), W, min_checkins=1).status == "pass"
    assert verdict(healthy(n=3, start=60), W, min_checkins=4).status == "fail"
    assert verdict([InstallObs(at(1), "installed", V)], W, min_checkins=0).status == "pass"


def test_checkins_after_window_end_do_not_count_toward_pass():
    obs = [InstallObs(at(1), "installed", V), CheckinObs(at(W + 1), "ok", V)]
    obs.append(CheckinObs(at(W + 2), "ok", V))
    assert verdict(obs, W + 10).status == "fail"


def test_reviewer_case_a_duplicated_checkin_counts_once():
    """Review finding 4: the same check-in twice is one check-in."""
    dup = CheckinObs(at(100), "ok", V)
    v = verdict([InstallObs(at(1), "installed", V), dup, dup], W)
    assert v.status == "fail" and "only 1 healthy check-ins" in v.why
    two = [InstallObs(at(1), "installed", V), CheckinObs(at(100), "ok", V)]
    assert verdict([*two, CheckinObs(at(100), "ok", V, device_id="sim-3")], W).status == "pass"


def test_reviewer_case_a_canary_that_goes_silent_cannot_pass():
    """Review finding 4: two ok check-ins in the first 5 s of a 600 s window, then silence."""
    obs = [
        InstallObs(at(1), "installed", V),
        CheckinObs(at(2), "ok", V),
        CheckinObs(at(5), "ok", V),
    ]
    v = verdict(obs, 600, window=600)
    assert v.status == "fail" and "no healthy check-in in the last 200 s" in v.why


def test_coverage_edge_is_inclusive():
    tail = W - W / 3  # 80 s
    base = [InstallObs(at(1), "installed", V), CheckinObs(at(10), "ok", V)]
    assert verdict([*base, CheckinObs(at(tail), "ok", V)], W).status == "pass"
    assert verdict([*base, CheckinObs(at(tail - 0.001), "ok", V)], W).status == "fail"


def test_degraded_that_recovers_passes():
    obs = [*healthy(n=3, start=5), CheckinObs(at(70), "degraded", V), CheckinObs(at(100), "ok", V)]
    v = verdict(obs, W)
    assert v.status == "pass" and "1 non-ok check-ins that recovered" in v.why


def test_degraded_last_checkin_fails_at_window_end_but_not_before():
    obs = [*healthy(n=5, start=5), CheckinObs(at(110), "degraded", V)]
    assert verdict(obs, 115).status == "pending"
    v = verdict(obs, W)
    assert v.status == "fail" and "last check-in in the window was degraded" in v.why


def test_zero_window():
    obs = [InstallObs(T0, "installed", V), CheckinObs(T0, "ok", V)]
    obs.append(CheckinObs(T0, "ok", V, device_id="b"))
    assert verdict(obs, 0, window=0).status == "pass"


@pytest.mark.parametrize("window", [-1, float("nan"), float("inf"), "120", True, None])
def test_bad_windows_are_a_clear_error(window):
    with pytest.raises(ValueError, match="finite number of seconds >= 0"):
        verdict([], 0, window=window)
    with pytest.raises(ValueError, match="finite number of seconds >= 0"):
        next_state("canary", inputs(canary_window_s=window), NOW)


def test_order_of_observations_does_not_matter():
    obs = [
        *healthy(n=5, start=10),
        CheckinObs(at(55), "degraded", V),
        CheckinObs(at(55), "degraded", V),
        IncidentObs(at(60), "gripper_stall"),
        CheckinObs(at(70), "ok", OLD),
    ]
    rng = random.Random(7)
    for now_s in (30, W, W + 60):
        expected = verdict(obs, now_s)
        for _ in range(20):
            shuffled = obs[:]
            rng.shuffle(shuffled)
            assert verdict(shuffled, now_s) == expected
    # and with failures in the mix, the earliest one wins whatever the order
    extra = [
        CheckinObs(at(90), "failing", V),
        InstallObs(at(80), "rolled_back", V),
        CheckinObs(at(85), "ok", V, overlay_id="ov-2"),
    ]
    for perm in itertools.permutations(extra):
        assert "install rolled_back 80 s" in verdict([*obs, *perm], W).why


def test_iterables_are_accepted():
    assert verdict(iter(healthy()), W).status == "pass"


# =========================================================================== state machine

PASS = ReplayResult(True, "replayed 1 incident; 1 pass")
FAIL = ReplayResult(False, "replayed 1 incident; 0 pass, 1 fail (x)")
STUB = replay_code_stub()
NOW = at(W + 1)


def inputs(**kw) -> GateInputs:
    defaults = dict(
        canary_available=True,
        candidate_version=V,
        canary_started_at=T0,
        canary_observations=tuple(healthy()),
        canary_window_s=W,
    )
    return GateInputs(**{**defaults, **kw})


def code(**kw) -> GateInputs:
    """A coding-agent code candidate that is allowed through: tests ran, real-code canary."""
    defaults = dict(
        kind="code", passthrough=False, real_code_canary=True, replay=PASS, install_type="firmware"
    )
    return inputs(**{**defaults, **kw})


# (state, inputs, expected new state, substring of why)
TABLE = [
    # replaying
    ("replaying", inputs(replay=None), "replaying", "waiting for replay"),
    ("replaying", inputs(replay=FAIL), "rolled_back", "never assigned to a device"),
    ("replaying", inputs(replay=PASS), "canary", "starting a fresh canary"),
    ("replaying", inputs(replay=PASS, kind="harness"), "canary", "starting a fresh canary"),
    ("replaying", inputs(replay=PASS, canary_available=False), "awaiting_canary", "no canary dev"),
    (
        "replaying",
        inputs(replay=PASS, kind="harness", canary_available=False),
        "awaiting_canary",
        "no canary dev",
    ),
    ("replaying", code(), "canary", "starting a fresh canary"),
    ("replaying", code(canary_available=False), "awaiting_canary", "no canary device is available"),
    ("replaying", code(passthrough=True), "awaiting_canary", "pass-through code candidate"),
    ("replaying", code(replay=STUB), "awaiting_canary", "no device tests ran"),
    (
        "replaying",
        code(passthrough=True, replay=STUB, allow_passthrough_code_promotion=True),
        "canary",
        "replay: stub, passed by default",
    ),
    ("replaying", code(real_code_canary=False), "awaiting_canary", "B7"),
    (
        "replaying",
        code(passthrough=True, real_code_canary=False, allow_passthrough_code_promotion=True),
        "awaiting_canary",
        "B7",
    ),
    ("replaying", code(kind="Code"), "awaiting_canary", "unknown candidate kind 'Code'"),
    ("replaying", inputs(replay=PASS, kind="policy"), "awaiting_canary", "unknown candidate kind"),
    ("replaying", inputs(replay=PASS, kind=""), "awaiting_canary", "unknown candidate kind ''"),
    ("replaying", code(replay=FAIL), "rolled_back", "replay"),
    # awaiting_canary
    ("awaiting_canary", code(real_code_canary=False), "awaiting_canary", "B7"),
    ("awaiting_canary", code(passthrough=True), "awaiting_canary", "pass-through"),
    ("awaiting_canary", code(replay=None), "awaiting_canary", "no device tests ran"),
    ("awaiting_canary", code(), "canary", "starting a fresh canary"),
    ("awaiting_canary", inputs(kind="firmware"), "canary", "starting a fresh canary"),
    ("awaiting_canary", inputs(canary_available=False), "awaiting_canary", "no canary device"),
    ("awaiting_canary", inputs(kind="Firmware"), "awaiting_canary", "unknown candidate kind"),
    # canary
    ("canary", inputs(canary_started_at=None), "canary", "not started"),
    ("canary", inputs(canary_observations=()), "rolled_back", "no successful firmware 8 install"),
    (
        "canary",
        inputs(canary_observations=(CheckinObs(at(5), "failing", V),)),
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
    (
        "canary",
        inputs(canary_observations=(*healthy(), CheckinObs(at(50), "ok", V, overlay_id="ov-3"))),
        "rolled_back",
        "needed a local overlay (ov-3)",
    ),
    ("canary", inputs(), "promoted", "promoting to the fleet"),
    (
        "canary",
        inputs(
            kind="harness",
            install_type="harness",
            canary_observations=(InstallObs(at(1), "installed", V, type="harness"), *healthy()[1:]),
        ),
        "promoted",
        "promoting",
    ),
    ("canary", inputs(kind="harness"), "rolled_back", "no successful harness 8 install"),
    ("canary", code(), "promoted", "promoting"),
    (
        "canary",
        code(real_code_canary=False),
        "awaiting_canary",
        "not promoting: no canary device runs",
    ),
    ("canary", code(passthrough=True), "awaiting_canary", "not promoting: pass-through"),
    ("canary", code(replay=STUB), "awaiting_canary", "not promoting: no device tests ran"),
    ("canary", code(kind="Code"), "awaiting_canary", "not promoting: unknown candidate kind"),
    ("canary", inputs(canary_available=False), "awaiting_canary", "not promoting: no canary"),
    ("canary", code(passthrough=True, canary_observations=()), "rolled_back", "no successful"),
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
    # every move into canary (and only that) asks for a fresh canary start
    assert t.start_canary == (expected == "canary" and state != "canary")


def test_canary_pending_stays_in_canary():
    t = next_state("canary", inputs(), at(30))
    assert t.state == "canary" and "window open" in t.why and not t.start_canary


def test_canary_promotes_exactly_at_the_window_end():
    assert next_state("canary", inputs(), at(W)).state == "promoted"
    assert next_state("canary", inputs(), at(W - 1)).state == "canary"


def test_canary_uses_the_inputs_window_and_min_checkins():
    gi = inputs(canary_window_s=600, min_checkins=10)
    assert next_state("canary", gi, at(300)).state == "canary"
    assert next_state("canary", gi, at(600)).state == "rolled_back"


def test_canary_needs_the_candidate_version():
    with pytest.raises(ValueError, match="candidate_version is required"):
        next_state("canary", inputs(candidate_version=None), NOW)


def test_defaults_fail_closed():
    gi = GateInputs(kind="code", replay=PASS, real_code_canary=True)
    assert gi.passthrough is True and gi.canary_available is False
    assert next_state("replaying", gi, NOW).state == "awaiting_canary"
    assert "no canary device" in next_state("replaying", GateInputs(replay=PASS), NOW).why


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("now_s", [-50, 0, 60, W, W + 1000])
def test_frozen_nothing_moves(state, now_s):
    """D11: while frozen, every live candidate goes to (or stays) frozen; finals stay final."""
    for gi in (
        inputs(frozen=True),
        inputs(frozen=True, replay=PASS),
        inputs(frozen=True, replay=FAIL),
        code(frozen=True),
    ):
        t = next_state(state, gi, at(now_s))
        assert not t.start_canary
        if state in TERMINAL:
            assert t.state == state
        else:
            assert t.state == "frozen"
            assert ("kill switch" in t.why) if state != "frozen" else ("still frozen" in t.why)


class Service:
    """What M3b does around `next_state`: on `start_canary`, set `canary_started_at = now`."""

    def __init__(self, state: str, started_at: datetime | None, obs: list):
        self.state, self.started_at, self.obs = state, started_at, obs

    def tick(self, now: datetime, **kw) -> str:
        gi = inputs(canary_started_at=self.started_at, canary_observations=tuple(self.obs), **kw)
        t = next_state(self.state, gi, now)
        if t.start_canary:
            self.started_at = now
        self.state = t.state
        return t.state


def test_reviewer_case_freeze_unfreeze_recanary_needs_a_fresh_window():
    """Review finding 2: canary -> frozen -> replaying -> canary must not reuse old reports."""
    svc = Service("canary", T0, healthy())  # a healthy first run, cut short by a freeze
    assert svc.tick(at(60), frozen=True) == "frozen"
    assert svc.tick(at(70)) == "replaying"
    assert svc.tick(at(80), replay=PASS) == "canary"
    assert svc.started_at == at(80)
    # the bug promoted here, on the old run's reports
    assert svc.tick(at(81)) == "canary"
    assert svc.tick(at(W + 1)) == "canary"  # pending until the fresh window ends
    assert svc.tick(at(80 + W)) == "rolled_back"  # nothing new arrived: no install, no pass


def test_freeze_unfreeze_recanary_promotes_on_fresh_reports():
    svc = Service("canary", T0, healthy())
    svc.tick(at(60), frozen=True)
    svc.tick(at(70))
    svc.tick(at(80), replay=PASS)
    svc.obs += [InstallObs(at(81), "installed", V)]
    svc.obs += [CheckinObs(at(85 + 20 * i), "ok", V) for i in range(6)]
    assert svc.tick(at(80 + W - 1)) == "canary"
    assert svc.tick(at(80 + W)) == "promoted"


def test_awaiting_then_canary_needs_a_fresh_window():
    """canary -> awaiting_canary -> canary also restarts the window."""
    svc = Service("canary", T0, healthy())
    assert svc.tick(at(W), canary_available=False) == "awaiting_canary"
    assert svc.tick(at(W + 5), canary_available=False) == "awaiting_canary"
    assert svc.tick(at(W + 10)) == "canary"
    assert svc.started_at == at(W + 10)
    assert svc.tick(at(W + 11)) == "canary"
    assert svc.tick(at(2 * W + 10)) == "rolled_back"


def test_freeze_then_unfreeze_restarts_from_replay():
    t = next_state("canary", inputs(frozen=True), NOW)
    assert t.state == "frozen"
    t = next_state(t.state, inputs(frozen=True), NOW)
    assert t.state == "frozen"
    t = next_state(t.state, inputs(frozen=False), NOW)
    assert t.state == "replaying" and not t.start_canary


@pytest.mark.parametrize(("state", "gi", "expected", "needle"), TABLE)
def test_touches_safety_never_changes_the_path(state, gi, expected, needle):
    flagged = GateInputs(**{**gi.__dict__, "touches_safety": True})
    plain, marked = next_state(state, gi, NOW), next_state(state, flagged, NOW)
    assert (marked.state, marked.start_canary) == (plain.state, plain.start_canary)
    if marked.state != state:
        assert marked.why == plain.why + " [touches safety layer or visor; K17]"
    else:
        assert marked.why == plain.why


def test_unknown_state_raises():
    with pytest.raises(ValueError, match="unknown candidate state"):
        next_state("shipping", inputs(), NOW)


def test_full_happy_path_through_the_machine():
    svc = Service("replaying", None, [])
    assert svc.tick(NOW) == "replaying"
    assert svc.tick(at(0), replay=PASS) == "canary" and svc.started_at == T0
    svc.obs = healthy()
    assert svc.tick(at(30)) == "canary"
    t = next_state("canary", inputs(), at(W))
    assert t.state == "promoted" and "canary healthy" in t.why


def test_code_candidates_that_must_wait_never_reach_canary_or_promoted():
    """BUILD §3 and F11: pass-through code, stub-only replay, or an odd kind never goes on."""
    for state in STATES:
        for kind in ("code", "Code", "CODE", "unknown"):
            for is_passthrough, replay_result in itertools.product(
                (True, False), (PASS, STUB, None)
            ):
                if kind == "code" and not is_passthrough and replay_result is PASS:
                    continue  # the one allowed combination
                for real, obs in itertools.product((True, False), ((), tuple(healthy()))):
                    gi = code(
                        kind=kind,
                        passthrough=is_passthrough,
                        replay=replay_result,
                        real_code_canary=real,
                        canary_observations=obs,
                    )
                    t = next_state(state, gi, NOW)
                    if state != "promoted":
                        assert t.state != "promoted", (state, gi)
                    if state != "canary":
                        assert t.state != "canary", (state, gi)


def test_replay_result_json_round_trip():
    r = replay(MANIFEST, [CAM, ReplayCase("x", {}, "safe_pose")])
    back = ReplayResult.from_json(r.to_json())
    assert (back.passed, back.why, back.stub) == (r.passed, r.why, r.stub)
    assert [(c.case_id, c.passed, c.fired) for c in back.cases] == [
        (c.case_id, c.passed, c.fired) for c in r.cases
    ]
    assert ReplayResult.from_json(STUB.to_json()).stub is True
    assert ReplayResult.from_json(None) is None
    assert ReplayResult.from_json({"passed": True, "why": "old row"}).stub is True  # fail closed


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


DOMAIN = Path(__file__).parents[3] / "src" / "romulus_cloud" / "domain"


def test_purity_check_sees_the_domain_modules():
    assert {"gate.py", "merge.py"} <= {p.name for p in DOMAIN.glob("*.py")}


@pytest.mark.parametrize("path", sorted(DOMAIN.glob("*.py")), ids=lambda p: p.name)
def test_domain_module_imports_only_contracts_and_stdlib(path):
    """Architecture §9 rule 3: every domain/*.py imports only romulus_contracts and the stdlib."""
    roots = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    assert roots - {"romulus_contracts"} <= sys.stdlib_module_names, roots
