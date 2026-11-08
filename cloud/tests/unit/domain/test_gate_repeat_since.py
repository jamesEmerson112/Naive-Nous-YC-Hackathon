"""domain/gate.py: a repeat incident seen while frozen fails the candidate on unfreeze."""

from datetime import UTC, datetime, timedelta

from romulus_cloud.domain.gate import CheckinObs, GateInputs, IncidentObs, next_state, repeat_since

T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
KINDS = frozenset({"camera_occluded"})


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def test_repeat_since_prefers_a_proven_repeat_after_the_start():
    hit = repeat_since(
        [
            IncidentObs(at(-5), "camera_occluded", 2),  # before the run
            IncidentObs(at(30), "camera_occluded", 1),  # the previous version
            IncidentObs(at(40), "gripper_stall", 2),  # another kind
            IncidentObs(at(45), "camera_occluded", None),  # unknown: only unproven
            IncidentObs(at(50), "camera_occluded", 2),
        ],
        T0,
        version=2,
        repeat_kinds=KINDS,
    )
    assert hit == ("canary repeated camera_occluded 50 s after the canary started", True)


def test_repeat_since_an_unknown_version_only_is_unproven():
    obs = [IncidentObs(at(45), "camera_occluded", None), IncidentObs(at(30), "x", 2)]
    assert repeat_since(obs, T0, version=2, repeat_kinds=KINDS) == (
        "canary repeated camera_occluded 45 s after the canary started on an unknown version",
        False,
    )


def test_unfreeze_marks_an_unknown_version_repeat_as_unproven():
    t = next_state("frozen", _unfrozen(IncidentObs(at(400), "camera_occluded", None)), at(900))
    assert t.state == "rolled_back" and t.unproven and "unknown version" in t.why


def test_repeat_since_ignores_everything_else():
    obs = [
        IncidentObs(at(30), "camera_occluded", 1),
        IncidentObs(at(40), "gripper_stall", 2),
        CheckinObs(at(50), "failing", 2),
    ]
    assert repeat_since(obs, T0, version=2, repeat_kinds=KINDS) is None
    assert repeat_since(obs, T0, version=2, repeat_kinds=()) is None


def _unfrozen(*obs, started=T0) -> GateInputs:
    return GateInputs(
        candidate_version=2,
        install_type="firmware",
        canary_started_at=started,
        canary_observations=tuple(obs),
        repeat_kinds=KINDS,
    )


def test_unfreeze_rolls_back_a_candidate_whose_canary_repeated_while_frozen():
    t = next_state("frozen", _unfrozen(IncidentObs(at(400), "camera_occluded", 2)), at(900))
    assert t.state == "rolled_back" and not t.unproven
    assert t.why == (
        "canary repeated camera_occluded 400 s after the canary started (seen on unfreeze); "
        "not restarting"
    )


def test_unfreeze_restarts_without_a_repeat_or_a_run():
    assert next_state("frozen", _unfrozen(), at(900)).state == "replaying"
    other = IncidentObs(at(400), "camera_occluded", 1)
    assert next_state("frozen", _unfrozen(other), at(900)).state == "replaying"
    never_ran = _unfrozen(IncidentObs(at(400), "camera_occluded", 2), started=None)
    assert next_state("frozen", never_ran, at(900)).state == "replaying"


def test_still_frozen_waits_even_with_a_repeat():
    inputs = GateInputs(
        frozen=True,
        candidate_version=2,
        canary_started_at=T0,
        canary_observations=(IncidentObs(at(400), "camera_occluded", 2),),
        repeat_kinds=KINDS,
    )
    assert next_state("frozen", inputs, at(900)).state == "frozen"
