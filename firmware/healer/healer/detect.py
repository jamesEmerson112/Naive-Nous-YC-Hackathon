"""Deterministic triage rules (no LLM). Each turns recent telemetry into findings.

A finding is a dict:
  kind        stable id, e.g. "acc_frozen" (incidents are keyed on it)
  severity    "critical" | "warn" | "info"
  summary     one sentence, what is observed
  evidence    numbers that back it up
  hypotheses  physically plausible causes, most likely first
  next_steps  tool calls that would tell the hypotheses apart

The rules check physical invariants, so they work without the robot arm:
  - at rest, an accelerometer measures |a| = 1 g (1000 mg)
  - a real MEMS sensor always has a noise floor; zero variance means stale data
  - the MCU's clock must agree with the host's wall clock
  - telemetry must fit the UART byte budget (115200 baud 8N1 = 11520 bytes/s)
  - an ultrasonic distance must match its own echo time (mm = us * 0.343 / 2)
"""
import math
import statistics

from .config import UART_BYTES_PER_S

US_TO_MM = 0.343 / 2      # HC-SR04: mm per microsecond of echo (sound at 20 C, out and back)


def _f(kind, severity, summary, evidence=None, hypotheses=None, next_steps=None):
    return {"kind": kind, "severity": severity, "summary": summary, "evidence": evidence or {},
            "hypotheses": hypotheses or [], "next_steps": next_steps or []}


def _std(xs):
    return statistics.pstdev(xs) if len(xs) > 1 else 0.0


def _window(recs, now, seconds):
    return [r for r in recs if r["ts"] >= now - seconds]


def check_link(s):
    out = []
    link = s["link"]
    if link["state"] == "down":
        out.append(_f("link_down", "critical", "The serial port is not open: %s" % link.get("error", "no port"),
                      {"port": link.get("port")},
                      ["the debug probe was unplugged or re-enumerated", "another process holds the port"],
                      ["chip_status (does SWD still reach the chip?)"]))
    elif link["state"] == "silent":
        out.append(_f("link_silent", "critical",
                      "No bytes from the pod for %.0f s" % link["silent_s"],
                      {"silent_s": round(link["silent_s"], 1), "last_line_age_s": link.get("last_line_age_s")},
                      ["firmware hung with interrupts off, or halted by a debugger",
                       "firmware stuck in a loop that never prints (the watchdog should reset it within ~2 s)",
                       "board unpowered"],
                      ["chip_status (is uptime advancing?)", "chip_halt_sample (where is the CPU?)"]))
    elif link["state"] == "garbled":
        out.append(_f("link_garbled", "critical",
                      "Bytes arrive but don't parse as telemetry (%d%% of lines valid)" % round(100 * link["valid_ratio"]),
                      {"bytes_per_s": link["bytes_per_s"], "valid_ratio": round(link["valid_ratio"], 2),
                       "baud": link["baud"], "sample": link.get("sample")},
                      ["baud mismatch: the MCU's UART runs at a different rate than the host expects, which "
                       "usually means the firmware's idea of its clock (SystemCoreClock) is wrong",
                       "electrical noise on the UART (unlikely over the probe's USB VCOM)"],
                      ["link_baud_scan", "chip_clock"]))
    return out


