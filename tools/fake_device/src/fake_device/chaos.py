"""Chaos schedule for the overnight run (flows F10 and F9, build plan M7).

`build_schedule` turns rates and a seed into a list of `ChaosEvent`s over a day of
schedule time: rules-patch and D1 code-patch incidents spread over the devices, the
occasional bad candidate armed on the canary, rare park/hardware faults, normal episodes,
short offline periods and quiet stretches. Same seed, same schedule.

`Chaos` plays a schedule against `FakeArm`s: each event becomes the scenario steps from
`scenarios.py` (incidents come with their recordings), and `run_due(elapsed_s, now)` runs
the steps whose time has come. It extends itself a day at a time, so it runs as long as
the process does. Nothing here sleeps or reads the clock: `__main__` passes real time
(scaled by `--speed`), tests pass any clock.

Offline periods need each arm on its own `Link` (an httpx transport that can be
unplugged): while it's down, check-ins fail and reports queue in the arm's outbox; once
it's back the outbox drains (F9). An episode is queued mid-outage so there is always
something to drain.

Rules and code faults land on the non-canary arms; the canary gets the bad-candidate
faults (each of which includes a rules patch), so its window stays free for them.

A restart resumes rather than replays: `__main__` keeps the run's start time in
`<state-dir>/chaos.json` (`started_at`), and `Chaos.skip_until` drops the steps whose time
passed while the process was down, so no arm is parked twice for one scheduled fault.

Park faults are capped per day (`hardware_max`, default 2) and land on distinct non-canary
arms, so the work orders they cause stay under the cloud's `ORDERS_PER_DAY_MAX` (3) and
`ORDERS_PER_ROBOT_DAY_MAX` (1) caps.
"""

from __future__ import annotations

import json
import logging
import random
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from fake_device import recordings
from fake_device.arm import FakeArm
from fake_device.outbox import atomic_write
from fake_device.scenarios import SCENARIOS, Step, park_hardware

log = logging.getLogger(__name__)

DAY_S = 86_400.0
FAULT_KINDS = ("rules", "code", "bad", "bad-late")


@dataclass(frozen=True)
class Rates:
    """How often things happen, in schedule seconds. A rate of 0 turns that thing off."""

    fault_every_s: float = 600
    """Mean gap between software faults across the fleet (F10: about every 10 minutes)."""
    code_share: float = 0.3
    """Share of faults that are D1 code patches; the rest are rules patches..."""
    bad_share: float = 0.2
    """...except this share, a bad candidate armed on the canary (F10: about 1 in 5)."""
    bad_late_share: float = 0.5
    """Of the bad ones, the share that fail late (after the install check) instead of at once."""
    hardware_every_s: float = 7200
    """Mean gap between park/hardware faults (F10: about every 2 hours)."""
    hardware_max: int = 2
    """At most this many park faults per day of schedule (under `ORDERS_PER_DAY_MAX`)."""
    repair_after_s: float = 3600
    """A parked arm gets its servo replaced this long after the fault."""
    offline_every_s: float = 5400
    """Mean gap between offline periods (one device each)."""
    offline_for_s: tuple[float, float] = (120, 600)
    """How long a device stays offline: uniform between these."""
    episode_every_s: float = 900
    """Mean gap between normal episodes, per device (unlabeled; the disk budget may drop them)."""
    episode_success: float = 0.9
    episode_pad_bytes: int = 4096
    quiet_every_s: float = 10_800
    """Mean gap between quiet stretches, when no fault starts."""
    quiet_for_s: float = 1800
    hold_after_s: float = 1200
    """An overlay is reported `held` this long after its incident (no recurrence)."""


PROFILES: dict[str, Rates] = {
    # F10 as written: a fault every ~10 min, ~1 in 5 bad, hardware every ~2 h, for >= 6 h.
    "overnight": Rates(),
    # Dense, for a few minutes of watching (or `--speed 60` smoke tests): a fault every
    # ~5 min of schedule time, quicker holds and repairs, at most one park a day.
    "demo": Rates(
        fault_every_s=300,
        hardware_every_s=3600,
        hardware_max=1,
        repair_after_s=1800,
        offline_every_s=2700,
        offline_for_s=(600, 1200),
        episode_every_s=600,
        quiet_every_s=7200,
        quiet_for_s=900,
        hold_after_s=600,
    ),
}


@dataclass(frozen=True)
class ChaosEvent:
    at_s: float
    kind: str  # rules | code | bad | bad-late | park | offline | episode | quiet
    device_id: str | None = None
    duration_s: float = 0.0
    """For offline and quiet: how long it lasts."""
    success: bool | None = None
    """For episode: whether the task attempt succeeded."""


def _arrivals(rng: random.Random, every_s: float, start_s: float, end_s: float) -> list[float]:
    """Times of a Poisson process with mean gap `every_s` in [start_s, end_s)."""
    if every_s <= 0:
        return []
    times, t = [], start_s
    while True:
        t += rng.expovariate(1 / every_s)
        if t >= end_s:
            return times
        times.append(t)


