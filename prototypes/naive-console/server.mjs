// Naïve console: send one message to Naïve's built-in `pi` loop (cloud) and to
// Hermes on this machine (which reaches Naïve over MCP), side by side, as one
// ongoing conversation on each side, and show how each manages memory.
//
// The server never sees an API key. It runs two CLIs that hold their own logins:
// `naive` (from `naive login`) and `hermes` (from `hermes auth add`, plus
// NAIVE_API_KEY in ~/.hermes/.env for the Naïve MCP server).
// Run: node prototypes/naive-console/server.mjs, open http://127.0.0.1:5180
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile, rm, stat } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

const HOST = "127.0.0.1";
const PORT = Number(process.env.PORT ?? 5180);
const ORIGIN = `http://${HOST}:${PORT}`;
// Budget of a new Naïve session (a whole conversation); the agent's per-task cap still applies
// (hello-luna $0.25, hello-sol $5). Naïve refuses a turn up front when its pre-flight quote
// (worst-case cost) is above what's left, and fed documents make sol turns cost cents each.
const SESSION_CAP_USD = "5.00";
// Naïve answers `rate_limited: the service is at capacity` at busy times: wait and retry.
const RATE_LIMITED = /rate_limited|at capacity|too many requests|\b429\b/i;
const RETRY_DELAYS_MS = [2_000, 4_000, 8_000, 15_000];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const HERMES = process.env.HERMES_BIN ?? join(homedir(), ".local/bin/hermes");
const HERMES_HOME = process.env.HERMES_HOME ?? join(homedir(), ".hermes");
// Naïve's MCP tools (toolset `mcp-<server>`) plus Hermes's own memory: `memory` writes long-term
// notes (USER.md / MEMORY.md), `session_search` recalls past conversations. No terminal, files or web.
const HERMES_TOOLSETS = "mcp-naive,memory,session_search";
const TURN_TIMEOUT_MS = 180_000;
// Long enough to paste a whole repo doc into one message (the fault catalog is ~25K characters).
const MAX_MESSAGE_CHARS = 60_000;
const PAGE = new URL("./index.html", import.meta.url);
// Repo docs the page may load into the message box ("Feed a document"). Read-only, fixed list.
const DOCS = {
  "so101-research": new URL("../../docs/so101-research.md", import.meta.url),
  "fault-catalog": new URL("../../docs/fault-catalog.md", import.meta.url),
};

/** Run a CLI with fixed args (no shell); resolve its stdout, reject with its error text. */
function runCmd(cmd, args) {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    let err = "";
    p.stdout.on("data", (d) => (out += d));
    p.stderr.on("data", (d) => (err += d));
    p.on("error", reject);
    p.on("close", (code) => (code === 0 ? resolve(out) : reject(new Error((err || out).trim() || `${cmd} exited ${code}`))));
  });
}

async function naiveJson(args) {
  const out = await runCmd("naive", args);
  try {
    return JSON.parse(out);
  } catch {
    throw new Error("naive printed something that is not JSON");
  }
}

/** `naiveJson`, waiting and retrying while Naïve is at capacity; `onWait(ms, why)` reports each wait. */
async function naiveRetry(args, onWait) {
  for (let i = 0; ; i++) {
    try {
      return await naiveJson(args);
    } catch (e) {
      if (i >= RETRY_DELAYS_MS.length || !RATE_LIMITED.test(e.message)) throw e;
      onWait?.(RETRY_DELAYS_MS[i], e.message);
      await sleep(RETRY_DELAYS_MS[i]);
    }
  }
}

function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

/** Only this page may call the API: blocks other websites from spending credits via localhost. */
function sameOrigin(req) {
  return req.headers.origin === ORIGIN || req.headers["sec-fetch-site"] === "same-origin";
}

async function readBody(req, limit = MAX_MESSAGE_CHARS + 4_000) {
  let raw = "";
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > limit) throw new Error("body too large");
  }
  return JSON.parse(raw || "{}");
}

/** Validated message from the request body, or null after answering 400. */
async function readMessage(req, res) {
  let body;
  try {
    body = await readBody(req);
  } catch {
    send(res, 400, { error: "bad JSON body" });
    return null;
  }
  const message = String(body.message ?? "").trim();
  if (!message || message.length > MAX_MESSAGE_CHARS) {
    send(res, 400, { error: `message must be 1-${MAX_MESSAGE_CHARS} characters` });
    return null;
  }
  return { body, message };
}

