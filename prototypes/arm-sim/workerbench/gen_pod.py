"""Pod-fault benchmark items: offline podd snapshots, built the way `arm/tests/simpod.py:70-116` builds
them (the firmware's own distance arithmetic, `mm_v10` and `mm_v17`), with no MuJoCo and no podd.

A snapshot is what podd's `snapshot()` returns (firmware/healer/healer/podd.py:263-272): D and A rows of
the last 6 s, H rows of the last 30 s, E events of the last 180 s, and the link state. Noise comes from
the real sample (echo jitter 0.4 us, firmware/ultrasonic-sample.json) and the virtual pod (accelerometer
noise, health fields, boot fields: armsim/vpod.py). Each variant declares the level-1 result it expects
(both rule sets: armsim/level1.py and detect.py), and `make_items` asserts it, so drift in the rules is
caught. The variant table is the data report's (docs/research/worker-benchmark-data.md §2.3).

    python3 -m workerbench.gen_pod --seed 99 --split test --per-variant 30
    python3 -m workerbench.gen_pod --seed 1 --split dev --per-variant 30     # image 08 and stuck-max held out

Writes data/workerbench/synth-pod-<split>.jsonl. Test targets are 600-2000 mm, dev 40-600 mm.
"""
import argparse
import collections
import json
import random
import sys
import zlib
from pathlib import Path

from . import kinds, l1

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUT = REPO / "data" / "workerbench"
US_PER_MM = 2000 / 343
US_JITTER = 0.4
BUILDS = {"good": "pod v1.1-sim 2026-11-07T22:00:00Z", "02": "pod v1.2-clock-hsi2-sim 2026-11-07T22:30:00Z",
          "07": "pod v1.7-us-dist-16bit-sim 2026-11-07T23:00:00Z", "08": "pod v1.8-us-near-hold-sim 2026-11-07T23:30:00Z",
          "01": "pod v1.9-unaligned-frame-sim 2026-11-07T23:40:00Z", "06": "pod v2.0-i2c-no-timeout-sim 2026-11-07T23:50:00Z",
          "03": "pod v2.1-acc-no-scrub-sim", "04": "pod v2.2-acc-rate-overrun-sim", "05": "pod v2.3-acc-scale-4g-sim"}


def crc_of(build):
    return "0x%08X" % (zlib.crc32(build.encode()) & 0xFFFFFFFF)


def mm_v10(us):
    return (us * 343 + 1000) // 2000                      # hcsr04.c: out->dist_mm = (us * 343u + 1000u) / 2000u


def mm_v17(us):
    return ((us * 343 + 1000) & 0xFFFF) // 2000           # bad image 07: the product kept in 16 bits


def st_of(us):
    return "ok" if 116 <= us <= 23300 else "out_of_range"


