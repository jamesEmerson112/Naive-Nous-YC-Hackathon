"""A simulated bench for the offline tests and heal_test.py --sim: the pod runs v1.0, v1.7 or v1.8, and everything
else follows from that.

The pod runs on its own clock: 10 distance pings a second, each one from the scene at that instant (a background
at distance_mm, plus any object planned in front of it, like a hand) through the real firmware arithmetic: the
expressions from hcsr04.c, the v1.7 bug.patch and v1.8's near-object hold, evaluated with C's 32-bit unsigned
semantics (test_bad_image.py checks both against the C, compiled with gcc). Triage goes through the real detect.py
rules, and the arm's job through the real arm/safe_replay.py main() with only its sensor and arm swapped. A job
runs on a simulated clock that the pod follows ping by ping, so an 8 s reflex or a 20-loop job takes no wall time.
"""
import collections
import contextlib
import functools
import io
import json
import random
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "arm", REPO / "firmware" / "harness", REPO / "firmware" / "healer"):
    sys.path.insert(0, str(p))

import arm_status  # noqa: E402
import safe_replay  # noqa: E402
from healer import detect  # noqa: E402

REST = [18.0, -108.0, 95.3, -101.7, 12.9, 1.3]       # the real arm's folded pose (arm_status.py state, Nov 7)
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
POD_SRC = REPO / "firmware" / "pod" / "src"
BAD_IMAGES = REPO / "firmware" / "demo" / "bad-images"
RELEASE_DIRS = {"v1.7": BAD_IMAGES / "07-us-dist-16bit", "v1.8": BAD_IMAGES / "08-us-near-hold"}
SCENARIOS = {"07": "v1.7", "08": "v1.8"}
BAD_DIR = RELEASE_DIRS["v1.7"]
IMAGES = {"v1.0": {"crc": "BAADB704", "version": "v1.0", "status": "known-good"},
          "v1.7": {"crc": "A12774C3", "version": "v1.7", "status": "candidate"},
          "v1.8": {"crc": "8FB0FA2D", "version": "v1.8", "status": "candidate"}}
GOOD, BAD = IMAGES["v1.0"], IMAGES["v1.7"]
US_PER_MM = 2 / 0.343
PING_S = 0.1                                          # the pod pings the HC-SR04 at 10 Hz (main.c, US_STREAM_HZ)
HOLD_NEAR_MM, HOLD_MS = 200, 500                      # v1.8 hcsr04.h


class FakeClock:
    """Simulated time for safe_replay: sleep() advances it, so an 8 s reflex or a 13 s wave takes no wall time."""

    def __init__(self, t=None):
        self.t = time.time() if t is None else t

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


class FakeArm:
    """Follows every command exactly. history keeps every commanded pose, in order."""

    def __init__(self, pose=None):
        self.pose = list(pose or REST)
        self.moves = 0
        self.history = []

    def get_observation(self):
        return {j + ".pos": v for j, v in zip(JOINTS, self.pose)}

    def send_action(self, action):
        self.pose = [float(action[j + ".pos"]) for j in JOINTS]
        self.moves += 1
        self.history.append(list(self.pose))
        return action

    def disconnect(self):
        pass


def mm_v10(us):
    return (us * 343 + 1000) // 2000                       # out->dist_mm = (us * 343u + 1000u) / 2000u;


def mm_v17(us):
    return ((us * 343 + 1000) & 0xFFFF) // 2000            # (uint16_t)(us * 343u + 1000u) / 2000u;


class NearHold:
    """v1.8's near_hold() from its bug.patch, line for line in uint32 arithmetic: it holds the nearest reading under
    HOLD_NEAR_MM and nothing ever lets go of it (HCSR04_HOLD_MS is never used). fixed=True adds the release it was
    meant to have: once nothing has been near for HOLD_MS, drop the held reading."""

    def __init__(self, fixed=False):
        self.held_mm, self.held_ms, self.fixed = 0, 0, fixed

    def __call__(self, mm, now_ms):
        if self.fixed and self.held_mm and ((now_ms - self.held_ms) & 0xFFFFFFFF) >= HOLD_MS:
            self.held_mm = 0                   # the fix: nothing near for HOLD_MS, let go
        if mm < HOLD_NEAR_MM:
            if self.held_mm == 0 or mm < self.held_mm:
                self.held_mm = mm              # something near: hold the nearest
            self.held_ms = now_ms
        return self.held_mm if self.held_mm else mm   # while something is held, report it