def build_schedule(
    device_ids: list[str],
    canary_id: str,
    rates: Rates,
    seed: int,
    start_s: float = 0.0,
    end_s: float = DAY_S,
) -> list[ChaosEvent]:
    """Everything that happens in [start_s, end_s) of schedule time, sorted by `at_s`.

    Deterministic for (device_ids, canary_id, rates, seed, start_s, end_s). Faults skip
    quiet stretches, parked arms, and arms still waiting for their last overlay to hold.
    """
    rng = random.Random(f"chaos:{seed}:{float(start_s)}")
    events: list[ChaosEvent] = []

    quiet: list[tuple[float, float]] = []
    for t in _arrivals(rng, rates.quiet_every_s, start_s, end_s):
        if not quiet or t >= quiet[-1][1]:
            quiet.append((t, t + rates.quiet_for_s))
            events.append(ChaosEvent(t, "quiet", None, rates.quiet_for_s))

    def is_quiet(t: float) -> bool:
        return any(a <= t < b for a, b in quiet)

    others = [d for d in device_ids if d != canary_id] or list(device_ids)
    parked: dict[str, tuple[float, float]] = {}  # device -> (from, until): one park a day
    busy_until: dict[str, float] = {}

    def is_parked(device_id: str, t: float) -> bool:
        start, until = parked.get(device_id, (0.0, -1.0))
        return start <= t < until

    def free(device_id: str, t: float) -> bool:
        return not is_parked(device_id, t) and busy_until.get(device_id, -1.0) <= t

    hardware = _arrivals(rng, rates.hardware_every_s, start_s, end_s)
    faults = _arrivals(rng, rates.fault_every_s, start_s, end_s)
    timeline = sorted([(t, "park") for t in hardware] + [(t, "fault") for t in faults])
    parks = 0
    park_pool = list(others)
    rng.shuffle(park_pool)
    for t, what in timeline:
        if is_quiet(t):
            continue
        if what == "park":
            candidates = [d for d in park_pool if free(d, t)]
            if parks >= rates.hardware_max or not candidates:
                continue
            device = candidates[0]
            park_pool.remove(device)  # one park per arm per day (ORDERS_PER_ROBOT_DAY_MAX)
            parks += 1
            parked[device] = (t, t + rates.repair_after_s)
            events.append(ChaosEvent(t, "park", device))
            continue
        r = rng.random()
        if r < rates.bad_share:
            kind = "bad-late" if rng.random() < rates.bad_late_share else "bad"
            candidates = [canary_id] if free(canary_id, t) else []
        else:
            kind = "code" if r < rates.bad_share + rates.code_share else "rules"
            candidates = [d for d in others if free(d, t)]
        if not candidates:
            continue
        device = rng.choice(candidates)
        busy_until[device] = t + rates.hold_after_s
        events.append(ChaosEvent(t, kind, device))

    for t in _arrivals(rng, rates.offline_every_s, start_s, end_s):
        low, high = rates.offline_for_s
        events.append(ChaosEvent(t, "offline", rng.choice(device_ids), rng.uniform(low, high)))

    for device in device_ids:
        for t in _arrivals(rng, rates.episode_every_s, start_s, end_s):
            if is_parked(device, t):
                continue  # a parked arm runs no tasks
            ok = rng.random() < rates.episode_success
            events.append(ChaosEvent(t, "episode", device, success=ok))

    return sorted(events, key=lambda e: (e.at_s, e.kind, e.device_id or ""))


class Link(httpx.BaseTransport):
    """One device's network link to the cloud. Set `up = False` to take it offline.

    Forwards each request through `upstream` (any `httpx.Client` whose base URL is the
    cloud, including a test bridge) and counts the response statuses in `statuses`.
    """

    _SKIP = {"content-encoding", "content-length", "transfer-encoding"}

    def __init__(self, upstream: httpx.Client) -> None:
        self.upstream = upstream
        self.up = True
        self.statuses: Counter[int] = Counter()

    def client(self) -> httpx.Client:
        """An `httpx.Client` for one arm that goes through this link."""
        return httpx.Client(transport=self, base_url=self.upstream.base_url)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("chaos: device offline", request=request)
        resp = self.upstream.send(request)
        self.statuses[resp.status_code] += 1
        headers = [(k, v) for k, v in resp.headers.multi_items() if k.lower() not in self._SKIP]
        return httpx.Response(
            resp.status_code, headers=headers, content=resp.content, request=request
        )


