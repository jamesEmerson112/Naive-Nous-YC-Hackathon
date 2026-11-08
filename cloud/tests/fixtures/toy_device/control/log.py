"""State-change log lines in the fault catalog's logging contract (`S OLD->NEW reason=...`)."""

import sys


def log_state(old, new, **fields):
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"S {old.value}->{new.value} {extra}".rstrip(), file=sys.stderr)
