#!/usr/bin/env python3
"""Agent loop for the sensor-pod healer (firmware/docs/HARNESS.md sections 4-7).

The model gets only the `heal` tools listed in healer/tools.json: no shell and no other file access.
Each tool call passes a risk gate, runs on the bench one at a time (locally, or over SSH when the
harness runs on another machine), and lands in a transcript together with the gate's decision.

  agent.py run   --target pi@host:/path/to/firmware --driver claude   # one run on the open incidents
  agent.py watch --target pi@host:/path/to/firmware --driver ollama   # wake on new warn/critical incidents

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
# system_info is left out because its text is already the system prompt.
CORE_TOOLS = ["status", "triage", "telemetry_stats", "events", "pod_cmd", "link_baud_scan", "chip_clock",
              "fw_symbolize", "src_read", "src_search", "src_diff", "src_edit", "src_commit", "fw_build",
              "fw_flash", "notify_human", "note"]

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
        try:
            p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout or self.timeout)
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


# ----------------------------------------------------------------------------------------- tools

def load_tools(bench, selection="all"):
    """healer/tools.json from the bench, in file order (stable, so prompt caching works)."""
    manifest = json.loads(bench.read("healer/tools.json"))
    if selection == "all":
        return manifest
    wanted = CORE_TOOLS if selection == "core" else [s.strip() for s in selection.split(",") if s.strip()]
    unknown = set(wanted) - {t["name"] for t in manifest}
    if unknown:
        raise SystemExit("unknown tools: %s" % ", ".join(sorted(unknown)))
    return [t for t in manifest if t["name"] in wanted]


def api_tools(tools):
    """API tool definitions: tools.json entries without `risk`."""
    return [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools]


def system_prompt(bench, flash_cap=FLASH_CAP):
    return bench.heal_json("system_info")["text"] + OPERATING_RULES.format(flash_cap=flash_cap)


def open_incidents(bench):
    return bench.heal_json("incidents").get("open", [])


def kickoff_text(incidents):
    """HARNESS 7: what woke the agent, written neutrally, without naming the scenario."""
    rows = [json.dumps({k: i.get(k) for k in ("kind", "severity", "summary", "first_evidence")}) for i in incidents]
    return ("podd opened these incidents:\n" + "\n".join(rows) +
            "\nInvestigate, fix what can be fixed, and report.")


def run_tool(bench, name, args):
    """Returns (parsed result or None, text for the model, is_error, seconds)."""
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
    """read, runtime, code and notify are allowed; flash is capped per run. Returns
    (decision, reason, kind) where kind is None, "invalid" (no such tool here) or "forbidden"."""

    def __init__(self, tools, flash_cap=FLASH_CAP):
        self.risk = {t["name"]: t["risk"] for t in tools}
        self.flash_cap = flash_cap
        self.flashes = 0

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

    def __init__(self, model="claude-opus-5-5", effort="high", max_tokens=32000):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model, self.effort, self.max_tokens = model, effort, max_tokens

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
        self.messages.append({"role": "assistant", "content": resp.content})     # unchanged, thinking included
        u = resp.usage
        return Turn(
            text="\n".join(b.text for b in resp.content if b.type == "text"),
            thinking="\n".join(b.thinking for b in resp.content if b.type == "thinking" and b.thinking),
            calls=[(b.id, b.name, b.input) for b in resp.content if b.type == "tool_use"],
            stop=resp.stop_reason,
            usage={"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                   "cache_read_input_tokens": u.cache_read_input_tokens or 0,
                   "cache_creation_input_tokens": u.cache_creation_input_tokens or 0},
            raw={"model": resp.model, "id": resp.id,
                 "stop_details": (resp.stop_details.model_dump(mode="json")
                                  if getattr(resp, "stop_details", None) else None),
                 "fallback": any(b.type == "fallback" for b in resp.content)})

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

    def start(self, system, tools, kickoff):
        self.tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                        "parameters": t["input_schema"]}} for t in tools]
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": kickoff}]
        self.n = 0

    def _chat(self):
        body = json.dumps({"model": self.model, "messages": self.messages, "tools": self.tools,
                           "think": self.think, "stream": False, "keep_alive": "30m",
                           "options": {"num_ctx": self.num_ctx}}).encode()
        req = urllib.request.Request(self.url + "/api/chat", body, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError("ollama HTTP %d: %s" % (e.code, e.read().decode(errors="replace")[:500]))

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
        return ClaudeDriver(model=a.model or "claude-opus-5-5", effort=a.effort)
    return OllamaDriver(model=a.model or "qwen3.5:4b", url=a.ollama_url, num_ctx=a.num_ctx)


# ----------------------------------------------------------------------------------------- the loop

class Transcript:
    def __init__(self, path):
        self.f = open(path, "a")

    def write(self, type_, **rec):
        self.f.write(json.dumps({"t": round(time.time(), 3), "type": type_} | rec, default=str) + "\n")
        self.f.flush()


def run_agent(bench, driver, out_dir, *, tools="all", kickoff=None, max_turns=60, time_limit=1500,
              flash_cap=FLASH_CAP, max_result_chars=MAX_RESULT_CHARS, blind=None, label=""):
    """One agent run. Returns a summary dict; transcript.jsonl and report.md go to out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = Transcript(out_dir / "transcript.jsonl")
    offered = load_tools(bench, tools)
    system = system_prompt(bench, flash_cap)
    if kickoff is None:
        kickoff = kickoff_text(open_incidents(bench))
    gate = Gate(offered, flash_cap)
    driver.start(system, offered, kickoff)
    log.write("run_start", label=label, driver=driver.name, model=driver.model, target=bench.target,
              tools=[t["name"] for t in offered], system=system, kickoff=kickoff, blind=bool(blind),
              limits={"max_turns": max_turns, "time_limit_s": time_limit, "flash_cap": flash_cap,
                      "max_result_chars": max_result_chars, "tool_timeout_s": bench.timeout})
    s = {"label": label, "driver": driver.name, "model": driver.model, "tools_offered": len(offered),
         "turns": 0, "tool_calls": 0, "by_risk": {}, "denied": [], "forbidden": 0, "invalid": 0,
         "tool_errors": 0, "truncated": 0, "usage": {}, "stop": "max_turns", "final_text": ""}
    t0 = time.time()
    for turn in range(max_turns):
        if time.time() - t0 > time_limit:
            s["stop"] = "time_limit"
            break
        try:
            t = driver.step()
        except ContextFull as e:
            log.write("model_error", turn=turn, error="context full: %s" % e)
            s["stop"] = "context_full"
            break
        except Exception as e:                  # API or network failure: record it and end the run
            log.write("model_error", turn=turn, error="%s: %s" % (type(e).__name__, e))
            s["stop"] = "model_error"
            break
        s["turns"] += 1
        for k, v in t.usage.items():
            s["usage"][k] = s["usage"].get(k, 0) + (v or 0)
        log.write("model_turn", turn=turn, stop_reason=t.stop, text=t.text, thinking=t.thinking,
                  calls=[{"id": c, "name": n, "input": a} for c, n, a in t.calls], usage=t.usage, raw=t.raw)
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
            if decision == "allow":
                sent = blind.args(name, args) if blind else args
                obj, text, is_error, secs = run_tool(bench, name, sent)
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
            results.append((cid, name, shown, is_error))
        driver.add_results(results)
    s["flashes_allowed"] = gate.flashes
    s["seconds"] = round(time.time() - t0, 1)
    log.write("run_end", **{k: v for k, v in s.items() if k != "final_text"})
    (out_dir / "report.md").write_text(s["final_text"] + "\n")
    return s