class Chaos:
    """Plays the schedule against the arms; call `run_due(elapsed_s, now)` every tick."""

    def __init__(
        self,
        arms: list[FakeArm],
        rates: Rates,
        seed: int = 0,
        links: dict[str, Link] | None = None,
        canary_id: str | None = None,
    ) -> None:
        self.arms = {a.device_id: a for a in arms}
        self.rates = rates
        self.seed = seed
        self.links = links or {}
        canary = next((a for a in arms if a.device.canary), arms[0])
        self.canary_id = canary_id or canary.device_id
        self.horizon_s = 0.0
        self.pending: list[tuple[float, str, Step]] = []
        self.ran: Counter[str] = Counter()
        """How many of each event kind have started (for logs and tests)."""
        self.failures: list[tuple[str, str, str]] = []
        """(device, step name, error) for steps that raised."""
        self._episodes: Counter[str] = Counter()

    def schedule(self, start_s: float, end_s: float) -> list[ChaosEvent]:
        return build_schedule(
            list(self.arms), self.canary_id, self.rates, self.seed, start_s, end_s
        )

    def _extend(self) -> None:
        start, end = self.horizon_s, self.horizon_s + DAY_S
        for event in self.schedule(start, end):
            for step in self._steps(event):
                self.pending.append((event.at_s + step.at_s, event.device_id or "-", step))
        self.pending.sort(key=lambda p: p[0])
        self.horizon_s = end

    def _counted(self, kind: str, step: Step) -> Step:
        def run(now: datetime) -> None:
            self.ran[kind] += 1
            step.run(now)

        return Step(step.at_s, step.name, run)

    def _steps(self, event: ChaosEvent) -> list[Step]:
        arm = self.arms.get(event.device_id or "")
        if event.kind == "quiet":
            return [self._counted("quiet", Step(0, f"quiet for {event.duration_s:.0f} s", _noop))]
        if arm is None:
            return []
        if event.kind in FAULT_KINDS:
            steps = SCENARIOS[event.kind](arm, self.rates.hold_after_s)
        elif event.kind == "park":
            steps = park_hardware(arm, self.rates.repair_after_s)
        elif event.kind == "episode":
            steps = [Step(0, "episode", self._episode(arm, bool(event.success)))]
        elif event.kind == "offline":
            link = self.links.get(arm.device_id)
            if link is None:
                return []  # no link to unplug: this arm can't go offline
            steps = [
                Step(0, f"offline for {event.duration_s:.0f} s", _set(link, False)),
                Step(event.duration_s / 2, "episode while offline", self._episode(arm, True)),
                Step(event.duration_s, "back online", _set(link, True)),
            ]
        else:
            raise ValueError(f"unknown chaos event kind {event.kind!r}")
        return [self._counted(event.kind, steps[0]), *steps[1:]]

    def _episode(self, arm: FakeArm, success: bool):
        def run(now: datetime) -> None:
            self._episodes[arm.device_id] += 1
            meta, data = recordings.episode(
                now,
                self._episodes[arm.device_id],
                success,
                arm.device.running(),
                pad_bytes=self.rates.episode_pad_bytes,
            )
            arm.outbox.add_recording(meta, data)
            arm.outbox.drain(arm.client, now)

        return run

    def skip_until(self, elapsed_s: float) -> int:
        """Drop every step due by `elapsed_s` without running it (after a restart, those
        happened before, or were lost with the process). Returns how many were dropped."""
        while elapsed_s >= self.horizon_s:
            self._extend()
        before = len(self.pending)
        self.pending = [p for p in self.pending if p[0] > elapsed_s]
        return before - len(self.pending)

    def run_due(self, elapsed_s: float, now: datetime) -> list[str]:
        """Run every step due by `elapsed_s` of schedule time, oldest first; return
        "device: step" for each. A step that raises is logged and recorded in `failures`."""
        while elapsed_s >= self.horizon_s:
            self._extend()
        done: list[str] = []
        while self.pending and self.pending[0][0] <= elapsed_s:
            _, device_id, step = self.pending.pop(0)
            try:
                step.run(now)
            except Exception as exc:  # keep the night going whatever one step does
                log.exception("chaos: %s: step %r failed", device_id, step.name)
                self.failures.append((device_id, step.name, repr(exc)))
                continue
            log.info("chaos: %s: %s", device_id, step.name)
            done.append(f"{device_id}: {step.name}")
        return done


def started_at(path: Path, key: dict[str, Any], now: datetime) -> datetime:
    """When this chaos run began. If `path` holds a start for the same `key` (profile,
    seed, speed, devices), resume from it; otherwise start now and write it down."""
    try:
        saved = json.loads(path.read_text())
        if saved.get("key") == key:
            return datetime.fromisoformat(saved["started_at"])
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps({"key": key, "started_at": now.isoformat()}).encode())
    return now


def _noop(now: datetime) -> None:
    return None


def _set(link: Link, up: bool):
    def run(now: datetime) -> None:
        link.up = up

    return run


def profile(name: str, **overrides) -> Rates:
    """A named profile, optionally with some rates changed."""
    return replace(PROFILES[name], **overrides)