class Pod:
    """One pod's timeline: a boot at `boot_ts`, a clock running at `clock` x real time."""

    def __init__(self, rng, now, *, boot_age, image="good", clock=1.0, rst="por", new_build=0, boots=1):
        self.rng, self.now, self.clock = rng, now, clock
        self.boot_ts = now - boot_age
        self.image = image
        self.E = [self.boot_event(self.boot_ts, rst=rst, boots=boots, new_build=new_build)]

    def ms(self, ts):
        return int((ts - self.boot_ts) * 1000 * self.clock)

    def boot_event(self, ts, *, rst="por", boots=1, streak=0, crashes=0, wdg=0, new_build=0, mode="normal",
                   last_cp="none", image=None):
        b = BUILDS[image or self.image]
        return {"type": "E", "ts": round(ts, 4), "t": 0, "ev": "boot", "build": b, "crc": crc_of(b), "rst": rst,
                "boots": boots, "streak": streak, "crashes": crashes, "wdg": wdg, "last_cp": last_cp, "mode": mode,
                "new_build": new_build, "clk": 48000000}

    def grid(self, hz, span, start=None):
        """Host timestamps at `hz` over (now - span, now], never before `start` (default the boot)."""
        start = self.boot_ts if start is None else start
        n = int(span * hz)
        out = []
        for k in range(n):
            ts = self.now - k / hz - self.rng.uniform(0, 0.003)
            if ts > start and ts > self.now - span:
                out.append(round(ts, 4))
        return sorted(out)

    def d(self, ts, us, conv=mm_v10):
        if us is None:
            return {"type": "D", "ts": ts, "t": self.ms(ts), "mm": 0, "us": 0, "st": "no_echo"}
        return {"type": "D", "ts": ts, "t": self.ms(ts), "mm": conv(us), "us": us, "st": st_of(us)}

    def us(self, mm):
        return int(round(mm * US_PER_MM + self.rng.gauss(0, US_JITTER)))

    def a(self, ts, x=-12, y=8, z=1001, noise=2.5):
        g = self.rng.gauss
        return {"type": "A", "ts": ts, "t": self.ms(ts), "x": int(round(x + g(0, noise))),
                "y": int(round(y + g(0, noise))), "z": int(round(z + g(0, noise))), "st": "ok"}

    def h(self, ts, **over):
        r = {"type": "H", "ts": ts, "t": self.ms(ts), "mode": "normal", "boots": 1, "streak": 0, "crashes": 0, "wdg": 0,
             "rst": "none", "loop_us": self.rng.randint(780, 920), "stk_free": 1184, "vdda": 3300 + self.rng.randint(-4, 4),
             "temp": round(31 + self.rng.random(), 1), "acc": "ok", "acc_errs": 0, "acc_cfg_lost": 0, "i2c_err": 0,
             "us": "ok", "tx_drop": 0, "rx_ovr": 0, "clk": 48000000, "acc_hz": 50}
        r.update(over)
        return r

    def healthy(self, target_mm, *, d_hz=10, a_hz=50, h_every=1.0, h_over=None):
        D = [self.d(ts, self.us(target_mm)) for ts in self.grid(d_hz, 6.0)]
        A = [self.a(ts) for ts in self.grid(a_hz, 6.0)]
        H = [self.h(ts, **(h_over or {})) for ts in self.grid(1 / h_every, 30.0)]
        return D, A, H

    def snap(self, D, A, H, link=None):
        return {"now": self.now, "D": D, "A": A, "H": H, "E": sorted(self.E, key=lambda e: e["ts"]),
                "link": link or {"state": "up", "bytes_per_s": 2480 + self.rng.randint(-60, 60), "valid_ratio": 1.0,
                                 "baud": 115200},
                "skills": [], "incidents": [], "J": [], "holding": False}


def _label(kind, *, accept=None, ev=None, facts=None, observable=True, params=None, status="fault", hard=None):
    sev = kinds.GOLD_SEVERITY[kind]
    if status == "healthy":
        return {"status": "healthy", "kind": "none", "accept": sorted(accept or {"none"}), "route": "none",
                "severity": "info", "escalate": False, "observable": True, "hard_negative": hard,
                "evidence_rows": {}, "facts": {}, "onset_s": None, "since_onset_s": None, "params": params or {},
                "label_source": "synthetic"}
    return {"status": "fault", "kind": kind, "accept": sorted(accept or {kind}), "route": kinds.ROUTE[kind],
            "severity": sev, "escalate": sev != "info", "observable": observable, "hard_negative": None,
            "evidence_rows": ev or {}, "facts": facts or {}, "onset_s": None, "since_onset_s": None,
            "params": params or {}, "label_source": "synthetic"}


def _ts(rows):
    return [r["ts"] for r in rows]


# ------------------------------------------------------------------------------------------ variants
# Each returns (snapshot, label, expect_l1). expect_l1 = {"alarm": bool, "names": set} where names lists
# the level-1 finding kinds of which at least one must appear when an alarm is expected.

