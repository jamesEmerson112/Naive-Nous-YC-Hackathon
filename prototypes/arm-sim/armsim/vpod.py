"""A virtual sensor pod: the sim's wrist rangefinder, spoken in the STM32 pod's own line format on a
pseudo-terminal, so Olir's real `podd` reads it exactly as it reads the board (docs/firmware-apis.md §2).

    pod = VirtualPod(arm); pod.start()
    POD_PORT=<pod.port> HEALER_VAR=... firmware/healer/bin/podd      # (the arm host starts it)

What it streams (rates as the firmware: firmware/pod/src/app.h, main.c):
  D t mm us st   10 Hz   from arm.sensor_m(); echo jitter from the real sample (us 285-286, std 0.4)
  A t x y z st   50 Hz   a still board: gravity plus noise
  H ...           1 Hz   the health fields detect.py reads
  E ev=boot ...          at every (re)boot, with the build and CRC the edge bridge reports
It answers podd's `@<id> <command>` lines with R lines: help, info, health, stats, us on|off|check,
stream a|d on|off, reset.

Faults (sim only, set from Python):
  frozen   the D values never change: a stuck sensor read (cleared by `us off` then `us on`)
  image02  bad image 02 (firmware/demo/bad-images/02-clock-hsi2): the MCU runs on half the clock it
           believes, so its millisecond counter, its stream rates and its echo times are all halved
  no_echo  every reading is st=no_echo
`flash(image)` reboots into "good" or "02", like an SWD flash would.
"""
import os
import random
import select
import threading
import time
import tty
import zlib

BUILDS = {
    "good": "pod v1.1-sim 2026-11-07T22:00:00Z",
    "02": "pod v1.2-clock-hsi2-sim 2026-11-07T22:30:00Z",
    # firmware/demo/bad-images/07-us-dist-16bit: the distance math is truncated to 16 bits, so the
    # reported mm disagrees with its own echo time (podd's us_mismatch); the bench demo's fault (#54)
    "07": "pod v1.7-us-dist-16bit-sim 2026-11-07T23:00:00Z",
}
US_JITTER = 0.4          # µs, the real sample's spread (firmware/ultrasonic-sample.json)


def crc_of(build):
    """The pod's image id: CRC-32 of the build-ID string (firmware/healer/healer/images.py)."""
    return "%08X" % (zlib.crc32(build.encode()) & 0xFFFFFFFF)


def d_line(t, mm, us, st):
    return f"D t={t} mm={mm} us={us} st={st}"


