"""Live terminal view of an agent run: what the model thinks, says and calls, what the gate decides, and
what each tool returned, as it happens.

agent.py drives it during a run (thinking and text stream token by token from Ollama); view.py replays a
saved transcript.jsonl through the same renderer. Stdlib only. Colors turn off when the output isn't a
terminal or NO_COLOR is set (FORCE_COLOR=1 forces them on).
"""
import json
import os
import shutil
import sys
import textwrap

STYLES = {"dim": "2", "bold": "1", "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35",
          "cyan": "36", "gray": "90", "bred": "1;31", "bgreen": "1;32", "byellow": "1;33", "bcyan": "1;36",
          "bmagenta": "1;35", "inv": "7"}

RISK_STYLE = {"read": "cyan", "runtime": "yellow", "code": "blue", "flash": "bmagenta", "notify": "green",
              "arm": "byellow"}


def _short(v, n=90):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


class Console:
    """thinking: "full" (stream it all), "brief" (first lines only) or "off"."""

    def __init__(self, out=None, color=None, thinking="full", width=None):
        self.out = out or sys.stdout
        if color is None:
            color = bool(os.environ.get("FORCE_COLOR")) or (
                hasattr(self.out, "isatty") and self.out.isatty() and not os.environ.get("NO_COLOR"))
        self.color = color
        self.thinking = thinking
        self.width = width or max(60, min(shutil.get_terminal_size((110, 30)).columns, 140))
        self._stream_kind = None      # what is streaming now: "thinking" or "text"
        self._col = 0
        self._streamed = {"thinking": 0, "text": 0}
        self._turn_t0 = None

    # ------------------------------------------------------------------ basics
    def c(self, style, s):
        if not self.color or not style:
            return s
        return "\033[%sm%s\033[0m" % (STYLES.get(style, style), s)

    def w(self, s=""):
        self.out.write(s)
        self.out.flush()

    def line(self, s=""):
        self._end_stream()
        self.w(s + "\n")

    def block(self, text, prefix, style=None, max_lines=None, label=None):
        """Wrap and print text with a prefix on every line (label: a different prefix for the first line)."""
        lines = []
        for para in (text or "").splitlines() or [""]:
            lines += textwrap.wrap(para, self.width - len(prefix), replace_whitespace=False,
                                   drop_whitespace=True) or [""]
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines] + ["… (%d more lines)" % (len(lines) - max_lines)]
        for i, ln in enumerate(lines):
            self.line(self.c("gray", label if (label and i == 0) else prefix) + self.c(style, ln))

    def rule(self, title="", style="bold", char="━"):
        title = " %s " % title if title else ""
        self.line(self.c(style, char * 2 + title + char * max(3, self.width - len(title) - 2)))

    def banner(self, title, style="bcyan"):
        self.line()
        self.rule(title, style)

    def step(self, ok, text, detail=None):
        """A checklist line for test scripts: ok True/False/None (None = info)."""
        mark = {True: self.c("bgreen", "✔ PASS"), False: self.c("bred", "✘ FAIL"), None: self.c("bcyan", "• ")}[ok]
        self.line("%s  %s" % (mark, text))
        if detail:
            self.block(detail, "         ", "gray", max_lines=12)

    # ------------------------------------------------------------------ streaming
    PREFIX = {"thinking": "  ┊ ", "text": "  ▌ "}
    STREAM_STYLE = {"thinking": "gray", "text": "bold"}

    def _end_stream(self):
        if self._stream_kind:
            self.w("\n")
            self._stream_kind = None

    def delta(self, kind, text):
        """A streamed piece of the model's output. kind: thinking, text, or reset (a resample)."""
        if kind == "reset":
            self.line(self.c("yellow", "  ↻ model output was malformed; asking again (%s)" % _short(text, 80)))
            return
        self._streamed[kind] = self._streamed.get(kind, 0) + len(text)
        if kind == "thinking" and self.thinking == "off":
            return
        if kind == "thinking" and self.thinking == "brief" and self._streamed[kind] > 600:
            if self._streamed[kind] - len(text) <= 600:
                self.w(self.c("gray", " …"))
            return
        if self._stream_kind != kind:
            self._end_stream()
            label = {"thinking": "thinking", "text": "says"}[kind]
            self.w(self.c("gray", "  %s\n" % label))
            self.w(self.c("gray", self.PREFIX[kind]))
            self._stream_kind, self._col = kind, len(self.PREFIX[kind])
            text = text.lstrip("\n")
        style = self.STREAM_STYLE[kind]
        buf = []
        for ch in text:
            if ch == "\n":
                buf.append("\n" + self.c("gray", self.PREFIX[kind]))
                self._col = len(self.PREFIX[kind])
                continue
            if (ch == " " and self._col >= self.width - 2) or self._col >= self.width + 12:
                buf.append("\n" + self.c("gray", self.PREFIX[kind]))
                self._col = len(self.PREFIX[kind])
                if ch == " ":
                    continue
            buf.append(self.c(style, ch) if self.color else ch)
            self._col += 1
        self.w("".join(buf))

    # ------------------------------------------------------------------ run events
    def run_start(self, info):
        lim = info.get("limits") or {}
        self.banner("pod healer agent · %s %s" % (info.get("driver"), info.get("model")))
        self.line(self.c("gray", "  model at  ") + str(info.get("endpoint") or "-"))
        self.line(self.c("gray", "  bench     ") + str(info.get("target") or "local") +
                  self.c("gray", "   blind ") + ("on" if info.get("blind") else "off"))
        self.block(", ".join(info.get("tools") or []), " " * 12, None, label="  tools     ")
        self.line(self.c("gray", "  limits    ") + "%s turns · %s s · %s flashes · %s arm runs" % (
            lim.get("max_turns"), lim.get("time_limit_s"), lim.get("flash_cap"), lim.get("arm_cap", "-")))
        if info.get("arm"):
            self.line(self.c("gray", "  arm       ") + str(info["arm"]))
        self.line()
        self.line(self.c("bold", "  ▶ what woke the agent"))
        self.block(info.get("kickoff") or "", "  │ ", None, max_lines=40)

    def turn_start(self, n):
        self._end_stream()
        self._streamed = {"thinking": 0, "text": 0}
        self.line()
        self.line(self.c("bold", "── turn %d " % (n + 1)) + self.c("gray", "─" * (self.width - 10 - len(str(n + 1)))))

    def turn_end(self, rec):
        """rec: a transcript model_turn record. Prints whatever wasn't streamed."""
        think, text = rec.get("thinking") or "", rec.get("text") or ""
        if think and not self._streamed.get("thinking") and self.thinking != "off":
            self.line(self.c("gray", "  thinking"))
            self.block(think, self.PREFIX["thinking"], "gray", max_lines=None if self.thinking == "full" else 8)
        if text.strip() and not self._streamed.get("text"):
            self.line(self.c("gray", "  says"))
            self.block(text.strip(), self.PREFIX["text"], "bold")
        self._end_stream()
        raw, u = rec.get("raw") or {}, rec.get("usage") or {}
        bits = []
        if raw.get("seconds"):
            bits.append("%.1f s" % raw["seconds"])
        if u.get("input_tokens") is not None:
            bits.append("%s in / %s out tokens" % (u.get("input_tokens"), u.get("output_tokens")))
        if raw.get("approx_ctx_tokens"):
            bits.append("context ≈%dk" % round(raw["approx_ctx_tokens"] / 1000))
        if rec.get("stop_reason") not in (None, "tool_use"):
            bits.append("stop: %s" % rec["stop_reason"])
        if bits:
            self.line(self.c("gray", "  · " + " · ".join(bits)))

    def call(self, rec):
        """Before a tool runs (or after the gate refused it)."""
        name, risk = rec.get("name"), rec.get("risk")
        args = rec.get("input") or {}
        argtxt = " ".join("%s=%s" % (k, _short(v, 60)) for k, v in args.items()) if isinstance(args, dict) else _short(args)
        if rec.get("gate") == "allow":
            tag = self.c(RISK_STYLE.get(risk, "cyan"), "[%s]" % (rec.get("reason") if risk in ("flash", "arm") else risk))
            self.line("  %s %s %s  %s" % (self.c("bcyan", "▶"), self.c("bold", name), self.c("cyan", argtxt), tag))
        else:
            self.line("  %s %s %s  %s" % (self.c("bred", "▶"), self.c("bold", name), argtxt,
                                          self.c("bred", "REFUSED by the harness: %s" % rec.get("reason"))))

    def note(self, text):
        """Something the harness itself told the model (e.g. the turn budget)."""
        self.line(self.c("byellow", "  ⚑ " + text))

    def result(self, rec):
        if rec.get("gate") != "allow":
            return
        text = rec.get("result") or ""
        try:
            obj = json.loads(text[text.index("{"):]) if "{" in text else None
        except ValueError:
            obj = None
        err = rec.get("is_error")
        head = self.c("bred" if err else "green", "  ◀ %s%.1f s" % ("error · " if err else "", rec.get("seconds") or 0))
        lines = summarize(rec.get("name"), obj, text)
        if not lines:
            self.line(head)
            return
        first, rest = lines[0], lines[1:]
        self.line(head + "  " + self._style_line(first, err))
        for ln in rest:
            self.line("      " + self._style_line(ln, err))

    def _style_line(self, ln, err):
        if isinstance(ln, tuple):
            return self.c(ln[0], ln[1])
        return self.c("red", ln) if err else ln

    def run_end(self, s, report=None):
        self.line()
        ok = s.get("stop") == "end_turn"
        self.rule("run finished · stop: %s" % s.get("stop"), "bgreen" if ok else "byellow")
        self.line("  %s turns · %s tool calls %s · %s refused · %s tool errors · %s s" % (
            s.get("turns"), s.get("tool_calls"), _short(s.get("by_risk") or {}, 80),
            len(s.get("denied") or []), s.get("tool_errors"), s.get("seconds")))
        if report and report.strip():
            self.line(self.c("bold", "  report"))
            self.block(report.strip(), "  │ ", None)