def v01(rng, now, target_mm):
    """Bad image 01: HardFault right after boot, three times (streak 1-3), then safe mode, sensors off."""
    p = Pod(rng, now, boot_age=rng.uniform(90, 150), image="01")
    t = now - rng.uniform(14, 20)
    ev = []
    for k in range(1, 4):
        crash = {"type": "E", "ts": round(t, 4), "t": rng.randint(200, 900), "ev": "crash", "build_crc": crc_of(BUILDS["01"]),
                 "same_build": 1, "pc": "0x08001A3C", "lr": "0x08001A11", "sp": "0x20000F60", "xpsr": "0x21000003",
                 "up": rng.randint(200, 900), "cp": "frame_pack"}
        boot = p.boot_event(t + 0.05, rst="sft", boots=1 + k, streak=k, crashes=k, last_cp="frame_pack",
                            mode="safe" if k == 3 else "normal")
        p.E += [crash, boot]
        ev += [crash["ts"], boot["ts"]]
        t += rng.uniform(1.5, 3.0)
    safe = {"type": "E", "ts": round(p.E[-1]["ts"] + 0.02, 4), "t": 20, "ev": "safe_mode", "reason": "boot_loop"}
    p.E.append(safe)
    ev.append(safe["ts"])
    last_boot = p.E[-2]["ts"]
    H = []
    for ts in p.grid(1.0, 30.0):
        if ts < last_boot:
            H.append(p.h(ts))
        else:
            H.append(dict(p.h(ts, mode="safe", boots=4, streak=3, crashes=3, rst="sft", us="off"),
                          t=int((ts - last_boot) * 1000)))
    D = [p.d(ts, p.us(target_mm)) for ts in p.grid(10, 6.0) if ts < last_boot]
    A = [p.a(ts) for ts in p.grid(50, 6.0) if ts < last_boot]
    s = p.snap(D, A, H)
    lab = _label("crash_loop", ev={"E": ev, "H": [h["ts"] for h in H if h["mode"] == "safe"]},
                 facts={"crash_n": 3, "streak_n": 3})
    return s, lab, {"alarm": True, "names": {"crash", "reboot_loop", "safe_mode"}}


def v02_real(rng, now, target_mm):
    """Bad image 02 on the real board: the UART baud is off with the clock, so the serial output is garbage."""
    flash_age = rng.uniform(8, 20)
    p = Pod(rng, now, boot_age=rng.uniform(100, 170))
    D, A, H = p.healthy(target_mm)
    cut = now - flash_age
    D, A, H = [r for r in D if r["ts"] < cut], [r for r in A if r["ts"] < cut], [r for r in H if r["ts"] < cut]
    valid = round(rng.uniform(0.0, 0.3), 2)
    link = {"state": "garbled", "bytes_per_s": rng.randint(1100, 1300), "valid_ratio": valid, "baud": 115200,
            "sample": "\\x8f\\xf0\\x1c\\xe3"}
    s = p.snap(D, A, H, link=link)
    return s, _label("link_garbled", accept={"link_garbled", "clock_skew"}, ev={"L": True},
                     facts={"valid_ratio": valid}), {"alarm": True, "names": {"link_garbled"}}


def _image02(rng, now, target_mm, boot_age):
    """The virtual pod's image 02 (armsim/vpod.py:98-103,123): the MCU runs at half the clock it believes,
    so t=, the stream rates and the echo times are halved; the link stays clean."""
    p = Pod(rng, now, boot_age=boot_age, image="02", clock=0.5, rst="pin", new_build=1, boots=2)
    old = Pod(rng, now, boot_age=rng.uniform(120, 170))          # the good image before the flash
    p.E = old.E + p.E
    cut = p.boot_ts
    D = [old.d(ts, old.us(target_mm)) for ts in old.grid(10, 6.0) if ts < cut]
    D += [p.d(ts, int(round(p.us(target_mm) * 0.5))) for ts in p.grid(5, 6.0)]
    A = [old.a(ts) for ts in old.grid(50, 6.0) if ts < cut] + [p.a(ts) for ts in p.grid(25, 6.0)]
    H = [old.h(ts) for ts in old.grid(1.0, 30.0) if ts < cut]
    Hn = [p.h(ts, boots=2, rst="pin") for ts in sorted(round(cut + 2.0 * k + 0.001, 4) for k in range(20))
          if ts <= now and ts > now - 30]
    s = p.snap(sorted(D, key=lambda r: r["ts"]), sorted(A, key=lambda r: r["ts"]), H + Hn)
    return s, Hn


