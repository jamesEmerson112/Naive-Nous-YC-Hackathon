"""Fake HC-SR04: replays a recorded distance trace.

One value per line in cm; "none" means no echo. Lines starting with # are comments.
$EDGE_TRACE picks the file; $EDGE_TRACE_END = "loop" (start over) or "hold" (repeat the last value).
"""
import os
from pathlib import Path


class TraceSensor:
    def __init__(self, sim_dir):
        path = Path(os.environ.get("EDGE_TRACE", Path(sim_dir) / "traces" / "normal.csv"))
        lines = [ln.strip() for ln in path.read_text().splitlines()]
        self.values = [None if ln.lower() == "none" else float(ln) for ln in lines if ln and not ln.startswith("#")]
        self.end = os.environ.get("EDGE_TRACE_END", "loop")
        self.i = 0

    def read_cm(self):
        if self.i < len(self.values):
            value = self.values[self.i]
        elif self.end == "hold":
            value = self.values[-1]
        else:
            value = self.values[self.i % len(self.values)]
        self.i += 1
        return value
