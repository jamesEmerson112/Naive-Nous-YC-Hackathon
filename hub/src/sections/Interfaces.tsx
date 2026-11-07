import { Fragment, useState } from "react";
import { CATALOG, COMPONENTS, DEVICE_LOOP, HEAL_SRC, HEAL_TERMS, HEALTH_TIERS, type Component } from "../content/heal";
import { DECISIONS, HOOKS, MILESTONES, ROMULUS_OPEN, ROMULUS_SRC, V0_LOOP, V0_SIDE, V0_STACK } from "../content/romulus";
import { API, PLUGIN_WIRING, TELEMETRY } from "../content/proposal";
import { More, Section, Sources, StatusChip, Table } from "../ui";
import { Icon, Steps } from "../viz";

function Catalog() {
  const [comp, setComp] = useState<Component | "all">("all");
  const list = comp === "all" ? CATALOG : CATALOG.filter((f) => f.comp === comp);
  const [selId, setSelId] = useState(CATALOG[0].id);
  const f = CATALOG.find((x) => x.id === selId) ?? CATALOG[0];
  const steps: [string, string, string?][] = [
    ["Plant it", f.plant || "—"],
    ["Spot it", f.detect],
    ["Fix it", f.fix],
    ["Check it", f.verify || "—"],
    ["Call a person", f.human || "not needed", f.human ? "human" : ""],
  ];
  return (
    <div className="catalog">
      <div className="filters" role="group" aria-label="Filter faults by part">
        <button type="button" aria-pressed={comp === "all"} onClick={() => setComp("all")}>
          all · {CATALOG.length}
        </button>
        {COMPONENTS.map((c) => (
          <button key={c.name} type="button" aria-pressed={comp === c.name} onClick={() => setComp(c.name)}>
            {c.name} · {CATALOG.filter((x) => x.comp === c.name).length}
          </button>
        ))}
      </div>
      <div className="faults" role="group" aria-label="Faults">
        {list.map((x) => (
          <button key={x.id} type="button" className="fault" aria-pressed={selId === x.id} onClick={() => setSelId(x.id)}>
            <Icon name={COMPONENTS.find((c) => c.name === x.comp)?.icon ?? "alert"} size={16} />
            <code>{x.id}</code>
            <span className="fault-name">{x.name}</span>
          </button>
        ))}
      </div>
      <div key={f.id} className="fault-flow five" aria-live="polite">
        {steps.map(([k, v, cls], i) => (
          <Fragment key={k}>
            {i > 0 ? (
              <span className="ff-arrow" aria-hidden="true">
                →
              </span>
            ) : null}
            <div className={cls === "human" ? "ff-human" : undefined}>
              <span className="eyebrow">{k}</span>
              <span>{v}</span>
            </div>
          </Fragment>
        ))}
      </div>
    </div>
  );
}

export function Interfaces() {
  const decided = DECISIONS.filter((d) => d.status === "decided").length;
  const totalHours = 10.5;
  return (
    <Section id="interfaces" title="Self-healing" layer="heal" owner="Ishaan's overview · Kent's v0" status="tentative" sub="The device heals first; the cloud locks good fixes in. Tap a fault to see how it's planted, caught and fixed.">
      <h3>On the device</h3>
      <Steps label="Device heal loop" layer="edge" items={DEVICE_LOOP} />
      <ul className="chips" aria-label="Health tiers">
        {HEALTH_TIERS.map((t) => (
          <li key={t.tier}>
            <span className="chip">
              <b>{t.tier}</b>&nbsp;{t.what}
            </span>
          </li>
        ))}
      </ul>

      <h3>In the cloud: Romulus v0</h3>
      <Steps label="Romulus v0 loop" layer="heal" items={V0_LOOP} />
      <div className="side3">
        {V0_SIDE.map((s) => (
          <div key={s.title} className="side-card l-heal">
            <Icon name={s.icon} />
            <b>{s.title}</b>
            <span className="small muted">{s.line}</span>
          </div>
        ))}
      </div>

      <h3>Fault catalog</h3>
      <Catalog />
      <Sources items={HEAL_SRC} />

      <div className="grid-2">
        <div className="panel">
          <div className="head">
            <h4>Decision log</h4>
            <span className="small muted">
              {decided} decided · D14 open · D15 pending
            </span>
          </div>
          <div className="decisions">
            {DECISIONS.map((d) => (
              <span key={d.id} className={`dec ${d.status}`} title={d.text}>
                {d.id}
              </span>
            ))}
          </div>
          <More title="Read all decisions" hint="one line each">
            <ul className="small">
              {DECISIONS.map((d) => (
                <li key={d.id}>
                  <b>{d.id}</b> {d.text} {d.status !== "decided" ? <StatusChip s={d.status} /> : null}
                </li>
              ))}
            </ul>
          </More>
        </div>
        <div className="panel">
          <div className="head">
            <h4>Kent's build plan</h4>
            <span className="small muted">~{totalHours} h · M0–M6</span>
          </div>
          <div className="msbar" role="img" aria-label="Milestones M0 to M6 by hours">
            {MILESTONES.map((m) => (
              <span key={m.id} className="ms" style={{ flexGrow: parseFloat(m.hours.replace(/[^0-9.]/g, "")) }} title={`${m.id} · ${m.title} · ${m.hours}`}>
                {m.id}
              </span>
            ))}
          </div>
          <ul className="small ms-list">
            {MILESTONES.map((m) => (
              <li key={m.id}>
                <b>{m.id}</b> {m.title} <span className="muted">{m.hours}</span>
              </li>
            ))}
          </ul>
          <ul className="chips">
            {V0_STACK.map((s) => (
              <li key={s}>
                <span className="chip">{s}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="hooks">
        <p className="eyebrow">Where our lane plugs in</p>
        {HOOKS.map((h) => (
          <div key={h.q} className="hook">
            <span className="small">{h.q}</span>
            <span className="ff-arrow" aria-hidden="true">
              →
            </span>
            <span className="small">
              <b>{h.ours}</b>
            </span>
          </div>
        ))}
      </div>

      <div className="grid-2">
        <More title="Kent's open questions" hint={`${ROMULUS_OPEN.length} items`}>
          <ul className="small">
            {ROMULUS_OPEN.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </More>
        <More title="Words: healer, overlay, visor…" hint={`${HEAL_TERMS.length} terms`}>
          <dl className="kv">
            {HEAL_TERMS.map((t) => (
              <div key={t.term} style={{ display: "contents" }}>
                <dt>{t.term}</dt>
                <dd>{t.what}</dd>
              </div>
            ))}
          </dl>
        </More>
        <More title="Our earlier arm-host draft" hint="superseded by the device side + Kent's API">
          <Table caption="Arm host API (draft)" head={["Endpoint", "Hermes tool", "Does"]} rows={API.map((a) => [<code key="e">{a[0]}</code>, <code key="t">{a[1]}</code>, a[2]])} />
          <ul className="small">
            {[...TELEMETRY, ...PLUGIN_WIRING].map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </More>
      </div>
      <Sources items={ROMULUS_SRC} />
    </Section>
  );
}
