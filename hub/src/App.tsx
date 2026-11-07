import { useEffect, useRef, useState } from "react";
import { AS_OF, LAYER_NAME, REPO, type Layer, type Status } from "./content/meta";
import { Glance, Journey, Pyramid } from "./sections/Overview";
import { Infra } from "./sections/Infra";
import { Hosts } from "./sections/Hosts";
import { Lanes } from "./sections/Lanes";
import { OurLane } from "./sections/OurLane";
import { Interfaces } from "./sections/Interfaces";
import { Sponsor, Tools } from "./sections/Tools";
import { Event, Open } from "./sections/Event";
import { LayerChip, StatusChip } from "./ui";
import { Countdowns } from "./viz";

const NAV: { id: string; label: string }[] = [
  { id: "glance", label: "Overview" },
  { id: "journey", label: "One request" },
  { id: "pyramid", label: "Pyramid" },
  { id: "infra", label: "Infrastructure" },
  { id: "hosts", label: "Hosts" },
  { id: "lanes", label: "Team" },
  { id: "ours", label: "Sim + control" },
  { id: "interfaces", label: "Self-healing" },
  { id: "sponsor", label: "Sponsor" },
  { id: "tools", label: "Tools" },
  { id: "event", label: "Timeline" },
  { id: "open", label: "Open" },
];

const LEGEND_LAYERS: Layer[] = ["naive", "hermes", "compute", "edge", "sim", "heal"];
const LEGEND_STATUS: Status[] = ["decided", "proposed", "tentative", "open"];

/** Highlights the section being read and keeps its link visible in the bar. */
function useActiveSection(ids: string[]) {
  const [active, setActive] = useState(ids[0]);
  useEffect(() => {
    const seen = new Map<string, boolean>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) seen.set(e.target.id, e.isIntersecting);
        const first = ids.find((id) => seen.get(id));
        if (first) setActive(first);
      },
      { rootMargin: "-72px 0px -55% 0px", threshold: 0 },
    );
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) io.observe(el);
    }
    return () => io.disconnect();
  }, [ids]);
  return active;
}

const IDS = NAV.map((n) => n.id);

export default function App() {
  const active = useActiveSection(IDS);
  const listRef = useRef<HTMLOListElement>(null);

  // Sections render after load, so the browser's own jump to #name misses. Redo it once.
  useEffect(() => {
    const id = decodeURIComponent(window.location.hash.slice(1));
    if (id) document.getElementById(id)?.scrollIntoView();
  }, []);

  useEffect(() => {
    const list = listRef.current;
    const link = list?.querySelector<HTMLAnchorElement>(`a[href="#${active}"]`);
    if (!list || !link) return;
    list.scrollTo({ left: Math.max(0, link.offsetLeft - list.clientWidth / 3), behavior: "smooth" });
  }, [active]);

  return (
    <>
      <header className="frame masthead">
        <p className="kicker">Naïve Autonomous Company Hackathon · Hermes Agent Plugin Hackathon</p>
        <div className="mast-row">
          <div className="mast-text">
            <h1>Arm Company Stack Map</h1>
            <p className="lede">An SO-101 robot-arm company on the hosts' stacks: Naïve is the office, RunPod the machine room, Hermes the hands.</p>
          </div>
          <Countdowns />
        </div>
        <div className="legend" aria-label="Legend">
          {LEGEND_LAYERS.map((l) => (
            <LayerChip key={l} layer={l} label={LAYER_NAME[l]} />
          ))}
          <span aria-hidden="true">·</span>
          {LEGEND_STATUS.map((s) => (
            <StatusChip key={s} s={s} />
          ))}
        </div>
      </header>

      <nav className="toc" aria-label="Contents">
        <ol ref={listRef}>
          {NAV.map((n) => (
            <li key={n.id}>
              <a href={`#${n.id}`} aria-current={active === n.id ? "true" : undefined}>
                {n.label}
              </a>
            </li>
          ))}
        </ol>
      </nav>

      <main className="frame">
        <Glance />
        <Journey />
        <Pyramid />
        <Infra />
        <Hosts />
        <Lanes />
        <OurLane />
        <Interfaces />
        <Sponsor />
        <Tools />
        <Event />
        <Open />
      </main>

      <footer className="frame">
        <p>
          As of <span className="mono">{AS_OF}</span> ·{" "}
          <a href={REPO} target="_blank" rel="noreferrer">
            repo
          </a>{" "}
          · source in <code>hub/</code>: edit <code>hub/src/content/*.ts</code>, run <code>npm run build</code>, republish.
        </p>
        <p>
          <span className="inferred">(inferred)</span> = worked out, not read in a source.
        </p>
      </footer>
    </>
  );
}
