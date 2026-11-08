"""F10 overnight unattended run, accelerated (flows doc F10, build plan M7).

Two hours of the seeded chaos schedule on an injected clock: three fake arms (unmodified
`fake_device`, through the `device_http` bridge, each on its own `Link` so it can go
offline), the worker's own `STEPS` run every simulated minute, and a small blob budget so
the disk budget has work to do. Then the digest, read from the event log like
`make digest`, must show the night ran itself: incidents in, agent and automation actions,
no human actions, nothing waiting for a person, evidence stored, no 5xx, every arm back.

The steps are whatever `worker.STEPS` holds, so batch/gate (M3b) and work orders (M5)
join in as they land; steps that take `settings` get the test's. Assertions on their event
kinds are conditional on those kinds appearing.
"""

import inspect
from collections import Counter
from datetime import timedelta
from functools import partial

from sqlalchemy import func, select

from fake_device.arm import FakeArm
from fake_device.chaos import DAY_S, FAULT_KINDS, Chaos, Link, profile
from romulus_cloud import worker
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.ids import utcnow
from romulus_cloud.models import Blob, Incident, Recording
from romulus_cloud.services import digest, telemetry
from romulus_cloud.settings import get_settings

DEVICES = ("sim-1", "sim-2", "sim-3")  # the seeded sims; sim-2 is the canary
HOURS = 2
TICK_S = 60
SEED = 7  # plans every kind of trouble within 2 h, both bad variants included
# The overnight rates, with hardware and outages often enough to show up within 2 h.
RATES = profile("overnight", hardware_every_s=2400, offline_every_s=1800)


def _bind(step: worker.Step, settings) -> worker.Step:
    if "settings" in inspect.signature(step.fn).parameters:
        return worker.Step(step.name, partial(step.fn, settings=settings), step.every_s)
    return step


def test_f10_overnight_run_accelerated(
    client, device_http, seeded, sessions, db_session, make_settings, tmp_path, monkeypatch
):
    # A step that falls back to get_settings() must read no .env and no shell variables.
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    monkeypatch.setattr(worker, "STEPS", list(worker.STEPS))

    seed_bytes = int(db_session.scalar(select(func.sum(Blob.size_bytes))))
    settings = make_settings(blob_budget_gb=(seed_bytes + 150_000) / telemetry.GB)
    steps = [_bind(s, settings) for s in worker.STEPS]

    links = {d: Link(device_http) for d in DEVICES}
    arms = [FakeArm(links[d].client(), d, tmp_path / d, canary=d == "sim-2") for d in DEVICES]
    chaos = Chaos(arms, RATES, seed=SEED, links=links)
    planned = Counter(e.kind for e in chaos.schedule(0, DAY_S) if e.at_s <= HOURS * 3600)
    for kind in ("rules", "code", "park", "offline", "episode"):
        assert planned[kind] > 0, f"seed {SEED} plans no {kind} in {HOURS} h"
    assert planned["bad"] + planned["bad-late"] > 0

    t0 = utcnow()  # after the seed: the digest window holds only the night
    last_run: dict = {}
    failed: list = []
    offline_seen = False
    end = t0
    for i in range(HOURS * 3600 // TICK_S + 1):
        end = t0 + timedelta(seconds=i * TICK_S)
        offline_seen |= not all([a.tick(end).online for a in arms])
        chaos.run_due(i * TICK_S, end)
        results = worker.run_tick(steps, sessions, end, last_run)
        failed += [(i, name) for name, r in results.items() if r != "ok"]

    # Morning: an outage still running is over; two more rounds drain every outbox (F9).
    for link in links.values():
        link.up = True
    for k in (1, 2):
        end += timedelta(seconds=TICK_S)
        assert all(a.tick(end).online for a in arms), "every arm checks in at the end"
        failed += [
            (k, n) for n, r in worker.run_tick(steps, sessions, end, {}).items() if r != "ok"
        ]

    # The night itself went cleanly.
    assert offline_seen, "some arm was really offline at a check-in"
    assert failed == [], "worker steps never failed"
    assert chaos.failures == []
    assert sum(n for link in links.values() for s, n in link.statuses.items() if s >= 500) == 0
    assert all(len(a.outbox) == 0 and a.outbox.dead_letters() == [] for a in arms)

    # The digest, from the event log.
    report = digest.report(
        db_session,
        end,
        since=t0.isoformat(),
        budget_gb=settings.blob_budget_gb,
    )
    d = report.digest
    print(report.text("America/Los_Angeles"))

    faults = sum(planned[k] for k in FAULT_KINDS) + planned["park"]
    incidents_in_db = db_session.scalar(select(func.count()).select_from(Incident))
    assert d.incidents == incidents_in_db >= faults  # bad-late adds a repeat incident
    assert d.heals > 0, "overlays held after their watch"
    assert d.actions_by_class["agent"] > 0, "the on-device agent acted"
    assert d.actions_by_class["automation"] > 0, "the platform acted (at least the disk budget)"
    assert d.actions_by_class["human"] == 0 and d.human_actions == ()
    assert d.no_human_share == 1.0
    assert d.waiting_for_person == ()  # parks stay under the per-day and per-robot order caps

    # Evidence: every incident recording is stored; the budget pruned only episodes.
    store = build_blobstore(settings.blob_dir)
    evidence = list(db_session.scalars(select(Recording).where(Recording.incident_id.is_not(None))))
    assert evidence and all(
        db_session.get(Blob, r.sha256) is not None and store.exists(r.sha256) for r in evidence
    )
    assert d.by_kind.get(telemetry.PRUNED, 0) > 0
    assert report.blob_bytes <= report.budget_bytes, "the blob store stayed under budget"

    # When M3b/M5 have landed, their outcomes show up too (conditional on the event kinds).
    if d.candidates_created:
        assert (
            d.candidates_promoted
            + d.candidates_rolled_back
            + d.by_kind.get("candidate.awaiting_canary", 0)
            + d.by_kind.get("candidate.canary_started", 0)
            > 0
        )
    if d.work_orders_created:
        assert d.work_orders_created <= planned["park"]
        assert d.work_orders_created <= settings.orders_per_day_max