def v02_sim(rng, now, target_mm):
    s, Hn = _image02(rng, now, target_mm, rng.uniform(12, 25))
    lab = _label("clock_skew", accept={"clock_skew", "us_scale"}, ev={"H": _ts(Hn)}, facts={"clock_ratio": 0.5})
    return s, lab, {"alarm": True, "names": {"clock_skew"}}


def v02_early(rng, now, target_mm):
    s, Hn = _image02(rng, now, target_mm, rng.uniform(4.3, 5.9))
    lab = _label("clock_skew", accept={"clock_skew", "us_scale"}, ev={"H": _ts(Hn)}, facts={"clock_ratio": 0.5},
                 observable=len(Hn) >= 2)
    return s, lab, {"alarm": False, "names": set()}


def v03(rng, now, target_mm):
    """Bad image 03: the LIS3DH reset itself and the firmware re-sends its last sample: A rows identical."""
    p = Pod(rng, now, boot_age=rng.uniform(60, 170), image="03")
    D, A, H = p.healthy(target_mm)
    x, y, z = A[0]["x"], A[0]["y"], A[0]["z"]
    A = [dict(r, x=x, y=y, z=z) for r in A]
    return (p.snap(D, A, H), _label("acc_frozen", ev={"A": _ts(A)}, facts={"n_identical": len(A)}),
            {"alarm": True, "names": {"acc_frozen"}})


def v04(rng, now, target_mm):
    """Bad image 04: the accelerometer stream at 400 Hz overruns the 115200-baud UART: tx_drop rises and
    distance rows arrive at 3-6 Hz instead of 10."""
    p = Pod(rng, now, boot_age=rng.uniform(60, 170), image="04")
    d_hz = rng.choice([3, 4, 5, 6])
    D = [p.d(ts, p.us(target_mm)) for ts in p.grid(d_hz, 6.0)]
    A = [p.a(ts) for ts in p.grid(400, 6.0) if rng.random() < 0.3]
    rate = rng.randint(150, 260)
    H = []
    for ts in p.grid(1.0, 30.0):
        H.append(p.h(ts, acc_hz=400, tx_drop=max(0, int((ts - p.boot_ts) * rate))))
    lab = _label("tx_overrun", ev={"H": _ts(H[-10:])}, facts={"acc_hz": 400, "drop_per_s": rate, "d_hz": d_hz})
    return p.snap(D, A, H), lab, {"alarm": True, "names": {"tx_overrun"}}


def v05(rng, now, target_mm):
    """Bad image 05: full scale set to 4 g but converted at the 2 g sensitivity: |a| reads about 500 mg."""
    p = Pod(rng, now, boot_age=rng.uniform(60, 170), image="05")
    D, _, H = p.healthy(target_mm)
    A = [p.a(ts, x=-6, y=4, z=500, noise=1.25) for ts in p.grid(50, 6.0)]
    return (p.snap(D, A, H), _label("acc_gravity", ev={"A": _ts(A)}, facts={"mag_mg": 500}),
            {"alarm": True, "names": {"acc_gravity"}})


