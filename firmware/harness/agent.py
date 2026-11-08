#!/usr/bin/env python3
"""Agent loop for the sensor-pod healer (firmware/docs/HARNESS.md sections 4-7).

The model gets only the `heal` tools listed in healer/tools.json: no shell and no other file access.
Each tool call passes a risk gate, runs on the bench one at a time (locally, or over SSH when the
harness runs on another machine), and lands in a transcript together with the gate's decision.

  agent.py run   --target pi@host:/path/to/firmware --driver claude   # one run on the open incidents
  agent.py watch --target pi@host:/path/to/firmware --driver ollama   # wake on new warn/critical incidents
  agent.py run   --tools arm --ollama-url http://192.168.50.1:11434    # the arm task: offers arm_test too

The run streams live to the terminal (--view): the model's thinking and text as it generates them, each
tool call with the gate's decision, and a summary of each result. view.py replays a saved transcript.

Drivers: `claude` (Anthropic API, the online fallback) and `ollama` (a local model; qwen3.5:4b with
thinking on is the team's pick for the device). Runs land in harness/runs/<time>-<label>/.
"""
import argparse
import json
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
FIRMWARE = HERE.parent
RUNS = HERE / "runs"

TOOL_TIMEOUT_S = 180          # HARNESS 4: at least 120 s; fw_flash with an auto-rollback takes about 60 s
MAX_RESULT_CHARS = 30_000     # HARNESS 4: cap each result, keeping the end (triage_after, rollback info)
FLASH_CAP = 3                 # flash-risk calls (fw_flash, fw_rollback) allowed per run
ALLOWED_RISKS = {"read", "runtime", "code", "notify"}   # HARNESS 5: notify is "allow" too
DEBOUNCE_S = 10               # HARNESS 7: one fault often opens several incidents within ~5 s

# A smaller tool set for small local models: the full 38 schemas plus the briefing are about 6k tokens.
# It holds every tool the briefing (SYSTEM.md) tells the agent to use, plus the source, build and flash
# tools; a small model calls what the briefing names, and a tool that isn't offered wastes a turn.
# system_info is left out because its text is already the system prompt.
CORE_TOOLS = ["status", "triage", "incidents", "telemetry_stats", "telemetry_recent", "events", "pod_cmd",
              "link_baud_scan", "link_set_baud", "chip_status", "chip_clock", "fw_symbolize", "fw_images",
              "src_read", "src_search", "src_diff", "src_edit", "src_commit", "fw_build", "fw_flash",
              "fw_rollback", "notify_human", "note"]

ARM_CAP = 4                   # arm_test runs allowed per run (each one may move the real arm)

# The arm task's tool set: observe, read the release history and source, flash, notify, and run the arm's
# job. No source editing or builds: in this task a firmware fault is fixed by restoring the known-good image.
ARM_TOOLS = ["status", "triage", "incidents", "telemetry_stats", "telemetry_recent", "events", "pod_cmd",
             "chip_status", "fw_images", "src_log", "src_read", "src_search", "src_diff", "fw_flash",
             "fw_rollback", "notify_human", "note"]

# A harness tool: implemented here (run_arm), not in healer/, because the arm isn't part of the pod. It runs
# arm/safe_replay.py on the bench exactly as an operator would.
ARM_TOOL = {
    "name": "arm_test", "risk": "arm",
    "description": ("Run the robot arm's job the way the robot runs it. Its start gate reads the pod's ultrasonic "
                    "distance (D lines) for 1.5 s and refuses to move if any sample is closer than the limit "
                    "(200 mm) or the sensor reports no_echo/busy. If the gate passes, the arm plays its recorded "
                    "motion (when no arm is attached, the job ends after the gate). Returns passed, the exit code "
                    "(0 ran, 2 refused to start, 3 stopped mid-motion), and the job's log with the last distance "
                    "samples it saw."),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

ARM_RULES = """
## The robot arm (this run)
- An SO-101 robot arm depends on this pod. Before it moves, its start gate (the arm_test tool) reads the pod's ultrasonic distance for 1.5 s and refuses to start if any sample is closer than {min_mm} mm, or the sensor reports no_echo/busy. A wrong distance either blocks the arm or lets it hit someone.
- If something really is in front of the sensor, or a wire is off, that is a person's job: notify_human with precise instructions, and don't flash.
- If the pod's firmware is at fault, restore service first: flash the known-good image (fw_rollback). In this run that is the fix; a person will repair the release afterwards.
- You don't have to explain the exact defect before you roll back. Telemetry that contradicts physics, plus a release since known-good that changed the code producing the wrong value, is enough: roll back, confirm with arm_test, then explain what you can and say what is still unclear.
- After a fix, run arm_test again. You are done only when arm_test passes. Say in your report whether it passed.
"""

OPERATING_RULES = """

## Operating rules for this run
- You work alone: nobody will answer questions until the run ends. Don't ask for permission; decide within the rules above.
- Your only access to the pod is the tools you were given. There is no shell and no other file access.
- Flashing (fw_flash, fw_rollback) is limited to {flash_cap} calls in this run; further calls are refused.
- Tool calls run one at a time, in the order you give them.
- Finish with a short report: the root cause and the evidence for it, what you changed, how you verified it, and anything a person must do.
"""

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30",
            "-o", "ControlMaster=auto", "-o", "ControlPersist=15m",
            "-o", "ControlPath=" + str(Path(tempfile.gettempdir()) / "podloop-%C")]