# The agent's own builds (fw_build in the simulator): the workspace's hcsr04.c is compiled on the host with gcc, with
# these stand-ins for the MCU headers, and driven one ping at a time through the real hcsr04_poll(): the harness sets
# the capture result the TIM3 interrupt would have left (pulse_us, pulse_done) and prints what the driver reports.
# The distance path is the agent's actual code; the hardware around it is not simulated.
FAKE_STM32_H = r"""
#include <stdint.h>
typedef struct { volatile uint32_t MODER, OTYPER, OSPEEDR, PUPDR, IDR, ODR, BSRR, LCKR, AFR[2], BRR,
                 CR1, CR2, SMCR, DIER, SR, EGR, CCMR1, CCMR2, CCER, CNT, PSC, ARR, CCR1, CCR2, CCR3, CCR4,
                 AHBENR, APB1ENR, APB2ENR, ISR, IER, CR, CFGR, CFGR1, CFGR2, SMPR, CHSELR, DR, CCR; } SIM_Periph;
static SIM_Periph sim_gpioa, sim_gpiob, sim_tim3, sim_rcc, sim_adc;
#define GPIOA (&sim_gpioa)
#define GPIOB (&sim_gpiob)
#define TIM3 (&sim_tim3)
#define RCC (&sim_rcc)
#define ADC1 (&sim_adc)
static uint32_t SystemCoreClock = 48000000u;
#define RCC_AHBENR_GPIOAEN (1u << 17)
#define RCC_AHBENR_GPIOBEN (1u << 18)
#define RCC_APB1ENR_TIM3EN (1u << 1)
#define TIM_CCMR2_CC3S_Pos 0
#define TIM_CCMR2_IC3F_Pos 4
#define TIM_CCER_CC3E (1u << 8)
#define TIM_CCER_CC3P (1u << 9)
#define TIM_CCER_CC3NP (1u << 11)
#define TIM_EGR_UG 1u
#define TIM_DIER_CC3IE (1u << 3)
#define TIM_CR1_CEN 1u
#define TIM_SR_CC3IF (1u << 3)
#define TIM_SR_CC3OF (1u << 11)
#define TIM3_IRQn 16
#define ADC_CHSELR_CHSEL8 (1u << 8)
#define ADC_CHSELR_CHSEL17 (1u << 17)
#define VREFINT_CAL 1500u
static inline void NVIC_SetPriority(int irq, int prio) { (void)irq; (void)prio; }
static inline void NVIC_EnableIRQ(int irq) { (void)irq; }
static inline void __disable_irq(void) {}
static inline void __enable_irq(void) {}
"""
FAKE_ADC_H = r"""
#include <stdint.h>
static inline void adc_init(void) {}
static inline uint32_t adc_read(uint32_t chsel) { (void)chsel; return 2048u; }
"""
PING_MAIN_C = r"""
#include <stdio.h>
#include "hcsr04.c"
int main(void)
{
    unsigned long us, now;
    hcsr04_result_t r;
    while (scanf("%lu %lu", &us, &now) == 2) {
        report_busy = 0; rise_seen = 0; active = 1;     /* a measurement was started ...      */
        pulse_us = (uint32_t)us; pulse_done = 1;        /* ... and TIM3 captured its echo     */
        if (hcsr04_poll((uint32_t)now, &r))
            printf("%lu %lu %s\n", (unsigned long)r.dist_mm, (unsigned long)r.echo_us, hcsr04_status_str(r.status));
        else
            printf("0 0 busy\n");
        fflush(stdout);
    }
    return 0;
}
"""


