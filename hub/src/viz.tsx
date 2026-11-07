import { useEffect, useState, type ReactNode } from "react";
import type { Layer } from "./content/meta";

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
type NodeId = "naive" | "board" | "romulus" | "hermes" | "armhost" | "so101" | "models" | "sim";

const NODES: { id: NodeId; x: number; y: number; title: string; sub: string; layer: Layer; line: string; href: string }[] = [
  { id: "naive", x: 40, y: 60, title: "Naïve", sub: "company agents", layer: "naive", line: "Runs the company: orders, board, inbox, payments, budgets. Its SDK is required.", href: "#hosts" },
  { id: "board", x: 400, y: 60, title: "Board", sub: "one card per arm job", layer: "naive", line: "Where the company and the bench meet. The bench pulls the next card (proposed).", href: "#infra" },
  { id: "romulus", x: 760, y: 60, title: "Romulus", sub: "incidents → versions · Kent", layer: "heal", line: "Kent's v0: incidents in, held fixes locked into versions, replay and one canary, then auto promote or rollback. A kill switch is the only human control.", href: "#interfaces" },
  { id: "hermes", x: 400, y: 240, title: "Hermes", sub: "bench agent + arm plugin", layer: "hermes", line: "Takes a card, decides each step, calls arm tools. A guard hook checks every call.", href: "#hosts" },
  { id: "armhost", x: 400, y: 420, title: "Pi 5", sub: "app · healer · visor", layer: "edge", line: "The edge computer: robot app, a healer agent with a local model, and the visor that installs versions. Owner TBD (D14).", href: "#interfaces" },
  { id: "so101", x: 40, y: 420, title: "SO-101", sub: "servos · camera · sonar", layer: "edge", line: "The red follower arm, a USB camera, an HC-SR04 ultrasonic sensor, an STM32 board and a physical e-stop.", href: "#lanes" },
  { id: "models", x: 760, y: 240, title: "Models", sub: "vLLM · OpenAI", layer: "compute", line: "The model Hermes calls, served on a RunPod GPU, plus OpenAI for comparison.", href: "#ours" },
  { id: "sim", x: 760, y: 420, title: "NVIDIA sim", sub: "Isaac · LeIsaac · training", layer: "sim", line: "Tests every new policy before the arm runs it, and drills faults safely.", href: "#ours" },
];

const EDGES: { id: string; d: string; label: string; lx: number; ly: number; dur: number; anchor?: "start" | "middle" | "end"; rotate?: boolean }[] = [
  { id: "jobs", d: "M240 95 H400", label: "jobs", lx: 320, ly: 86, dur: 2.2 },
  { id: "pull", d: "M500 130 V240", label: "card pull", lx: 510, ly: 190, dur: 2.2, anchor: "start" },
  { id: "calls", d: "M500 310 V420", label: "tool calls", lx: 510, ly: 370, dur: 2.2, anchor: "start" },
  { id: "goals", d: "M400 448 H240", label: "joint goals", lx: 320, ly: 440, dur: 2.2 },
  { id: "health", d: "M240 470 H400", label: "temp · load", lx: 320, ly: 490, dur: 2.6 },
  { id: "tlm", d: "M600 445 H655 V125 Q655 110 670 110 H760", label: "incidents", lx: 668, ly: 300, dur: 3.2, rotate: true },
  { id: "incident", d: "M860 60 V40 Q860 28 848 28 H152 Q140 28 140 40 V60", label: "work orders → parts call", lx: 500, ly: 22, dur: 4.2 },
  { id: "ask", d: "M600 262 H760", label: "model calls", lx: 712, ly: 254, dur: 1.8 },
  { id: "answer", d: "M760 290 H600", label: "", lx: 0, ly: 0, dur: 1.8 },
  { id: "policy", d: "M760 455 H600", label: "policy ✓", lx: 712, ly: 447, dur: 2.6 },
];

export function SystemLoop() {
  const reduced = useReducedMotion();
  const [sel, setSel] = useState<NodeId>("hermes");
  const node = NODES.find((n) => n.id === sel)!;
  return (
    <div className="loop-wrap">
      <div className="loop-scroll">
        <svg className="loop" viewBox="0 0 1000 540" role="group" aria-label="How the system fits together. Select a box to read one line about it.">
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0 10 5 0 10Z" className="arrowhead" />
            </marker>
          </defs>
          <rect className="band" x="10" y="10" width="980" height="150" rx="10" />
          <rect className="band" x="10" y="190" width="630" height="340" rx="10" />
          <rect className="band band-gpu" x="670" y="190" width="320" height="340" rx="10" />
          <text className="band-label" x="24" y="52">cloud</text>
          <text className="band-label" x="24" y="214">bench</text>
          <text className="band-label" x="684" y="214">RunPod GPUs</text>

          {EDGES.map((e) => (
            <g key={e.id}>
              <path className="edge" d={e.d} markerEnd="url(#arrow)" />
              {!reduced ? (
                <circle r="4.5" className="packet">
                  <animateMotion dur={`${e.dur}s`} repeatCount="indefinite" path={e.d} />
                </circle>
              ) : null}
              {e.label ? (
                <text
                  className="edge-label"
                  x={e.lx}
                  y={e.ly}
                  textAnchor={e.anchor ?? "middle"}
                  transform={e.rotate ? `rotate(-90 ${e.lx} ${e.ly})` : undefined}
                >
                  {e.label}
                </text>
              ) : null}
            </g>
          ))}

          {NODES.map((n) => (
            <g
              key={n.id}
              className={`node l-${n.layer}${sel === n.id ? " on" : ""}`}
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
              <rect x={n.x} y={n.y} width="200" height="70" rx="8" />
              <text className="node-title" x={n.x + 16} y={n.y + 30}>
                {n.title}
              </text>
              <text className="node-sub" x={n.x + 16} y={n.y + 52}>
                {n.sub}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <div className={`loop-info l-${node.layer}`} aria-live="polite">
        <span className="dot" aria-hidden="true" />
        <b>{node.title}</b>
        <span>{node.line}</span>
        <a href={node.href}>More ↓</a>
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
      <text className="edge-label" x="196" y="148" textAnchor="middle">out: pull cards</text>
      <text className="edge-label" x="526" y="148" textAnchor="middle">out: model calls</text>
      <text className="edge-label" x="372" y="140" textAnchor="start">or a tunnel in</text>
      <g className="box l-hermes">
        <rect x="245" y="168" width="230" height="64" rx="8" />
        <text x="360" y="194" textAnchor="middle">Bench</text>
        <text x="360" y="216" textAnchor="middle" className="tiny">Hermes + arm host · the hands</text>
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