# ----------------------------------------------------------------------------------------- the bench

class Bench:
    """Runs `heal` tools and operator commands in firmware/ on the machine with the pod attached.

    target None: this checkout's firmware/. target "user@host:/path/to/firmware": over SSH (key auth;
    BatchMode means it fails instead of prompting for a password)."""

    def __init__(self, target=None, timeout=TOOL_TIMEOUT_S):
        self.target = target
        if target:
            self.host, _, self.fw = target.partition(":")
            if not self.fw:
                raise SystemExit("--target must be user@host:/path/to/firmware")
        else:
            self.host, self.fw = None, str(FIRMWARE)
        self.timeout = timeout

    def sh(self, cmd, timeout=None):
        """Run a shell command inside firmware/. Returns (exit status, stdout, stderr, seconds)."""
        if self.host is None:
            argv, cwd = ["bash", "-c", cmd], self.fw
        else:
            cd = self.fw if self.fw.startswith("~") else shlex.quote(self.fw)
            argv, cwd = ["ssh", *SSH_OPTS, self.host, "cd %s && %s" % (cd, cmd)], None
        t0 = time.time()
        try:                                    # no stdin: nothing on the bench may wait for a keypress
            p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout or self.timeout,
                               stdin=subprocess.DEVNULL)
            return p.returncode, p.stdout, p.stderr, time.time() - t0
        except subprocess.TimeoutExpired:
            return None, "", "timed out after %d s" % (timeout or self.timeout), time.time() - t0

    def heal(self, name, args):
        return self.sh("healer/bin/heal %s --json %s" % (shlex.quote(name), shlex.quote(json.dumps(args))))

    def heal_json(self, name, args=None):
        code, out, err, _ = self.heal(name, args or {})
        try:
            return json.loads(out)
        except ValueError:
            raise RuntimeError("heal %s failed (exit %s): %s" % (name, code, (err or out)[-500:]))

    def scenario(self, *words):
        """Operator only (demo/scenario). Never reachable by the agent."""
        return self.sh("demo/scenario " + " ".join(shlex.quote(w) for w in words), timeout=300)

    def read(self, rel):
        return self.sh("cat %s 2>/dev/null || true" % shlex.quote(rel))[1]

    def clock(self):
        """The bench's clock: (epoch seconds, local ISO time). Tool timestamps use this clock."""
        out = self.sh("python3 -c 'import time; t = time.time(); "
                      "print(t, time.strftime(\"%Y-%m-%dT%H:%M:%S\", time.localtime(t)))'")[1].split()
        return float(out[0]), out[1]


# ----------------------------------------------------------------------------------------- the arm

@dataclass
class ArmConfig:
    """How arm_test runs arm/safe_replay.py on the bench. port None: no arm attached, so the job stops after
    the start gate (--check-only). With a port it moves the real arm, so python must have LeRobot."""
    port: str = None
    python: str = None
    min_mm: int = 200
    extra: str = ""                         # more safe_replay.py flags, e.g. "--speed 0.5"
    timeout: int = 240

    def command(self):
        py = self.python or ("~/lerobot-venv/bin/python" if self.port else "python3")
        py = py if py.startswith("~/") else shlex.quote(py)      # leave ~ for the bench's shell to expand
        args = ["arm/safe_replay.py", "--min-mm", str(self.min_mm)]
        args += ["--arm-port", self.port] if self.port else ["--check-only"]
        return "cd .. && %s -u %s %s" % (py, " ".join(shlex.quote(a) for a in args), self.extra)

    def describe(self):
        return ("moves the real arm on %s" % self.port) if self.port else "start gate only (no arm attached)"


ARM_VERDICT = {0: "the job ran", 2: "refused to start", 3: "stopped mid-motion"}


