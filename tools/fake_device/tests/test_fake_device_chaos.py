"""The chaos schedule (flow F10): deterministic, rates respected, park faults capped, and
the runner plays it against fake arms with offline periods that drain afterwards (F9)."""

from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx
import pytest

import fake_device.__main__ as cli
from fake_device.arm import FakeArm
from fake_device.chaos import DAY_S, PROFILES, Chaos, Link, build_schedule, profile
from fake_device.fake_cloud import FakeCloud

DEVICES = ["sim-1", "sim-2", "sim-3", "sim-4"]
T0 = datetime(2026, 11, 8, 0, 30, tzinfo=UTC)


def overnight(seed=7, rates=None, devices=DEVICES, end_s=8 * 3600):
    return build_schedule(devices, "sim-2", rates or PROFILES["overnight"], seed, 0, end_s)


def kinds(events):
    return Counter(e.kind for e in events)


def test_same_seed_same_schedule_and_a_new_seed_changes_it():
    assert overnight(seed=7) == overnight(seed=7)
    assert overnight(seed=7) != overnight(seed=8)
    events = overnight()
    assert [e.at_s for e in events] == sorted(e.at_s for e in events)
    assert all(0 <= e.at_s < 8 * 3600 for e in events)


def test_an_overnight_has_every_kind_of_trouble():
    k = kinds(overnight(seed=3, end_s=DAY_S))
    for kind in ("rules", "code", "park", "offline", "episode", "quiet"):
        assert k[kind] > 0, kind
    assert k["bad"] + k["bad-late"] > 0


def test_fault_rates_are_respected_on_average():
    """Over 20 seeded days: ~one fault per 10 min minus quiet stretches and busy arms,
    about 1 in 5 bad, about 3 in 10 code patches."""
    k = Counter()
    for seed in range(20):
        k += kinds(overnight(seed=seed, end_s=DAY_S))
    faults = k["rules"] + k["code"] + k["bad"] + k["bad-late"]
    per_day = faults / 20
    assert 70 < per_day < 144  # 144 = 24 h / 10 min, before quiet stretches and busy arms
    assert 0.15 < (k["bad"] + k["bad-late"]) / faults < 0.25
    assert 0.25 < k["code"] / faults < 0.4
    assert 0.3 < k["bad-late"] / (k["bad"] + k["bad-late"]) < 0.7


def test_bad_candidates_only_on_the_canary():
    events = overnight(end_s=DAY_S)
    assert {e.device_id for e in events if e.kind.startswith("bad")} == {"sim-2"}
    assert {e.device_id for e in events if e.kind in ("rules", "code")} == set(DEVICES) - {"sim-2"}


def test_park_faults_are_capped_per_day_on_distinct_non_canary_arms():
    rates = profile("overnight", hardware_every_s=60)  # wants hundreds of parks a day
    for seed in range(5):
        parks = [e for e in overnight(seed, rates, end_s=DAY_S) if e.kind == "park"]
        assert len(parks) == rates.hardware_max == 2
        devices = [e.device_id for e in parks]
        assert len(set(devices)) == 2 and "sim-2" not in devices
    # the next day may park again (ORDERS_PER_DAY_MAX is per day)
    day2 = build_schedule(DEVICES, "sim-2", rates, 0, DAY_S, 2 * DAY_S)
    assert kinds(day2)["park"] == 2


def test_nothing_faults_in_a_quiet_stretch_or_on_a_parked_or_busy_arm():
    rates = PROFILES["overnight"]
    for seed in range(10):
        events = overnight(seed, end_s=DAY_S)
        quiet = [(e.at_s, e.at_s + e.duration_s) for e in events if e.kind == "quiet"]
        faults = [e for e in events if e.kind in ("rules", "code", "bad", "bad-late", "park")]
        assert not [f for f in faults if any(a <= f.at_s < b for a, b in quiet)]
        for park in (e for e in events if e.kind == "park"):
            until = park.at_s + rates.repair_after_s
            during = [
                e
                for e in events
                if e.device_id == park.device_id
                and park.at_s < e.at_s < until
                and e.kind in ("rules", "code", "bad", "bad-late", "episode")
            ]
            assert during == []
        last: dict[str, float] = {}
        for f in (f for f in faults if f.kind != "park"):
            assert f.at_s >= last.get(f.device_id, -1e9) + rates.hold_after_s
            last[f.device_id] = f.at_s


def test_a_rate_of_zero_turns_that_trouble_off():
    rates = profile("overnight", hardware_every_s=0, offline_every_s=0, quiet_every_s=0)
    k = kinds(overnight(rates=rates, end_s=DAY_S))
    assert k["park"] == k["offline"] == k["quiet"] == 0 and k["rules"] > 0


@pytest.fixture
def fleet(tmp_path):
    cloud = FakeCloud.seeded()
    upstream = cloud.http()
    links = {d: Link(upstream) for d in ("sim-1", "sim-2", "sim-3")}
    arms = [
        FakeArm(links[d].client(), d, tmp_path / d, canary=d == "sim-2")
        for d in ("sim-1", "sim-2", "sim-3")
    ]
    return cloud, links, arms