# ---------------------------------------------------------------------- result summaries

def _finding_lines(fs):
    sev = {"critical": "bred", "warn": "byellow", "info": "cyan"}
    return [(sev.get(f.get("severity"), None), "%s %s: %s" % (f.get("severity"), f.get("kind"), f.get("summary")))
            for f in fs]


def _kv(row, skip=("ts", "type")):
    return " ".join("%s=%s" % (k, v) for k, v in row.items() if k not in skip)


def _motion_lines(r):
    """A motion report (arm/arm_status.py) in a few lines: gate, reflex, playback vs plan."""
    out = []
    g = r.get("start_gate") or {}
    if g:
        out.append("gate: %s" % g.get("message"))
    x = r.get("obstacle_reflex") or {}
    if x.get("ran"):
        out.append(("byellow", "reflex: %s swings, %s direction changes, base %s° (home %s°)%s" % (
            x.get("swings"), x.get("direction_changes"), x.get("base_measured_range_deg"), x.get("base_home_deg"),
            ", cleared" if x.get("cleared") else ", never cleared")))
    pb = r.get("playback") or {}
    if pb.get("frames_planned"):
        style = "bgreen" if pb.get("complete") else "bred"
        out.append((style, "playback: %s of %s frames" % (pb.get("frames_played"), pb.get("frames_planned"))))
        cov = pb.get("range_coverage") or {}
        if cov:
            out.append("range reached: " + "  ".join("%s %s" % (j, v) for j, v in cov.items()))
    return out