/**
 * Spawn a CLI and stream it to the page as NDJSON. `onLine(line, write)` handles each stdout
 * line (true = consumed); `onEnd(rest, write)` may be async and gets any unparsed tail. The page
 * also gets a server-measured wall time from `t0`, so both loops are timed the same way.
 */
function stream(req, res, { cmd, args, cwd, t0, onLine, onEnd }) {
  res.writeHead(200, { "content-type": "application/x-ndjson", "cache-control": "no-store" });
  const write = (obj) => res.write(JSON.stringify(obj) + "\n");
  const p = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], cwd });
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    p.kill("SIGTERM");
  }, TURN_TIMEOUT_MS);
  // The page went away mid-turn: stop the CLI rather than leave it running.
  res.on("close", () => {
    if (!res.writableEnded && p.exitCode === null) p.kill("SIGTERM");
  });
  let buf = "";
  let rest = "";
  let err = "";
  p.stdout.on("data", (d) => {
    buf += d;
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i);
      buf = buf.slice(i + 1);
      if (!onLine(line, write)) rest += line + "\n";
    }
  });
  p.stderr.on("data", (d) => (err += d));
  p.on("error", (e) => {
    clearTimeout(timer);
    write({ kind: "error", message: e.message });
    res.end();
  });
  p.on("close", async (code) => {
    clearTimeout(timer);
    try {
      await onEnd(rest + buf, write);
    } catch (e) {
      write({ kind: "error", message: e.message });
    }
    write({ kind: "wall", ms: Math.round(performance.now() - t0) });
    if (timedOut) write({ kind: "error", message: `no answer after ${TURN_TIMEOUT_MS / 1000} s, so it was stopped` });
    else if (code !== 0) write({ kind: "error", message: err.trim().split("\n").slice(-3).join(" ") || `${cmd} exited ${code}` });
    res.end();
  });
}

/**
 * Stream one turn of a Naïve session (`naive session stream`, which exits once the session is idle),
 * passing each event to `onEvent`. If Naïve drops the stream at capacity, reconnect from the last
 * event seen. Resolves true when the turn reached idle.
 */
async function streamNaiveTurn({ session, afterSeq, onEvent, onWait, isAlive, setChild }) {
  let after = afterSeq;
  for (let retry = 0; isAlive(); ) {
    let idle = false;
    const { code, err } = await new Promise((resolve) => {
      const p = spawn("naive", ["session", "stream", "--session", session, "--after-seq", String(after)], { stdio: ["ignore", "pipe", "pipe"] });
      setChild(p);
      let buf = "";
      let err = "";
      p.stdout.on("data", (d) => {
        buf += d;
        let i;
        while ((i = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, i);
          buf = buf.slice(i + 1);
          if (!line.startsWith('{"id":"evt_')) continue; // one JSON event per line
          const ev = JSON.parse(line);
          after = Math.max(after, ev.seq ?? after);
          if (ev.type === "session.idle") idle = true;
          onEvent(ev);
        }
      });
      p.stderr.on("data", (d) => (err += d));
      p.on("error", (e) => resolve({ code: -1, err: e.message }));
      p.on("close", (code) => resolve({ code, err }));
    });
    if (idle) return true;
    if (RATE_LIMITED.test(err) && retry < RETRY_DELAYS_MS.length) {
      onWait(RETRY_DELAYS_MS[retry], err);
      await sleep(RETRY_DELAYS_MS[retry++]);
      continue;
    }
    if (code !== 0 && isAlive()) throw new Error(err.trim().split("\n").slice(-2).join(" ") || `naive session stream exited ${code}`);
    return false;
  }
  return false;
}

/**
 * POST /api/run: one turn on Naïve's pi, streamed as NDJSON. Without `session` it creates a session
 * on `agent` with the message; with one it sends the message into that session. Both then stream the
 * turn until the session is idle. Calls Naïve refuses at capacity are retried with backoff, and the
 * time spent waiting is reported apart from the wall time so latency numbers stay honest.
 */