def run_arm(bench, arm):
    """Run the arm's job once. Returns the arm_test result dict."""
    code, out, err, secs = bench.sh(arm.command(), timeout=arm.timeout)
    log = (out + ("\n" + err if err.strip() else "")).strip()
    verdict = ARM_VERDICT.get(code, "error (exit %s)" % code)
    if code == 0 and not arm.port:
        verdict = "start gate passed (no arm attached, so no motion)"
    return {"passed": code == 0, "exit": code, "verdict": verdict, "mode": arm.describe(),
            "log": clip_tail(log, 4000)[0], "seconds": round(secs, 1)}


HARNESS_TOOLS = {"arm_test": run_arm}


# ----------------------------------------------------------------------------------------- tools

def load_tools(bench, selection="all", extra=()):
    """healer/tools.json from the bench, in file order (stable, so prompt caching works), then any
    harness tools (extra) at the end."""
    manifest = json.loads(bench.read("healer/tools.json"))
    if selection == "all":
        return manifest + list(extra)
    named = {"core": CORE_TOOLS, "arm": ARM_TOOLS}
    wanted = named[selection] if selection in named else [s.strip() for s in selection.split(",") if s.strip()]
    unknown = set(wanted) - {t["name"] for t in manifest}
    if unknown:
        raise SystemExit("unknown tools: %s" % ", ".join(sorted(unknown)))
    return [t for t in manifest if t["name"] in wanted] + list(extra)


def api_tools(tools):
    """API tool definitions: tools.json entries without `risk`."""
    return [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools]


def system_prompt(bench, flash_cap=FLASH_CAP, arm=None):
    text = bench.heal_json("system_info")["text"] + OPERATING_RULES.format(flash_cap=flash_cap)
    return text + (ARM_RULES.format(min_mm=arm.min_mm) if arm else "")


def arm_kickoff(job, incidents):
    """What woke the agent in the arm task: the arm's job failed. Neutral: the job's own log, podd's open
    incidents, nothing about the cause."""
    rows = [json.dumps({k: i.get(k) for k in ("kind", "severity", "summary", "first_evidence")}) for i in incidents]
    return ("The robot arm's job did not run (exit %s: %s). Its log:\n%s\n\n" % (job.get("exit"), job.get("verdict"),
                                                                             job.get("log", "").strip()) +
            ("podd has these open incidents:\n" + "\n".join(rows) if rows else "podd has no open incidents.") +
            "\n\nFind out why, restore service if you can, and confirm with arm_test.")


def open_incidents(bench):
    return bench.heal_json("incidents").get("open", [])


def kickoff_text(incidents):
    """HARNESS 7: what woke the agent, written neutrally, without naming the scenario."""
    if not incidents:
        return "podd reports no open incidents. Check the pod's health, fix what can be fixed, and report."
    rows = [json.dumps({k: i.get(k) for k in ("kind", "severity", "summary", "first_evidence")}) for i in incidents]
    return ("podd opened these incidents:\n" + "\n".join(rows) +
            "\nInvestigate, fix what can be fixed, and report.")


def run_tool(bench, name, args, arm=None):
    """Returns (parsed result or None, text for the model, is_error, seconds)."""
    if name in HARNESS_TOOLS:
        t0 = time.time()
        try:
            obj = HARNESS_TOOLS[name](bench, arm)
            return obj, json.dumps(obj, ensure_ascii=False), False, time.time() - t0
        except Exception as e:                  # the tool broke, not the arm's job: an error result
            obj = {"ok": False, "error": "harness: %s: %s" % (type(e).__name__, e)}
            return obj, json.dumps(obj), True, time.time() - t0
    code, out, err, secs = bench.heal(name, args)
    if code is None:
        return None, json.dumps({"ok": False, "error": "harness: tool " + err}), True, secs
    try:
        obj = json.loads(out)
    except ValueError:
        text = (out + ("\n" + err if err else "")).strip() or "exit status %s and no output" % code
        return None, text, True, secs
    is_error = code != 0 or (isinstance(obj, dict) and obj.get("ok") is False)
    return obj, json.dumps(obj, ensure_ascii=False), is_error, secs


def clip_tail(text, limit=MAX_RESULT_CHARS):
    if len(text) <= limit:
        return text, False
    return ("[harness: this result had %d characters; the first %d are cut and the end is kept]\n"
            % (len(text), len(text) - limit)) + text[-limit:], True


# ----------------------------------------------------------------------------------------- the gate

