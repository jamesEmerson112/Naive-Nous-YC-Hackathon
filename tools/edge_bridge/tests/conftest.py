"""Fixtures: a fake healer var/ folder, a fake podd socket, the fake cloud, a bridge."""

from __future__ import annotations

import pytest

from edge_bridge.bridge import Bridge
from edge_bridge.fake_healer import FakeSocket, PoddVar
from edge_bridge.faults import DEFAULT_TIMING, Timing
from fake_device.client import DeviceClient
from fake_device.fake_cloud import FakeCloud
from fake_device.outbox import Outbox

# -- fixtures ------------------------------------------------------------------------------


@pytest.fixture
def pod(tmp_path) -> PoddVar:
    return PoddVar(tmp_path)


@pytest.fixture
def sock() -> FakeSocket:
    return FakeSocket()


@pytest.fixture
def cloud() -> FakeCloud:
    return FakeCloud()


@pytest.fixture
def make_bridge(pod, sock, cloud, tmp_path):
    """A bridge on `pod`'s var/, talking to `cloud`; the same state dir every time, so a
    second one is the first after a restart."""

    def make(http=None, timing: Timing = DEFAULT_TIMING, backfill_s: float = 0.0) -> Bridge:
        state = tmp_path / "state" / "pod-1"
        return Bridge(
            pod.paths,
            "pod-1",
            DeviceClient(http or cloud.http(), "pod-1"),
            Outbox(state / "outbox"),
            sock,
            state / "state.json",
            timing,
            backfill_s=backfill_s,
        )

    return make
