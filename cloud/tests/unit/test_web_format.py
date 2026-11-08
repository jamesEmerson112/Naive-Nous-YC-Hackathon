"""The page formatting helpers: local, readable times, gaps and names (M2, D10)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from romulus_cloud.api.web import clock, day, gap, what, who

PT = ZoneInfo("America/Los_Angeles")


def test_clock_and_day_are_local():
    at = datetime(2026, 11, 8, 1, 42, 7, tzinfo=UTC)  # 5:42:07 PM Sat in San Francisco
    assert clock(at, PT) == "5:42:07 PM"
    assert day(at, PT) == "Sat Nov 7"


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "same moment"),
        (12, "12 s later"),
        (59.6, "1 min later"),
        (90, "1 min 30 s later"),
        (20 * 60 + 5, "20 min later"),
        (3600, "1 h later"),
        (2 * 3600 + 5 * 60, "2 h 5 min later"),
    ],
)
def test_gap(seconds, text):
    assert gap(seconds) == text


def test_readable_names():
    assert who("device:arm-1") == "arm-1"
    assert who("human:kent") == "kent"
    assert who("gate") == "gate"
    assert what("incident.reported") == "incident reported"
    assert what("overlay.locked_in") == "overlay locked in"
