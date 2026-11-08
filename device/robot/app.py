"""Robot app: hold an object while the ultrasonic distance drives the arm (see SPEC.md).

Environment (set by the supervisor and the test bed):
  EDGE_MAX_STEPS  stop after N steps (0 = run forever)
  EDGE_FAST=1     don't sleep between steps (replays recorded data as fast as possible)
"""
import faulthandler
import json
import os
import signal
import time
import tomllib
from pathlib import Path

import control
import hal
import sensors

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
RUN = ROOT / "run"


def load_config():
    with open(ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def with_retries(fn, tries, what):
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except ConnectionError as e:
            print(f"E code=BUS_READ_FAILED what={what} attempt={attempt}/{tries} err={e}", flush=True)
            if attempt == tries:
                raise
            time.sleep(0.01)


def main():
    LOGS.mkdir(exist_ok=True)
    RUN.mkdir(exist_ok=True)
    stack_file = open(LOGS / "stack.txt", "w")
    faulthandler.register(signal.SIGUSR1, file=stack_file, all_threads=True)  # the supervisor's stack dump

    cfg = load_config()
    hz = cfg["app"]["loop_hz"]
    every = cfg["app"]["log_every"]
    max_steps = int(os.environ.get("EDGE_MAX_STEPS", "0"))
    fast = os.environ.get("EDGE_FAST") == "1"

    arm = hal.make_arm(cfg)
    sensor = hal.make_sensor(cfg)
    ctl = control.Controller(cfg)
    arm.connect()
    print(f"BOOT app=robot backend={cfg['app']['backend']} loop_hz={hz}", flush=True)

    telemetry = open(LOGS / "telemetry.jsonl", "w")
    state, step, period = ctl.state, 0, 1.0 / hz
    try:
        while True:
            t0 = time.monotonic()
            dist = sensors.read_distance(sensor)
            obs = with_retries(arm.get_observation, cfg["arm"]["read_retries"], "Present_Position")
            action, info = ctl.step(dist)
            arm.send_action(action)

            if info["state"] != state:
                print(f"S {state}->{info['state']} step={step} dist_cm={dist}", flush=True)
                state = info["state"]
            record = {"step": step, "t": round(time.time(), 3), **info,
                      "gripper": action["gripper.pos"], "shoulder_lift": round(action["shoulder_lift.pos"], 2),
                      "observed_gripper": obs.get("gripper.pos")}
            telemetry.write(json.dumps(record) + "\n")
            telemetry.flush()
            if step % every == 0:
                print(f"T step={step} dist_cm={dist} valid={int(info['valid'])} lift={info['lift']} "
                      f"gripper={action['gripper.pos']} state={state}", flush=True)
            (RUN / "heartbeat").write_text(f"{step} {time.time():.3f}\n")

            step += 1
            if max_steps and step >= max_steps:
                break
            if not fast:
                time.sleep(max(0.0, period - (time.monotonic() - t0)))
    finally:
        arm.disconnect()
    print(f"EXIT ok steps={step}", flush=True)


if __name__ == "__main__":
    main()
