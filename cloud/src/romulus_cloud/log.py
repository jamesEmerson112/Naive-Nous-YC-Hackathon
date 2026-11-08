"""One JSON line per request or worker step, to stdout (architecture §10)."""

import json
import sys
from typing import Any

from romulus_cloud.ids import utcnow


def log_line(msg: str, *, level: str = "info", **fields: Any) -> None:
    line = {"ts": utcnow().isoformat(), "level": level, "msg": msg, **fields}
    sys.stdout.write(json.dumps(line, default=str) + "\n")
    sys.stdout.flush()
