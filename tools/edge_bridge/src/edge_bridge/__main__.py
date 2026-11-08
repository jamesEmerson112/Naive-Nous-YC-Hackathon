"""`python -m edge_bridge`: run the bridge next to the pod healer. The only real sleeps."""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path

import httpx

from edge_bridge.bridge import Bridge
from edge_bridge.faults import Timing
from edge_bridge.podd import PoddPaths, PoddSocket
from fake_device.client import DeviceClient
from fake_device.outbox import Outbox

log = logging.getLogger("edge_bridge")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="python -m edge_bridge",
        description="Report what the pod healer records (podd logs, heal tools) to the cloud.",
    )
    ap.add_argument("--healer", default=os.environ.get("HEALER_DIR", "firmware/healer"),
                    help="the healer folder (holds tools.json and var/); default %(default)s")
    ap.add_argument("--var", default=None, help="the healer's var/ ($HEALER_VAR, else <healer>/var)")
    ap.add_argument("--url", default=os.environ.get("CLOUD_URL", "http://localhost:8000"),
                    help="cloud base URL ($CLOUD_URL); default %(default)s")
    ap.add_argument("--device-id", default=os.environ.get("POD_DEVICE_ID", "pod-1"),
                    help="this pod's device id in the cloud; default %(default)s")
    ap.add_argument("--state-dir", default="data/edge_bridge",
                    help="state and outbox live in <state-dir>/<device-id>/; default %(default)s")
    ap.add_argument("--interval", type=float, default=10, help="seconds between ticks")
    ap.add_argument("--backfill-hours", type=float, default=0,
                    help="on the first start, also report faults from this many hours back")
    ap.add_argument("--quiet", type=float, default=Timing.quiet_s,
                    help="seconds of quiet after the healer asks a person for help before an "
                         "unresolved fault is reported; default %(default)s")
    ap.add_argument("--max-open", type=float, default=Timing.max_open_s,
                    help="report a fault that's still open after this many seconds; "
                         "default %(default)s")
    ap.add_argument("--ticks", type=int, default=None, help="stop after this many ticks")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)  # fmt: skip

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    paths = PoddPaths.at(args.healer, args.var)
    if not paths.logs.is_dir():
        log.warning("%s doesn't exist yet; waiting for podd to create it", paths.logs)
    state_dir = Path(args.state_dir) / args.device_id
    timing = Timing(quiet_s=args.quiet, max_open_s=args.max_open)

    with httpx.Client(base_url=args.url, timeout=10) as http:
        bridge = Bridge(
            paths,
            args.device_id,
            DeviceClient(http, args.device_id),
            Outbox(state_dir / "outbox"),
            PoddSocket(paths.socket),
            state_dir / "state.json",
            timing,
            backfill_s=args.backfill_hours * 3600,
        )
        log.info("bridging %s → %s as %s", paths.var, args.url, args.device_id)
        ticks = 0
        try:
            while args.ticks is None or ticks < args.ticks:
                started = time.time()
                bridge.tick(started)
                ticks += 1
                if args.ticks is None or ticks < args.ticks:
                    time.sleep(max(0.0, args.interval - (time.time() - started)))
        except KeyboardInterrupt:
            log.info("stopped")


if __name__ == "__main__":
    main()