def isolation_violation(name, args):
    """HARNESS 6: the agent reaches the pod only through the tools; nothing outside the workspace."""
    if name == "pod_cmd" and str(args.get("command", "")).strip().split()[:1] == ["inject"]:
        return "fault injection is operator-only"
    for key in ("image", "to"):                 # an image path would let fw_* read files outside the registry
        v = args.get(key)
        if v is not None and ("/" in str(v) or str(v).lower().endswith(".elf")):
            return "%s must be an image crc or a keyword (known-good, latest, deployed, chip), not a path" % key
    for key in ("rev", "rev_a", "rev_b"):       # a leading '-' would be read by git as an option
        if str(args.get(key) or "").startswith("-"):
            return "%s must be a revision, not an option" % key
    p = args.get("path")
    if p and (str(p).startswith(("/", "~")) or ".." in PurePosixPath(str(p)).parts):
        return "paths must stay inside the firmware workspace"
    return None


class Gate:
    """read, runtime, code and notify are allowed; flash and arm are capped per run. Returns
    (decision, reason, kind) where kind is None, "invalid" (no such tool here) or "forbidden"."""

    def __init__(self, tools, flash_cap=FLASH_CAP, arm_cap=ARM_CAP):
        self.risk = {t["name"]: t["risk"] for t in tools}
        self.flash_cap = flash_cap
        self.flashes = 0
        self.arm_cap = arm_cap
        self.arm_runs = 0

    def check(self, name, args):
        if name not in self.risk:
            return "deny", "there is no tool named %r in this run" % name, "invalid"
        if not isinstance(args, dict):
            return "deny", "arguments must be a JSON object", "invalid"
        why = isolation_violation(name, args)
        if why:
            return "deny", why, "forbidden"
        risk = self.risk[name]
        if risk == "flash":
            if self.flashes >= self.flash_cap:
                return "deny", "the flash budget for this run is used up (%d)" % self.flash_cap, "forbidden"
            self.flashes += 1
            return "allow", "flash %d of %d" % (self.flashes, self.flash_cap), None
        if risk == "arm":
            if self.arm_runs >= self.arm_cap:
                return "deny", "the arm_test budget for this run is used up (%d)" % self.arm_cap, "forbidden"
            self.arm_runs += 1
            return "allow", "arm run %d of %d" % (self.arm_runs, self.arm_cap), None
        if risk in ALLOWED_RISKS:
            return "allow", risk, None
        return "deny", "risk level %r is not allowed" % risk, "forbidden"


# ----------------------------------------------------------------------------------------- blind mode

