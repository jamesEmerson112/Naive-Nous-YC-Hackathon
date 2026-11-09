"""The arm host's view of the sensor pod, through Olir's podd (docs/firmware-apis.md §4).

The same code runs tonight against the virtual pod and tomorrow against the real pod on the Pi:
the arm host never opens the pod's serial port, it only talks to podd's socket.

  - every 0.1 s: `recent kind=D` -> new rows join the arm's predicted distance (by wall-clock time)
    and feed the level-1 watchdog
  - every 1 s:   `incidents`      -> podd's own open incidents (clock_skew, link_*, us_no_echo, ...)
  - on request:  `cmd`, `note`    -> restart the sensor; leave a note in podd's event log
"""
import os
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HEALER = REPO / "firmware" / "healer"


def healer_client(var):
    """Olir's podd client (stdlib only), pointed at a podd whose HEALER_VAR is `var`."""
    os.environ["HEALER_VAR"] = str(var)
    if str(HEALER) not in sys.path:
        sys.path.insert(0, str(HEALER))
    from healer import client               # reads HEALER_VAR on first import
    return client


class PodSource:
    def __init__(self, arm, client, *, objects=(), poll_s=0.1):
        self.arm, self.client, self.objects, self.poll_s = arm, client, objects, poll_s
        self.last_ts = 0.0
        self.up = False
        self.open = []                      # podd's open incidents, refreshed every second
        self.on_incidents = []              # callbacks(open_list)
        self.running = False
        self._next_inc = 0.0

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, name="podsource", daemon=True).start()
        return self

    def stop(self):
        self.running = False

    def pred_at(self, ts):
        """What the arm's pose predicted the sensor would read at wall-clock `ts`."""
        best = None
        for t, p in reversed(self.arm.pred_hist):
            if abs(t - ts) <= 0.3 and (best is None or abs(t - ts) < abs(best[0] - ts)):
                best = (t, p)
            if t < ts - 0.3:
                break
        if best is not None:
            return best[1]
        return self.arm.predicted_mm(objects=self.objects)    # the arm is still: its pose now

    def _loop(self):
        while self.running:
            try:
                rows = self.client.call("recent", timeout=2.0, kind="D", seconds=3, limit=60)["rows"]
                new = [r for r in rows if r["ts"] > self.last_ts]
                for r in new:
                    pred = self.pred_at(r["ts"])
                    row = {"ts": r["ts"], "t": r.get("t"), "mm": r.get("mm") if r.get("st") == "ok" else None,
                           "us": r.get("us"), "st": r.get("st"), "pred_mm": None if pred is None else round(pred, 1)}
                    self.arm.d_rows.append(row)
                    if self.arm.recorder:
                        self.arm.recorder.distance(self.arm.t, row)
                if new:
                    self.last_ts = new[-1]["ts"]
                    for watch in self.arm.watchers:
                        watch(self.arm)
                if time.time() >= self._next_inc:
                    self._next_inc = time.time() + 1.0
                    self.open = self.client.call("incidents", timeout=2.0)["open"]
                    for cb in self.on_incidents:
                        cb(self.open)
                self.up = True
            except (OSError, ValueError, KeyError):
                self.up = False                  # podd down or restarting: keep trying
            time.sleep(self.poll_s)

    def cmd(self, line):
        return self.client.call("cmd", timeout=6.0, line=line)

    def note(self, ev, **fields):
        try:
            return self.client.call("note", timeout=2.0, ev=ev, fields=fields)
        except OSError:
            return None

    def triage(self):
        return self.client.call("triage", timeout=5.0)
