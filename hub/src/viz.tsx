import { useEffect, useState, type ReactNode } from "react";
import type { Layer } from "./content/meta";
import { ownersOf, untouched } from "./content/features";

/* ---------- Motion preference ---------- */
export function useReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const on = () => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

/* ---------- Icons: 20×20 strokes that take the text color ---------- */
const ICONS: Record<string, ReactNode> = {
  alert: <path d="M10 3 18 17H2L10 3Zm0 5v4m0 2.5v.5" />,
  arm: <path d="M3 17h7M6 17v-3l4-5 4 2M10 9l-1-4h4l1 3M14 11l2 1v2" />,
  loop: <path d="M5 8a6 6 0 0 1 10-2l1 1M15 12a6 6 0 0 1-10 2l-1-1M16 4v3h-3M4 16v-3h3" />,
  chart: <path d="M3 17h14M5 14V9M9 14V5M13 14v-7M17 14v-3" />,
  eye: <path d="M2 10s3-5 8-5 8 5 8 5-3 5-8 5-8-5-8-5Zm8 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z" />,
  shield: <path d="M10 2 16 5v5c0 4-3 6.5-6 8-3-1.5-6-4-6-8V5l6-3Zm-3 8 2 2 4-4" />,
  doc: <path d="M5 2h7l3 3v13H5V2Zm7 0v3h3M7 9h6M7 12h6M7 15h4" />,
  mail: <path d="M2 5h16v10H2V5Zm0 0 8 6 8-6" />,
  phone: <path d="M6 2h8v16H6V2Zm3 13h2" />,
  cube: <path d="m10 2 7 4v8l-7 4-7-4V6l7-4Zm0 8 7-4M10 10v8M10 10 3 6" />,
  gate: <path d="M10 2 18 10 10 18 2 10 10 2Zm-2 8 2 2 3-4" />,
  bug: <path d="M7 6a3 3 0 0 1 6 0v1H7V6Zm-1 2h8v5a4 4 0 0 1-8 0V8Zm-3 1h3m8 0h3M3 14h3m8 0h3M10 8v9" />,
  dollar: <path d="M10 2v16M14 6c0-1.5-2-2.5-4-2.5S6 4.5 6 6s2 2.2 4 2.8 4 1.5 4 3.2-2 2.5-4 2.5-4-1-4-2.5" />,
  building: <path d="M2 18h16M4 18V3h8v15M12 8h4v10M6 6h2M6 9h2M6 12h2M14 11h1M14 14h1" />,
  gpu: <path d="M5 5h10v10H5V5Zm3 3h4v4H8V8ZM7 2v3m3-3v3m3-3v3M7 15v3m3-3v3m3-3v3M2 7h3m-3 3h3m-3 3h3m10-6h3m-3 3h3m-3 3h3" />,
  plug: <path d="M7 2v4m6-4v4M5 6h10v3a5 5 0 0 1-10 0V6Zm5 8v4" />,
  lock: <path d="M5 9h10v9H5V9Zm2 0V6a3 3 0 0 1 6 0v3m-3 4v2" />,
};
export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[name]}
    </svg>
  );
}

/* ---------- Steps: a strip of icon cards; a highlight walks through them ---------- */
export function Steps({ items, layer = "neutral", label }: { items: { icon: IconName; title: string; sub?: string }[]; layer?: Layer; label: string }) {
  return (
    <ol className={`steps l-${layer}`} aria-label={label} style={{ ["--n" as string]: items.length }}>
      {items.map((s, i) => (
        <li key={s.title} className="step" style={{ ["--i" as string]: i }}>
          <span className="step-icon">
            <Icon name={s.icon} />
          </span>
          <span className="step-title">{s.title}</span>
          {s.sub ? <span className="step-sub">{s.sub}</span> : null}
        </li>
      ))}
    </ol>
  );
}