def summarize(name, obj, text, max_lines=14):
    """A few readable lines for one tool result."""
    if not isinstance(obj, dict):
        return [ln for ln in text.strip().splitlines()[:4]]
    if obj.get("ok") is False and obj.get("error"):
        return ["%s" % obj["error"]]
    out = []
    if name in ("triage", "incidents"):
        fs = obj.get("findings", obj.get("open", []))
        out = _finding_lines(fs) or [("green", "no findings" if name == "triage" else "no open incidents")]
        if obj.get("podd"):
            out.append(("bred", "podd: %s" % obj["podd"]))
    elif name == "status":
        link, h = obj.get("link") or {}, obj.get("last_health") or {}
        out = ["link %s at %s baud, %s bytes/s · health: mode=%s us=%s acc=%s" % (
            link.get("state"), link.get("baud"), link.get("bytes_per_s"), h.get("mode"), h.get("us"), h.get("acc"))]
        out += _finding_lines([dict(i, severity=i.get("severity")) for i in obj.get("open_incidents") or []])
    elif name == "telemetry_recent":
        rows = obj.get("rows", [])
        out = ["%d rows" % len(rows)] + [_kv(r) for r in rows[-8:]]
    elif name == "telemetry_stats":
        d, h = obj.get("distance") or {}, obj.get("health_latest") or {}
        acc = (obj.get("accel") or {}).get("magnitude_mg") or {}
        out = ["distance: %s" % _short(d, 110)]
        if acc:
            out.append("|a| mean %s mg" % acc.get("mean"))
        if h:
            out.append("health: mode=%s us=%s acc=%s boots=%s" % (h.get("mode"), h.get("us"), h.get("acc"), h.get("boots")))
    elif name in ("src_diff", "src_show"):
        diff = obj.get("diff") or obj.get("text") or ""
        for ln in diff.splitlines():
            if ln.startswith(("index ", "--- ", "+++ ")):
                continue
            style = "green" if ln.startswith("+") else "red" if ln.startswith("-") else "cyan" if ln.startswith("@@") else "gray"
            out.append((style, ln[:160]))
        out = out or ["(no differences)"]
    elif name == "src_log":
        out = [ln for ln in (obj.get("log") or "").splitlines() if ln.strip()]
    elif name == "src_search":
        hits = obj.get("hits", [])
        out = ["%d hits" % len(hits)] + ["%s:%s  %s" % (h.get("path"), h.get("line"), h.get("text", "").strip()) for h in hits[:8]]
    elif name == "src_read":
        out = ["%s lines %s-%s of %s" % (obj.get("path"), obj.get("start"), obj.get("end"), obj.get("total_lines"))]
        out += [("gray", ln) for ln in (obj.get("text") or "").splitlines()[:6]]
    elif name in ("fw_flash", "fw_rollback"):
        img = obj.get("image") or {}
        out = [("bgreen" if obj.get("ok") else "bred", "flash %s · now running %s %s (%s)" % (
            obj.get("result"), img.get("version"), img.get("crc"), img.get("status")))]
        checks = obj.get("checks")
        if isinstance(checks, list):            # fw_flash: [{"check": "boot_event", "ok": true, ...}, ...]
            out.append("verified: " + "  ".join("%s %s" % ("✔" if c.get("ok") else "?" if c.get("ok") is None else "✘",
                                                          c.get("check")) for c in checks if isinstance(c, dict)))
        if "triage_after" in obj:
            out += ["triage after the flash:"] + (_finding_lines(obj["triage_after"]) or [("green", "  no findings")])
        if obj.get("rollback"):
            out.append(("byellow", "auto-rollback: %s" % _short(obj["rollback"].get("result"), 80)))
    elif name == "fw_images":
        out = ["%s %-5s %-10s %s" % (m.get("crc"), m.get("version"), m.get("status"), m.get("origin"))
               for m in obj.get("images", [])[-8:]]
        deps = obj.get("deployments") or []
        out += ["deployed %s %s (%s)" % (d.get("crc"), d.get("result"), _short(d.get("reason") or "", 50)) for d in deps[-3:]]
    elif name == "chip_status":
        out = ["running %s · crc %s · %s · uptime ratio %s" % (
            obj.get("build_id"), obj.get("crc"), "running" if obj.get("running") else "NOT running",
            obj.get("uptime_ratio_to_real_time"))]
        if obj.get("image"):
            out.append("registry: %s %s" % (obj["image"].get("version"), obj["image"].get("status")))
    elif name == "arm_run":
        passed = obj.get("passed")
        out = [("bgreen" if passed else "bred", "%s · exit %s · %s" % (
            "ARM JOB OK" if passed else "ARM JOB FAILED", obj.get("exit"), obj.get("verdict")))]
        out += _motion_lines(obj.get("motion_report") or {})
        log = [ln for ln in (obj.get("log") or "").splitlines() if ln.startswith(("reflex:", "ultrasonic:", "REFLEX", "arm:"))]
        out += [("gray", ln) for ln in log[-6:]]
    elif name == "arm_report":
        out = ["run %s · exit %s · %s" % (obj.get("run_id"), obj.get("exit"), obj.get("verdict"))] + _motion_lines(obj)
    elif name == "arm_state":
        js = obj.get("joints") or {}
        out = ["  ".join("%s %s°%s" % (j, v.get("position"), "" if v.get("torque_on") else " (limp)") for j, v in js.items())]
    elif name in ("note", "notify_human"):
        rec = obj.get("recorded") or {}
        out = [("green", "recorded") ] + ([_short(rec.get("summary"), 140)] if rec.get("summary") else [])
    elif name == "pod_cmd":
        out = [_kv(r) for r in obj.get("responses", [])[:6]] or [_short(obj, 140)]
    else:
        out = [_short({k: v for k, v in obj.items() if k != "ok"}, 160)]
    if len(out) > max_lines:
        out = out[:max_lines] + ["… (%d more lines)" % (len(out) - max_lines)]
    return out
