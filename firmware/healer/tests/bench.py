"""Bench regression tests for the six demo scenarios, driven through the same tools an agent uses.

Needs the board attached, podd running, and `demo/scenario setup` + `build-images` done. Run from firmware/:
    python3 healer/tests/bench.py A1 A2 A3 A4 A5      # detection (and the 01 fix)
    python3 healer/tests/bench.py B1 B2              # durable fixes for 03 and 04
    python3 healer/tests/bench.py C1 C2 C3           # rollback rule, recovery after replug, halt sampling
Each test resets the board first. Results: PASS/FAIL per check, and healer/var/tests/results-<time>.json.
This scripts the known answers; it is a regression test of the infrastructure, not an agent evaluation.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

FW = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FW / "healer"))
from healer import tools  # noqa: E402

RESULTS = []


def T(name, args=None):
    r = tools.call(name, args or {})
    return r


def scen(*a):
    p = subprocess.run([str(FW / "demo" / "scenario"), *a], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(p.stdout + p.stderr)
    return p.stdout


def kinds(tr):
    return [f["kind"] for f in tr.get("findings", [])]


def find(tr, kind):
    return next((f for f in tr.get("findings", []) if f["kind"] == kind), None)


def check(test, label, cond, detail=""):
    RESULTS.append((test, label, bool(cond)))
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", label, detail), flush=True)
    return cond


def wait_for(fn, timeout, step=1.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


def flash_fix(test, reason):
    b = T("fw_build")
    check(test, "fw_build ok", b.get("ok"), "%s warnings=%s" % (b.get("image", {}).get("build_id"), b.get("warnings")))
    f = T("fw_flash", {"image": "latest", "reason": reason})
    failed = [c["check"] for c in f.get("checks", []) if not c["ok"]]
    check(test, "fw_flash verified", f.get("ok"), "result=%s failed_checks=%s" % (f.get("result"), failed))
    return f


# --------------------------------------------------------------------------- A: rerun on rebuilt images

def A1():
    scen("reset"); scen("start", "01")
    tr = wait_for(lambda: (lambda t: t if find(t, "safe_mode") else None)(T("triage")), 20)
    check("A1", "crash/reboot_loop/safe_mode found", {"crash", "reboot_loop", "safe_mode"} <= set(kinds(tr)), kinds(tr))
    c = T("pod_cmd", {"command": "crash"})["responses"]
    rec = {k: v for d in c for k, v in d.items()}
    check("A1", "crash record r3 odd", int(rec["r3"], 16) & 1, "pc=%s r3=%s build_crc=%s" % (rec["pc"], rec["r3"], rec["build_crc"]))
    s = T("fw_symbolize", {"address": rec["pc"], "image": rec["build_crc"]})
    loc = s["frames"][0]["location"] if s.get("frames") else None
    check("A1", "symbolized to frame.c:63", loc == "src/frame.c:63", loc)
    check("A1", "disassembly shows str", "str\tr1, [r3, #0]" in s.get("disassembly", ""))
    old = ("    /* Direct stores: one instruction per field instead of byte shifts. */\n    buf[0] = FRAME_TYPE;\n"
           "    *(uint32_t *)(buf + 1) = s->t_ms;\n    *(int16_t *)(buf + 5) = (int16_t)(s->x0 + mx);\n"
           "    *(int16_t *)(buf + 7) = (int16_t)(s->y0 + my);\n    *(int16_t *)(buf + 9) = (int16_t)(s->z0 + mz);\n"
           "    *(uint16_t *)(buf + 11) = (uint16_t)isqrt(var);\n    *(uint16_t *)(buf + 13) = s->n;")
    new = ("    /* Fields sit at odd offsets and Cortex-M0 faults on unaligned word/halfword\n"
           "     * stores, so copy them byte-wise (memcpy is alignment-safe). */\n    uint32_t t = s->t_ms;\n"
           "    int16_t mean[3] = { (int16_t)(s->x0 + mx), (int16_t)(s->y0 + my), (int16_t)(s->z0 + mz) };\n"
           "    uint16_t rms = (uint16_t)isqrt(var), cnt = s->n;\n    buf[0] = FRAME_TYPE;\n    memcpy(buf + 1, &t, 4);\n"
           "    memcpy(buf + 5, mean, 6);\n    memcpy(buf + 11, &rms, 2);\n    memcpy(buf + 13, &cnt, 2);")
    e1 = T("src_edit", {"path": "src/frame.c", "old": old, "new": new})
    e2 = T("src_edit", {"path": "src/frame.c", "old": '#include "frame.h"', "new": '#include <string.h>\n#include "frame.h"'})
    check("A1", "src_edit x2", e1.get("replacements") == 1 and e2.get("replacements") == 1)
    T("src_commit", {"message": "frame: alignment-safe packing (memcpy); Cortex-M0 faults on unaligned stores"})
    f = flash_fix("A1", "fix unaligned stores")
    check("A1", "triage_after empty", f.get("triage_after") == [], [x["kind"] for x in f.get("triage_after", [])])
    time.sleep(8)
    st = T("telemetry_stats", {"seconds": 6})
    fr = st.get("radio_frame_latest") or {}
    check("A1", "radio frames decode", st.get("frames_n", 0) >= 4 and fr.get("crc_ok"), "frames_n=%s frame=%s" % (st.get("frames_n"), fr))


def A2():
    try:
        _A2()
    finally:
        T("link_set_baud", {"baud": 115200})


def _A2():
    scen("reset"); scen("start", "02")
    tr = wait_for(lambda: (lambda t: t if find(t, "link_garbled") else None)(T("triage")), 15)
    check("A2", "link_garbled", find(tr, "link_garbled"), kinds(tr))
    b = T("link_baud_scan")
    check("A2", "baud scan best 57600", b.get("best_baud") == 57600, b.get("note"))
    c = T("chip_clock")
    check("A2", "chip_clock mismatch 96 vs 48 MHz", c.get("firmware_SystemCoreClock_hz") == 96000000 and c["real_clock"]["sysclk_hz"] == 48e6,
          "uart_actual=%s systick=%s tim3=%s" % (c.get("uart_actual_baud"), c.get("systick_period_ms_actual"), c.get("tim3_tick_us_actual")))
    s = T("chip_status")
    check("A2", "uptime ratio ~0.5", abs(s.get("uptime_ratio_to_real_time", 0) - 0.5) < 0.03, s.get("uptime_ratio_to_real_time"))
    T("link_set_baud", {"baud": 57600})
    tr = wait_for(lambda: (lambda t: t if find(t, "clock_skew") else None)(T("triage")), 20)
    f = find(tr, "clock_skew")
    check("A2", "clock_skew at 57600", f, f and f["summary"])
    h = T("telemetry_recent", {"kind": "H", "seconds": 5, "limit": 1})["rows"]
    check("A2", "health clk=96000000", h and h[-1].get("clk") == 96000000, h[-1].get("clk") if h else None)
    d = T("src_diff", {"rev_a": "v1.0"})["diff"]
    check("A2", "diff shows PLLSRC change", "RCC_CFGR_PLLSRC_HSI_DIV2" in d)
    T("link_set_baud", {"baud": 115200})


def A3():
    scen("reset"); scen("start", "03")
    time.sleep(5)
    scen("trigger", "03")
    tr = wait_for(lambda: (lambda t: t if find(t, "acc_frozen") else None)(T("triage")), 15)
    f = find(tr, "acc_frozen")
    check("A3", "acc_frozen", f, f and f["evidence"])
    r = {k: v for d in T("pod_cmd", {"command": "acc regs"})["responses"] for k, v in d.items()}
    check("A3", "ctrl1=0x07 who=0x33", r.get("ctrl1") == "0x07" and r.get("who_am_i") == "0x33", "ctrl1=%s ctrl4=%s" % (r.get("ctrl1"), r.get("ctrl4")))
    T("pod_cmd", {"command": "acc init"})
    tr = wait_for(lambda: (lambda t: t if not find(t, "acc_frozen") else None)(T("triage")), 15)
    check("A3", "clears after acc init", tr is not None and not find(tr, "acc_frozen"), kinds(tr or {}))


def A4():
    scen("reset"); scen("start", "04")
    tr = wait_for(lambda: (lambda t: t if find(t, "tx_overrun") else None)(T("triage")), 20)
    f = find(tr, "tx_overrun")
    check("A4", "tx_overrun", f, f and {k: f["evidence"].get(k) for k in ("observed_bytes_per_s", "acc_hz_requested", "D_lines_per_s_received")})
    T("pod_cmd", {"command": "rate acc 50"})
    time.sleep(13)
    tr = T("triage")
    check("A4", "clears after rate acc 50", not find(tr, "tx_overrun"), kinds(tr))


def A5():
    scen("reset"); scen("start", "05")
    tr = wait_for(lambda: (lambda t: t if find(t, "acc_gravity") else None)(T("triage")), 15)
    f = find(tr, "acc_gravity")
    check("A5", "acc_gravity", f, f and "%s | %s" % (f["summary"], f["evidence"].get("note")))
    r = {k: v for d in T("pod_cmd", {"command": "acc regs"})["responses"] for k, v in d.items()}
    check("A5", "ctrl4=0x98 mg_per_digit=1", r.get("ctrl4") == "0x98" and r.get("mg_per_digit") == 1)


# --------------------------------------------------------------------------- B: durable fixes

def B1():
    scen("reset"); scen("start", "03")
    d = T("src_diff", {"rev_a": "v1.0"})["diff"]
    check("B1", "diff shows acc_check_task removed", "-static void acc_check_task" in d)
    T("src_restore", {"rev": "v1.0"})
    T("src_commit", {"message": "acc: restore once-a-second config readback; a silent LIS3DH reset froze the data"})
    flash_fix("B1", "restore acc config readback")
    time.sleep(3)
    t0 = time.time()
    scen("trigger", "03")

    def healed():
        rows = T("events", {"minutes": 1, "kinds": "acc_cfg_lost,acc_init"})["rows"]
        rows = [r for r in rows if r["ts"] >= t0]
        lost = [r for r in rows if r.get("ev") == "acc_cfg_lost"]
        ok = [r for r in rows if r.get("ev") == "acc_init" and r.get("why") == "config_lost" and r.get("result") == "ok"]
        return (lost, ok) if lost and ok else None
    h = wait_for(healed, 8)
    check("B1", "self-heals after glitch", h, h and "ctrl1=%s" % h[0][0].get("ctrl1"))
    time.sleep(4)
    tr = T("triage")
    check("B1", "triage clean", not tr["findings"], kinds(tr))


def B2():
    scen("reset"); scen("start", "04")
    e = T("src_edit", {"path": "src/app.h", "old": "ACC_STREAM_HZ   400u", "new": "ACC_STREAM_HZ   100u"})
    check("B2", "src_edit", e.get("replacements") == 1, e.get("error"))
    T("src_commit", {"message": "acc: stream at 100 Hz (400 Hz exceeded the 11.5 kB/s UART budget)"})
    f = flash_fix("B2", "fit telemetry into UART budget")
    check("B2", "triage_after empty", f.get("triage_after") == [], [x["kind"] for x in f.get("triage_after", [])])
    time.sleep(6)
    st = T("telemetry_stats", {"seconds": 5})
    h = st["health_latest"]
    check("B2", "accel ~100 Hz, distance ~10 Hz",
          90 <= st["accel"]["rate_hz"] <= 105 and st["distance"]["rate_hz"] >= 9.5,
          "acc=%s Hz dist=%s Hz bytes/s=%s tx_drop=%s loop_us=%s" % (st["accel"]["rate_hz"], st["distance"]["rate_hz"],
                                                                     st["link"]["bytes_per_s"], h.get("tx_drop"), h.get("loop_us")))
    check("B2", "no dropped lines", h.get("tx_drop") == 0, h.get("tx_drop"))


# --------------------------------------------------------------------------- C: new or changed code

def C1():
    scen("reset"); scen("start", "05")      # unverified v1.5 running
    time.sleep(3)
    bad = str(FW / "demo" / "bad-images" / "02-clock-hsi2" / "image" / "pod.elf")
    from healer import images
    meta = images.register(bad, origin="test")
    if meta["status"] == "bad":
        images.set_status(meta["crc"], "candidate", "re-test of rollback rule")
    f = T("fw_flash", {"image": meta["crc"], "reason": "C1 rollback-rule test"})
    check("C1", "verification fails", f.get("ok") is False and f.get("result") == "failed")
    rb = f.get("rollback", {})
    ver = (rb.get("image") or {}).get("version")
    check("C1", "rolled back to known-good v1.0 (not v1.5)", rb.get("ok") and ver == "v1.0", "rollback version=%s" % ver)


def C2():
    scen("reset"); scen("start", "06")
    time.sleep(4)
    scen("trigger", "06")
    time.sleep(13)
    T("src_restore", {"rev": "v1.0"})
    T("src_commit", {"message": "i2c: restore error/timeout checks in wait_isr"})
    f = flash_fix("C2", "restore i2c timeouts")
    check("C2", "acc_error after fix (sensor still 'gone')", [x["kind"] for x in f.get("triage_after", [])] == ["acc_error"],
          [x["kind"] for x in f.get("triage_after", [])])
    t0 = time.time()
    print("  " + scen("clear").strip())

    def recovered():
        rows = [r for r in T("events", {"minutes": 1, "kinds": "acc_init"})["rows"] if r["ts"] >= t0]
        return next((r for r in rows if r.get("why") == "retry" and r.get("result") == "ok"), None)
    r = wait_for(recovered, 6, 0.5)
    check("C2", "acc_init why=retry result=ok after clear", r, r and round(r["ts"] - t0, 1))
    tr = wait_for(lambda: (lambda t: t if not t["findings"] else None)(T("triage")), 10)
    check("C2", "triage clean after recovery", tr is not None, kinds(tr or T("triage")))


def C3():
    scen("reset"); scen("start", "06")
    time.sleep(4)
    scen("trigger", "06")
    time.sleep(0.5)
    hs = T("chip_halt_sample", {"samples": 4})
    funcs = [s["pc_at"][0]["function"] + "@" + s["pc_at"][0]["location"] for s in hs.get("samples", []) if s.get("pc_at")]
    check("C3", "halt samples land in wait_isr", funcs and all(f.startswith("wait_isr") for f in funcs), funcs or hs)


if __name__ == "__main__":
    for name in sys.argv[1:]:
        print("== %s" % name, flush=True)
        try:
            globals()[name]()
        except Exception as e:
            check(name, "exception", False, repr(e))
    print("\nSUMMARY")
    for t in sys.argv[1:]:
        rs = [r for r in RESULTS if r[0] == t]
        print("  %s: %s (%d/%d)" % (t, "PASS" if rs and all(r[2] for r in rs) else "FAIL", sum(r[2] for r in rs), len(rs)))
    out = FW / "healer" / "var" / "tests"
    out.mkdir(parents=True, exist_ok=True)
    json.dump(RESULTS, open(out / ("results-%d.json" % time.time()), "w"))