def v06(rng, now, target_mm):
    """Bad image 06: an I2C wait with no timeout hangs the loop; the independent watchdog resets the MCU
    (rst=iwdg, streak 1-3), then the firmware falls back to safe mode."""
    p = Pod(rng, now, boot_age=rng.uniform(90, 150), image="06")
    t = now - rng.uniform(16, 22)
    ev = []
    for k in range(2):
        e = {"type": "E", "ts": round(t - 2 + k * 0.5, 4), "t": rng.randint(1000, 9000), "ev": "acc_err", "err": "timeout",
             "addr": "0x18"}
        p.E.append(e)
        ev.append(e["ts"])
    for k in range(1, 4):
        boot = p.boot_event(t, rst="iwdg", boots=1 + k, streak=k, wdg=k, last_cp="acc_read",
                            mode="safe" if k == 3 else "normal")
        wd = {"type": "E", "ts": round(t + 0.03, 4), "t": 30, "ev": "watchdog_reset", "last_cp": "acc_read"}
        p.E += [boot, wd]
        ev += [boot["ts"], wd["ts"]]
        t += rng.uniform(2.0, 3.0)
    safe = {"type": "E", "ts": round(p.E[-1]["ts"] + 0.02, 4), "t": 40, "ev": "safe_mode", "reason": "boot_loop"}
    p.E.append(safe)
    ev.append(safe["ts"])
    last_boot = p.E[-3]["ts"]
    H = []
    for ts in p.grid(1.0, 30.0):
        if ts < last_boot:
            H.append(p.h(ts))
        else:
            H.append(dict(p.h(ts, mode="safe", boots=4, streak=3, wdg=3, rst="iwdg", us="off", acc="err", i2c_err=3),
                          t=int((ts - last_boot) * 1000)))
    D = [p.d(ts, p.us(target_mm)) for ts in p.grid(10, 6.0) if ts < last_boot - 6]
    s = p.snap(D, [], H)
    lab = _label("watchdog_loop", ev={"E": ev, "H": [h["ts"] for h in H if h["mode"] == "safe"]},
                 facts={"wdg_n": 3, "streak_n": 3})
    return s, lab, {"alarm": True, "names": {"watchdog", "reboot_loop", "safe_mode"}}


def v07(rng, now, target_mm):
    """Bad image 07 (the bench demo's fault, #54): the distance product is truncated to 16 bits."""
    p = Pod(rng, now, boot_age=rng.uniform(30, 170), image="07", rst="pin", new_build=1, boots=2)
    target = min(max(target_mm, 200), 1500)
    D = [p.d(ts, p.us(target), conv=mm_v17) for ts in p.grid(10, 6.0)]
    _, A, H = p.healthy(target)
    last = D[-1]
    lab = _label("us_mismatch", ev={"D": _ts(D)},
                 facts={"echo_us": last["us"], "reported_mm": last["mm"], "true_mm": round(last["us"] * 0.1715)})
    return p.snap(D, A, H), lab, {"alarm": True, "names": {"us_mismatch"}}


def _image08(rng, now, target_mm, *, held_after_s):
    p = Pod(rng, now, boot_age=rng.uniform(30, 170), image="08", rst="pin", new_build=1, boots=2)
    hand = rng.uniform(50, 150)
    grid = p.grid(10, 6.0)
    leave = now - held_after_s
    arrive = leave - 1.0
    D, held = [], []
    hand_mm = None
    for ts in grid:
        if ts < arrive or held_after_s is None:
            D.append(p.d(ts, p.us(target_mm)))
        elif ts < leave:
            r = p.d(ts, p.us(hand))
            hand_mm = r["mm"]
            D.append(r)
        else:
            u = p.us(target_mm)
            r = {"type": "D", "ts": ts, "t": p.ms(ts), "mm": hand_mm, "us": u, "st": st_of(u)}
            D.append(r)
            held.append(r)
    _, A, H = p.healthy(target_mm)
    return p, D, A, H, held, hand_mm


def v08_dormant(rng, now, target_mm):
    p = Pod(rng, now, boot_age=rng.uniform(20, 170), image="08", rst="pin", new_build=1, boots=2)
    D, A, H = p.healthy(target_mm)
    return p.snap(D, A, H), _label("none", status="healthy", hard="08_dormant"), {"alarm": False, "names": set()}