/* ---------- Ranked: a priority list, not a sequence (no arrows, no walking highlight) ---------- */
export function Ranked({ items, layer = "neutral", label }: { items: { icon: IconName; title: string; sub?: string; flag?: string }[]; layer?: Layer; label: string }) {
  return (
    <ol className={`ranked l-${layer}`} aria-label={label}>
      {items.map((s, i) => (
        <li key={s.title} className="rank" style={{ ["--r" as string]: i }}>
          <span className="rank-top">
            <span className="rank-badge">P{i + 1}</span>
            <span className="rank-meter" aria-label={`priority ${i + 1} of ${items.length}`}>
              {items.map((_, k) => (
                <span key={k} className={k < items.length - i ? "on" : ""} />
              ))}
            </span>
          </span>
          <span className="step-icon">
            <Icon name={s.icon} />
          </span>
          <span className="step-title">{s.title}</span>
          {s.sub ? <span className="step-sub">{s.sub}</span> : null}
          {s.flag ? (
            <span className="rank-flag">
              <Icon name="alert" size={14} /> {s.flag}
            </span>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

/* ---------- Avatars ---------- */
export function Avatars({ who, layer }: { who: string[]; layer: Layer }) {
  return (
    <span className={`avatars l-${layer}`}>
      {who.map((w) => (
        <span key={w} className={`avatar${w === "?" ? " unknown" : ""}`} title={w === "?" ? "unassigned" : w}>
          {w}
        </span>
      ))}
    </span>
  );
}

/* ---------- Countdowns ---------- */
export const DEADLINES: { label: string; at: string }[] = [
  { label: "Hacking ends", at: "2026-11-08T19:00:00Z" },
  { label: "Demos", at: "2026-11-08T21:00:00Z" },
  { label: "Hermes entry due", at: "2026-11-11T22:00:00Z" },
];

function useNow(ms = 30_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}

function left(ms: number) {
  if (ms <= 0) return "done";
  const m = Math.floor(ms / 60000);
  const d = Math.floor(m / 1440);
  const h = Math.floor((m % 1440) / 60);
  const mm = m % 60;
  return d > 0 ? `${d}d ${h}h` : `${h}h ${String(mm).padStart(2, "0")}m`;
}

export function Countdowns() {
  const now = useNow();
  return (
    <ul className="countdowns" aria-label="Time left">
      {DEADLINES.map((d) => (
        <li key={d.label}>
          <span className="cd-num">{left(Date.parse(d.at) - now)}</span>
          <span className="cd-label">{d.label}</span>
        </li>
      ))}
    </ul>
  );
}

/* ---------- Timeline with a live "now" marker (events evenly spaced) ---------- */
export function Timeline({ events }: { events: { at: string; label: string; when: string }[] }) {
  const now = useNow();
  const ts = events.map((e) => Date.parse(e.at));
  let pos = 0;
  if (now >= ts[ts.length - 1]) pos = 1;
  else if (now > ts[0]) {
    const k = ts.findIndex((t, i) => i < ts.length - 1 && now >= t && now < ts[i + 1]);
    const frac = (now - ts[k]) / (ts[k + 1] - ts[k]);
    pos = (k + frac) / (ts.length - 1);
  }
  return (
    <div className="timeline" role="img" aria-label={`Timeline: ${events.map((e) => `${e.when} ${e.label}`).join("; ")}`}>
      <div className="tl-track">
        <div className="tl-fill" style={{ width: `${pos * 100}%` }} />
        <div className="tl-now" style={{ left: `${pos * 100}%` }}>
          <span>now</span>
        </div>
      </div>
      <ol className="tl-events">
        {events.map((e, i) => (
          <li key={e.at} className={now >= ts[i] ? "past" : ""} style={{ left: `${(i / (events.length - 1)) * 100}%` }}>
            <span className="tl-dot" />
            <span className="tl-when">{e.when}</span>
            <span className="tl-label">{e.label}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/* ---------- System loop: the whole company in one animated picture ---------- */
export type NodeId = "naive" | "board" | "models" | "sim" | "hermes" | "romulus" | "pi" | "so101";

interface LoopNode {
  id: NodeId;
  x: number;
  y: number;
  title: string;
  sub: string;
  layer: Layer;
  href: string;
  owner: string;
  plain: string;
  a: { h: string; items: string[] };
  b: { h: string; items: string[] };
}

const NW = 180;

/** Owner initials drawn on a box, from features.txt; "?" when nobody has a line there yet. */
const whoOf = (id: NodeId) => {
  const w = ownersOf(id);
  return w.length ? w.slice(0, 3) : ["?"];
};
const NH = 66;

const NODES: LoopNode[] = [
  {
    id: "naive", x: 30, y: 62,  title: "Naïve", sub: "the office", layer: "naive", href: "#hosts", owner: "Kent set up the org · hires on hold (N1)",
    plain: "A hosted platform where AI agents run the company. Naïve provides the infrastructure the agents live on; we write the agents. Both notes are right. It never touches the arm. Today: Pro plan, $249.92, one CEO agent.",
    a: { h: "Naïve gives us", items: ["agent runtime (micro-VMs)", "Board", "persona inbox + phone", "vault for secrets", "apps + database", "CRM", "budgets + approvals", "audit log"] },
    b: { h: "We build on it", items: ["agent configs: planner, ops", "fleet-ops work-order agent", "customer intake", "Stripe store"] },
  },
  {
    id: "board", x: 300, y: 62,  title: "Board", sub: "shared to-do list", layer: "naive", href: "#infra", owner: "part of Naïve · Kent's setup",
    plain: "Naïve's shared to-do list, like Trello. Each job is a card that moves todo → doing → blocked → done.",
    a: { h: "Cards come from", items: ["Naïve agents: arm jobs", "Romulus: work orders"] },
    b: { h: "Cards go to", items: ["Hermes on the bench: arm jobs (scoped token, no tunnel)", "fleet-ops agent: work orders"] },
  },
  {
    id: "models", x: 560, y: 62,  title: "Models", sub: "RunPod · OpenAI", layer: "compute", href: "#ours", owner: "Kent: batch model proxy (D22) · bench key open",
    plain: "The LLMs the bench and Romulus call: open models on rented RunPod GPUs, or an API.",
    a: { h: "Options", items: ["Qwen3-VL-8B on vLLM (RunPod)", "OpenAI ($100 credits)", "Claude API (Kent's batch)"] },
    b: { h: "Needs", items: ["≥ 64K context for Hermes", "reads images", "tool calling", "an endpoint by 5:15 PM"] },
  },
  {
    id: "sim", x: 790, y: 62,  title: "NVIDIA sim", sub: "RunPod · canary", layer: "sim", href: "#ours", owner: "nobody yet · James + Claude later",
    plain: "A simulated arm on a RunPod GPU. It can be Romulus's canary: new versions run here before the real arm gets them.",
    a: { h: "Tools", items: ["Isaac Sim 5.1 + Isaac Lab", "LeIsaac SO-101 tasks", "MuJoCo + Menagerie"] },
    b: { h: "Our use", items: ["canary device sim-2", "fault drills", "VLA vs LLM benchmark"] },
  },
  {
    id: "hermes", x: 300, y: 252,  title: "Hermes", sub: "the hands", layer: "hermes", href: "#hosts", owner: "nobody yet · the bench control lane",
    plain: "Nous Research's agent, running at the bench. It pulls the next card and works the arm through our plugin.",
    a: { h: "Hermes gives us", items: ["agent loop", "plugins + tools", "pre_tool_call guard", "cron every 60 s", "gateway → phone", "any model endpoint"] },
    b: { h: "We build on it", items: ["arm plugin", "guard rules", "card-pulling job", "alerts"] },
  },
  {
    id: "romulus", x: 790, y: 330,  title: "Romulus", sub: "Kent's laptop", layer: "heal", href: "#interfaces", owner: "Kent",
    plain: "Kent's back office for the arm's health, on his laptop on the bench network. It logs incidents, turns fixes into versions, tests them on the sim canary, and rolls out or back.",
    a: { h: "What it does", items: ["incident timeline", "version registry", "batch lock-in", "replay + canary", "kill switch", "work-order cards"] },
    b: { h: "Built with", items: ["FastAPI :8000", "worker", "Postgres 17", "Docker Compose", "outbound calls only"] },
  },
  {
    id: "pi", x: 300, y: 432,  title: "Pi 5", sub: "app · healer · visor", layer: "edge", href: "#interfaces", owner: "Ishaan: app, healer, health checks",
    plain: "The computer next to the arm. It runs the robot app, a healer agent with a small local model, and the visor that installs new versions.",
    a: { h: "Runs", items: ["robot app (Python)", "healer + llama.cpp", "visor: check in, pull, install", "LeRobot 0.7", "incident outbox"] },
    b: { h: "Talks to Romulus", items: ["check-in every ~10 s", "incidents + recordings", "pulls firmware + harness"] },
  },
  {
    id: "so101", x: 30, y: 432,  title: "SO-101", sub: "the metal", layer: "edge", href: "#lanes", owner: "Olir + Andre: build · Olir: STM32",
    plain: "The physical arm and its sensors on the bench.",
    a: { h: "Hardware", items: ["6× STS3215 servos", "USB camera 720p", "HC-SR04 ultrasonic", "STM32 Nucleo-F042K6", "e-stop"] },
    b: { h: "STM32 firmware (Olir)", items: ["LED + boot banner", "1 s heartbeat", "build ID via make check"] },
  },
];

const EDGES: { id: string; from: NodeId; to: NodeId; d: string; label: string; lx: number; ly: number; dur: number; anchor?: "start" | "middle" | "end" }[] = [
  { id: "jobs", from: "naive", to: "board", d: "M210 84 H300", label: "jobs", lx: 255, ly: 76, dur: 2.2 },
  { id: "wo", from: "board", to: "naive", d: "M300 108 H210", label: "work orders", lx: 255, ly: 124, dur: 2.6 },
  { id: "pull", from: "hermes", to: "board", d: "M390 252 V128", label: "pulls cards", lx: 398, ly: 214, dur: 2.2, anchor: "start" },
  { id: "ask", from: "hermes", to: "models", d: "M480 270 H620 Q650 270 650 240 V128", label: "model calls", lx: 560, ly: 262, dur: 2.0 },
  { id: "calls", from: "hermes", to: "pi", d: "M390 318 V432", label: "tool calls", lx: 398, ly: 380, dur: 2.2, anchor: "start" },
  { id: "goals", from: "pi", to: "so101", d: "M300 456 H210", label: "goals", lx: 255, ly: 448, dur: 2.2 },
  { id: "health", from: "so101", to: "pi", d: "M210 480 H300", label: "health", lx: 255, ly: 500, dur: 2.6 },
  { id: "checkin", from: "pi", to: "romulus", d: "M480 452 H700 Q720 452 720 432 V372 Q720 352 740 352 H790", label: "check-in · incidents", lx: 600, ly: 444, dur: 2.8 },
  { id: "versions", from: "romulus", to: "pi", d: "M790 380 H760 Q745 380 745 395 V470 Q745 484 730 484 H480", label: "versions (pulled)", lx: 600, ly: 500, dur: 3.0 },
  { id: "cards", from: "romulus", to: "board", d: "M840 330 V196 Q840 180 824 180 H436 Q420 180 420 164 V128", label: "work-order cards", lx: 640, ly: 172, dur: 3.4 },
  { id: "canary", from: "sim", to: "romulus", d: "M900 128 V330", label: "canary", lx: 908, ly: 240, dur: 2.4, anchor: "start" },
];

export function SystemLoop() {
  const reduced = useReducedMotion();
  const [sel, setSel] = useState<NodeId>("naive");
  const node = NODES.find((n) => n.id === sel)!;
  const title = (id: NodeId) => NODES.find((n) => n.id === id)!.title;
  const links = EDGES.filter((e) => e.from === sel || e.to === sel);
  return (
    <div className="loop-wrap">
      <div className="loop-scroll">
        <svg className="loop" viewBox="0 0 1000 540" role="group" aria-label="How the system fits together. Select a box to see what it does and what it talks to.">
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0 10 5 0 10Z" className="arrowhead" />
            </marker>
          </defs>
          <rect className="band" x="10" y="10" width="980" height="150" rx="10" />
          <rect className="band" x="10" y="200" width="980" height="330" rx="10" />
          <text className="band-label" x="24" y="38">cloud · only gets called</text>
          <text className="band-label" x="24" y="226">bench network · travel router</text>

          {EDGES.map((e) => {
            const on = e.from === sel || e.to === sel;
            const cls = on ? "hot" : "dim";
            return (
              <g key={e.id}>
                <path className={`edge ${cls}`} d={e.d} markerEnd="url(#arrow)" />
                {!reduced ? (
                  <circle r="4.5" className={`packet ${cls}`}>
                    <animateMotion dur={`${e.dur}s`} repeatCount="indefinite" path={e.d} />
                  </circle>
                ) : null}
                <text className={`edge-label ${cls}`} x={e.lx} y={e.ly} textAnchor={e.anchor ?? "middle"}>
                  {e.label}
                </text>
              </g>
            );
          })}

          {NODES.map((n) => (
            <g
              key={n.id}
              className={`node l-${n.layer}${sel === n.id ? " on" : ""}${untouched(n.id) ? " untouched" : ""}`}
              role="button"
              tabIndex={0}
              aria-pressed={sel === n.id}
              aria-label={`${n.title}: ${n.sub}`}
              onClick={() => setSel(n.id)}
              onKeyDown={(ev) => {
                if (ev.key === "Enter" || ev.key === " ") {
                  ev.preventDefault();
                  setSel(n.id);
                }
              }}
            >
              <rect x={n.x} y={n.y} width={NW} height={NH} rx="8" />
              {whoOf(n.id).map((w, i) => (
                <g key={w} className={`badge${w === "?" ? " unknown" : ""}`}>
                  <circle cx={n.x + NW - 16 - i * 22} cy={n.y + 18} r="10" />
                  <text x={n.x + NW - 16 - i * 22} y={n.y + 22} textAnchor="middle">
                    {w}
                  </text>
                </g>
              ))}
              <text className="node-title" x={n.x + 14} y={n.y + 28}>
                {n.title}
              </text>
              <text className="node-sub" x={n.x + 14} y={n.y + 50}>
                {n.sub}
              </text>
            </g>
          ))}
        </svg>
      </div>

      <div className="owners-row" aria-label="Who owns which box">
        <span className="eyebrow">Owners</span>
        {NODES.filter((n) => !untouched(n.id)).map((n) => (
          <button key={n.id} type="button" className={`chip owner-chip l-${n.layer}`} onClick={() => setSel(n.id)}>
            <span className="dot" aria-hidden="true" />
            {n.title}: {n.owner}
          </button>
        ))}
        <span className="chip">Ishaan: overview + fault catalog (spans boxes)</span>
      </div>
      <div className="owners-row gaps" aria-label="Boxes nobody owns yet">
        <span className="eyebrow">Black · not worked on yet</span>
        {NODES.filter((n) => untouched(n.id)).map((n) => (
          <button key={n.id} type="button" className="chip gap-chip" onClick={() => setSel(n.id)}>
            ? {n.title}
          </button>
        ))}
      </div>

      <div key={node.id} className={`zoom l-${node.layer}`} aria-live="polite">
        <div className="zoom-head">
          <span className="dot" aria-hidden="true" />
          <b className="zoom-title">{node.title}</b>
          <span className="chip">{node.owner}</span>
          <a href={node.href}>More ↓</a>
        </div>
        <p className="zoom-plain">{node.plain}</p>
        <div className="zoom-cols">
          {[node.a, node.b].map((col) => (
            <div key={col.h}>
              <span className="eyebrow">{col.h}</span>
              <ul className="chips">
                {col.items.map((it) => (
                  <li key={it}>
                    <span className="chip">{it}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="zoom-links">
          <span className="eyebrow">Talks to · tap to jump</span>
          <ul className="chips">
            {links.map((e) => {
              const out = e.from === sel;
              const other = out ? e.to : e.from;
              return (
                <li key={e.id}>
                  <button type="button" className="chip linkchip" onClick={() => setSel(other)}>
                    {out ? "→" : "←"} {title(other)} · {e.label}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </div>
  );
}

/* ---------- Canary gate: a new policy must pass sim before the arm ---------- */
export function CanaryFlow() {
  const reduced = useReducedMotion();
  const passPath = "M150 85 H420 M480 85 H520 Q540 85 548 58 L556 42";
  const failPath = "M150 85 H420 M480 85 H520 Q540 85 548 112 L556 128";
  return (
    <svg className="mini" viewBox="0 0 720 170" role="img" aria-label="Canary gate: a new policy runs N tries in sim; a pass goes to the arm, a fail rolls back to the last good version.">
      <defs>
        <marker id="arrow2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0 10 5 0 10Z" className="arrowhead" />
        </marker>
      </defs>
      <g className="box l-sim">
        <rect x="20" y="60" width="130" height="50" rx="8" />
        <text x="85" y="91" textAnchor="middle">policy v2</text>
      </g>
      <path className="edge" d="M150 85 H230" markerEnd="url(#arrow2)" />
      <g className="box l-sim">
        <rect x="230" y="60" width="140" height="50" rx="8" />
        <text x="300" y="91" textAnchor="middle">sim × 20 tries</text>
      </g>
      <path className="edge" d="M370 85 H420" markerEnd="url(#arrow2)" />
      <g className="box gate">
        <path d="M450 55 480 85 450 115 420 85Z" />
        <text x="450" y="135" textAnchor="middle" className="tiny">beats v1?</text>
      </g>
      <path className="edge" d="M480 85 H520 Q540 85 548 58 L556 42" markerEnd="url(#arrow2)" />
      <path className="edge" d="M480 85 H520 Q540 85 548 112 L556 128" markerEnd="url(#arrow2)" />
      <g className="box l-edge">
        <rect x="560" y="15" width="140" height="44" rx="8" />
        <text x="630" y="43" textAnchor="middle">✓ to the arm</text>
      </g>
      <g className="box l-heal">
        <rect x="560" y="111" width="140" height="44" rx="8" />
        <text x="630" y="139" textAnchor="middle">✗ rollback → v1</text>
      </g>
      {!reduced ? (
        <>
          <circle r="5" className="packet">
            <animateMotion dur="4s" repeatCount="indefinite" path={passPath} />
          </circle>
          <circle r="5" className="packet bad">
            <animateMotion dur="4s" begin="2s" repeatCount="indefinite" path={failPath} />
          </circle>
        </>
      ) : null}
    </svg>
  );
}

/* ---------- Reset loop: each success is the next try's reset ---------- */
export function ResetLoop() {
  const reduced = useReducedMotion();
  const go = "M150 120 C 200 30 320 30 370 120";
  const back = "M370 170 C 320 250 200 250 150 170";
  return (
    <svg className="mini" viewBox="0 0 520 270" role="img" aria-label="Reset loop: move the cube from bin A to bin B, then back from B to A. Each success sets up the next try.">
      <defs>
        <marker id="arrow3" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0 10 5 0 10Z" className="arrowhead" />
        </marker>
      </defs>
      <path className="edge" d={go} markerEnd="url(#arrow3)" />
      <path className="edge" d={back} markerEnd="url(#arrow3)" />
      <text className="edge-label" x="260" y="44" textAnchor="middle">"to the right bin"</text>
      <text className="edge-label" x="260" y="250" textAnchor="middle">"to the left bin"</text>
      <g className="box l-edge">
        <rect x="70" y="120" width="120" height="50" rx="6" />
        <text x="130" y="151" textAnchor="middle">bin A</text>
      </g>
      <g className="box l-edge">
        <rect x="330" y="120" width="120" height="50" rx="6" />
        <text x="390" y="151" textAnchor="middle">bin B</text>
      </g>
      {!reduced ? (
        <>
          <rect width="12" height="12" x="-6" y="-6" rx="2" className="cube">
            <animateMotion dur="5s" repeatCount="indefinite" path={go} />
          </rect>
          <rect width="12" height="12" x="-6" y="-6" rx="2" className="cube">
            <animateMotion dur="5s" begin="2.5s" repeatCount="indefinite" path={back} />
          </rect>
        </>
      ) : null}
    </svg>
  );
}

/* ---------- Bench ↔ cloud bridge: neither cloud can reach the arm on its own ---------- */
export function Bridge() {
  const reduced = useReducedMotion();
  const toNaive = "M300 168 C 300 130 190 128 160 88";
  const toPod = "M420 168 C 420 130 530 128 560 88";
  return (
    <svg className="mini" viewBox="0 0 720 250" role="img" aria-label="Naïve and RunPod both run in the cloud. Neither can reach the arm unless the bench connects out to them, or a tunnel lets them in.">
      <defs>
        <marker id="arrow4" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0 10 5 0 10Z" className="arrowhead" />
        </marker>
      </defs>
      <rect className="band" x="10" y="8" width="700" height="96" rx="10" />
      <text className="band-label" x="360" y="30" textAnchor="middle">cloud · can't reach the arm</text>
      <g className="box l-naive">
        <rect x="40" y="36" width="230" height="56" rx="8" />
        <text x="155" y="60" textAnchor="middle">Naïve</text>
        <text x="155" y="80" textAnchor="middle" className="tiny">the office</text>
      </g>
      <g className="box l-compute">
        <rect x="450" y="36" width="230" height="56" rx="8" />
        <text x="565" y="60" textAnchor="middle">RunPod</text>
        <text x="565" y="80" textAnchor="middle" className="tiny">the machine room</text>
      </g>
      <path className="edge solid" d={toNaive} markerEnd="url(#arrow4)" />
      <path className="edge solid" d={toPod} markerEnd="url(#arrow4)" />
      <path className="edge tunnel" d="M360 104 V166" markerEnd="url(#arrow4)" />
      <text className="edge-label" x="196" y="148" textAnchor="middle">out: cards (poll)</text>
      <text className="edge-label" x="526" y="148" textAnchor="middle">out: model calls</text>
      <text className="edge-label" x="372" y="140" textAnchor="start">no tunnel in v0</text>
      <g className="box l-hermes">
        <rect x="245" y="168" width="230" height="64" rx="8" />
        <text x="360" y="194" textAnchor="middle">Bench</text>
        <text x="360" y="216" textAnchor="middle" className="tiny">Pi · Hermes · Romulus</text>
      </g>
      {!reduced ? (
        <>
          <circle r="4.5" className="packet">
            <animateMotion dur="2.4s" repeatCount="indefinite" path={toNaive} />
          </circle>
          <circle r="4.5" className="packet">
            <animateMotion dur="2.4s" begin="1.2s" repeatCount="indefinite" path={toPod} />
          </circle>
        </>
      ) : null}
    </svg>
  );
}

/* ---------- DevOps loop: the project is DevOps for a physical arm ---------- */
const DEVOPS: { stage: string; ours: string; layer: Layer; x: number; y: number; anchor: "start" | "middle" | "end" }[] = [
  { stage: "Plan", ours: "incident report in", layer: "heal", x: 260, y: 38, anchor: "middle" },
  { stage: "Build", ours: "batch lock-in", layer: "heal", x: 128, y: 146, anchor: "end" },
  { stage: "Test", ours: "replay + canary", layer: "sim", x: 260, y: 268, anchor: "middle" },
  { stage: "Release", ours: "versioned manifest (D3)", layer: "heal", x: 400, y: 214, anchor: "middle" },
  { stage: "Deploy", ours: "visor pulls + checks", layer: "edge", x: 540, y: 38, anchor: "middle" },
  { stage: "Operate", ours: "Pi app + Hermes", layer: "hermes", x: 672, y: 146, anchor: "start" },
  { stage: "Monitor", ours: "health tiers 1–3", layer: "edge", x: 540, y: 268, anchor: "middle" },
  { stage: "Respond", ours: "park → healer overlay", layer: "edge", x: 400, y: 92, anchor: "middle" },
];
const INFINITY = "M400 150 C 340 64 140 64 140 150 C 140 236 340 236 400 150 C 460 64 660 64 660 150 C 660 236 460 236 400 150 Z";

export function DevOpsLoop() {
  const reduced = useReducedMotion();
  return (
    <div className="loop-scroll">
      <svg className="loop devops" viewBox="0 0 800 300" role="img" aria-label="DevOps loop for the arm: plan, build, test in sim, release, deploy to the arm, operate, monitor, respond.">
        <path className="infinity" d={INFINITY} />
        <text className="band-label" x="260" y="156" textAnchor="middle">dev</text>
        <text className="band-label" x="540" y="156" textAnchor="middle">ops</text>
        {!reduced ? (
          <circle r="6" className="packet">
            <animateMotion dur="9s" repeatCount="indefinite" path={INFINITY} />
          </circle>
        ) : null}
        {DEVOPS.map((d) => (
          <g key={d.stage} className={`stage l-${d.layer}`}>
            <text className="stage-name" x={d.x} y={d.y} textAnchor={d.anchor}>
              {d.stage}
            </text>
            <text className="stage-ours" x={d.x} y={d.y + 17} textAnchor={d.anchor}>
              {d.ours}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

/* ---------- Request journey: a stepper that walks one request through the stack ---------- */
export function JourneyPlayer({ ok, fail }: { ok: { who: string; title: string; layer: Layer; icon: IconName; line: string }[]; fail: { who: string; title: string; layer: Layer; icon: IconName; line: string }[] }) {
  const reduced = useReducedMotion();
  const [mode, setMode] = useState<"ok" | "fail">("ok");
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(true);
  const hops = mode === "ok" ? ok : fail;
  const hop = hops[Math.min(i, hops.length - 1)];

  useEffect(() => {
    if (!playing || reduced) return;
    const t = window.setInterval(() => setI((x) => (x + 1) % hops.length), 2600);
    return () => window.clearInterval(t);
  }, [playing, reduced, hops.length]);

  const pick = (k: number) => {
    setI(k);
    setPlaying(false);
  };

  return (
    <div className="journey">
      <div className="journey-bar">
        <div className="seg-toggle" role="group" aria-label="Which path">
          <button type="button" aria-pressed={mode === "ok"} onClick={() => { setMode("ok"); setI(0); }}>
            Happy path
          </button>
          <button type="button" aria-pressed={mode === "fail"} onClick={() => { setMode("fail"); setI(0); }}>
            Something breaks
          </button>
        </div>
        <div className="journey-ctl">
          <button type="button" onClick={() => pick((i - 1 + hops.length) % hops.length)} aria-label="Previous step">◀</button>
          <button type="button" onClick={() => setPlaying((p) => !p)} aria-label={playing ? "Pause" : "Play"}>
            {playing && !reduced ? "Pause" : "Play"}
          </button>
          <button type="button" onClick={() => pick((i + 1) % hops.length)} aria-label="Next step">▶</button>
        </div>
      </div>
      <ol className="hops" aria-label="Steps">
        {hops.map((h, k) => (
          <li key={`${mode}-${k}`} className={`hop l-${h.layer}${k === i ? " on" : ""}${k < i ? " done" : ""}${mode === "fail" && k >= ok.length - 1 && k > 6 ? " bad" : ""}`}>
            <button type="button" onClick={() => pick(k)} aria-current={k === i ? "step" : undefined} aria-label={`${k + 1}. ${h.who}: ${h.title}`}>
              <span className="hop-icon">
                <Icon name={h.icon} />
              </span>
              <span className="hop-title">{h.title}</span>
              <span className="hop-who">{h.who}</span>
            </button>
          </li>
        ))}
      </ol>
      <div key={`${mode}-${i}`} className={`hop-card l-${hop.layer}`} aria-live="polite">
        <span className="hop-n">
          {i + 1}/{hops.length}
        </span>
        <b>{hop.who}</b>
        <span>{hop.line}</span>
      </div>
    </div>
  );
}

/* ---------- Schedule: time blocks on a real clock, with a live "now" line ---------- */
export function Schedule({ blocks, from, to }: { blocks: { id: string; what: string; from: string; to: string; flows?: string }[]; from: string; to: string }) {
  const now = useNow();
  const t0 = Date.parse(from);
  const t1 = Date.parse(to);
  const pct = (t: number) => Math.min(100, Math.max(0, ((t - t0) / (t1 - t0)) * 100));
  const fmt = (iso: string) => new Date(iso).toLocaleTimeString("en-US", { timeZone: "America/Los_Angeles", hour: "numeric", minute: "2-digit" });
  return (
    <div className="sched">
      <div className="sched-bar" role="img" aria-label={`Build plan from ${fmt(from)} to ${fmt(to)}: ${blocks.map((b) => `${b.id} ${b.what}`).join("; ")}`}>
        {blocks.map((b) => {
          const left = pct(Date.parse(b.from));
          const width = pct(Date.parse(b.to)) - left;
          const done = Date.parse(b.to) <= now;
          return (
            <span
              key={b.id}
              className={`sb${b.id === "ON" ? " night" : ""}${done ? " done" : ""}`}
              style={{ left: `${left}%`, width: `${width}%` }}
              title={`${b.id} · ${b.what} · ${fmt(b.from)}–${fmt(b.to)}`}
            >
              {width > 3.5 ? b.id : ""}
            </span>
          );
        })}
        {now > t0 && now < t1 ? (
          <span className="sched-now" style={{ left: `${pct(now)}%` }}>
            <span>now</span>
          </span>
        ) : null}
      </div>
      <div className="sched-scale">
        <span>Sat {fmt(from)}</span>
        <span>overnight run</span>
        <span>Sun {fmt(to)} · hacking ends</span>
      </div>
      <ol className="sched-list">
        {blocks.map((b) => (
          <li key={b.id} className={Date.parse(b.to) <= now ? "done" : ""}>
            <b>{b.id}</b> <span className="mono small muted">{fmt(b.from)}</span> {b.what} {b.flows ? <span className="chip">{b.flows}</span> : null}
          </li>
        ))}
      </ol>
    </div>
  );
}

/* ---------- Budget bar: planned RunPod spend against the cap ---------- */
export function BudgetBar({ cap, parts }: { cap: number; parts: { label: string; lo: number; hi: number; layer: Layer }[] }) {
  const hi = parts.reduce((s, p) => s + p.hi, 0);
  const lo = parts.reduce((s, p) => s + p.lo, 0);
  return (
    <div className="budget" role="img" aria-label={`Planned RunPod spend about $${lo}–${hi} of a $${cap} cap`}>
      <div className="budget-bar">
        {parts.map((p) => (
          <span key={p.label} className={`seg l-${p.layer}`} style={{ width: `${(p.hi / cap) * 100}%` }} title={`${p.label}: $${p.lo}–${p.hi}`}>
            <span>{p.label}</span>
          </span>
        ))}
        <span className="seg headroom" style={{ width: `${((cap - hi) / cap) * 100}%` }}>
          <span>headroom</span>
        </span>
      </div>
      <div className="budget-scale">
        <span>$0</span>
        <span>
          plan ≈ ${lo}–{hi}
        </span>
        <span>cap ${cap}</span>
      </div>
      <ul className="budget-legend">
        {parts.map((p) => (
          <li key={p.label} className={`l-${p.layer}`}>
            <span className="dot" aria-hidden="true" />
            {p.label} ${p.lo}–{p.hi}
          </li>
        ))}
      </ul>
    </div>
  );
}