def play(chaos, arms, hours, tick_s=60):
    """Tick every arm and the chaos schedule on an injected clock; return per-tick online flags."""
    online = []
    for i in range(int(hours * 3600 / tick_s) + 1):
        now = T0 + timedelta(seconds=i * tick_s)
        online.append({a.device_id: a.tick(now).online for a in arms})
        chaos.run_due(i * tick_s, now)
    return online


def test_chaos_plays_against_arms_and_offline_reports_drain_afterwards(fleet):
    cloud, links, arms = fleet
    rates = profile("overnight", hardware_every_s=1800, offline_every_s=1200)
    chaos = Chaos(arms, rates, seed=11, links=links)
    planned = kinds(e for e in chaos.schedule(0, DAY_S) if e.at_s <= 3 * 3600)
    assert planned["offline"] > 0 and planned["park"] > 0

    online = play(chaos, arms, hours=3)
    assert any(not ok for tick in online for ok in tick.values())  # really offline at times
    # Morning: an outage still running at the end is over; two more check-ins drain it.
    for link in links.values():
        link.up = True
    end = T0 + timedelta(hours=3)
    for i in (1, 2):
        assert all(a.tick(end + timedelta(minutes=i)).online for a in arms)
    assert chaos.failures == []
    assert chaos.ran["offline"] == planned["offline"]
    # every report made it: incidents and their recordings arrived, nothing left queued
    assert all(len(a.outbox) == 0 for a in arms)
    assert all(a.outbox.dead_letters() == [] for a in arms)
    incident_kinds = Counter(i["kind"] for i in cloud.incidents.values())
    assert incident_kinds["servo_absent"] == planned["park"]
    assert incident_kinds["camera_occluded"] >= planned["rules"]
    assert incident_kinds["unexpected_release"] == planned["code"]
    episodes = [r for r in cloud.recordings.values() if r["kind"] == "episode"]
    assert len(episodes) > planned["episode"]  # plus the ones queued while offline
    assert all(r["incident_id"] is None and r["labels"] == [] for r in episodes)
    assert set(cloud.blobs) >= {r["sha256"] for r in cloud.recordings.values()}
    held = [b for _, _, b in cloud.overlays if b["status"] == "held"]
    assert held  # overlays held 20 min after their incidents
    assert sum(link.statuses[s] for link in links.values() for s in link.statuses if s >= 500) == 0


def test_chaos_extends_past_the_first_day(fleet):
    _, links, arms = fleet
    chaos = Chaos(arms, PROFILES["demo"], seed=1, links=links)
    chaos.run_due(0, T0)
    assert chaos.horizon_s == DAY_S
    chaos.run_due(DAY_S + 1, T0)
    assert chaos.horizon_s == 2 * DAY_S


def test_a_step_that_raises_is_recorded_and_the_rest_still_run(fleet, monkeypatch):
    _, links, arms = fleet
    chaos = Chaos(arms, PROFILES["demo"], seed=2, links=links)
    monkeypatch.setattr(arms[0].outbox, "add_recording", _boom)
    chaos.run_due(4 * 3600, T0)
    assert chaos.failures and all(device == "sim-1" for device, _, _ in chaos.failures)
    assert sum(chaos.ran.values()) > len(chaos.failures)


def _boom(*args, **kwargs):
    raise RuntimeError("disk full")


def test_link_offline_raises_a_connect_error(fleet):
    _, links, arms = fleet
    links["sim-1"].up = False
    assert arms[0].tick(T0).online is False
    with pytest.raises(httpx.ConnectError):
        links["sim-1"].client().get("/v1/firmware/1")
    links["sim-1"].up = True
    assert arms[0].tick(T0).online is True


def test_cli_flags():
    args = cli.parse_args(["--chaos", "demo", "--speed", "60", "--seed", "3"])
    assert (args.chaos, args.speed, args.seed) == ("demo", 60.0, 3)
    assert cli.parse_args([]).chaos == "off"


def test_cli_runs_chaos_at_speed(tmp_path, monkeypatch):
    cloud = FakeCloud.seeded()
    real_client = httpx.Client
    monkeypatch.setattr(
        cli.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(cloud.handle), base_url="http://x"),
    )
    clock = iter(T0 + timedelta(seconds=10 * i) for i in range(1000))

    class FakeDatetime:
        @staticmethod
        def now(tz=None):
            return next(clock)

    monkeypatch.setattr(cli, "datetime", FakeDatetime)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    # 30 ticks of 10 s at x60 = 5 h of demo schedule
    args = "--n 3 --chaos demo --speed 60 --seed 4 --ticks 30"
    cli.main([*args.split(), "--state-dir", str(tmp_path)])
    assert len(cloud.incidents) >= 5
    assert any(r["kind"] == "episode" for r in cloud.recordings.values())
