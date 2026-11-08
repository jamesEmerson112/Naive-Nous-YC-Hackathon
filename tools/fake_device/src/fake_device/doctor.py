"""Fleet Doctor, device side: when the healer asks for advice (L1), and how the device takes a
fix the cloud offers (L2). Fleet Doctor interfaces §10, architecture §3-§6.

- `TriggerHook` + `CircuitBreaker` (L1): detect T2, T4, T5, T6, T7 in the healer's loop,
  ask `POST /v1/devices/{id}/doctor/ask`, and turn the answer into a short text to inject.
- `ProposalApplier` + `MarkGoodTimer` (L2): when a check-in points at a proposal, pull it,
  check it, apply it as an overlay, watch health for 60 s, and report the result.

The Doctor never acts on the device. It only answers questions and offers patches; the
device decides, applies, and reverts (architecture §6 item 1).

How to adopt this on the Pi
===========================

Copy this file next to `client.py` and `outbox.py` and fix up the `fake_device.` imports.
Like them it needs only the standard library, httpx and romulus-contracts (Python 3.11+).
The two halves are independent.

**1. L1, in the healer's harness loop** (harness code, not a model tool)::

    hook = TriggerHook(client)                 # one per healer process
    hook.start_heal(incident_id)               # at the start of every heal (a UUID, or None)
    ...
    # after every healer step:
    text = hook.maybe_ask(steps, verify_failures=n, budget_used=used, tier=tier,
                          skill_matched=skill is not None,
                          context={"code_rev": rev, "log_tail": tail, "files": files})
    if text:
        messages.append({"role": "user", "content": text})   # inject it and carry on

- `steps` is the heal so far as `HealStep`s. Build them from mini-swe-agent's trajectory:
  the command, the files it read or changed, pass/fail when it ran a check, and whether a
  build or flash failed. `budget_used` is the larger of steps used / step limit and time
  used / time limit.
- `maybe_ask` returns None almost every time. Each trigger asks at most once per heal, an
  incident gets at most 6 asks, and after one failed ask (timeout, error, any non-200) the
  breaker skips every later ask in that heal. With dead Wi-Fi a heal loses one timeout
  (`budget_s`, 20 s), not six.
- The text is a numbered list of hypotheses, each with how to check it, under 300 tokens.
  It is advice, not orders: the healer stays in charge.

**2. L2, in the visor loop, after each check-in**::

    applier = ProposalApplier(client, host, outbox, state_path=STATE / "doctor.json",
                              safety_paths=K17_PATHS)
    resp = client.checkin(...)
    applier.tick(now)                          # the mark-good timer
    if resp.proposal is not None:
        applier.handle(resp.proposal, now, frozen=resp.frozen)
    outbox.drain(client, now)

`host` is the healer's overlay machinery (D5a) behind the small `ProposalHost` protocol
below; `sim.SimDevice` is the fake one. What each method means on the Pi:

- `parked_and_idle()`: the arm is parked and no task is running.
- `running()`: what's installed: firmware, harness, code_rev, mcu_build.
- `active_overlay_id()`: the overlay in force, or None.
- `snapshot()` / `restore(snap)`: the healer's snapshot before patching, and putting it back.
- `apply_overlay(overlay)`: `git apply` `overlay.code_patch` onto `device/` and restart the
  app. Keep `overlay.proposal_id` with the overlay: later incidents under it send it.
- `verify(proposal)`: run `proposal.repro_test` plus your verify scripts; `(ok, detail)`.
- `healthy()`: the tier 1-3 health checks; False if anything fails.
- `mark_good(overlay_id)`: persist "this overlay is good" and resume work (unpark).

Every result goes through the outbox (kind `proposal_result`), like overlay statuses, so a
cloud outage only delays it. An `applied` result is how the cloud learns about the overlay
at all (it creates the overlay row from it), so it must never be lost.

**What the sim skips and the Pi should do:**

- Call `applier.tick(now)` every second or two while `applier.timer` is set, not only once
  per check-in, so a health failure inside the 60 s is caught quickly.
- Persist the watch. If the app restarts before mark-good, revert the overlay at boot: an
  unconfirmed overlay never survives a restart (RAUC's mark-good rule).
- Keep the Doctor out of the visor's `Device` protocol and out of the safety layer. The
  cloud discards proposals that touch safety paths; `safety_paths` here is a second check.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from fake_device.client import DeviceClient, HashMismatch, InvalidPackage
from fake_device.outbox import Outbox, atomic_write
from romulus_contracts import (
    CONTRACT_VERSION,
    Overlay,
    OverlayStatus,
    Proposal,
    ProposalBase,
    ProposalPointer,
    ProposalResult,
    Running,
)
from romulus_contracts.codepatch import (
    MCU_ROOT,
    is_under,
    patch_paths,
    touches_safety,
    validate_code_patch,
)

log = logging.getLogger(__name__)

# --- tunables (architecture §3, §4, §6) --------------------------------------------------

VERIFY_FAILURES_K = 2
"""T2: ask once verify still fails after this many patch attempts."""
REPEAT_COMMAND = 3
"""T4: the same command this many times in a row is a loop."""
NO_PROGRESS_STEPS = 5
"""T4: this many steps in a row with no new file read and no file changed is a loop."""
BUDGET_SHARE = 0.7
"""T5: ask once this share of the step or time budget is used with no passing check."""
MAX_ASKS_PER_INCIDENT = 6
"""Cap on L1 asks per incident (SWE-Protégé's cap; research.md §4). The cloud caps too."""
ASK_BUDGET_S = 20.0
"""How long the healer waits for an answer (`DoctorAsk.budget_s`, also the HTTP timeout)."""
ADVICE_MAX_TOKENS = 300
"""The injected text stays under about this many tokens."""
CHARS_PER_TOKEN = 4
"""A rough estimate for English text; good enough for a cap."""
MARK_GOOD_S = 60.0
"""A proposal overlay must stay healthy this long, or it reverts (architecture §6 item 2)."""
RECENT_STEPS = 5
"""How many of the healer's last steps go into an ask (`DoctorContext.recent_steps`)."""
HANDLED_KEEP = 100
"""How many handled proposal ids the applier remembers on disk."""


# =========================================================================================
# L1: when to ask
# =========================================================================================


@dataclass(frozen=True)
class HealStep:
    """One step of the healer's loop, as the trigger hook needs it.

    - `command`: the shell command the healer ran (compared with whitespace collapsed).
    - `read` / `changed`: files the step read or changed (paths as the healer wrote them).
    - `check`: `"pass"` or `"fail"` when the step ran a verify or health check, else None.
    - `build_failed`: the step built or flashed something and that failed (T2).
    """

    command: str
    read: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    check: str | None = None
    build_failed: bool = False

    def summary(self) -> str:
        """One line for `DoctorContext.recent_steps`."""
        line = " ".join(self.command.split())
        if self.check:
            line += f": {self.check.upper()}"
        if self.build_failed:
            line += ": BUILD FAILED"
        return line


class CircuitBreaker:
    """Per heal: after the first failed or timed-out ask, every later ask is skipped.

    Without it, a heal on dead Wi-Fi would wait out a timeout at every trigger. Call
    `reset()` at the start of each heal (`TriggerHook.start_heal` does).
    """

    def __init__(self) -> None:
        self.tripped = False
        self.reason = ""

    def allow(self) -> bool:
        return not self.tripped

    def trip(self, reason: str) -> None:
        if not self.tripped:
            log.info("doctor: circuit breaker tripped (%s); no more asks this heal", reason)
        self.tripped = True
        self.reason = reason

    def reset(self) -> None:
        self.tripped = False
        self.reason = ""


def _looping(steps: Sequence[HealStep]) -> bool:
    """T4: the same command `REPEAT_COMMAND` times in a row, or `NO_PROGRESS_STEPS` steps in
    a row that read no file the heal hadn't seen yet and changed no file."""
    commands = [" ".join(s.command.split()) for s in steps]
    last = commands[-REPEAT_COMMAND:]
    if len(last) == REPEAT_COMMAND and last[0] and len(set(last)) == 1:
        return True
    if len(steps) < NO_PROGRESS_STEPS:
        return False
    seen: set[str] = set()
    progress: list[bool] = []
    for step in steps:
        progress.append(bool(set(step.read) - seen) or bool(step.changed))
        seen.update(step.read, step.changed)
    return not any(progress[-NO_PROGRESS_STEPS:])


def format_advice(
    advice: Mapping[str, Any], trigger: str, *, max_tokens: int = ADVICE_MAX_TOKENS
) -> str | None:
    """The text to inject into the healer's conversation, or None if there are no hypotheses.

    A header, then one numbered item per hypothesis: the claim, then how to check it.
    Hypotheses that don't fit in `max_tokens` (estimated) are dropped; the first one is
    cut short rather than dropped.
    """
    hypotheses = [h for h in advice.get("hypotheses") or [] if (h.get("claim") or "").strip()]
    if not hypotheses:
        return None
    max_chars = max_tokens * CHARS_PER_TOKEN
    lines = [f"Fleet Doctor ({trigger}): hypotheses to check, not instructions."]
    for i, h in enumerate(hypotheses, 1):
        item = f"{i}. {h['claim'].strip()}"
        if (h.get("how_to_check") or "").strip():
            item += f"\n   Check: {h['how_to_check'].strip()}"
        if len("\n".join([*lines, item])) > max_chars:
            if i == 1:
                room = max_chars - len(lines[0]) - 2
                lines.append(item[:room].rstrip() + "…")
            break
        lines.append(item)
    return "\n".join(lines)


_QUESTIONS = {
    "T2": "Verify still fails after {verify_failures} patch attempts. What am I missing?",
    "T4": "I seem to be going in circles (repeating a command, or reading nothing new). "
    "What should I look at instead?",
    "T5": "Most of my budget is used and no check passes yet. What is the most likely cause?",
    "T6": "Tier 3: wrong behavior with no error. What should I check so I fix the cause "
    "instead of hiding the symptom?",
    "T7": "No skill matches this fault. Where should I start?",
}


class TriggerHook:
    """Decides when the healer asks the Fleet Doctor (L1), asks, and formats the answer.

    One per healer process. `start_heal()` at the start of each heal; `maybe_ask()` after
    each step. Tunables are the module constants; pass others for tests or tuning.
    """

    def __init__(
        self,
        client: DeviceClient,
        *,
        k: int = VERIFY_FAILURES_K,
        budget_share: float = BUDGET_SHARE,
        budget_s: float = ASK_BUDGET_S,
        max_asks_per_incident: int = MAX_ASKS_PER_INCIDENT,
    ) -> None:
        self.client = client
        self.k = k
        self.budget_share = budget_share
        self.budget_s = budget_s
        self.max_asks_per_incident = max_asks_per_incident
        self.breaker = CircuitBreaker()
        self.incident_id: str | None = None
        self.asked_this_heal: set[str] = set()
        self.asks_per_incident: Counter[str] = Counter()
        self.history: list[dict[str, Any]] = []
        """Every ask made: `{"trigger", "ask", "advice"}` (advice None if it failed). Put
        it in the agent trace so the cloud sees what the Doctor said."""

    def start_heal(self, incident_id: uuid.UUID | str | None = None) -> None:
        """A new heal: reset the breaker and the once-per-heal memory."""
        self.incident_id = str(incident_id) if incident_id is not None else None
        self.breaker.reset()
        self.asked_this_heal.clear()

    def detect(
        self,
        steps: Sequence[HealStep],
        *,
        verify_failures: int = 0,
        budget_used: float = 0.0,
        tier: int | str | None = None,
        skill_matched: bool = True,
    ) -> str | None:
        """The trigger that holds right now, or None. Pure: no asking, no memory.

        Checked in this order, first match wins:

        - T2: `verify_failures >= k` (2), or a step whose build or flash failed.
        - T4: looping (see `_looping`).
        - T5: `budget_used >= 0.7` and no step's check has passed yet.
        - T6: tier 3, at the start of the heal (no steps yet).
        - T7: no skill matches, at the start of the heal (no steps yet).
        """
        if verify_failures >= self.k or any(s.build_failed for s in steps):
            return "T2"
        if _looping(steps):
            return "T4"
        if budget_used >= self.budget_share and not any(s.check == "pass" for s in steps):
            return "T5"
        if not steps:
            if str(tier) == "3":
                return "T6"
            if not skill_matched:
                return "T7"
        return None

    def maybe_ask(
        self,
        steps: Sequence[HealStep],
        *,
        verify_failures: int = 0,
        budget_used: float = 0.0,
        tier: int | str | None = None,
        skill_matched: bool = True,
        question: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> str | None:
        """Ask if a trigger holds and the caps allow it; return the text to inject, or None.

        `context` fills `DoctorContext` (`code_rev`, `signals`, `log_tail`, `files`);
        `tier` and the last 5 steps are added for you. `question` overrides the default
        question for the trigger.
        """
        trigger = self.detect(
            steps,
            verify_failures=verify_failures,
            budget_used=budget_used,
            tier=tier,
            skill_matched=skill_matched,
        )
        if trigger is None or trigger in self.asked_this_heal:
            return None
        if not self.breaker.allow():
            return None
        incident_key = self.incident_id or "-"
        if self.asks_per_incident[incident_key] >= self.max_asks_per_incident:
            return None

        ask = self.build_ask(
            trigger, steps, verify_failures, tier, skill_matched, question, context
        )
        self.asked_this_heal.add(trigger)
        self.asks_per_incident[incident_key] += 1
        advice = self.client.ask_doctor(ask, timeout_s=self.budget_s)
        self.history.append({"trigger": trigger, "ask": ask, "advice": advice})
        if advice is None:
            self.breaker.trip(f"{trigger} ask got no answer")
            return None
        return format_advice(advice, trigger)

    def build_ask(
        self,
        trigger: str,
        steps: Sequence[HealStep],
        verify_failures: int,
        tier: int | str | None,
        skill_matched: bool,
        question: str | None,
        context: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """The `DoctorAsk` body as a dict (validated by `DeviceClient.ask_doctor`)."""
        if question is None:
            question = _QUESTIONS[trigger].format(verify_failures=verify_failures)
            if trigger == "T6" and not skill_matched:
                question += " No skill matches it either."
        ctx: dict[str, Any] = {
            "tier": tier,
            "recent_steps": [s.summary() for s in steps[-RECENT_STEPS:]],
            **(context or {}),
        }
        return {
            "incident_id": self.incident_id,
            "trigger": trigger,
            "question": question,
            "context": ctx,
            "budget_s": self.budget_s,
            "contract_version": CONTRACT_VERSION,
        }


# =========================================================================================
# L2: taking a proposal
# =========================================================================================


class ProposalHost(Protocol):
    """What `ProposalApplier` needs from the device: the healer's overlay machinery (D5a).

    Deliberately separate from the visor's `Device` protocol. `sim.SimDevice` fakes it;
    see the module docstring for what each method means on the Pi.
    """

    def parked_and_idle(self) -> bool:
        """The arm is parked and no task is running."""

    def running(self) -> Running | None:
        """What's installed: firmware, harness, code_rev, mcu_build."""

    def active_overlay_id(self) -> str | None:
        """The overlay in force, or None."""

    def snapshot(self) -> Any:
        """Whatever `restore` needs to undo the next `apply_overlay`."""

    def restore(self, snapshot: Any) -> None:
        """Put back what `snapshot` captured (the previous overlay, if any)."""

    def apply_overlay(self, overlay: Overlay) -> None:
        """Apply `overlay.code_patch` and restart the app with it."""

    def verify(self, proposal: Proposal) -> tuple[bool, str]:
        """Run the proposal's repro test and the verify scripts: `(passed, detail)`."""

    def healthy(self) -> bool:
        """The health checks pass right now."""

    def mark_good(self, overlay_id: str) -> None:
        """The overlay passed its watch: keep it for good and resume work."""


def is_mcu_patch(code_patch: str) -> bool:
    """True if the patch touches the STM32 code (`firmware/`), so `mcu_build` must match."""
    return any(is_under(path, MCU_ROOT) for path in patch_paths(code_patch))


def base_mismatch(
    base: ProposalBase, running: Running | None, active_overlay_id: str | None, code_patch: str
) -> str:
    """Why the proposal's base isn't what's running, or "" if it matches.

    - `firmware` and `code_rev` always have to match.
    - `harness` only when the proposal names one.
    - `mcu_build` only for a patch under `firmware/` (STM32 code).
    - The active overlay has to be the one the proposal replaces (both None is a match):
      a proposal drafted without the current overlay would silently drop it.
    """
    r = running or Running()
    problems = []
    if base.firmware != r.firmware:
        problems.append(f"firmware {r.firmware} (proposal is for {base.firmware})")
    if base.harness is not None and base.harness != r.harness:
        problems.append(f"harness {r.harness} (proposal is for {base.harness})")
    if base.code_rev != r.code_rev:
        problems.append(f"code_rev {r.code_rev} (proposal is for {base.code_rev})")
    if is_mcu_patch(code_patch) and base.mcu_build != r.mcu_build:
        problems.append(f"mcu_build {r.mcu_build} (proposal is for {base.mcu_build})")
    if base.overlay_id != active_overlay_id:
        problems.append(f"overlay {active_overlay_id} (proposal replaces {base.overlay_id})")
    return "running " + ", ".join(problems) if problems else ""


@dataclass
class MarkGoodTimer:
    """RAUC's mark-good for one proposal overlay (architecture §6 item 2).

    Health must hold for `hold_s` seconds after the overlay is applied; then the overlay is
    good. One failed health check before that and it reverts. This is not the "held"
    report, which comes 20 minutes after with no recurrence and starts lock-in.
    """

    overlay_id: str
    proposal_id: str
    started_at: datetime
    hold_s: float = MARK_GOOD_S
    replaced_overlay_id: str | None = None
    """The overlay this proposal replaced, reported `superseded` once this one is good."""

    def check(self, now: datetime, healthy: bool) -> str:
        """`"failed"` if unhealthy, `"good"` once `hold_s` has passed, else `"watching"`."""
        if not healthy:
            return "failed"
        if (now - self.started_at).total_seconds() >= self.hold_s:
            return "good"
        return "watching"


class ProposalApplier:
    """Takes a proposal the check-in points at: check, apply, watch, report (architecture §5.3).

    `handle(pointer, now)` runs each check in turn. The first one that fails decides the
    result; nothing is applied unless every check passes:

    1. Already handled (or under watch)? Do nothing: the check-in keeps pointing at a
       proposal until the cloud has the result, so seeing it again is normal.
    2. Pull it; the sha256 must match `X-Sha256` and the pointer's `sha256`. Else `rejected`.
    3. The arm is parked and no task is running. Else `rejected`.
    4. The base matches what's running (`base_mismatch`). Else `stale`.
    5. It hasn't expired. Else `rejected`.
    6. The patch is well-formed and touches no safety path. Else `rejected`.

    Then: snapshot, apply as a new overlay carrying `proposal_id`, run verify and health
    (a failure restores the snapshot and reports `failed_verify`), start a `MarkGoodTimer`,
    and queue `applied` with the overlay id. `tick(now)` then runs the timer: health held
    for 60 s marks the overlay good; a failure reverts it and reports overlay status
    `reverted`. Every result goes through the outbox.
    """

    def __init__(
        self,
        client: DeviceClient,
        host: ProposalHost,
        outbox: Outbox,
        *,
        state_path: str | Path | None = None,
        mark_good_s: float = MARK_GOOD_S,
        safety_paths: Iterable[str] = (),
    ) -> None:
        self.client = client
        self.host = host
        self.outbox = outbox
        self.state_path = Path(state_path) if state_path is not None else None
        self.mark_good_s = mark_good_s
        self.safety_paths = tuple(safety_paths)
        self.timer: MarkGoodTimer | None = None
        self._snapshot: Any = None
        self.handled: list[str] = self._load()
        """Proposal ids with a result already queued (or gone from the cloud), oldest first.
        Kept on disk so a restart doesn't apply the same proposal twice."""

    # -- state file -------------------------------------------------------------------

    def _load(self) -> list[str]:
        if self.state_path is None:
            return []
        try:
            return list(json.loads(self.state_path.read_text())["handled"])
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            return []

    def _remember(self, proposal_id: str) -> None:
        self.handled = [*self.handled, proposal_id][-HANDLED_KEEP:]
        if self.state_path is not None:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self.state_path, json.dumps({"handled": self.handled}).encode())

    # -- a new pointer ----------------------------------------------------------------

    def handle(
        self, pointer: ProposalPointer, now: datetime, *, frozen: bool = False
    ) -> str | None:
        """Act on a check-in's proposal pointer. Returns the result queued, or None if
        nothing was decided (already handled, frozen, busy, or the pull failed for now)."""
        pid = pointer.proposal_id
        if pid in self.handled or (self.timer and self.timer.proposal_id == pid):
            return None  # 1. idempotent: the same proposal twice is a no-op
        if frozen:
            return None  # kill switch: take nothing (the cloud stops offering anyway)
        if self.timer is not None:
            return None  # one proposal at a time: wait until this watch ends

        # 2. pull and check the hash before trusting a byte of it
        try:
            data, _sha = self.client.get_proposal(pid, expected_sha256=pointer.sha256)
        except HashMismatch as exc:
            return self._report(pid, "rejected", str(exc), now)
        except InvalidPackage as exc:
            return self._report(pid, "rejected", f"invalid proposal: {exc}", now)
        except httpx.HTTPStatusError as exc:
            if 400 <= exc.response.status_code < 500 and exc.response.status_code != 429:
                log.info("doctor: proposal %s is gone (%s)", pid, exc.response.status_code)
                self._remember(pid)  # 404 / 410: nothing left to report on
            return None  # 5xx or 429: try again on the next check-in
        except httpx.TransportError as exc:
            log.info("doctor: pulling proposal %s failed (%s); will retry", pid, exc)
            return None
        proposal = Proposal.model_validate(data)

        # 3. only a parked, idle arm takes a patch
        if not self.host.parked_and_idle():
            return self._report(pid, "rejected", "not parked, or a task is running", now)
        # 4. the patch was made against what's running now
        active = self.host.active_overlay_id()
        mismatch = base_mismatch(proposal.base, self.host.running(), active, proposal.code_patch)
        if mismatch:
            return self._report(pid, "stale", mismatch, now)
        # 5. still in date
        if proposal.expires_at <= now:
            return self._report(pid, "rejected", f"expired at {proposal.expires_at}", now)
        # 6. well-formed, and nowhere near the safety layer
        problems = validate_code_patch(proposal.code_patch)
        if problems:
            return self._report(pid, "rejected", f"invalid code_patch: {problems[0]}", now)
        if touches_safety(proposal.code_patch, self.safety_paths):
            return self._report(pid, "rejected", "code_patch touches a safety path", now)

        return self._apply(proposal, active, now)

    def _apply(self, proposal: Proposal, replaced: str | None, now: datetime) -> str:
        pid = proposal.proposal_id
        overlay = Overlay(
            overlay_id=f"ov-{self.client.device_id}-{uuid.uuid4().hex[:8]}",
            diff={},
            code_patch=proposal.code_patch,
            proposal_id=pid,
        )
        snapshot = self.host.snapshot()
        try:
            self.host.apply_overlay(overlay)
            passed, detail = self.host.verify(proposal)
            if passed and not self.host.healthy():
                passed, detail = False, f"{detail}; health check failing after apply"
        except Exception as exc:  # a crash in apply or verify is a failed verify
            log.exception("doctor: applying proposal %s failed", pid)
            passed, detail = False, f"apply or verify raised {type(exc).__name__}: {exc}"
        if not passed:
            self.host.restore(snapshot)
            return self._report(pid, "failed_verify", f"{detail}; rolled back", now)

        self._snapshot = snapshot
        self.timer = MarkGoodTimer(overlay.overlay_id, pid, now, self.mark_good_s, replaced)
        reason = f"parked, base matched, sha256 ok; {detail}"
        return self._report(pid, "applied", reason, now, overlay_id=overlay.overlay_id)

    def _report(
        self,
        proposal_id: str,
        result: str,
        reason: str,
        now: datetime,
        overlay_id: str | None = None,
    ) -> str:
        log.info("doctor: proposal %s %s: %s", proposal_id, result, reason)
        self.outbox.add_proposal_result(
            proposal_id,
            ProposalResult(result=result, overlay_id=overlay_id, reason=reason, occurred_at=now),
        )
        self._remember(proposal_id)
        return result

    # -- the mark-good timer ----------------------------------------------------------

    def tick(self, now: datetime) -> tuple[str, str] | None:
        """Run the mark-good timer. Returns `(overlay_id, "good" | "reverted")` when the
        watch ends this call, else None."""
        timer = self.timer
        if timer is None:
            return None
        if self.host.active_overlay_id() != timer.overlay_id:
            # Something else replaced it (a new version, `drop_overlay`): nothing to watch,
            # just as the "held" step skips an overlay that's gone.
            self.timer, self._snapshot = None, None
            return None
        try:
            healthy = self.host.healthy()
        except Exception:
            log.exception("doctor: health check raised")
            healthy = False
        state = timer.check(now, healthy)
        if state == "watching":
            return None

        self.timer = None
        if state == "good":
            self.host.mark_good(timer.overlay_id)
            self._snapshot = None
            if timer.replaced_overlay_id is not None:
                reason = f"replaced by Fleet Doctor proposal {timer.proposal_id}"
                status = OverlayStatus(status="superseded", reason=reason, occurred_at=now)
                self.outbox.add_overlay_status(timer.replaced_overlay_id, status)
            log.info("doctor: overlay %s marked good", timer.overlay_id)
            return timer.overlay_id, "good"

        self.host.restore(self._snapshot)
        self._snapshot = None
        reason = (
            f"health failed within {timer.hold_s:.0f} s of applying Fleet Doctor proposal "
            f"{timer.proposal_id} (mark-good)"
        )
        self.outbox.add_overlay_status(
            timer.overlay_id, OverlayStatus(status="reverted", reason=reason, occurred_at=now)
        )
        log.info("doctor: overlay %s reverted: %s", timer.overlay_id, reason)
        return timer.overlay_id, "reverted"