def check_resets(s):
    out = []
    E = s["E"]
    all_boots = [e for e in E if e.get("ev") == "boot"]
    # Only judge the build that is running now: a reflash legitimately reboots, and crashes of an
    # older build are history (they stay visible through the `events` tool).
    cur = str(all_boots[-1].get("crc", "")).upper() if all_boots else None
    boots = [e for e in all_boots if e["ts"] >= s["now"] - 60 and str(e.get("crc", "")).upper() == cur]
    # A boot is abnormal if the firmware says the previous run ended in a crash or watchdog reset
    # (streak >= 1) or the supply dropped (por/lpwr). Deliberate reflashes and resets don't count.
    abnormal = [b for b in boots if (b.get("streak") or 0) >= 1
                or any(x in str(b.get("rst", "")) for x in ("por", "lpwr", "wwdg"))]
    crashes = [e for e in E if e.get("ev") == "crash" and e["ts"] >= s["now"] - 120
               and str(e.get("build_crc", "")).upper() == cur]
    wdgs = [e for e in E if e.get("ev") == "watchdog_reset" and e["ts"] >= s["now"] - 120
            and any(abs(e["ts"] - b["ts"]) < 1.0 for b in boots)]
    if len(abnormal) >= 2:
        out.append(_f("reboot_loop", "critical", "%d abnormal boots in the last 60 s" % len(abnormal),
                      {"boots": [{k: b.get(k) for k in ("rst", "streak", "last_cp", "mode", "crc")} for b in boots[-5:]]},
                      ["firmware crashes (HardFault) shortly after boot", "watchdog resets: firmware stops kicking it",
                       "supply brownouts (rst would include por/lpwr)"],
                      ["events kinds=crash,watchdog_reset", "fw_symbolize on the crash pc/lr"]))
    if crashes:
        c = crashes[-1]
        out.append(_f("crash", "critical",
                      "HardFault at pc=%s (lr=%s) in build %s, during '%s'" % (c.get("pc"), c.get("lr"), c.get("build_crc"), c.get("cp")),
                      {k: c.get(k) for k in ("pc", "lr", "sp", "xpsr", "r0", "r1", "r2", "r3", "r12", "build_crc", "same_build", "up", "cp")}
                      | {"count_120s": len(crashes)},
                      ["Cortex-M0 HardFault causes: unaligned load/store (M0 has no unaligned access), "
                       "bad pointer / bus error, stack overflow, executing from an invalid address, divide by zero "
                       "is NOT a fault on M0 (no hardware divide)"],
                      ["fw_symbolize address=<pc> image=<build_crc>", "fw_symbolize address=<lr>",
                       "src_log / src_diff (what changed in this build?)"]))
    if wdgs:
        w = wdgs[-1]
        out.append(_f("watchdog", "critical", "Watchdog reset; the main loop was last at checkpoint '%s'" % w.get("last_cp"),
                      {"last_cp": w.get("last_cp"), "count_120s": len(wdgs)},
                      ["a loop waiting on hardware without a timeout (I2C flag, UART, ADC)",
                       "an infinite loop / deadlock at that checkpoint"],
                      ["chip_halt_sample while it's stuck", "src_search for the checkpoint name"]))
    H = s["H"]
    if H and H[-1].get("mode") == "safe":
        out.append(_f("safe_mode", "critical", "Firmware is in safe mode (sensors off) after a boot loop",
                      {"streak": H[-1].get("streak"), "crashes": H[-1].get("crashes"), "wdg": H[-1].get("wdg")},
                      ["the firmware detected repeated crash/watchdog resets and stopped the sensors to stay up"],
                      ["pod_cmd 'crash'", "events kinds=boot,crash,watchdog_reset", "pod_cmd 'safe off' to retry after a fix"]))
    return out