def build_driver(files, out_dir):
    """Compile the workspace's HC-SR04 driver for the host. Returns (path of the executable or None, compiler output)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("hcsr04.c", "hcsr04.h"):
        (out_dir / name).write_text(files["src/" + name])
    (out_dir / "stm32f0xx.h").write_text(FAKE_STM32_H)
    (out_dir / "adc.h").write_text(FAKE_ADC_H)
    (out_dir / "ping.c").write_text(PING_MAIN_C)
    p = subprocess.run(["gcc", "-std=gnu11", "-O1", "-Wall", "-Wno-unused-function", "-o", str(out_dir / "ping"),
                        str(out_dir / "ping.c")], capture_output=True, text=True, cwd=out_dir)
    log = (p.stdout + p.stderr).replace(str(out_dir) + "/", "src/")
    return (out_dir / "ping" if p.returncode == 0 else None), log


class DriverProcess:
    """One boot of an agent-built image: the compiled driver, fed one ping at a time (its statics live as long as
    the process, as on the chip until the next reset)."""

    def __init__(self, exe):
        self.p = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def ping(self, us, now_ms):
        self.p.stdin.write("%d %d\n" % (us, now_ms & 0xFFFFFFFF))
        self.p.stdin.flush()
        mm, _echo, st = self.p.stdout.readline().split()
        return int(mm), st

    def close(self):
        with contextlib.suppress(Exception):
            self.p.kill()
            self.p.wait(timeout=2)


def unified(a, b):
    """git-style diff between two {path: text} dicts."""
    import difflib
    out = []
    for path in sorted(set(a) | set(b)):
        if a.get(path) != b.get(path):
            out += ["diff --git a/%s b/%s\n" % (path, path)]
            out += difflib.unified_diff((a.get(path) or "").splitlines(True), (b.get(path) or "").splitlines(True),
                                        "a/" + path, "b/" + path)
    return "".join(out)


@functools.lru_cache(maxsize=None)
def release_files(version):
    """The workspace's changed files at a release: v1.0's source with that release's bug.patch applied by git."""
    patch = RELEASE_DIRS[version] / "bug.patch"
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(POD_SRC, Path(tmp) / "src")
        subprocess.run(["git", "apply", str(patch)], cwd=tmp, check=True, capture_output=True)
        changed = [ln.split(" b/", 1)[1] for ln in patch.read_text().splitlines() if ln.startswith("diff --git")]
        return {f: (Path(tmp) / f).read_text() for f in changed}


@functools.lru_cache(maxsize=None)
def release_info(version):
    """(commit hash, commit message) of a release, as demo/scenario puts it in the workspace history."""
    d = RELEASE_DIRS[version]
    return (json.loads((d / "image" / "build.json").read_text())["commit"],
            json.loads((d / "scenario.json").read_text())["commit"])


class FakeSensor:
    """A fixed list of D rows, all of them always "recent" (for tests of the gate on its own)."""

    def __init__(self, rows):
        now = time.time()
        self.rows = [(now - 0.1 * (len(rows) - i), r) for i, r in enumerate(rows)]
        self.source, self.error = "simulated pod", None

    def samples(self, seconds):
        return list(self.rows)

    def close(self):
        pass


class SimSensor:
    """safe_replay's Ultrasonic during a simulated job: every call first runs the pod, ping by ping, up to the job's
    clock, then returns the samples of the last `seconds` on that clock."""

    def __init__(self, pod, clock):
        self.pod, self.clock = pod, clock
        self.buf = collections.deque(maxlen=300)
        self.source, self.error = "simulated pod", None

    def samples(self, seconds):
        self.buf.extend(self.pod.run_until(self.clock.t))
        t0 = self.clock.t - seconds
        return [(r["ts"], r) for r in self.buf if r["ts"] >= t0]

    def close(self):
        pass


