from datetime import UTC, datetime, timedelta, timezone

import pytest

from romulus_cloud.ids import PREFIXES, ensure_utc, new_id, utcnow


@pytest.mark.parametrize("prefix", sorted(PREFIXES))
def test_new_id_has_prefix_and_is_unique(prefix):
    a, b = new_id(prefix), new_id(prefix)
    assert a.startswith(prefix + "_") and b.startswith(prefix + "_")
    assert a != b
    assert len(a) == len(prefix) + 1 + 32


def test_new_id_accepts_trailing_underscore():
    assert new_id("evt_").startswith("evt_") and "__" not in new_id("evt_")


def test_new_id_rejects_unknown_prefix():
    with pytest.raises(ValueError):
        new_id("inc")


def test_utcnow_is_aware_utc():
    t = utcnow()
    assert t.tzinfo is not None and t.utcoffset() == timedelta(0)


def test_ensure_utc():
    naive = datetime(2026, 11, 7, 22, 0)
    assert ensure_utc(naive) == datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
    pst = datetime(2026, 11, 7, 14, 0, tzinfo=timezone(timedelta(hours=-8)))
    assert ensure_utc(pst) == datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
    assert ensure_utc(pst).tzinfo == UTC