def check_accel(s):
    out = []
    A = _window(s["A"], s["now"], 3)
    H = s["H"]
    E = s["E"]
    last_h = H[-1] if H else {}
    sensors_on = last_h.get("mode", "normal") != "safe"

    acc_errs = [e for e in E if e.get("ev") == "acc_err" and e["ts"] >= s["now"] - 30]
    if sensors_on and (last_h.get("acc") == "err" or (acc_errs and not A)):
        err = acc_errs[-1].get("err") if acc_errs else None
        out.append(_f("acc_error", "warn", "Accelerometer not responding on I2C (last error: %s)" % err,
                      {"last_err": err, "addr": acc_errs[-1].get("addr") if acc_errs else None,
                       "acc_errs": last_h.get("acc_errs"), "i2c_err": last_h.get("i2c_err")},
                      ["nack: device absent / unplugged / wrong address / SDA or SCL wire off",
                       "timeout: bus held low (a slave stuck mid-transfer, or a short)",
                       "bad_who_am_i: a different chip answered"],
                      ["pod_cmd 'i2c lines' (both should be 1 when idle)", "pod_cmd 'i2c scan' (expect 0x18)",
                       "pod_cmd 'acc regs'"]))

    if len(A) >= 20:
        xs, ys, zs = [a["x"] for a in A], [a["y"] for a in A], [a["z"] for a in A]
        stale = sum(1 for a in A if a.get("st") == "stale")
        sx, sy, sz = _std(xs), _std(ys), _std(zs)
        mags = [math.sqrt(a["x"] ** 2 + a["y"] ** 2 + a["z"] ** 2) for a in A]
        mmag, smag = statistics.fmean(mags), _std(mags)
        ev = {"n": len(A), "std_mg": [round(sx, 2), round(sy, 2), round(sz, 2)],
              "mean_mg": [round(statistics.fmean(v), 1) for v in (xs, ys, zs)],
              "mean_mag_mg": round(mmag, 1), "stale_fraction": round(stale / len(A), 2)}
        if sx == 0 and sy == 0 and sz == 0:
            out.append(_f("acc_frozen", "critical",
                          "Accelerometer output is frozen: %d samples, every axis identical (zero noise)" % len(A), ev,
                          ["the LIS3DH reset itself (brownout/ESD/loose VCC) and is back in power-down mode "
                           "(CTRL_REG1=0x07), so no new samples are produced and the old ones repeat",
                           "firmware is re-sending a cached value"],
                          ["pod_cmd 'acc regs' (compare ctrl1/ctrl4 with expect_*)", "pod_cmd 'acc init' (runtime fix)",
                           "src_search 'check_config' (does the firmware re-check the sensor config?)"]))
        elif stale / len(A) > 0.5:
            out.append(_f("acc_stale", "warn", "Most accelerometer reads had no new sample (%d%%)" % round(100 * stale / len(A)), ev,
                          ["reading faster than the sensor's output data rate", "sensor in power-down / low ODR"],
                          ["pod_cmd 'acc regs'", "pod_cmd 'stats'"]))
        elif smag < 30 and abs(mmag - 1000) > 150:   # LIS3DH: +-40 mg offset per axis, few % sensitivity tolerance
            ratio = mmag / 1000.0
            pow2 = min((0.25, 0.5, 2.0, 4.0), key=lambda p: abs(math.log2(ratio / p)))
            note = ("ratio is ~%.2g, a power of two: typical of a full-scale range that doesn't match the "
                    "sensitivity (mg/digit) used for conversion" % pow2) if abs(math.log2(ratio / pow2)) < 0.15 else ""
            out.append(_f("acc_gravity", "warn",
                          "Board is at rest but measures %.0f mg, not 1000 mg (ratio %.2f)" % (mmag, ratio),
                          ev | {"ratio_to_1g": round(ratio, 3), "note": note},
                          ["full-scale setting (CTRL_REG4 FS bits) changed without changing mg/digit",
                           "wrong sensor driver constants", "sensor damaged (rare)"],
                          ["pod_cmd 'acc regs' (FS = bits 5:4 of ctrl4; mg_per_digit)", "src_search 'MG_PER_DIGIT|CTRL4'",
                           "src_log / src_diff"]))
    return out