class VirtualPod:
    def __init__(self, arm, *, seed=0):
        self.arm = arm
        self.rng = random.Random(seed)
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)                 # raw before podd opens it: no echo, no line editing
        self.port = os.ttyname(self.slave)
        self.lock = threading.Lock()
        self.running = False
        self.image = "good"
        self.faults = set()
        self.us_on, self.streams = True, {"a": True, "d": True}
        self.boots = 0
        self._last_d = None
        self._inbuf = b""
        self.lines_out = 0

    # ---- control (Python side) ---------------------------------------------------------------
    def set_fault(self, name, on=True):
        with self.lock:
            (self.faults.add if on else self.faults.discard)(name)
            if name == "frozen" and on:
                self._last_d = self._last_d or self._read_d()

    def flash(self, image):
        """Reboot into another image, the way a verified SWD flash ends (reset by the probe: rst=pin)."""
        with self.lock:
            self.image = image
            self._boot("pin")

    @property
    def build(self):
        return BUILDS[self.image]

    @property
    def crc(self):
        return crc_of(self.build)

    # ---- the firmware ------------------------------------------------------------------------
    def start(self):
        self.running = True
        with self.lock:
            self._boot("por")
        threading.Thread(target=self._loop, name="vpod", daemon=True).start()
        return self

    def stop(self):
        self.running = False

    def _clock_scale(self):
        return 0.5 if self.image == "02" else 1.0

    def _ms(self):
        """Device `t`: MCU milliseconds since boot (runs at half speed on image 02)."""
        return int((time.monotonic() - self.boot_mono) * 1000 * self._clock_scale())

    def _boot(self, rst):
        self.boots += 1
        self.boot_mono = time.monotonic()
        self.next_a, self.next_d, self.next_h = 0, 0, 0
        self.us_on, self.streams = True, {"a": True, "d": True}
        self.faults.discard("frozen")
        self._last_d = None
        self._write("# SO-101 sensor pod (virtual, armsim.vpod)")
        self._write(f"# build {self.build}")
        self._write("# D t mm us st | A t x y z st | H health 1 Hz | E events | R replies")
        self._write(f'E t=0 ev=boot build="{self.build}" crc=0x{self.crc} rst={rst} boots={self.boots} streak=0 '
                    f"crashes=0 wdg=0 last_cp=none mode=normal new_build={int(self.boots > 1)} clk=48000000")

    def _read_d(self):
        m = self.arm.sensor_m()
        if m is None or "no_echo" in self.faults:
            return 0, 0, "no_echo"
        us = m * 1000.0 * 2000.0 / 343.0 + self.rng.gauss(0.0, US_JITTER)
        us = int(round(us * self._clock_scale()))          # a half-speed timer counts half the echo
        if self.image == "07":
            mm = ((us * 343 + 1000) & 0xFFFF) // 2000        # v1.7: the product kept in 16 bits
            return mm, us, ("ok" if 116 <= us <= 23300 else "out_of_range")
        mm = (us * 343 + 1000) // 2000
        return mm, us, ("ok" if 116 <= us <= 23300 else "out_of_range")

    def _health(self, t):
        us = "off" if not self.us_on else (self._last_d[2] if self._last_d else "ok")
        return (f"H t={t} mode=normal boots={self.boots} streak=0 crashes=0 wdg=0 rst=none loop_us={self.rng.randint(780, 920)} "
                f"stk_free=1184 vdda={3300 + self.rng.randint(-4, 4)} temp={31 + self.rng.random():.1f} acc=ok "
                f"acc_errs=0 acc_cfg_lost=0 i2c_err=0 us={us} tx_drop=0 rx_ovr=0 clk=48000000 acc_hz=50")

    def _loop(self):
        while self.running:
            with self.lock:
                t = self._ms()
                if t >= self.next_a:
                    self.next_a = t + 20
                    if self.streams["a"]:
                        g = self.rng.gauss
                        self._write(f"A t={t} x={int(round(-12 + g(0, 2.5)))} y={int(round(8 + g(0, 2.5)))} "
                                    f"z={int(round(1001 + g(0, 2.5)))} st=ok")
                if t >= self.next_d:
                    self.next_d = t + 100
                    if self.us_on and self.streams["d"]:
                        if "frozen" in self.faults and self._last_d:
                            mm, us, st = self._last_d              # stale: the same reading, forever
                        else:
                            mm, us, st = self._last_d = self._read_d()
                        self._write(d_line(t, mm, us, st))
                if t >= self.next_h:
                    self.next_h = t + 1000
                    self._write(self._health(t))
            self._serve_commands()
            time.sleep(0.004)

    # ---- serial ------------------------------------------------------------------------------
    def _write(self, line):
        try:
            os.write(self.master, (line + "\r\n").encode("ascii"))
            self.lines_out += 1
        except OSError:
            pass                                   # nobody has the port open yet: bytes are dropped

    def _serve_commands(self):
        try:
            r, _, _ = select.select([self.master], [], [], 0)
            if not r:
                return
            self._inbuf += os.read(self.master, 4096)
        except OSError:
            return
        *lines, self._inbuf = self._inbuf.split(b"\n")
        for raw in lines:
            text = raw.decode("ascii", "replace").strip()
            if text.startswith("@") and " " in text:
                cid, cmd = text[1:].split(" ", 1)
                if cid.isdigit():
                    with self.lock:
                        self._command(int(cid), cmd.strip())

    def _reply(self, cid, fields="", ok=True, err=None):
        """Fields on their own R line, then the end line, as the firmware does (podd keeps only the
        fields lines as `responses`)."""
        if fields:
            self._write(f"R id={cid} {fields}")
        self._write(f"R id={cid} " + ("end=1 ok=1" if ok else f'end=1 ok=0 err="{err}"'))

    def _command(self, cid, cmd):
        words = cmd.split()
        if cmd == "help":
            return self._reply(cid, 'cmds="help info health stats us on|off|check stream a|d on|off reset" sensors="d a"')
        if cmd == "info":
            return self._reply(cid, f'build="{self.build}" crc=0x{self.crc} mode=normal boots={self.boots} up={self._ms()}')
        if cmd == "health":
            return self._write(self._health(self._ms()))          # an H line, not an R (cmd.c:392)
        if cmd == "stats":
            return self._reply(cid, f"tx_drop_lines=0 rx_overrun=0 acc_period_ms=20 us_period_ms=100 stk_free=1184")
        if words[:1] == ["us"] and len(words) == 2:
            if words[1] == "off":
                self.us_on = False
                return self._reply(cid)
            if words[1] == "on":
                self.us_on = True
                self.faults.discard("frozen")      # the sensor task restarts: a stuck read clears
                self._last_d = None
                return self._reply(cid)
            if words[1] == "check":
                return self._reply(cid, "result=ok echo_mv=3280 vdda_mv=3300 saturated=0")
        if words[:1] == ["stream"] and len(words) == 3 and words[1] in self.streams and words[2] in ("on", "off"):
            self.streams[words[1]] = words[2] == "on"
            return self._reply(cid)
        if cmd == "reset":
            self._reply(cid)
            return self._boot("sft")
        return self._reply(cid, ok=False, err="unknown command; try help")
