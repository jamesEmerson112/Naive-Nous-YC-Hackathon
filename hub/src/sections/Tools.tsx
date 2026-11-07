import { useState } from "react";
import { SPONSOR_PICK, SPONSOR_SRC } from "../content/journey";
import { TOOL_CATS, TOOLS, type ToolCat } from "../content/tools";
import { Section, Sources } from "../ui";

function Meter({ n, label }: { n: number; label: string }) {
  return (
    <span className="meter" aria-label={`${label}: ${n} of 3`}>
      {[1, 2, 3].map((k) => (
        <span key={k} className={k <= n ? "on" : ""} />
      ))}
    </span>
  );
}

export function Sponsor() {
  return (
    <Section id="sponsor" title="One sponsor track is enough" sub="Naïve already covers most of the company. Pick the sponsor that adds what Naïve can't do.">
      <div className="picks">
        {SPONSOR_PICK.map((s) => (
          <article key={s.name} className={`pick${s.pick ? " picked" : ""}`}>
            <div className="head">
              <h3>{s.name}</h3>
              {s.pick ? <span className="chip status decided">our pick (inferred)</span> : null}
            </div>
            <p className="small">{s.what}</p>
            <dl className="kv">
              <dt>Adds to Naïve</dt>
              <dd>
                <Meter n={s.adds} label="Adds to Naïve" />
              </dd>
              <dt>Fits the arm</dt>
              <dd>
                <Meter n={s.fit} label="Fits the arm story" />
              </dd>
            </dl>
            <p className="small muted">{s.story}</p>
            <p className="cost">{s.prize}</p>
            <p className="small muted">{s.setup}</p>
          </article>
        ))}
      </div>
      <p className="small muted">The Main track (Naïve) is the goal; a sponsor track is a bonus. Can one company win both? Ask the organizers.</p>
      <Sources items={SPONSOR_SRC} />
    </Section>
  );
}

type Filter = ToolCat | "All";

export function Tools() {
  const [filter, setFilter] = useState<Filter>("All");
  const [open, setOpen] = useState<string | null>(null);
  const shown = filter === "All" ? TOOLS : TOOLS.filter((t) => t.cat === filter);
  const options: Filter[] = ["All", ...TOOL_CATS];

  return (
    <Section id="tools" title="Tool catalog" sub="Everything we've collected. Tap a tool for details.">
      <div className="filters" role="group" aria-label="Filter tools by category">
        {options.map((o) => (
          <button key={o} type="button" aria-pressed={filter === o} onClick={() => setFilter(o)}>
            {o} · {o === "All" ? TOOLS.length : TOOLS.filter((t) => t.cat === o).length}
          </button>
        ))}
      </div>
      <div className="tool-grid">
        {shown.map((t) => {
          const isOpen = open === t.name;
          return (
            <article key={t.name} className={`tool l-${t.layer}${isOpen ? " open" : ""}`}>
              <button type="button" className="tool-btn" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : t.name)}>
                <span className="tool-name">{t.name}</span>
                <span className={`chip status ${t.use === "core" ? "decided" : t.use === "planned" ? "proposed" : t.use === "candidate" ? "open" : "ref"}`}>{t.use}</span>
                <span className="tool-role">{t.role}</span>
              </button>
              {isOpen ? (
                <div className="tool-more">
                  <p className="small">{t.what}</p>
                  {t.cost ? <p className="cost">{t.cost}</p> : null}
                  <Sources items={[t.src]} lead="Source" />
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </Section>
  );
}