def run_dir(label):
    return RUNS / ("%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), label))


def watch(bench, a):
    """HARNESS 7 triggering: wake on a new warn/critical incident, debounce, then run once. Incidents
    open when a run starts or ends count as handled, so a run doesn't wake the next one."""
    handled = set()
    print("watching %s for incidents (Ctrl-C to stop)" % (bench.target or "local bench"), flush=True)
    while True:
        new = [i for i in open_incidents(bench) if i.get("severity") in ("warn", "critical") and i["id"] not in handled]
        if new:
            time.sleep(DEBOUNCE_S)
            incidents = open_incidents(bench)
            handled |= {i["id"] for i in incidents}
            out = run_dir("%s-watch" % a.driver)
            s = run_agent(bench, make_driver(a), out, tools=a.tools, kickoff=kickoff_text(incidents),
                          max_turns=a.max_turns, time_limit=a.time_limit, flash_cap=a.flash_cap,
                          max_result_chars=a.max_result_chars, label="watch")
            handled |= {i["id"] for i in open_incidents(bench)}
            print("run done: %s, %d tool calls, stop %s -> %s" % (out.name, s["tool_calls"], s["stop"], out), flush=True)
        time.sleep(3)


def add_agent_args(p):
    p.add_argument("--target", help="user@host:/path/to/firmware on the bench (default: this checkout)")
    p.add_argument("--driver", choices=["claude", "ollama"], default="claude")
    p.add_argument("--model", help="default: claude-opus-5-5 or qwen3.5:4b")
    p.add_argument("--effort", default="high", help="Claude effort (low, medium, high, xhigh, max)")
    p.add_argument("--tools", default="all", help="all, core (%d tools for small models), or a comma list" % len(CORE_TOOLS))
    p.add_argument("--ollama-url", default="http://localhost:11434")
    p.add_argument("--num-ctx", type=int, default=32768, help="Ollama context window")
    p.add_argument("--max-turns", type=int, default=60)
    p.add_argument("--time-limit", type=int, default=1500, help="seconds per run")
    p.add_argument("--flash-cap", type=int, default=FLASH_CAP)
    p.add_argument("--max-result-chars", type=int, default=MAX_RESULT_CHARS)


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
    s = run_agent(bench, make_driver(a), out, tools=a.tools, max_turns=a.max_turns, time_limit=a.time_limit,
                  flash_cap=a.flash_cap, max_result_chars=a.max_result_chars, label="run")
    print(json.dumps({k: v for k, v in s.items() if k != "final_text"}, indent=2))
    print("\n" + s["final_text"] + "\n\ntranscript: %s" % (out / "transcript.jsonl"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
