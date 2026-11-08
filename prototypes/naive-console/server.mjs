// Naïve console: send one message to Naïve's built-in `pi` loop (cloud) and to
// Hermes on this machine (which reaches Naïve over MCP), side by side.
//
// The server never sees an API key. It runs two CLIs that hold their own logins:
// `naive` (from `naive login`) and `hermes` (from `hermes auth add`, plus
// NAIVE_API_KEY in ~/.hermes/.env for the Naïve MCP server).
// Run: node prototypes/naive-console/server.mjs, open http://127.0.0.1:5180
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

const HOST = "127.0.0.1";
const PORT = Number(process.env.PORT ?? 5180);
const ORIGIN = `http://${HOST}:${PORT}`;
// Hard cap on every Naïve run, whatever the agent's own caps. Naïve refuses a run up front
// when its pre-flight quote (worst-case cost) is above this, so big models like gpt-6-sol need ~$1.
const SESSION_CAP_USD = "1.00";
const HERMES = process.env.HERMES_BIN ?? join(homedir(), ".local/bin/hermes");
// Hermes may call Naïve's MCP tools only: no terminal, files, browser or web on this machine.
// Hermes names an MCP server's toolset `mcp-<server>`; our server is `naive` in ~/.hermes/config.yaml.
const HERMES_TOOLSETS = "mcp-naive";
// `hermes chat --reasoning` levels. Naïve agents (pi) have no effort setting, so this is Hermes-only.
const REASONING_LEVELS = new Set(["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]);
const PAGE = new URL("./index.html", import.meta.url);

/** Run the naive CLI with fixed args (no shell) and return parsed JSON stdout. */
function naiveJson(args) {
  return new Promise((resolve, reject) => {
    const p = spawn("naive", args, { stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    let err = "";
    p.stdout.on("data", (d) => (out += d));
    p.stderr.on("data", (d) => (err += d));
    p.on("error", reject);
    p.on("close", (code) => {
      if (code !== 0) return reject(new Error((err || out).trim() || `naive exited ${code}`));
      try {
        resolve(JSON.parse(out));
      } catch {
        reject(new Error("naive printed something that is not JSON"));
      }
    });
  });
}

function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

/** Only this page may call the API: blocks other websites from spending credits via localhost. */
function sameOrigin(req) {
  return req.headers.origin === ORIGIN || req.headers["sec-fetch-site"] === "same-origin";
}

async function readBody(req, limit = 16_000) {
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
  if (!message || message.length > 4000) {
    send(res, 400, { error: "message must be 1-4000 characters" });
    return null;
  }
  return { body, message };
}

/**
 * Spawn a CLI and stream it to the page as NDJSON. `onLine(line, write)` handles
 * each stdout line; `onEnd(rest, write)` gets any unparsed tail. The page also gets
 * a server-measured wall time, so both loops are timed the same way.
 */
function stream(req, res, cmd, args, opts, onLine, onEnd) {
  res.writeHead(200, { "content-type": "application/x-ndjson", "cache-control": "no-store" });
  const write = (obj) => res.write(JSON.stringify(obj) + "\n");
  const started = performance.now();
  const p = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], ...opts });
  req.on("close", () => {
    if (p.exitCode === null) p.kill("SIGTERM");
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
    write({ kind: "error", message: e.message });
    res.end();
  });
  p.on("close", (code) => {
    onEnd(rest + buf, write);
    write({ kind: "wall", ms: Math.round(performance.now() - started) });
    if (code !== 0) write({ kind: "error", message: err.trim().split("\n").slice(-3).join(" ") || `${cmd} exited ${code}` });
    res.end();
  });
}

/** POST /api/run: a new Naïve session on the chosen agent (pi), streamed. */
async function runNaive(req, res) {
  const got = await readMessage(req, res);
  if (!got) return;
  const agent = String(got.body.agent ?? "");
  if (!/^agt_[a-z0-9]{10,40}$/.test(agent)) return send(res, 400, { error: "pick an agent" });
  stream(
    req,
    res,
    "naive",
    ["run", "--agent", agent, "--message", got.message, "--budget-usd", SESSION_CAP_USD],
    {},
    (line, write) => {
      // Events arrive one JSON object per line; the final session object is pretty-printed.
      if (!line.startsWith('{"id":"evt_')) return false;
      write({ kind: "event", event: JSON.parse(line) });
      return true;
    },
    (rest, write) => {
      try {
        write({ kind: "session", session: JSON.parse(rest.trim()) });
      } catch {
        /* no final object, e.g. the run failed before a session existed */
      }
    },
  );
}

/** POST /api/hermes: one Hermes one-shot run on this machine, streamed. */
async function runHermes(req, res) {
  const got = await readMessage(req, res);
  if (!got) return;
  if (!existsSync(HERMES)) return send(res, 503, { error: "Hermes isn't installed on this machine" });
  // Optional model to match the pi agent (e.g. gpt-6-sol); otherwise Hermes's configured default.
  const model = got.body.model == null ? "" : String(got.body.model);
  if (model && !/^gpt-[0-9a-z.\-]{1,40}$/.test(model)) return send(res, 400, { error: "unsupported Hermes model" });
  // Optional reasoning effort (Hermes's --reasoning levels); empty means Hermes's default (medium).
  const effort = got.body.effort == null ? "" : String(got.body.effort);
  if (effort && !REASONING_LEVELS.has(effort)) return send(res, 400, { error: "unsupported reasoning effort" });
  stream(
    req,
    res,
    HERMES,
    // --ignore-rules: no AGENTS.md, memory or skills injected, so the run is comparable.
    ["chat", "-q", got.message, "--oneshot", "--format", "stream-json", "--source", "tool", "--ignore-rules", "-t", HERMES_TOOLSETS,
      ...(model ? ["-m", model] : []), ...(effort ? ["--reasoning", effort] : [])],
    { cwd: tmpdir() },
    (line, write) => {
      if (!line.startsWith("{")) return false;
      write({ kind: "hermes", event: JSON.parse(line) });
      return true;
    },
    () => {},
  );
}

createServer(async (req, res) => {
  const { pathname } = new URL(req.url ?? "/", ORIGIN);
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
    return send(res, 404, { error: "not found" });
  } catch (e) {
    return send(res, 502, { error: e.message });
  }
}).listen(PORT, HOST, () => console.log(`Naïve console on ${ORIGIN}`));
