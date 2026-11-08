"""Grouping podd incidents into faults, and when a fault is ready (faults.py). Pure."""

import pytest

from edge_bridge.faults import Fault, FaultTracker, Timing

T = 1000.0


def opened(kind, ts, severity="critical"):
    return {"ts": ts, "status": "open", "id": f"{kind}-{int(ts)}", "kind": kind,
            "opened": ts, "severity": severity, "summary": kind, "first_evidence": {}}  # fmt: skip


def resolved(kind, opened_at, ts):
    return {"ts": ts, "status": "resolved", "id": f"{kind}-{int(opened_at)}", "kind": kind,
            "duration_s": ts - opened_at}  # fmt: skip


def test_one_fault_opens_several_incidents_and_is_reported_once_settled():
    # Scenario 01 opens crash, reboot_loop and safe_mode within ~5 s (HARNESS.md §7).
    t = FaultTracker()
    for kind, at in (("crash", T), ("reboot_loop", T + 2), ("safe_mode", T + 4)):
        t.observe_incident(opened(kind, at), mcu_build="v1.1@32ACFDAF")
    assert len(t.faults) == 1
    fault = t.faults[0]
    assert (fault.key, fault.mcu_build) == ("crash-1000", "v1.1@32ACFDAF")
    assert [i.kind for i in fault.incidents] == ["crash", "reboot_loop", "safe_mode"]

    for kind, at in (("crash", T), ("reboot_loop", T + 2), ("safe_mode", T + 4)):
        t.observe_incident(resolved(kind, at, T + 60))
    assert t.pop_ready(T + 74) == []  # settle_s (15 s) not over yet
    ((ready, reason),) = t.pop_ready(T + 75)
    assert (ready.key, reason) == ("crash-1000", "resolved")
    assert t.faults == []


def test_the_primary_incident_is_the_most_severe_then_the_earliest():
    t = FaultTracker()
    t.observe_incident(opened("acc_stale", T, "warn"))
    t.observe_incident(opened("acc_frozen", T + 1, "critical"))
    t.observe_incident(opened("watchdog", T + 2, "critical"))
    assert t.faults[0].primary.kind == "acc_frozen"


def test_an_incident_joins_while_the_fault_is_open_or_recent_else_starts_a_new_fault():
    t = FaultTracker()
    t.observe_incident(opened("crash", T))
    t.observe_incident(resolved("crash", T, T + 20))
    t.observe_incident(opened("watchdog", T + 29))  # 9 s after the last record: joins
    t.observe_incident(resolved("watchdog", T + 29, T + 40))
    t.observe_incident(opened("supply", T + 51, "warn"))  # 11 s later: a new fault
    assert [[i.kind for i in f.incidents] for f in t.faults] == [["crash", "watchdog"], ["supply"]]


def test_tool_calls_keep_a_fault_in_progress():
    t = FaultTracker()
    t.observe_incident(opened("crash", T))
    t.observe_incident(resolved("crash", T, T + 10))
    t.observe_activity(T + 20)  # the healer is still verifying
    assert t.pop_ready(T + 30) == []
    assert [r for _, r in t.pop_ready(T + 35)] == ["resolved"]


def test_activity_after_a_fault_was_ready_belongs_to_no_fault():
    # The outcome mustn't depend on when the bridge ticks: at T + 25 this fault was
    # already ready, so a later call doesn't reopen it.
    t = FaultTracker()
    t.observe_incident(opened("crash", T))
    t.observe_incident(resolved("crash", T, T + 10))
    t.observe_activity(T + 40)
    assert t.faults[0].last_activity == T + 10
    assert [r for _, r in t.pop_ready(T + 41)] == ["resolved"]


def test_a_fault_ready_at_a_records_time_takes_no_new_incidents_or_resolves():
    timing = Timing(max_open_s=100)
    t = FaultTracker(timing)
    t.observe_incident(opened("acc_error", T, "warn"))
    t.observe_incident(resolved("acc_error", T, T + 150))  # it had timed out at T + 100
    t.observe_incident(opened("acc_error", T + 151, "warn"))
    assert len(t.faults) == 2
    assert t.faults[0].unresolved
    assert [r for _, r in t.pop_ready(T + 152)] == ["timed_out"]


def test_asking_a_person_hands_an_unresolved_fault_off_after_quiet():
    t = FaultTracker()
    t.observe_incident(opened("acc_error", T, "warn"))
    t.observe_activity(T + 30)
    t.observe_activity(T + 70, handoff=True)  # notify_human with instructions
    assert t.faults[0].handoff_at == T + 70
    assert t.pop_ready(T + 129) == []
    ((fault, reason),) = t.pop_ready(T + 130)
    assert (reason, fault.unresolved) == ("handed_off", True)


def test_a_notification_without_instructions_is_not_a_handoff():
    t = FaultTracker()
    t.observe_incident(opened("acc_error", T, "warn"))
    t.observe_activity(T + 70, handoff=False)
    assert t.faults[0].handoff_at is None
    assert t.pop_ready(T + 899) == []
    assert [r for _, r in t.pop_ready(T + 900)] == ["timed_out"]


def test_a_busy_resolved_fault_is_still_reported_after_max_open():
    t = FaultTracker(Timing(max_open_s=60))
    t.observe_incident(opened("crash", T))
    t.observe_incident(resolved("crash", T, T + 5))
    for s in range(10, 60, 5):
        t.observe_activity(T + s)  # someone keeps calling tools
    assert [r for _, r in t.pop_ready(T + 60)] == ["resolved"]


def test_a_resolve_for_an_unknown_incident_is_ignored():
    t = FaultTracker()
    t.observe_incident(resolved("crash", T - 500, T))
    t.observe_activity(T)
    assert t.faults == []


def test_faults_survive_a_round_trip_through_json():
    t = FaultTracker()
    t.observe_incident(opened("crash", T), mcu_build="v1.1@32ACFDAF")
    t.observe_incident(opened("safe_mode", T + 3))
    t.observe_incident(resolved("crash", T, T + 9))
    t.observe_activity(T + 12, handoff=True)
    again = [Fault.from_dict(d) for d in t.to_list()]
    assert again == t.faults


def test_settle_must_cover_merge():
    with pytest.raises(ValueError):
        Timing(merge_s=20, settle_s=15)