async function runNaive(req, res) {
  const t0 = performance.now();
  const got = await readMessage(req, res);
  if (!got) return;
  let session = got.body.session ? String(got.body.session) : "";
  const agent = String(got.body.agent ?? "");
  if (session && !/^ses_[a-z0-9]{10,40}$/.test(session)) return send(res, 400, { error: "bad session id" });
  if (!session && !/^agt_[a-z0-9]{10,40}$/.test(agent)) return send(res, 400, { error: "pick an agent" });

  res.writeHead(200, { "content-type": "application/x-ndjson", "cache-control": "no-store" });
  const write = (obj) => res.write(JSON.stringify(obj) + "\n");
  let alive = true;
  let child = null;
  res.on("close", () => {
    alive = false; // the page went away mid-turn: stop the CLI rather than leave it running
    if (child && child.exitCode === null) child.kill("SIGTERM");
  });
  const timer = setTimeout(() => {
    alive = false;
    if (child && child.exitCode === null) child.kill("SIGTERM");
  }, TURN_TIMEOUT_MS);
  let waitMs = 0;
  const onWait = (ms, why) => {
    waitMs += ms;
    write({ kind: "retry", ms, reason: why.split("(req_")[0].trim() });
  };
  try {
    let afterSeq = 0;
    if (session) {
      const accepted = await naiveRetry(["session", "send", "--session", session, "--message", got.message], onWait);
      afterSeq = accepted.accepted_seq ?? 0;
    } else {
      const created = await naiveRetry(["session", "create", "--agent", agent, "--message", got.message, "--budget-usd", SESSION_CAP_USD], onWait);
      session = created.id;
      write({ kind: "session", session: created }); // the page learns the id even if the turn then fails
    }
    const idle = await streamNaiveTurn({
      session, afterSeq, onWait,
      onEvent: (ev) => write({ kind: "event", event: ev }),
      isAlive: () => alive,
      setChild: (p) => (child = p),
    });
    if (!idle) write({ kind: "error", message: alive ? "the turn ended without finishing" : `no answer after ${TURN_TIMEOUT_MS / 1000} s, so it was stopped` });
    write({ kind: "session", session: await naiveRetry(["session", "get", session], onWait) });
  } catch (e) {
    write({ kind: "error", message: e.message });
  } finally {
    clearTimeout(timer);
  }
  write({ kind: "wall", ms: Math.round(performance.now() - t0), waitMs });
  res.end();
}

/** POST /api/hermes: one Hermes turn on this machine, in the named conversation if given. */
async function runHermes(req, res) {
  const t0 = performance.now();
  const got = await readMessage(req, res);
  if (!got) return;
  if (!existsSync(HERMES)) return send(res, 503, { error: "Hermes isn't installed on this machine" });
  // Optional model to match the pi agent (e.g. gpt-6-sol); otherwise Hermes's configured default.
  const model = got.body.model == null ? "" : String(got.body.model);
  if (model && !/^gpt-[0-9a-z.\-]{1,40}$/.test(model)) return send(res, 400, { error: "unsupported Hermes model" });
  // Optional reasoning effort (Hermes's --reasoning levels); empty means Hermes's default (medium).
  const effort = got.body.effort == null ? "" : String(got.body.effort);
  if (effort && !REASONING_LEVELS.has(effort)) return send(res, 400, { error: "unsupported reasoning effort" });
  // A conversation is a Hermes session found by title; model, toolsets and effort are fixed at creation.
  const conversation = got.body.conversation == null ? "" : String(got.body.conversation);
  if (conversation && !/^console-[a-z0-9-]{4,60}$/.test(conversation)) return send(res, 400, { error: "bad conversation id" });
  stream(req, res, {
    cmd: HERMES,
    args: ["chat", "-q", got.message, "--oneshot", "--format", "stream-json", "--source", "tool", "-t", HERMES_TOOLSETS,
      ...(model ? ["-m", model] : []), ...(effort ? ["--reasoning", effort] : []),
      ...(conversation ? ["-c", conversation, "--create-if-missing"] : [])],
    cwd: tmpdir(),
    t0,
    onLine: (line, write) => {
      if (!line.startsWith("{")) return false;
      write({ kind: "hermes", event: JSON.parse(line) });
      return true;
    },
    onEnd: () => {},
  });
}

