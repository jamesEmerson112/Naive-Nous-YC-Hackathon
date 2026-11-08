import { useState } from "react";
import type { Layer } from "../content/meta";
import { countsOf, featuresIn, initial, ownersOf, PROTOTYPE, untouched, type BoxId, type Feature } from "../content/features";

const BOXES: { id: BoxId; title: string; layer: Layer }[] = [
  { id: "naive", title: "Naïve", layer: "naive" },
  { id: "board", title: "Board", layer: "naive" },
  { id: "models", title: "Models", layer: "compute" },
  { id: "sim", title: "NVIDIA sim", layer: "sim" },
  { id: "hermes", title: "Hermes", layer: "hermes" },
  { id: "romulus", title: "Romulus", layer: "heal" },
  { id: "pi", title: "Pi 5", layer: "edge" },
  { id: "so101", title: "SO-101", layer: "edge" },
  { id: "demo", title: "Demo and faults", layer: "neutral" },
  { id: "hub", title: "Hub and docs", layer: "neutral" },
];
const BANDS: { label: string; ids: BoxId[] }[] = [
  { label: "cloud · only gets called", ids: ["naive", "board", "models", "sim"] },
  { label: "bench network · travel router", ids: ["hermes", "romulus", "pi", "so101"] },
  { label: "across every box", ids: ["demo", "hub"] },
];

type Filter = "all" | "gaps" | "proto";
const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "gaps", label: "Not worked on" },
  { id: "proto", label: "Prototype path" },
];

const MARK: Record<Feature["status"], string> = { done: "✓", building: "●", gap: "?", hold: "‖" };
const SAY: Record<Feature["status"], string> = { done: "done", building: "being built", gap: "not worked on", hold: "on hold" };

function shown(f: Feature, filter: Filter) {
  if (filter === "gaps") return f.status === "gap" || f.status === "hold";
  if (filter === "proto") return PROTOTYPE.has(f.text);
  return true;
}

function Box({ id, title, layer, filter }: { id: BoxId; title: string; layer: Layer; filter: Filter }) {
  const items = featuresIn(id).filter((f) => shown(f, filter));
  const c = countsOf(id);
  const who = ownersOf(id);
  if (!items.length && filter !== "all") return null;
  return (
    <article className={`fbox l-${layer}${untouched(id) ? " untouched" : ""}`} aria-label={`${title}: ${c.done} done, ${c.building} being built, ${c.gap} not worked on`}>
      <header className="fbox-head">
        <b>{title}</b>
        <span className="fbox-who">{who.length ? who.join(" ") : "nobody yet"}</span>
        <span className="fbox-counts mono">
          <span title="done">✓{c.done}</span> <span title="being built">●{c.building}</span> <span className={c.gap ? "has-gap" : ""} title="not worked on">?{c.gap}</span>
        </span>
      </header>
      <ul className="fcells">
        {items.map((f) => (
          <li key={f.text} className={`fcell s-${f.status}`} title={`${f.text} · ${SAY[f.status]}${f.owners.length ? ` · ${f.owners.join(", ")}` : ""}`}>
            <span className="fcell-mark" aria-hidden="true">{MARK[f.status]}</span>
            <span className="fcell-text">{f.text}</span>
            {f.owners.length ? <span className="fcell-who mono">{f.owners.map(initial).join("")}</span> : null}
            <span className="sr-only">{SAY[f.status]}</span>
          </li>
        ))}
      </ul>
    </article>
  );
}

export function FeatureMap() {
  const [filter, setFilter] = useState<Filter>("all");
  return (
    <div className="fmap">
      <div className="fmap-bar">
        <div className="seg-toggle" role="group" aria-label="Which features to show">
          {FILTERS.map((x) => (
            <button key={x.id} type="button" aria-pressed={filter === x.id} onClick={() => setFilter(x.id)}>
              {x.label}
            </button>
          ))}
        </div>
        <ul className="fkey" aria-label="Key">
          <li><span className="fcell s-done">✓</span> done</li>
          <li><span className="fcell s-building">●</span> being built</li>
          <li><span className="fcell s-gap">?</span> not worked on</li>
        </ul>
      </div>
      {BANDS.filter((band) => band.ids.some((id) => featuresIn(id).some((f) => shown(f, filter)))).map((band) => (
        <section key={band.label} className="fband" aria-label={band.label}>
          <span className="band-label-html">{band.label}</span>
          <div className="fband-grid">
            {band.ids.map((id) => {
              const b = BOXES.find((x) => x.id === id)!;
              return <Box key={id} {...b} filter={filter} />;
            })}
          </div>
        </section>
      ))}
      <p className="small muted">
        Read from <code>features.txt</code> at build time. Claim a line with <code>&lt;--- Name</code>, push, rebuild, and it changes color here.
      </p>
    </div>
  );
}
