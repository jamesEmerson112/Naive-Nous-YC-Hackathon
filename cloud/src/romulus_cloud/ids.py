"""Server-made IDs and the one clock helper (architecture §10).

Server IDs are prefixed strings (`ov_`, `cand_`, `wo_`, `rec_`, `ds_`, `exp_`, `evt_`, and the
Fleet Doctor's `cons_` consults, `prop_` proposals and `ask_` asks).
Incident and recording IDs are UUIDs made on the device, so they never come from here (D8).
"""

import uuid
from datetime import UTC, datetime

PREFIXES = frozenset({"ov", "cand", "wo", "rec", "ds", "exp", "evt", "cons", "prop", "ask"})


def new_id(prefix: str) -> str:
    """Return `<prefix>_<32 hex chars>`, e.g. `evt_3f9e…`. The prefix is given without `_`."""
    prefix = prefix.rstrip("_")
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix {prefix!r}; known: {sorted(PREFIXES)}")
    return f"{prefix}_{uuid.uuid4().hex}"


def utcnow() -> datetime:
    """Timezone-aware UTC now. Only services and the worker call this; domain code takes `now`."""
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Treat a naive datetime as UTC and convert an aware one to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