// `hermes chat --reasoning` levels. Naïve agents (pi) have no effort setting, so this is Hermes-only.
const REASONING_LEVELS = new Set(["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]);

/** GET /api/hermes/memory: Hermes's long-term notes, injected into every new conversation. */
async function hermesMemory(res) {
  const files = [];
  for (const name of ["USER.md", "MEMORY.md"]) {
    const path = join(HERMES_HOME, "memories", name);
    try {
      const s = await stat(path);
      files.push({ name, text: (await readFile(path, "utf8")).slice(0, 20_000), mtime: s.mtimeMs });
    } catch {
      files.push({ name, text: null, mtime: null });
    }
  }
  return send(res, 200, { files });
}

const textOf = (c) =>
  typeof c === "string" ? c : Array.isArray(c) ? c.map((p) => p?.text ?? "").join("") : c == null ? "" : JSON.stringify(c);
// Hermes ends every compaction summary with this marker (agent/context_compressor.py).
const SUMMARY_END = "END OF CONTEXT SUMMARY";

/** GET /api/hermes/session?id=…: what Hermes holds for one conversation, from `hermes sessions export`. */
async function hermesSession(res, id) {
  if (!/^local-[a-f0-9]{16,128}$/.test(id ?? "")) return send(res, 400, { error: "bad Hermes session id" });
  const out = join(tmpdir(), `hermes-export-${process.pid}-${Date.now()}.jsonl`);
  try {
    await runCmd(HERMES, ["sessions", "export", out, "--session-id", id, "--format", "jsonl"]);
    const raw = JSON.parse((await readFile(out, "utf8")).split("\n")[0]);
    let anchor = null;
    try {
      anchor = JSON.parse(raw.model_config ?? "{}")._usage_anchor ?? null;
    } catch {
      /* no usage anchor yet */
    }
    const messages = (raw.messages ?? []).slice(-300).map((m) => {
      const text = textOf(m.content);
      const summary = text.includes(SUMMARY_END) || /compact|summary/i.test(m.display_kind ?? "");
      return {
        role: m.role,
        tool: m.tool_name ?? null,
        text: summary ? text.slice(0, 8000) : text.slice(0, 400),
        compacted: Boolean(m.compacted),
        active: m.active !== 0 && m.active !== false,
        summary,
      };
    });
    return send(res, 200, {
      id,
      message_count: raw.message_count ?? messages.length,
      // Prompt tokens of the most recent model call: the size of the context Hermes is carrying now.
      context_tokens: anchor?.prompt_tokens ?? null,
      input_tokens: raw.input_tokens ?? null,
      output_tokens: raw.output_tokens ?? null,
      system_prompt: (raw.system_prompt ?? "").slice(0, 40_000),
      messages,
    });
  } catch (e) {
    return send(res, 502, { error: e.message });
  } finally {
    await rm(out, { force: true });
  }
}

createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", ORIGIN);
  const { pathname } = url;
  try {
    if (req.method === "GET" && pathname === "/") {
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      return res.end(await readFile(PAGE));
    }
    if (!pathname.startsWith("/api/")) return send(res, 404, { error: "not found" });
    if (!sameOrigin(req)) return send(res, 403, { error: "this API only answers its own page" });

    if (req.method === "GET" && pathname === "/api/whoami") {
      const w = await naiveJson(["whoami"]);
      return send(res, 200, { org: w.organization, scopes: w.scopes, principal: w.principal?.type, hermes: existsSync(HERMES) });
    }
    if (req.method === "GET" && pathname === "/api/agents") {
      const a = await naiveJson(["agent", "list"]);
      return send(res, 200, (a.data ?? []).map(({ id, name, model, harness }) => ({ id, name, model, harness })));
    }
    if (req.method === "POST" && pathname === "/api/run") return runNaive(req, res);
    if (req.method === "POST" && pathname === "/api/hermes") return runHermes(req, res);
    if (req.method === "GET" && pathname === "/api/hermes/memory") return hermesMemory(res);
    if (req.method === "GET" && pathname === "/api/hermes/session") return hermesSession(res, url.searchParams.get("id"));
    if (req.method === "GET" && pathname.startsWith("/api/docs/")) {
      const doc = DOCS[pathname.slice("/api/docs/".length)];
      if (!doc) return send(res, 404, { error: "unknown document" });
      return send(res, 200, { text: await readFile(doc, "utf8") });
    }
    return send(res, 404, { error: "not found" });
  } catch (e) {
    return send(res, 502, { error: e.message });
  }
}).listen(PORT, HOST, () => console.log(`Naïve console on ${ORIGIN}`));
