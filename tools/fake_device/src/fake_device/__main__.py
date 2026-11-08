"""Run a fake fleet against the cloud: `python -m fake_device --n 4 --url http://api:8000`.

Devices are `sim-1` … `sim-N` (A18); `--canary` (default `sim-2`, matching `make seed`)
marks the one that runs the bad-candidate scenario. Which device is the canary is the
cloud's decision; this flag only picks where `bad` runs. Every device checks in every
`--interval` seconds. This is the only place that sleeps or reads the clock.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from fake_device.arm import FakeArm
from fake_device.scenarios import HOLD_AFTER_S, SCENARIOS, Step

log = logging.getLogger("fake_device")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m fake_device", description=__doc__.split("\n")[0])
    p.add_argument("--n", type=int, default=4, help="number of fake arms (default 4)")
    p.add_argument("--url", default="http://localhost:8000", help="cloud base URL")
    p.add_argument("--interval", type=float, default=10.0, help="seconds between check-ins")
    p.add_argument(
        "--scenario",
        action="append",
        choices=[*SCENARIOS, "all"],
        default=[],
        help="scenario to run once (repeatable): rules, code, bad, park or all",
    )
    p.add_argument("--canary", default=None, help="device that runs `bad` (default sim-2)")
    p.add_argument("--hold-after", type=float, default=HOLD_AFTER_S, help="seconds until held")
    p.add_argument(
        "--outbox-dir",
        type=Path,
        default=Path("data/fake_device"),
        help="outbox root (git-ignored)",
    )
    p.add_argument("--seed", type=int, default=0, help="seed for metrics noise")
    p.add_argument("--ticks", type=int, default=None, help="stop after this many check-ins")
    return p.parse_args(argv)


def plan(arms: list[FakeArm], names: list[str], hold_after_s: float) -> list[tuple[str, Step]]:
    """Pick a device for each scenario: `bad` on the canary, the rest round-robin on others."""
    if "all" in names:
        names = list(SCENARIOS)
    canary = next((a for a in arms if a.device.canary), arms[0])
    others = [a for a in arms if a is not canary] or [canary]
    steps: list[tuple[str, Step]] = []
    for i, name in enumerate(names):
        arm = canary if name == "bad" else others[i % len(others)]
        if name == "park":
            new = SCENARIOS[name](arm)
        else:
            new = SCENARIOS[name](arm, hold_after_s)
        steps += [(arm.device_id, s) for s in new]
    return steps


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    canary_id = args.canary or ("sim-2" if args.n >= 2 else "sim-1")
    http = httpx.Client(base_url=args.url, timeout=5.0)
    arms = [
        FakeArm(
            http,
            f"sim-{i}",
            args.outbox_dir / f"sim-{i}",
            canary=f"sim-{i}" == canary_id,
            seed=args.seed,
        )
        for i in range(1, args.n + 1)
    ]
    pending = plan(arms, args.scenario, args.hold_after)
    started = datetime.now(UTC)
    log.info("%d fake arms against %s; canary %s", len(arms), args.url, canary_id)

    tick = 0
    while args.ticks is None or tick < args.ticks:
        now = datetime.now(UTC)
        for arm in arms:
            try:
                result = arm.tick(now)
            except Exception:  # keep the fleet running overnight whatever one arm does
                log.exception("%s: tick failed", arm.device_id)
                continue
            for r in result.installs:
                log.info("%s: %s %s %s", arm.device_id, r.type, r.version, r.result)
        elapsed = (now - started).total_seconds()
        for item in [p for p in pending if p[1].at_s <= elapsed]:
            pending.remove(item)
            device_id, step = item
            log.info("%s: scenario step %r", device_id, step.name)
            try:
                step.run(now)
            except Exception:
                log.exception("%s: scenario step %r failed", device_id, step.name)
        tick += 1
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