def check_rates(s):
    out = []
    H = _window(s["H"], s["now"], 10)
    if len(H) >= 2:
        d_drop = H[-1].get("tx_drop", 0) - H[0].get("tx_drop", 0)
        if d_drop > 0:
            span = max(H[-1]["ts"] - H[0]["ts"], 1e-3)
            acc_hz = H[-1].get("acc_hz")
            A = _window(s["A"], s["now"], 5)
            alen = statistics.fmean(a.get("_len", 38) + 2 for a in A) if A else 40
            out.append(_f("tx_overrun", "warn",
                          "Telemetry exceeds the UART: %d lines dropped in %.0f s" % (d_drop, span),
                          {"dropped_lines_per_s": round(d_drop / span, 1), "uart_capacity_bytes_per_s": UART_BYTES_PER_S,
                           "observed_bytes_per_s": s["link"].get("bytes_per_s"), "acc_hz_requested": acc_hz,
                           "avg_A_line_bytes": round(alen, 1),
                           "A_bytes_per_s_requested": round(alen * acc_hz) if acc_hz else None,
                           "A_lines_per_s_received": round(len(A) / 5.0, 1),
                           "D_lines_per_s_received": round(len(_window(s["D"], s["now"], 5)) / 5.0, 1),
                           "D_lines_per_s_expected": 10},
                          ["a stream rate was raised past what 115200 baud can carry",
                           "the host stopped reading (unlikely: bytes still arrive)"],
                          ["pod_cmd 'stats'", "pod_cmd 'rate acc 50' (runtime fix)", "src_search 'STREAM_HZ'"]))
    H = _window(s["H"], s["now"], 25)     # a slow MCU clock also slows the health rate; use a wide window
    for i in range(len(H) - 1, 0, -1):    # only the current run: MCU time restarts at every reset/reflash
        if H[i]["t"] <= H[i - 1]["t"]:
            H = H[i:]
            break
    if len(H) >= 4:
        dt_mcu = H[-1]["t"] - H[0]["t"]
        dt_host = H[-1]["ts"] - H[0]["ts"]
        # median of record-to-record ratios: robust to one late/batched host timestamp
        pairs = [((b["t"] - a["t"]) / 1000.0) / (b["ts"] - a["ts"]) for a, b in zip(H, H[1:]) if b["ts"] - a["ts"] > 0.2]
        if dt_host > 3 and dt_mcu > 0 and pairs:
            ratio = statistics.median(pairs)
            if abs(ratio - 1) > 0.05:
                out.append(_f("clock_skew", "critical",
                              "The MCU's millisecond counter runs at %.2fx real time" % ratio,
                              {"mcu_ms": dt_mcu, "host_s": round(dt_host, 2), "ratio": round(ratio, 3),
                               "firmware_clk_hz": H[-1].get("clk")},
                              ["SysTick is programmed from SystemCoreClock; if that variable disagrees with the real "
                               "clock tree, every derived rate is off by the same factor (UART baud, timers, SysTick)"],
                              ["chip_clock (decodes the RCC registers and compares with SystemCoreClock)"]))
    return out