def v08_held(rng, now, target_mm):
    """Bad image 08 (bug.patch near_hold): after a hand at 50-150 mm leaves, mm stays held while the echo
    shows the background. detect needs over half of 4 s wrong (detect.py:252)."""
    after = rng.choice([0.5, 1.0, 1.5, 2.5, 3.0, 3.5])
    p, D, A, H, held, hand_mm = _image08(rng, now, target_mm, held_after_s=after)
    last = held[-1]
    lab = _label("us_mismatch", ev={"D": _ts(held)}, params={"held_after_s": after},
                 facts={"held_mm": hand_mm, "echo_us": last["us"], "true_mm": round(last["us"] * 0.1715)})
    return p.snap(D, A, H), lab, {"alarm": after >= 2.5, "names": {"us_mismatch"} if after >= 2.5 else set()}


def _frozen(rng, now, target_mm, healthy_n, frozen_row):
    p = Pod(rng, now, boot_age=rng.uniform(60, 170))
    grid = p.grid(10, 6.0)
    D = [p.d(ts, p.us(target_mm)) for ts in grid[:healthy_n]]
    fr = frozen_row(p) if frozen_row else dict(D[-1])
    frozen = [dict(fr, ts=ts, t=p.ms(ts)) for ts in grid[healthy_n:]]
    _, A, H = p.healthy(target_mm)
    return p, D + frozen, A, H, frozen


def v_frozen_early(rng, now, target_mm):
    """25 healthy rows, then 35 identical: the 4 s frozen rule needs the whole 4 s frozen (level1.py:32-36).
    Measured, not as the data report predicted (missed by both): level 1 still fires when the healthy rows
    inside its 4 s happen to share the frozen echo time (real echo times repeat for up to 1.8 s), so the
    expectation is not asserted (alarm None); run.py recomputes level 1 per item anyway."""
    p, D, A, H, fr = _frozen(rng, now, target_mm, 25, None)
    lab = _label("us_frozen", ev={"D": _ts(fr)}, facts={"stuck_us": fr[0]["us"], "stuck_mm": fr[0]["mm"],
                                                        "n_identical": len(fr)})
    return p.snap(D, A, H), lab, {"alarm": None, "names": {"us_frozen"}}


def v_frozen_no_echo(rng, now, target_mm):
    """The freeze lands on a no_echo row. Without kinematics this looks exactly like a lost echo, so both
    kinds are accepted (inferred: the data report scores it as us_frozen only)."""
    p, D, A, H, fr = _frozen(rng, now, target_mm, 20, lambda p: {"type": "D", "mm": 0, "us": 0, "st": "no_echo"})
    lab = _label("us_frozen", accept={"us_frozen", "us_no_echo"}, ev={"D": _ts(fr)}, facts={"no_echo_n": len(fr)})
    return p.snap(D, A, H), lab, {"alarm": True, "names": {"us_no_echo"}}


def v_frozen_oor(rng, now, target_mm):
    """At home the jaw sits in the beam (out_of_range, about 42 us, armsim/arm.py); the freeze lands there.
    level1 keeps only ok rows (level1.py:32) and detect has no out_of_range rule."""
    jaw_mm = rng.uniform(6.5, 8.5)
    p, D, A, H, fr = _frozen(rng, now, jaw_mm, 15, None)
    lab = _label("us_frozen", ev={"D": _ts(fr)}, facts={"stuck_us": fr[0]["us"], "stuck_mm": fr[0]["mm"],
                                                        "n_identical": len(fr)}, params={"jaw_mm": round(jaw_mm, 1)})
    return p.snap(D, A, H), lab, {"alarm": False, "names": set()}