class Blind:
    """For evaluation runs: hide every record older than the scenario start, so earlier runs and bench
    tests (their fixes, notes, flash reasons and builds) can't leak answers. Time-windowed tools are
    clamped to the time since the start; fw_images, notes and the serial log are filtered."""

    WINDOW_MIN = {"events": 30, "log_search": 60, "incidents": 60}     # the tools' defaults

    def __init__(self, since, since_iso, t0_local):
        self.since, self.since_iso, self.t0 = since, since_iso, t0_local

    def args(self, name, args):
        elapsed = time.time() - self.t0 + 1
        try:
            if name in self.WINDOW_MIN and float(args.get("minutes", self.WINDOW_MIN[name])) > elapsed / 60:
                return {**args, "minutes": round(elapsed / 60, 3)}
            if name in ("telemetry_stats", "telemetry_recent") and float(args.get("seconds", 10)) > elapsed:
                return {**args, "seconds": round(elapsed, 1)}
        except (TypeError, ValueError):
            pass
        return args

    def result(self, name, args, obj):
        if not isinstance(obj, dict):
            return obj
        if name == "fw_images":
            deps = [d for d in obj.get("deployments", []) if d.get("ts", 0) >= self.since]
            keep = {d.get("crc") for d in deps}
            obj["deployments"] = deps
            obj["images"] = [m for m in obj.get("images", []) if m.get("created", 0) >= self.since
                             or m.get("status") == "known-good" or m.get("crc") in keep]
        if name == "log_search" and args.get("log") == "serial":    # serial lines carry local ISO times
            obj["rows"] = [r for r in obj.get("rows", []) if str(r)[:19] >= self.since_iso]
            obj["matched"] = len(obj["rows"])
        self._notes(obj)
        return obj

    def _notes(self, x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "notes" and isinstance(v, list):
                    x[k] = [n for n in v if not isinstance(n, dict) or n.get("ts", self.since) >= self.since]
                else:
                    self._notes(v)
        elif isinstance(x, list):
            for v in x:
                self._notes(v)


# ----------------------------------------------------------------------------------------- drivers

class ContextFull(RuntimeError):
    """The next request would not fit the local model's context window (Ollama would cut it silently)."""


@dataclass
class Turn:
    text: str
    thinking: str
    calls: list               # [(call id, tool name, arguments)]
    stop: str                 # end_turn, tool_use, max_tokens, refusal, pause_turn
    usage: dict
    raw: dict = field(default_factory=dict)


class ClaudeDriver:
    """Claude through the Anthropic API (the online fallback). Parameters follow the claude-api
    reference for claude-opus-5-5: thinking is always on (effort is the control, default medium, so
    it's set explicitly), server-side refusal fallback, automatic prompt caching, append-only history."""

    name = "claude"

    def __init__(self, model="claude-opus-5-5", effort="high", max_tokens=32000, base_url=None):
        import anthropic
        self.client = anthropic.Anthropic(base_url=base_url)   # None: ANTHROPIC_BASE_URL or the Anthropic API
        self.model, self.effort, self.max_tokens = model, effort, max_tokens
        self.endpoint = base_url or "Anthropic API"

    def start(self, system, tools, kickoff):
        self.params = dict(model=self.model, max_tokens=self.max_tokens, system=system, tools=api_tools(tools),
                           thinking={"type": "adaptive", "display": "summarized"},
                           output_config={"effort": self.effort},
                           betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                           cache_control={"type": "ephemeral"})
        self.messages = [{"role": "user", "content": kickoff}]

    def step(self):
        with self.client.beta.messages.stream(messages=self.messages, **self.params) as stream:
            resp = stream.get_final_message()
        # After a mid-output refusal fallback, the declining model's thinking and tool_use blocks before the
        # last `fallback` block are not part of the conversation: they stay out of the history and those calls
        # don't run. Its text, the fallback block and everything after it are kept (claude-api reference).
        cut = max((i for i, b in enumerate(resp.content) if b.type == "fallback"), default=-1)
        content = [b for i, b in enumerate(resp.content) if i >= cut or b.type in ("text", "fallback")]
        self.messages.append({"role": "assistant", "content": content})     # otherwise unchanged, thinking included
        u = resp.usage
        return Turn(
            text="\n".join(b.text for b in content if b.type == "text"),
            thinking="\n".join(b.thinking for b in content if b.type == "thinking" and b.thinking),
            calls=[(b.id, b.name, b.input) for b in content if b.type == "tool_use"],
            stop=resp.stop_reason,
            usage={"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                   "cache_read_input_tokens": u.cache_read_input_tokens or 0,
                   "cache_creation_input_tokens": u.cache_creation_input_tokens or 0},
            raw={"model": resp.model, "id": resp.id,
                 "stop_details": (resp.stop_details.model_dump(mode="json")
                                  if getattr(resp, "stop_details", None) else None),
                 "fallback": cut >= 0, "dropped_before_fallback": len(resp.content) - len(content)})

    def add_results(self, results):
        self.messages.append({"role": "user", "content": [            # all results in ONE message
            {"type": "tool_result", "tool_use_id": cid, "content": text, "is_error": is_error}
            for cid, _name, text, is_error in results]})


class OllamaDriver:
    """A local model through Ollama's /api/chat (stdlib HTTP). Ollama takes one `tool` message per
    result, so a turn's results go back as consecutive tool messages."""

    name = "ollama"

    def __init__(self, model="qwen3.5:4b", url="http://localhost:11434", num_ctx=32768, think=True,
                 timeout=1200):
        self.model, self.url, self.num_ctx, self.think, self.timeout = model, url.rstrip("/"), num_ctx, think, timeout
        self.malformed = 0
        self.on_delta = None        # callback(kind, text): when set, the reply streams (kind thinking/text/reset)

    @property
    def endpoint(self):
        return self.url

    def start(self, system, tools, kickoff):
        self.tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                        "parameters": t["input_schema"]}} for t in tools]
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": kickoff}]
        self.n = 0

    def _chat(self):
        stream = self.on_delta is not None
        body = json.dumps({"model": self.model, "messages": self.messages, "tools": self.tools,
                           "think": self.think, "stream": stream, "keep_alive": "30m",
                           "options": {"num_ctx": self.num_ctx}}).encode()
        req = urllib.request.Request(self.url + "/api/chat", body, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return self._read_stream(r) if stream else json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError("ollama HTTP %d: %s" % (e.code, e.read().decode(errors="replace")[:500]))

    def _read_stream(self, r):
        """Ollama's streamed reply: one JSON object per line, the last with done=true and the counts. Thinking
        and text arrive in pieces (passed to on_delta as they come); tool calls arrive whole. Returns the same
        shape as a non-streamed reply."""
        msg, calls, final = {"role": "assistant", "content": "", "thinking": ""}, [], {}
        for line in r:
            if not line.strip():
                continue
            ch = json.loads(line)
            if ch.get("error"):                 # e.g. a malformed tool call found mid-stream
                raise RuntimeError("ollama error: %s" % ch["error"])
            m = ch.get("message") or {}
            for key, kind in (("thinking", "thinking"), ("content", "text")):
                if m.get(key):
                    msg[key] += m[key]
                    self.on_delta(kind, m[key])
            calls += m.get("tool_calls") or []
            if ch.get("done"):
                final = ch
                break
        if calls:
            msg["tool_calls"] = calls
        if not msg["thinking"]:
            del msg["thinking"]
        return {**final, "message": msg}

    def step(self):
        approx_ctx = len(json.dumps(self.messages) + json.dumps(self.tools)) // 3     # tokens, on the high side
        if approx_ctx > self.num_ctx:
            raise ContextFull("about %d tokens of prompt, num_ctx is %d" % (approx_ctx, self.num_ctx))
        for attempt in range(3):                # a malformed tool call is an HTTP error; resample
            try:
                r = self._chat()
                break
            except RuntimeError as e:
                if "tool" not in str(e).lower() or attempt == 2:
                    raise
                self.malformed += 1
                if self.on_delta:
                    self.on_delta("reset", str(e))
        msg = r["message"]
        self.messages.append({k: v for k, v in msg.items() if k in ("role", "content", "thinking", "tool_calls")})
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"_unparsed": args}
            calls.append((tc.get("id") or "call_%d_%d" % (self.n, i), fn.get("name", ""), args))
        self.n += 1
        return Turn(text=msg.get("content") or "", thinking=msg.get("thinking") or "", calls=calls,
                    stop="tool_use" if calls else ("max_tokens" if r.get("done_reason") == "length" else "end_turn"),
                    usage={"input_tokens": r.get("prompt_eval_count", 0), "output_tokens": r.get("eval_count", 0)},
                    raw={"model": r.get("model"), "done_reason": r.get("done_reason"), "approx_ctx_tokens": approx_ctx,
                         "malformed_retries": self.malformed,
                         "seconds": round(r.get("total_duration", 0) / 1e9, 1)})

    def add_results(self, results):
        for _cid, name, text, _is_error in results:
            self.messages.append({"role": "tool", "tool_name": name, "content": text})