def check_ultrasonic(s):
    out = []
    D = _window(s["D"], s["now"], 4)
    if len(D) < 10:
        return out
    no_echo = [d for d in D if d.get("st") == "no_echo"]
    ok = [d for d in D if d.get("st") == "ok"]
    frac = len(no_echo) / len(D)
    if frac > 0.8:
        out.append(_f("us_no_echo", "warn", "Ultrasonic gets no echo pulse at all (%d%% of pings)" % round(100 * frac),
                      {"n": len(D), "no_echo_fraction": round(frac, 2)},
                      ["ECHO or TRIG wire off, or the sensor unpowered (5 V)", "sensor dead"],
                      ["pod_cmd 'us check' (measures the ECHO voltage at D3)", "notify_human to check wiring"]))
    elif 0.05 < frac <= 0.8:
        A = _window(s["A"], s["now"], 4)
        mags = [math.sqrt(a["x"] ** 2 + a["y"] ** 2 + a["z"] ** 2) for a in A]
        moving = _std(mags) > 40 if len(mags) > 10 else None
        out.append(_f("us_intermittent", "warn",
                      "Ultrasonic drops out intermittently (%d%% no_echo)%s" %
                      (round(100 * frac), ", while the board is moving" if moving else ""),
                      {"n": len(D), "no_echo_fraction": round(frac, 2), "accel_mag_std_mg": round(_std(mags), 1) if mags else None},
                      ["loose ECHO/TRIG/VCC jumper that opens when the board moves" if moving else
                       "marginal wiring or a target at the edge of the beam",
                       "soft or angled target that doesn't reflect"],
                      ["pod_cmd 'us check'", "notify_human: reseat the HC-SR04 wires (ECHO -> D3 divider, TRIG -> A0)"]))
    # The firmware reports both the echo time and the distance it computed from it, so the two can be
    # checked against each other: distance = echo_us * 0.343 / 2 (speed of sound at 20 C, out and back).
    # A mismatch is a firmware fault, whatever is in front of the sensor. Both fields feed safety
    # decisions (the arm's start gate reads mm), so a wrong distance is critical in either direction.
    timed = [d for d in D if d.get("st") in ("ok", "out_of_range") and (d.get("us") or 0) > 0 and "mm" in d]
    if len(timed) >= 10:
        def expect(d):
            return d["us"] * US_TO_MM

        bad = [d for d in timed if abs(d["mm"] - expect(d)) > max(10.0, 0.05 * expect(d))]
        if len(bad) / len(timed) > 0.5:
            ex = bad[-3:]
            out.append(_f("us_mismatch", "critical",
                          "Ultrasonic distance disagrees with its own echo time: echo %d us means ~%d mm, "
                          "but the firmware reports %d mm (%d of %d samples wrong)"
                          % (ex[-1]["us"], round(expect(ex[-1])), ex[-1]["mm"], len(bad), len(timed)),
                          {"n": len(timed), "mismatched": len(bad), "formula": "mm = us * 0.343 / 2",
                           "samples": [{"us": d["us"], "mm_reported": d["mm"], "mm_from_echo": round(expect(d)),
                                        "st": d.get("st")} for d in ex],
                           "mm_reported_range": [min(d["mm"] for d in bad), max(d["mm"] for d in bad)]},
                          ["the firmware computes the distance from the echo time wrongly",
                           "not a real obstruction: the echo time is the physical measurement, and an object at the "
                           "reported distance would return a much shorter echo"],
                          ["telemetry_recent kind=D (compare us and mm in each sample)",
                           "src_log / src_diff (what changed since the known-good release?)"]))
    if len(ok) >= 10:
        mm = [d["mm"] for d in ok]
        # Covered sensor: short distance AND a short echo. A short distance with a long echo is us_mismatch.
        echo_short = statistics.fmean(d.get("us", 0) for d in ok) < 300
        if statistics.fmean(mm) < 30 and _std(mm) < 3 and echo_short:
            out.append(_f("us_blocked", "info", "Ultrasonic reads a constant %.0f mm: something is covering it" % statistics.fmean(mm),
                          {"mean_mm": round(statistics.fmean(mm), 1), "std_mm": round(_std(mm), 2), "n": len(ok)},
                          ["object or hand right against the sensor (its minimum range is ~20 mm)"],
                          ["wait for it to clear; treat distance as untrusted meanwhile"]))
    return out


def check_health(s):
    out = []
    H = s["H"]
    if not H:
        return out
    h = H[-1]
    if h.get("loop_us", 0) > 20000:
        out.append(_f("loop_slow", "warn", "Main loop took %d us at worst (budget ~20 ms)" % h["loop_us"],
                      {"loop_us": h["loop_us"]}, ["blocking I/O in the loop (I2C timeouts, ADC, full TX ring)"],
                      ["pod_cmd 'stats'"]))
    if h.get("stk_free", 9999) < 256:
        out.append(_f("stack_low", "warn", "Only %d bytes of stack never used" % h["stk_free"], {"stk_free": h["stk_free"]},
                      ["large local arrays or deep call chains; the stack grows down into .bss/.noinit"], []))
    v = h.get("vdda", 3300)
    if v and not 3000 <= v <= 3600:
        out.append(_f("supply", "warn", "MCU supply VDDA = %d mV (expect ~3300)" % v, {"vdda_mv": v},
                      ["weak USB supply, regulator problem, or excessive load"], ["notify_human"]))
    return out


RULES = [check_link, check_resets, check_accel, check_rates, check_ultrasonic, check_health]


def run(snapshot):
    findings = []
    for rule in RULES:
        try:
            findings += rule(snapshot)
        except Exception as e:  # a broken rule must not hide the others
            findings.append(_f("detector_error", "info", "rule %s failed: %r" % (rule.__name__, e)))
    order = {"critical": 0, "warn": 1, "info": 2}
    return sorted(findings, key=lambda f: order.get(f["severity"], 3))