def v_spikes(rng, now, target_mm):
    """3-6 of 60 rows +180-320 us on a still target (level2/chatts_test.py:23-25); the median-based spread
    ignores sparse outliers by design (level1.py:128-132)."""
    p = Pod(rng, now, boot_age=rng.uniform(60, 170))
    D, A, H = p.healthy(target_mm)
    idx = rng.sample(range(len(D)), rng.randint(3, 6))
    spikes = []
    for i in idx:
        u = D[i]["us"] + rng.choice([180, 240, 320])
        D[i] = p.d(D[i]["ts"], u)
        spikes.append(D[i])
    lab = _label("us_noisy", ev={"D": _ts(spikes)}, facts={"spike_n": len(idx), "spike_us": 240},
                 params={"n": len(idx)})
    return p.snap(D, A, H), lab, {"alarm": False, "names": set()}


def v_stuck_max(rng, now, target_mm):
    """TRIG/ECHO swapped (fault catalog U3): every reading at about 23,800 us, out_of_range."""
    p = Pod(rng, now, boot_age=rng.uniform(60, 170))
    _, A, H = p.healthy(target_mm)
    D = [p.d(ts, 23800 + rng.randint(-3, 3)) for ts in p.grid(10, 6.0)]
    lab = _label("us_out_of_range", ev={"D": _ts(D)}, facts={"stuck_us": 23800})
    return p.snap(D, A, H), lab, {"alarm": False, "names": set()}


# ---- pod hard negatives, all healthy

def hn_covered(rng, now, target_mm):
    """A hand or object right against the sensor: a constant 20-29 mm (detect's us_blocked is info only)."""
    p = Pod(rng, now, boot_age=rng.uniform(60, 170))
    _, A, H = p.healthy(target_mm)
    mm = rng.uniform(21, 28)
    D = [p.d(ts, p.us(mm)) for ts in p.grid(10, 6.0)]
    return (p.snap(D, A, H), _label("none", status="healthy", hard="covered", accept={"none", "us_blocked"}),
            {"alarm": False, "names": set()})


def hn_reflash(rng, now, target_mm):
    """A deliberate reflash: one boot with rst=pin new_build=1 (ignored by detect.py:71-78)."""
    p = Pod(rng, now, boot_age=rng.uniform(8, 40), rst="pin", new_build=1, boots=2)
    old = Pod(rng, now, boot_age=rng.uniform(120, 170))
    p.E = old.E + p.E
    D, A, H = p.healthy(target_mm)
    H = [old.h(ts) for ts in old.grid(1.0, 30.0) if ts < p.boot_ts] + [dict(h, boots=2) for h in H]
    return p.snap(D, A, H), _label("none", status="healthy", hard="reflash"), {"alarm": False, "names": set()}


def hn_sparse_no_echo(rng, now, target_mm):
    """1-2 of the last 40 pings without an echo: under detect's 5% intermittent threshold (detect.py:230)."""
    p = Pod(rng, now, boot_age=rng.uniform(60, 170))
    D, A, H = p.healthy(target_mm)
    for i in rng.sample(range(len(D) - 38, len(D)), rng.randint(1, 2)):
        D[i] = p.d(D[i]["ts"], None)
    return p.snap(D, A, H), _label("none", status="healthy", hard="sparse_no_echo"), {"alarm": False, "names": set()}


def hn_ts_batch(rng, now, target_mm):
    """podd stamps rows as they arrive, so a batch of lines shares one ts (runs/20261108-110519-01d45c.jsonl)."""
    p = Pod(rng, now, boot_age=rng.uniform(60, 170))
    D, A, H = p.healthy(target_mm)
    for i in range(1, len(D), 3):
        D[i] = dict(D[i], ts=D[i - 1]["ts"])
        if i + 1 < len(D):
            D[i + 1] = dict(D[i + 1], ts=D[i - 1]["ts"])
    return p.snap(D, A, H), _label("none", status="healthy", hard="ts_batch"), {"alarm": False, "names": set()}


