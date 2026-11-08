import { useState } from "react";
import { OFFICE_VS_ROOM, PYRAMID, PYRAMID_SRC } from "../content/infra";
import { JOURNEY, JOURNEY_FAIL, JOURNEY_NOTES } from "../content/journey";
import { PLAIN } from "../content/plain";
import { LayerChip, More, Section, Sources } from "../ui";
import { DevOpsLoop, Icon, JourneyPlayer, Ranked, SystemLoop } from "../viz";
import { FeatureMap } from "./FeatureMap";

export function Glance() {
  const [view, setView] = useState<"flow" | "inside">("flow");
  return (
    <Section id="glance" title="The whole thing" sub="Tap a box. Dots are data moving. Colored boxes have work done or under way; black ones nobody has started.">
      <div className="seg-toggle view-toggle" role="group" aria-label="Diagram view">
        <button type="button" aria-pressed={view === "flow"} onClick={() => setView("flow")}>
          Flow
        </button>
        <button type="button" aria-pressed={view === "inside"} onClick={() => setView("inside")}>
          Features inside
        </button>
      </div>
      {view === "flow" ? <SystemLoop /> : <FeatureMap />}
      <div className="plain" aria-label="Words in one line">
        {PLAIN.map((p) => (
          <div key={p.term} className={`plain-card l-${p.layer}`}>
            <span className="plain-icon">
              <Icon name={p.icon} />
            </span>
            <b>{p.term}</b>
            <span className="small muted">{p.line}</span>
          </div>
        ))}
      </div>
      <h3>Demo features, by priority</h3>
      <Ranked
        label="Demo features by priority, highest first"
        layer="sim"
        items={[
          { icon: "alert", title: "Fault drill", sub: "break it, watch it heal" },
          { icon: "arm", title: "VLA on the arm", sub: "a policy we trained", flag: "team overview: no model training this hackathon" },
          { icon: "loop", title: "Self-reset loop", sub: "it puts the cube back" },
          { icon: "chart", title: "Benchmark chart", sub: "VLA vs LLM" },
        ]}
      />
    </Section>
  );
}

export function Journey() {
  return (
    <Section id="journey" title="Follow one request" sub={'"Wave if the weather is good, shake if not." Press play, or tap a step.'} status="proposed">
      <JourneyPlayer ok={JOURNEY} fail={[...JOURNEY.slice(0, 7), ...JOURNEY_FAIL]} />
      <More title="Shortcut and delay" hint="two notes">
        <dl className="kv">
          {JOURNEY_NOTES.map((n) => (
            <div key={n.k} style={{ display: "contents" }}>
              <dt>{n.k}</dt>
              <dd>{n.v}</dd>
            </div>
          ))}
        </dl>
      </More>
    </Section>
  );
}

export function Pyramid() {
  const [open, setOpen] = useState<string | null>(null);
  const n = PYRAMID.length;
  return (
    <Section id="pyramid" title="Tech stack pyramid" sub="Bottom-up: each tier stands on the ones below. Tap a tier for one line.">
      <div className="pyramid" role="list" aria-label="Tech stack, apex first">
        {PYRAMID.map((t, i) => {
          const isOpen = open === t.name;
          return (
            <div key={t.name} role="listitem" className={`tier l-${t.layer}`} style={{ ["--d" as string]: `${(n - i) * 110}ms` }}>
              <button type="button" className="tier-btn" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : t.name)}>
                <span className="t-icon">
                  <Icon name={t.icon} />
                </span>
                <span className="t-num">T{n - i}</span>
                <span className="t-name">{t.name}</span>
                {t.tag ? <span className="t-tag">{t.tag}</span> : null}
              </button>
              <ul className="chips">
                {t.items.map((it) => (
                  <li key={it.label}>
                    <LayerChip layer={it.layer} label={it.label} />
                  </li>
                ))}
              </ul>
              {isOpen ? (
                <p className="t-why">
                  {t.why} <span className="t-where">· {t.where}</span>
                </p>
              ) : null}
            </div>
          );
        })}
      </div>

      <div className="vs" role="table" aria-label="Naïve vs RunPod">
        <div className="vs-head" role="row">
          <span role="columnheader" />
          <span role="columnheader" className="l-naive">
            <Icon name="building" /> Naïve · the office
          </span>
          <span role="columnheader" className="l-compute">
            <Icon name="gpu" /> RunPod · the machine room
          </span>
        </div>
        {OFFICE_VS_ROOM.map((r) => (
          <div key={r.row} className="vs-row" role="row">
            <span role="rowheader">{r.row}</span>
            <span role="cell">{r.naive}</span>
            <span role="cell">{r.runpod}</span>
          </div>
        ))}
        <div className="vs-foot">
          <Icon name="plug" /> <b>Hermes on the bench</b> connects the office and the machine room to the arm.
        </div>
      </div>

      <h3>It's DevOps for a robot arm</h3>
      <p className="sub muted small">Kent's PRD is CI/CD for firmware, harness and models; Romulus is incident response. Each stage shows who owns it.</p>
      <DevOpsLoop />
      <Sources items={PYRAMID_SRC} />
    </Section>
  );
}