class SimPod:
    target, timeout = "sim", 180

    def __init__(self, firmware="v1.7", distance_mm=900, seed=7, runs_dir=None):
        self.distance_mm = distance_mm
        self.arm = FakeArm()                               # used when a command names an --arm-port
        self.runs_dir = runs_dir
        self.rng = random.Random(seed)
        self.t = time.time()                               # the pod's clock (s); a job runs it ahead of the wall clock
        self.objects, self.pending = [], []                # [(t0, t1, mm)] in front of the background; next job's
        self.echo_ok = True                                # False: no echo at all (ECHO wire off, sensor unpowered)
        self.release = firmware if firmware != "v1.0" else "v1.7"     # the bad release the workspace history has
        self.proc = None                                   # an agent-built image's compiled driver, while it runs
        self._firmware, self.hold, self.boot_t = firmware, NearHold(), self.t - 300
        self.workspace = firmware                          # a rollback flashes; it doesn't touch the source
        self.tree, self.edits, self.commits = {}, {}, []   # the agent's committed and uncommitted source changes
        self.built = {}                                    # crc -> the agent's builds (fw_build), with "exe"
        self.build_dir = Path(tempfile.mkdtemp(prefix="simpod-builds-"))
        self.deployments, self.notes, self.notified, self.calls, self.ops = [], [], [], [], []

    @property
    def firmware(self):
        return self._firmware

    @firmware.setter
    def firmware(self, version):
        """Flashing (or resetting) reboots the chip: uptime and v1.8's hold start over (an agent build: its
        compiled driver starts over)."""
        if self.proc:
            self.proc.close()
        self._firmware, self.hold, self.boot_t = version, NearHold(), self.t
        self.proc = DriverProcess(self.built[version]["exe"]) if version in self.built else None
        if version in IMAGES and version != "v1.0":
            self.release = version

    def image(self, version=None):
        """Registry entry of a release ('v1.8') or an agent build (its crc)."""
        v = self.firmware if version is None else version
        return IMAGES[v] if v in IMAGES else self.built[v]

    # ---------------------------------------------------------------- the pod's physics
    def scene_mm(self, t):
        return min([self.distance_mm] + [mm for t0, t1, mm in self.objects if t0 <= t < t1])

    def ping(self, t):
        """One D line at pod time t, as the running firmware reports it."""
        now_ms = int(round((t - self.boot_t) * 1000))
        if not self.echo_ok:
            return {"type": "D", "t": now_ms, "mm": 0, "us": 0, "st": "no_echo", "ts": t}
        us = round(self.scene_mm(t) * US_PER_MM) + self.rng.randint(-12, 12)
        if self.proc:                                      # an agent build: its own compiled driver
            mm, st = self.proc.ping(us, now_ms)
        else:
            mm = (mm_v17 if self.firmware == "v1.7" else mm_v10)(us)
            if self.firmware == "v1.8":
                mm = self.hold(mm, now_ms)
            st = "ok" if 116 <= us <= 23300 else "out_of_range"
        return {"type": "D", "t": now_ms, "mm": mm, "us": us, "st": st, "ts": t}

    def run_until(self, t):
        """Run the pod ping by ping up to its time t (v1.8's hold depends on every ping); the new D lines."""
        rows = []
        while self.t + PING_S <= t + 1e-9:
            self.t += PING_S
            rows.append(self.ping(self.t))
        return rows

    def d_rows(self, n=20):
        """The next n pings. The pod never runs behind the wall clock."""
        self.run_until(time.time())
        return self.run_until(self.t + n * PING_S)

    def plan_object(self, after_s, seconds=1.0, mm=80):
        """Something (a hand) mm in front of the sensor for `seconds`, starting after_s into the next arm job."""
        self.pending.append((after_s, seconds, mm))

    def findings(self):
        rows = self.d_rows(40)
        return detect.run({"link": {"state": "up"}, "E": [], "H": [], "A": [], "D": rows, "now": self.t})

    def flash(self, image, reason):
        ref = str(image).strip()
        key = ref.upper().removeprefix("0X")
        own = [c for c in self.built if len(key) >= 4 and c.startswith(key)]
        if ref in ("known-good", "known_good", "v1.0") or (len(key) >= 4 and GOOD["crc"].startswith(key)):
            img, version = GOOD, "v1.0"
        elif own:
            img, version = self.built[own[0]], own[0]
        elif ref in ("latest",) and self.built:
            version = list(self.built)[-1]
            img = self.built[version]
        elif ref in ("latest", "deployed", self.release) or (len(key) >= 4 and IMAGES[self.release]["crc"].startswith(key)):
            img, version = IMAGES[self.release], self.release
        else:                                              # as images.resolve: nothing written to the chip
            return {"ok": False, "error": "KeyError: \"no unique image matches %r (0 hits)\"" % ref}
        self.firmware = version
        self.deployments.append({"ts": time.time(), "crc": img["crc"], "result": "ok", "reason": reason})
        return {"ok": True, "result": "ok", "image": {k: v for k, v in img.items() if k != "exe"},
                "checks": [{"check": c, "ok": True} for c in ("build_id_in_flash", "boot_event", "no_reboots",
                                                               "health_flowing", "normal_mode")],
                "triage_after": self.findings()}

    # ---------------------------------------------------------------- the workspace (the real source)
    def release_tree(self):
        """The files at the workspace's HEAD before the agent's commits: v1.0 plus the release."""
        f = {"src/" + q.name: q.read_text() for q in sorted(POD_SRC.iterdir()) if q.is_file()}
        if self.workspace != "v1.0":
            f.update(release_files(self.workspace))
        return f

    def files(self):
        return self.release_tree() | self.tree | self.edits

    def src_edit(self, path, old, new, replace_all=False):
        text = self.files().get(path)
        if text is None:
            return {"ok": False, "error": "WorkspaceError: no such file: %s" % path}
        n = text.count(old) if old else 0
        if n == 0 or (n > 1 and not replace_all):
            return {"ok": False, "error": "WorkspaceError: old text found %d times in %s (must be exactly once)" % (n, path)}
        before = self.release_tree() | self.tree
        self.edits[path] = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        return {"path": path, "replacements": n if replace_all else 1,
                "diff": unified({path: before.get(path)}, {path: self.edits[path]})}

    def src_commit(self, message):
        if not self.edits:
            return {"ok": False, "error": "WorkspaceError: nothing to commit"}
        self.tree.update(self.edits)
        self.edits = {}
        commit = "%07x" % (abs(hash((message, len(self.commits)))) & 0xFFFFFFF)
        self.commits.append((commit, message))
        return {"commit": commit, "describe": "%s-%d-g%s" % (self.workspace, len(self.commits), commit)}

    def src_diff(self, rev_a=None, rev_b=None, path=None):
        """As git diff: no revs = uncommitted changes; one rev = that rev vs the working tree; two = between them.
        v1.0 against the release with no changes of the agent's is the release's own patch, as demo/scenario made it."""
        def at(rev):
            if rev in (None, "WORKTREE"):
                return self.files()
            if str(rev).startswith("v1.0"):
                return {"src/" + q.name: q.read_text() for q in sorted(POD_SRC.iterdir()) if q.is_file()}
            return self.release_tree() | self.tree               # HEAD, the release tag, a commit
        if rev_a is None and rev_b is None:
            a, b = at("HEAD"), at(None)
        else:
            a, b = at(rev_a), at(rev_b if rev_b is not None else None)
        if str(rev_a).startswith("v1.0") and self.workspace != "v1.0" and not (self.tree or self.edits) and not path:
            return {"diff": (RELEASE_DIRS[self.workspace] / "bug.patch").read_text()}
        if path:
            a, b = {path: a.get(path)}, {path: b.get(path)}
        return {"diff": unified(a, b)}

    def fw_build(self):
        files = self.files()
        n = len(self.built) + 1
        exe, log = build_driver(files, self.build_dir / ("b%d" % n))
        if exe is None:
            return {"ok": False, "warnings": [], "errors": [ln for ln in log.splitlines() if "error" in ln][:30],
                    "log_tail": log[-3000:], "memory": {}}
        import hashlib
        crc = hashlib.sha1(json.dumps(files, sort_keys=True).encode()).hexdigest()[:8].upper()
        version = ("%s-%d-g%s" % (self.workspace, len(self.commits), self.commits[-1][0]) if self.commits
                   else self.workspace) + ("-dirty" if self.edits else "")
        self.built[crc] = {"crc": crc, "version": version, "build_id": "pod " + version, "status": "candidate",
                           "origin": "workspace", "created": time.time(), "exe": exe}
        return {"ok": True, "warnings": [ln for ln in log.splitlines() if "warning:" in ln], "errors": [],
                "memory": {"FLASH": "simulated", "RAM": "simulated"},
                "image": {k: v for k, v in self.built[crc].items() if k != "exe"}}

    def src_read(self, path, start=1, end=None):
        text = self.files().get(path)
        if text is None:
            return {"ok": False, "error": "FileNotFoundError: %s" % path}
        lines = text.splitlines()
        start, end = max(1, int(start or 1)), min(len(lines), int(end or start + 199))
        return {"path": path, "total_lines": len(lines), "start": start, "end": end,
                "text": "\n".join("%5d  %s" % (i, lines[i - 1]) for i in range(start, end + 1))}

    def src_search(self, pattern, path=None):
        import re
        rx = re.compile(pattern)
        return {"hits": [{"path": p, "line": i, "text": ln} for p, t in self.files().items() if not path or p == path
                         for i, ln in enumerate(t.splitlines(), 1) if rx.search(ln)][:50]}

    def src_log(self):
        log = "43566ce v1.0"
        if self.workspace != "v1.0":
            commit, msg = release_info(self.workspace)
            log = "%s %s %s\n%s" % (commit, self.workspace, msg, log)
        return {"log": log}

    def stats(self, seconds=10):
        rows = self.d_rows(max(10, int(float(seconds) * 10)))
        ok = [r["mm"] for r in rows if r["st"] == "ok"]
        return {"distance": {"n": len(rows), "rate_hz": 10.0, "status": {"ok": len(ok)},
                             "mm_ok": {"n": len(ok), "mean": round(sum(ok) / len(ok), 1), "min": min(ok), "max": max(ok)}
                             if ok else None},
                "accel": {"magnitude_mg": {"mean": 1076.0}}, "health_latest": self.health()}

    def health(self):
        return {"type": "H", "mode": "normal", "boots": 3, "streak": 0, "crashes": 0, "wdg": 0, "vdda": 3308,
                "acc": "ok", "us": "ok" if self.echo_ok else "no_echo", "clk": 48000000, "tx_drop": 0}

    def pod_cmd(self, command):
        command = str(command or "").strip()
        if command == "reset":                           # the chip reboots: v1.8's hold starts over
            self.firmware = self.firmware
            return {"command": command, "ok": True, "responses": [{"result": "ok"}]}
        return {"command": command, "ok": True, "responses": (
            [{"result": "ok", "echo_mv": 3195, "vdda_mv": 3308, "saturated": 0}] if command.startswith("us check") else [])}

    # ---------------------------------------------------------------- the Bench interface
    def heal(self, name, args):
        self.calls.append((name, args))
        img = self.image()
        r = {
            "system_info": lambda: {"text": "BRIEFING"},
            "triage": lambda: {"findings": self.findings()},
            "incidents": lambda: {"open": [dict(f, id=f["kind"], first_evidence=f["evidence"]) for f in self.findings()
                                           if f["severity"] in ("warn", "critical")]},
            "status": lambda: {"link": {"state": "up", "baud": 115200, "bytes_per_s": 2500}, "last_health": self.health(),
                               "open_incidents": self.heal_json("incidents")["open"]},
            "telemetry_recent": lambda: {"rows": self.d_rows(min(int(args.get("limit", 10)), 40))
                                         if args.get("kind", "D") == "D" else []},
            "telemetry_stats": lambda: self.stats(args.get("seconds", 10)),
            "events": lambda: {"rows": [], "matched": 0},
            "pod_cmd": lambda: self.pod_cmd(args.get("command")),
            "src_read": lambda: self.src_read(args.get("path", ""), args.get("start", 1), args.get("end")),
            "src_search": lambda: self.src_search(args.get("pattern", ""), args.get("path")),
            "src_log": self.src_log,
            "src_diff": lambda: self.src_diff(args.get("rev_a"), args.get("rev_b"), args.get("path")),
            "src_edit": lambda: self.src_edit(args.get("path", ""), args.get("old", ""), args.get("new", ""),
                                              args.get("replace_all", False)),
            "src_revert": lambda: (self.edits.pop(args["path"], None) if args.get("path") else self.edits.clear())
                                  or {"reverted": args.get("path") or "all"},
            "src_commit": lambda: self.src_commit(args.get("message", "")),
            "fw_build": self.fw_build,
            "fw_images": lambda: {"images": [dict(GOOD), dict(IMAGES[self.release])] +
                                            [{k: v for k, v in m.items() if k != "exe"} for m in self.built.values()],
                                  "deployments": self.deployments},
            "chip_status": lambda: {"crc": img["crc"], "build_id": img.get("build_id") or "pod " + img["version"],
                                    "running": True},
            "fw_rollback": lambda: self.flash(args.get("to", "known-good"), args.get("reason") or "rollback"),
            "fw_flash": lambda: self.flash(args["image"], args.get("reason")),
            "note": lambda: self.notes.append(args) or {"ok": True},
            "notify_human": lambda: self.notified.append(args) or {"ok": True, "recorded": args},
        }.get(name, lambda: {"ok": True})()
        return (1 if r.get("ok") is False else 0), json.dumps(r), "", 0.01

    def heal_json(self, name, args=None):
        return json.loads(self.heal(name, args or {})[1])

    def read(self, rel):
        if rel == "healer/tools.json":
            return (REPO / "firmware" / "healer" / "tools.json").read_text()
        if rel == "healer/var/deployments.jsonl":
            return "".join(json.dumps(d) + "\n" for d in self.deployments)
        return ""

    def sh(self, cmd, timeout=None):
        """The arm's commands: safe_replay.py (the job) and arm_status.py (state, report, list), run in-process
        on this pod and the simulated arm."""
        if "arm/arm_status.py" in cmd:
            return self._status(shlex.split(cmd.split("arm/arm_status.py", 1)[1]))
        assert "arm/safe_replay.py" in cmd, cmd
        argv = shlex.split(cmd.split("arm/safe_replay.py", 1)[1])
        if self.runs_dir and "--runs-dir" not in argv:
            argv += ["--runs-dir", str(self.runs_dir)]
        out = io.StringIO()
        saved = (safe_replay.Ultrasonic, safe_replay.time, safe_replay.connect_arm, sys.argv)
        self.run_until(time.time())                        # the pod has kept pinging while nobody looked
        clock = FakeClock(self.t)
        self.objects += [(clock.t + after, clock.t + after + secs, mm) for after, secs, mm in self.pending]
        self.pending = []
        safe_replay.Ultrasonic = lambda port=None: SimSensor(self, clock)
        safe_replay.time = types.SimpleNamespace(sleep=clock.sleep, time=clock.time, monotonic=clock.monotonic,
                                                 localtime=time.localtime, strftime=time.strftime)
        safe_replay.connect_arm = lambda port, robot_id, max_step, units: self.arm
        sys.argv = ["safe_replay.py"] + argv
        try:
            with contextlib.redirect_stdout(out):
                code = safe_replay.main()
        finally:
            safe_replay.Ultrasonic, safe_replay.time, safe_replay.connect_arm, sys.argv = saved
        self.run_until(clock.t)
        return code, out.getvalue(), "", 0.1

    def stream(self, cmd, on_line, timeout=None):
        """Bench.stream: the job runs in no time here, so its lines are handed over when it's done."""
        code, out, err, secs = self.sh(cmd, timeout)
        for ln in out.splitlines():
            on_line(ln)
        return code, out, err, secs

    def _status(self, argv):
        a = dict(zip(argv[1::2], argv[2::2])) if len(argv) > 1 else {}
        runs = Path(a.get("--runs-dir") or self.runs_dir or arm_status.runs_dir())
        if argv[0] == "state":
            out = {"ok": True, "robot_id": "simulated", "port": a.get("--arm-port"),
                   "joints": {j: {"position": round(v, 1), "torque_on": False} for j, v in zip(JOINTS, self.arm.pose)}}
        elif argv[0] == "report":
            try:
                out = arm_status.summarize(arm_status.load_run(runs, a.get("--run")))
            except SystemExit as e:
                return 1, str(e), "", 0.01
        else:
            out = {"runs": [json.loads(f.read_text()).get("run_id") for f in sorted(runs.glob("*.json"))]}
        return 0, json.dumps(out), "", 0.01

    def scenario(self, *words):
        self.ops.append(words)
        if words[0] in ("reset", "start"):               # the workspace goes back to a release: the agent's changes go
            self.tree, self.edits, self.commits = {}, {}, []
        if words[0] == "reset":
            self.firmware = self.workspace = "v1.0"
            self.deployments.append({"ts": time.time(), "crc": GOOD["crc"], "result": "unverified",
                                     "reason": "operator reset to known-good"})
        elif words[0] == "start":
            version = SCENARIOS[words[1]]
            self.firmware = self.workspace = version
            self.deployments.append({"ts": time.time(), "crc": IMAGES[version]["crc"], "result": "unverified",
                                     "reason": "operator deploy %s" % version})
        return 0, "", "", 0.0

    def clock(self):
        t = time.time()
        return t, time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
