// Naïve mini console: a local page that talks to the real Naïve org.
//
// The server never sees an API key. It runs the `naive` CLI, which uses the
// login you stored with `naive login`, and streams the CLI's JSON events to
// the page. Run: node prototypes/naive-console/server.mjs, open http://127.0.0.1:5180
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";

const HOST = "127.0.0.1";
const PORT = Number(process.env.PORT ?? 5180);
const ORIGIN = `http://${HOST}:${PORT}`;
const SESSION_CAP_USD = "0.25"; // hard cap on every run, whatever the agent's own caps
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

/** POST /api/run: one new session per message, streamed back as NDJSON. */
async function run(req, res) {
  let body;
  try {
    body = await readBody(req);
  } catch {
    return send(res, 400, { error: "bad JSON body" });
  }
  const agent = String(body.agent ?? "");
  const message = String(body.message ?? "").trim();
  if (!/^agt_[a-z0-9]{10,40}$/.test(agent)) return send(res, 400, { error: "pick an agent" });
  if (!message || message.length > 4000) return send(res, 400, { error: "message must be 1-4000 characters" });

  res.writeHead(200, { "content-type": "application/x-ndjson", "cache-control": "no-store" });
  const p = spawn("naive", ["run", "--agent", agent, "--message", message, "--budget-usd", SESSION_CAP_USD], {
    stdio: ["ignore", "pipe", "pipe"],
  });
  req.on("close", () => {
    if (p.exitCode === null) p.kill("SIGTERM");
  });

  // Events arrive one JSON object per line; the final session object is pretty-printed.
  let buf = "";
  let tail = "";
  p.stdout.on("data", (d) => {
    buf += d;
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i);
      buf = buf.slice(i + 1);
      if (line.startsWith('{"id":"evt_')) res.write(JSON.stringify({ kind: "event", event: JSON.parse(line) }) + "\n");
      else tail += line + "\n";
    }
  });
  let err = "";
  p.stderr.on("data", (d) => (err += d));
  p.on("error", (e) => {
    res.write(JSON.stringify({ kind: "error", message: e.message }) + "\n");
    res.end();
  });
  p.on("close", (code) => {
    tail += buf;
    let session = null;
    try {
      session = JSON.parse(tail.trim());
    } catch {
      /* no final object, e.g. the run failed before a session existed */
    }
    if (session) res.write(JSON.stringify({ kind: "session", session }) + "\n");
    if (code !== 0) res.write(JSON.stringify({ kind: "error", message: err.trim() || `naive exited ${code}` }) + "\n");
    res.end();
  });
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
      return send(res, 200, { org: w.organization, scopes: w.scopes, principal: w.principal?.type });
    }
    if (req.method === "GET" && pathname === "/api/agents") {
      const a = await naiveJson(["agent", "list"]);
      return send(res, 200, (a.data ?? []).map(({ id, name, model, harness }) => ({ id, name, model, harness })));
    }
    if (req.method === "POST" && pathname === "/api/run") return run(req, res);
    return send(res, 404, { error: "not found" });
  } catch (e) {
    return send(res, 502, { error: e.message });
  }
}).listen(PORT, HOST, () => console.log(`Naïve console on ${ORIGIN}`));
