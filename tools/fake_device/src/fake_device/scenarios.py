"""Scripted scenarios for the fake fleet (BUILD §4 row 10, flows F2-F4, F6, F7).

Each scenario is a plain function that takes a `FakeArm` and returns its steps. A step is
`Step(at_s, name, run)`: call `step.run(now)` once `at_s` seconds have passed since the
scenario started. Nothing here sleeps or reads the clock, so tests call the steps one after
another with any `now`, and `__main__` runs them on real time.

- `rules_patch`: camera occluded → safe pose + an overlay raising the threshold (a `rules`
  diff) → incident with three recordings → `held` later. Feeds F2, F3, F7.
- `code_patch`: fault D1, drop-on-proximity, fixed with a `code_patch` (the contracts
  example), log lines and an agent trace → `held` later. Feeds F11.
- `bad_candidate`: arm the device so the next firmware it installs fails its health check
  (it rolls back and reports `rolled_back`), then run `rules_patch` so the batch job has a
  held overlay to build that candidate from. Run it on the canary. Feeds F4.
- `park_hardware`: fault D3-style, wrist servo silent → park with torque off, unresolved,
  `suspected_cause: hardware`, no overlay. Feeds F6.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from romulus_contracts import Agent, IncidentReport, Outcome, Overlay, OverlayStatus, Running

from fake_device import recordings
from fake_device.arm import FakeArm

HOLD_AFTER_S = 1200.0
"""When an overlay is reported `held`: 20 min without recurrence (contracts README, I0 item 2)."""

# Fault D1's fix, the same patch as packages/contracts/examples/incident_d1_code_patch.json
# (a test keeps them in sync). Paths are relative to device/ (APIs §2.10).
D1_CODE_PATCH = '''\
--- a/control/hold.py
+++ b/control/hold.py
@@ -18,6 +18,5 @@ def step(state, dist_mm, cmd):
     """One control step: distance in, gripper command out (see SPEC.md)."""
     if state == State.HOLDING:
-        if dist_mm is not None and dist_mm < RELEASE_THRESHOLD_MM:
-            log_state(State.HOLDING, State.RELEASE, reason="dist_lt_threshold", dist_mm=dist_mm)
-            return State.RELEASE, cmd.open_gripper()
+        # SPEC.md: while holding, never open the gripper because of the ultrasonic reading.
+        # The proximity release rule is removed (fault D1).
         return State.HOLDING, cmd.hold()
--- /dev/null
+++ b/tests/test_hold_proximity.py
@@ -0,0 +1,9 @@
+from control.hold import State, step
+from tests.fakes import FakeCommand
+
+
+def test_hand_near_while_holding_keeps_gripper_closed():
+    state = State.HOLDING
+    for _ in range(60):  # hand at 3 cm for 2 s at 30 Hz
+        state, command = step(state, 30, FakeCommand())
+        assert state == State.HOLDING and command.gripper == "closed"
'''


@dataclass(frozen=True)
class Step:
    at_s: float
    name: str
    run: Callable[[datetime], None]


def _incident_running(arm: FakeArm) -> Running | None:
    """`running` for an incident: versions plus `code_rev`, the base of any code patch."""
    r = arm.device.running()
    if r is None:
        return None
    return Running(firmware=r.firmware, harness=r.harness, code_rev=r.code_rev)


def _overlay_id(arm: FakeArm) -> str:
    # Device-made and stored as given (APIs §1), so unique across devices and restarts.
    return f"ov-{arm.device_id}-{uuid.uuid4().hex[:8]}"


def _hold(arm: FakeArm, overlay_id: str) -> Callable[[datetime], None]:
    def run(now: datetime) -> None:
        if arm.device.overlay is None or arm.device.overlay.overlay_id != overlay_id:
            return  # a newer version replaced it already (D7); nothing left to hold
        arm.report_overlay(
            now,
            overlay_id,
            OverlayStatus(status="held", reason="no recurrence for 20 min", occurred_at=now),
        )
        arm.device.overlay_held = True  # the overlay stays active until a version replaces it

    return run


def rules_patch(arm: FakeArm, hold_after_s: float = HOLD_AFTER_S) -> list[Step]:
    state: dict[str, str] = {}

    def fault(now: datetime) -> None:
        signals = {"brightness": [0.41, 0.18, 0.12, 0.33]}
        current = arm.device.rules().get("camera_occluded", {}).get("below", 0.2)
        new_below = round(min(current + 0.15, 0.6), 2)
        overlay = Overlay(
            overlay_id=_overlay_id(arm),
            diff={"rules": {"camera_occluded": {"below": new_below}}},
            code_patch=None,
        )
        reflexes = [f.action for f in arm.device.reflexes(signals)]
        incident_id = uuid.uuid4()
        running = _incident_running(arm)
        agent = Agent(
            reasoning="Frames dark for 3 s; occlusion, not hardware. Held safe pose, "
            "raised threshold.",
            actions=["safe_pose", f"patch rules.camera_occluded.below {current} -> {new_below}"],
        )
        labels = ["camera_occluded"]
        evidence = [
            recordings.incident_window(now, incident_id, running, labels, overlay.overlay_id),
            recordings.log_window(
                [
                    "T brightness=0.41 state=PICK",
                    "T brightness=0.18 state=PICK",
                    f"S PICK->SAFE_POSE reason=camera_occluded reflexes={reflexes}",
                ],
                now,
                incident_id,
                running,
                labels,
                overlay.overlay_id,
            ),
            recordings.agent_trace(
                agent.reasoning,
                agent.actions,
                now,
                incident_id,
                running,
                labels,
                overlay.overlay_id,
            ),
        ]
        incident = IncidentReport(
            incident_id=incident_id,
            occurred_at=now,
            kind="camera_occluded",
            summary="Camera brightness fell below threshold during pick",
            running=running,
            agent=agent,
            overlay=overlay,
            outcome=Outcome(
                resolved=True,
                resolving_action="safe_pose",
                suspected_cause="software",
                category="fix-config",
            ),
            signals=signals,
            recordings=[meta.recording_id for meta, _ in evidence],
        )
        arm.device.apply_overlay(overlay)
        arm.report_incident(now, incident, evidence)
        state["overlay_id"] = overlay.overlay_id

    def held(now: datetime) -> None:
        _hold(arm, state["overlay_id"])(now)

    return [Step(0, "camera_occluded", fault), Step(hold_after_s, "overlay held", held)]


def code_patch(arm: FakeArm, hold_after_s: float = HOLD_AFTER_S) -> list[Step]:
    state: dict[str, str] = {}

    def fault(now: datetime) -> None:
        log_tail = [
            "T t=81233 raw_us=180 dist_mm=31 valid=1 state=HOLDING goal=[0,12,40,88,0,35]",
            "S HOLDING->RELEASE reason=dist_lt_threshold dist_mm=31",
        ]
        running = _incident_running(arm) or Running()
        if running.code_rev is None:
            running.code_rev = "c0de001"  # the base the patch was made against
        overlay = Overlay(overlay_id=_overlay_id(arm), diff={}, code_patch=D1_CODE_PATCH)
        agent = Agent(
            reasoning="Tier 3: 'S HOLDING->RELEASE reason=dist_lt_threshold dist_mm=31' while "
            "holding. SPEC.md says never open the gripper because of the ultrasonic reading "
            "while holding. Removed the rule in control/hold.py, added a regression test, "
            "restarted the app; the hand-at-3-cm trace now keeps the gripper closed.",
            actions=[
                "snapshot",
                "park",
                "collect_evidence",
                "patch control/hold.py: remove proximity release",
                "add tests/test_hold_proximity.py",
                "restart robot-app",
                "verify d1_hand_trace",
            ],
        )
        incident_id = uuid.uuid4()
        labels = ["unexpected_release", "fix-code"]
        evidence = [
            recordings.incident_window(now, incident_id, running, labels, overlay.overlay_id),
            recordings.log_window(log_tail, now, incident_id, running, labels, overlay.overlay_id),
            recordings.agent_trace(
                agent.reasoning,
                agent.actions,
                now,
                incident_id,
                running,
                labels,
                overlay.overlay_id,
            ),
        ]
        incident = IncidentReport(
            incident_id=incident_id,
            occurred_at=now,
            kind="unexpected_release",
            summary="Gripper opened while holding when a hand came within 3 cm; the object dropped",
            running=running,
            agent=agent,
            overlay=overlay,
            outcome=Outcome(
                resolved=True,
                resolving_action="restart_app",
                suspected_cause="software",
                category="fix-code",
            ),
            signals={"dist_mm": [212, 140, 64, 31, 29, 30], "gripper_closed": [1, 1, 1, 0, 0, 0]},
            recordings=[meta.recording_id for meta, _ in evidence],
            tier=3,
            log_tail=log_tail,
        )
        arm.device.apply_overlay(overlay)
        arm.report_incident(now, incident, evidence)
        state["overlay_id"] = overlay.overlay_id

    def held(now: datetime) -> None:
        _hold(arm, state["overlay_id"])(now)

    return [Step(0, "unexpected_release (D1)", fault), Step(hold_after_s, "overlay held", held)]


def bad_candidate(arm: FakeArm, hold_after_s: float = HOLD_AFTER_S) -> list[Step]:
    def arm_bad_mode(now: datetime) -> None:
        arm.device.bad_next = True

    return [Step(0, "bad-candidate mode on", arm_bad_mode), *rules_patch(arm, hold_after_s)]


def park_hardware(arm: FakeArm, repair_after_s: float | None = None) -> list[Step]:
    def fault(now: datetime) -> None:
        running = _incident_running(arm)
        agent = Agent(
            reasoning="Every bus read fails for id 5 ('Missing motor IDs: [5]') after 3 "
            "retries and a bus reconnect; the other five servos answer. A servo that stays "
            "silent is hardware (cable or servo), so I parked with torque off and left the "
            "code alone.",
            actions=["snapshot", "retry_bus_read x3", "reconnect_bus", "park", "torque_off"],
        )
        incident_id = uuid.uuid4()
        labels = ["servo_absent"]
        evidence = [
            recordings.log_window(
                ["E code=SERVO_TIMEOUT id=5", "E Missing motor IDs: [5]", "S RUN->PARKED"],
                now,
                incident_id,
                running,
                labels,
            ),
            recordings.agent_trace(
                agent.reasoning, agent.actions, now, incident_id, running, labels
            ),
        ]
        incident = IncidentReport(
            incident_id=incident_id,
            occurred_at=now,
            kind="servo_absent",
            summary="Wrist servo id 5 stopped answering; arm parked with torque off",
            running=running,
            agent=agent,
            overlay=None,
            outcome=Outcome(
                resolved=False,
                resolving_action="park",
                suspected_cause="hardware",
                category="park",
            ),
            signals={"bus_read_errors": [0, 1, 2, 3, 3, 3]},
            recordings=[meta.recording_id for meta, _ in evidence],
            tier=1,
        )
        arm.device.parked = True
        arm.report_incident(now, incident, evidence)

    def repaired(now: datetime) -> None:
        arm.device.parked = False

    steps = [Step(0, "servo_absent (park)", fault)]
    if repair_after_s is not None:
        steps.append(Step(repair_after_s, "servo replaced", repaired))
    return steps


SCENARIOS: dict[str, Callable[..., list[Step]]] = {
    "rules": rules_patch,
    "code": code_patch,
    "bad": bad_candidate,
    "park": park_hardware,
}