VARIANTS = {
    "01": v01, "02-real": v02_real, "02-sim": v02_sim, "02-early": v02_early, "03": v03, "04": v04, "05": v05,
    "06": v06, "07": v07, "08-dormant": v08_dormant, "08-held": v08_held, "frozen-early": v_frozen_early,
    "frozen-at-no_echo": v_frozen_no_echo, "frozen-at-out_of_range": v_frozen_oor, "spikes": v_spikes,
    "stuck-max": v_stuck_max,
    "hn-covered": hn_covered, "hn-reflash": hn_reflash, "hn-sparse-no-echo": hn_sparse_no_echo,
    "hn-ts-batch": hn_ts_batch,
}
HELD_OUT_OF_DEV = {"08-dormant", "08-held", "stuck-max"}     # unseen scenarios (data report §2.5)
TARGETS = {"dev": (40, 600), "test": (600, 2000)}


def healthy_d(rng, n, target_mm, now):
    """n healthy D rows ending at `now` (10 Hz, echo jitter 0.4 us)."""
    p = Pod(rng, now, boot_age=n / 10 + 60)
    return [p.d(ts, p.us(target_mm)) for ts in p.grid(10, n / 10)]


def synth(variant, rng, *, target_mm, now):
    """One snapshot of `variant`: {"snapshot", "label", "expect_l1"}."""
    s, lab, exp = VARIANTS[variant](rng, now, target_mm)
    return {"snapshot": s, "label": lab, "expect_l1": exp}


def check_expect(snapshot, exp):
    """(ok, level-1 alarm kinds): the rules must do what the variant expects."""
    got = [f["kind"] for f in l1.alarms(l1.l1_findings(snapshot))]
    if exp["alarm"] is None:                   # not asserted: the rules may or may not fire (see the variant)
        return not got or bool(set(got) & exp["names"]), got
    if not exp["alarm"]:
        return not got, got
    return bool(got) and bool(set(got) & exp["names"]), got


def make_items(seed, split, per_variant, variants=None):
    rng = random.Random(seed)
    lo, hi = TARGETS.get(split, TARGETS["test"])
    items, bad = [], []
    for v in variants or VARIANTS:
        if split == "dev" and v in HELD_OUT_OF_DEV:
            continue
        for k in range(per_variant):
            now = round(1794100000.0 + rng.uniform(0, 86400), 3)
            target = rng.uniform(lo, hi)
            r = synth(v, rng, target_mm=target, now=now)
            ok, got = check_expect(r["snapshot"], r["expect_l1"])
            if not ok:
                bad.append((v, k, got, r["label"].get("params")))
            items.append({"id": f"{split}-pod-{seed}-{v}-{k:03d}", "split": split, "source": "synth-pod",
                          "kinematics": False, "snapshot": r["snapshot"], "label": r["label"],
                          "meta": {"seed": seed, "variant": v, "run_id": f"synth-{seed}-{v}-{k}",
                                   "target_mm": round(target, 1), "expect_l1": {"alarm": r["expect_l1"]["alarm"],
                                                                               "names": sorted(r["expect_l1"]["names"])},
                                   "l1": got}})
    return items, bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=99)
    ap.add_argument("--split", default="test")
    ap.add_argument("--per-variant", type=int, default=30)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    items, bad = make_items(a.seed, a.split, a.per_variant)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"synth-pod-{a.split}.jsonl"
    with path.open("w") as f:
        for it in items:
            f.write(json.dumps(it, separators=(",", ":")) + "\n")
    c = collections.Counter((i["meta"]["variant"], bool(i["meta"]["l1"])) for i in items)
    print(f"wrote {len(items)} items -> {path}")
    print("variant                   gold kind        level 1 fired / n   expected")
    for v in VARIANTS:
        n = c[(v, True)] + c[(v, False)]
        if n:
            it = next(i for i in items if i["meta"]["variant"] == v)
            e = it["meta"]["expect_l1"]["alarm"]
            print(f"  {v:24s} {it['label']['kind']:16s} {c[(v, True)]:3d} / {n:3d}          "
                  f"{'either' if e is None else 'alarm' if e else 'silent'}")
    if bad:
        print(f"EXPECTATION FAILURES: {len(bad)}")
        for b in bad[:20]:
            print("  ", b)
        return 1
    print("every variant did what it expects of level 1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