def make_driver(a):
    if a.driver == "claude":
        return ClaudeDriver(model=a.model or "claude-opus-5-5", effort=a.effort, base_url=a.base_url)
    return OllamaDriver(model=a.model or "qwen3.5:4b", url=a.ollama_url, num_ctx=a.num_ctx)


# ----------------------------------------------------------------------------------------- the loop

class Transcript:
    def __init__(self, path):
        self.f = open(path, "a")

    def write(self, type_, **rec):
        self.f.write(json.dumps({"t": round(time.time(), 3), "type": type_} | rec, default=str) + "\n")
        self.f.flush()


class NullUI:
    """No live view (tests, scripts). console.Console is the real one."""

    def __getattr__(self, name):
        return lambda *a, **k: None


def run_agent(bench, driver, out_dir, *, tools="all", kickoff=None, max_turns=60, time_limit=1500,
              flash_cap=FLASH_CAP, max_result_chars=MAX_RESULT_CHARS, blind=None, label="", ui=None, arm=None,
              arm_cap=ARM_CAP, budget_notice=True):
    """One agent run. Returns a summary dict; transcript.jsonl and report.md go to out_dir.
    ui: a console.Console for the live view. arm: an ArmConfig to offer the arm_test tool."""
    ui = ui or NullUI()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = Transcript(out_dir / "transcript.jsonl")
    offered = load_tools(bench, tools, extra=[ARM_TOOL] if arm else ())
    system = system_prompt(bench, flash_cap, arm)
    if kickoff is None:
        kickoff = kickoff_text(open_incidents(bench))
    gate = Gate(offered, flash_cap, arm_cap)
    if hasattr(driver, "on_delta") and not isinstance(ui, NullUI):
        driver.on_delta = ui.delta              # stream thinking and text into the live view
    driver.start(system, offered, kickoff)
    start = dict(label=label, driver=driver.name, model=driver.model, target=bench.target,
                 endpoint=getattr(driver, "endpoint", None),
                 tools=[t["name"] for t in offered], system=system, kickoff=kickoff, blind=bool(blind),
                 arm=arm.describe() if arm else None,
                 limits={"max_turns": max_turns, "time_limit_s": time_limit, "flash_cap": flash_cap,
                         "arm_cap": arm_cap if arm else None,
                         "max_result_chars": max_result_chars, "tool_timeout_s": bench.timeout})
    log.write("run_start", **start)
    ui.run_start(start)
    s = {"label": label, "driver": driver.name, "model": driver.model, "tools_offered": len(offered),
         "turns": 0, "tool_calls": 0, "by_risk": {}, "denied": [], "forbidden": 0, "invalid": 0,
         "tool_errors": 0, "truncated": 0, "usage": {}, "stop": "max_turns", "final_text": ""}
    t0 = time.time()
    for turn in range(max_turns):
        if time.time() - t0 > time_limit:
            s["stop"] = "time_limit"
            break
        ui.turn_start(turn)
        try:
            t = driver.step()
        except ContextFull as e:
            log.write("model_error", turn=turn, error="context full: %s" % e)
            ui.line("  context full: %s" % e)
            s["stop"] = "context_full"
            break
        except Exception as e:                  # API or network failure: record it and end the run
            log.write("model_error", turn=turn, error="%s: %s" % (type(e).__name__, e))
            ui.line("  model error: %s: %s" % (type(e).__name__, e))
            s["stop"] = "model_error"
            break
        s["turns"] += 1
        for k, v in t.usage.items():
            s["usage"][k] = s["usage"].get(k, 0) + (v or 0)
        turn_rec = dict(turn=turn, stop_reason=t.stop, text=t.text, thinking=t.thinking,
                        calls=[{"id": c, "name": n, "input": a} for c, n, a in t.calls], usage=t.usage, raw=t.raw)
        log.write("model_turn", **turn_rec)
        ui.turn_end(turn_rec)
        if t.text.strip():
            s["final_text"] = t.text
        if t.stop in ("refusal", "max_tokens"):
            s["stop"] = t.stop
            break
        if not t.calls:
            if t.stop == "pause_turn":
                continue
            s["stop"] = "end_turn"
            break
        results = []
        for cid, name, args in t.calls:         # one at a time, in order
            decision, reason, kind = gate.check(name, args)
            rec = {"turn": turn, "id": cid, "name": name, "input": args, "risk": gate.risk.get(name),
                   "gate": decision, "reason": reason}
            ui.call(rec)
            if decision == "allow":
                sent = blind.args(name, args) if blind else args
                obj, text, is_error, secs = run_tool(bench, name, sent, arm)
                if blind and obj is not None:
                    text = json.dumps(blind.result(name, sent, obj), ensure_ascii=False)
                s["tool_calls"] += 1
                s["by_risk"][rec["risk"]] = s["by_risk"].get(rec["risk"], 0) + 1
                s["tool_errors"] += is_error
                rec |= {"sent": sent if sent != args else None, "seconds": round(secs, 2), "is_error": is_error}
            else:
                text, is_error = json.dumps({"ok": False, "error": "refused by the harness: " + reason}), True
                s["denied"].append({"name": name, "input": args, "reason": reason, "kind": kind})
                s[kind] += 1
            shown, cut = clip_tail(text, max_result_chars)
            s["truncated"] += cut
            rec |= {"result": shown, "result_chars": len(text), "truncated": cut}
            log.write("tool_call", **rec)
            ui.result(rec)
            results.append((cid, name, shown, is_error))
        left = max_turns - (turn + 1)
        if budget_notice and results and left == max(3, max_turns // 4):
            # Small models lose track of their budget and investigate until the run ends. Say it once,
            # on the last result of this turn, clearly marked as the harness speaking.
            note = ("[harness: %d model turns left in this run. If you know the fix, apply and verify it now; "
                    "otherwise report what you found.]" % left)
            cid, name, shown, is_error = results[-1]
            results[-1] = (cid, name, shown + "\n" + note, is_error)
            log.write("harness_note", turn=turn, text=note)
            ui.note(note)
        driver.add_results(results)
    s["flashes_allowed"] = gate.flashes
    s["arm_runs_allowed"] = gate.arm_runs
    s["seconds"] = round(time.time() - t0, 1)
    log.write("run_end", **{k: v for k, v in s.items() if k != "final_text"})
    (out_dir / "report.md").write_text(s["final_text"] + "\n")
    ui.run_end(s, s["final_text"])
    ui.line("  transcript: %s" % (out_dir / "transcript.jsonl"))
    return s


def run_dir(label):
    return RUNS / ("%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), label))


def watch(bench, a):
    """HARNESS 7 triggering: wake on a new warn/critical incident, debounce, then run once. Incidents
    open when a run starts or ends count as handled, so a run doesn't wake the next one."""
    handled, down = set(), None
    print("watching %s for incidents (Ctrl-C to stop)" % (bench.target or "local bench"), flush=True)
    while True:
        r = bench.heal_json("incidents")
        if r.get("podd") != down:              # without podd there are no incidents to wake on; say so once
            down = r.get("podd")
            print("podd unreachable: %s" % down if down else "podd reachable", flush=True)
        new = [i for i in r.get("open", []) if i.get("severity") in ("warn", "critical") and i["id"] not in handled]
        if new:
            time.sleep(DEBOUNCE_S)
            incidents = open_incidents(bench)
            handled |= {i["id"] for i in incidents}
            out = run_dir("%s-watch" % a.driver)
            s = run_agent(bench, make_driver(a), out, tools=a.tools, kickoff=kickoff_text(incidents),
                          max_turns=a.max_turns, time_limit=a.time_limit, flash_cap=a.flash_cap,
                          max_result_chars=a.max_result_chars, label="watch", ui=make_ui(a), arm=make_arm(a),
                          arm_cap=a.arm_cap)
            handled |= {i["id"] for i in open_incidents(bench)}
            print("run done: %s, %d tool calls, stop %s -> %s" % (out.name, s["tool_calls"], s["stop"], out), flush=True)
        time.sleep(3)


def add_agent_args(p):
    p.add_argument("--target", help="user@host:/path/to/firmware on the bench (default: this checkout)")
    p.add_argument("--driver", choices=["claude", "ollama"], default="ollama")
    p.add_argument("--model", help="default: claude-opus-5-5 or qwen3.5:4b")
    p.add_argument("--effort", default="high", help="Claude effort (low, medium, high, xhigh, max)")
    p.add_argument("--base-url", help="Messages API endpoint for the claude driver, e.g. a passthrough on the "
                                      "Romulus server (default: ANTHROPIC_BASE_URL or the Anthropic API)")
    p.add_argument("--tools", default="all", help="all, core (%d tools for small models), arm (%d tools plus arm_test), or a comma list" % (len(CORE_TOOLS), len(ARM_TOOLS)))
    p.add_argument("--ollama-url", default="http://localhost:11434")
    p.add_argument("--num-ctx", type=int, default=32768, help="Ollama context window")
    p.add_argument("--max-turns", type=int, default=60)
    p.add_argument("--time-limit", type=int, default=1500, help="seconds per run")
    p.add_argument("--flash-cap", type=int, default=FLASH_CAP)
    p.add_argument("--max-result-chars", type=int, default=MAX_RESULT_CHARS)
    g = p.add_argument_group("the robot arm (offers the arm_test tool)")
    g.add_argument("--arm", action="store_true", help="offer arm_test: runs arm/safe_replay.py's start gate "
                                                      "(no motion unless --arm-port is given)")
    g.add_argument("--arm-port", help="servo bus adapter (/dev/serial/by-id/...): arm_test moves the real arm")
    g.add_argument("--arm-python", help="python for arm/safe_replay.py (default: python3, or ~/lerobot-venv/bin/python "
                                        "with --arm-port)")
    g.add_argument("--arm-args", default="", help="more safe_replay.py flags, e.g. '--speed 0.5'")
    g.add_argument("--min-mm", type=int, default=200, help="the arm's start-gate distance limit")
    g.add_argument("--arm-cap", type=int, default=ARM_CAP, help="arm_test runs allowed per run")
    p.add_argument("--view", choices=["full", "brief", "off", "none"], default="full",
                   help="live view: full (stream all thinking), brief (first lines of thinking), off (no thinking), "
                        "none (no live view; JSON summary only)")


def make_arm(a):
    if not (a.arm or a.arm_port or a.tools == "arm"):
        return None
    return ArmConfig(port=a.arm_port, python=a.arm_python, min_mm=a.min_mm, extra=a.arm_args)


def make_ui(a):
    if a.view == "none":
        return None
    import console
    return console.Console(thinking=a.view)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["run", "watch"])
    add_agent_args(p)
    a = p.parse_args(argv)
    bench = Bench(a.target)
    if a.command == "watch":
        watch(bench, a)
        return 0
    out = run_dir("%s-run" % a.driver)
    ui = make_ui(a)
    s = run_agent(bench, make_driver(a), out, tools=a.tools, max_turns=a.max_turns, time_limit=a.time_limit,
                  flash_cap=a.flash_cap, max_result_chars=a.max_result_chars, label="run", ui=ui, arm=make_arm(a),
                  arm_cap=a.arm_cap)
    print(json.dumps({k: v for k, v in s.items() if k != "final_text"}, indent=2))
    if ui is None:
        print("\n" + s["final_text"] + "\n\ntranscript: %s" % (out / "transcript.jsonl"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
