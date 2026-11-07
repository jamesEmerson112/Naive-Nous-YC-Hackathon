import { Fragment, useState } from "react";
import { CATALOG, COMPONENTS, DEVICE_LOOP, HEAL_SRC, HEAL_TERMS, HEALTH_TIERS, type Component } from "../content/heal";
import {
  API_META,
  DECISIONS,
  DEMO_ORDER,
  DEVICE_API,
  FLOWS,
  HOOKS,
  NAIVE_LIMITS,
  NEEDS,
  ROMULUS_OPEN,
  ROMULUS_SRC,
  SCHEDULE,
  STREAMS,
  V0_LOOP,
  V0_NETWORK,
  V0_SIDE,
  V0_STACK,
  WORK_ORDER_FLOW,
} from "../content/romulus";
import { More, Section, Sources, StatusChip, Table } from "../ui";
import { Icon, Schedule, Steps } from "../viz";

function Catalog() {
  const [comp, setComp] = useState<Component | "all">("all");
  const list = comp === "all" ? CATALOG : CATALOG.filter((f) => f.comp === comp);
  const [selId, setSelId] = useState(CATALOG[0].id);
  const f = CATALOG.find((x) => x.id === selId) ?? CATALOG[0];
  const steps: [string, string, boolean][] = [
    ["Plant it", f.plant || "—", false],
    ["Spot it", f.detect, false],
    ["Fix it", f.fix, false],
    ["Check it", f.verify || "—", false],
    ["Call a person", f.human || "not needed", Boolean(f.human)],
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
        {steps.map(([k, v, human], i) => (
          <Fragment key={k}>
            {i > 0 ? (
              <span className="ff-arrow" aria-hidden="true">
                →
              </span>
            ) : null}
            <div className={human ? "ff-human" : undefined}>
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
  return (
    <Section id="interfaces" title="Self-healing" layer="heal" owner="Ishaan's overview · Kent's Romulus" status="tentative" sub="The device heals first; Romulus locks good fixes in. Tap a fault to see how it's planted, caught and fixed.">
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

      <h3>In Romulus</h3>
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

      <h3>Hardware fault → phone order</h3>
      <Steps label="Work-order flow" layer="naive" items={WORK_ORDER_FLOW} />
      <More title="Naïve limits Kent found" hint={`${NAIVE_LIMITS.length} notes`}>
        <ul className="small">
          {NAIVE_LIMITS.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </More>

      <h3>Fault catalog</h3>
      <Catalog />
      <Sources items={HEAL_SRC} />

      <h3>Kent's build plan</h3>
      <Schedule blocks={SCHEDULE} from="2026-11-08T00:15:00Z" to="2026-11-08T19:00:00Z" />
      <div className="needs" aria-label="What Kent needs, by when">
        {NEEDS.map((n) => (
          <div key={n.what} className="need">
            <span className="need-by">{n.by}</span>
            <b>{n.what}</b>
            <span className="small muted">{n.from}</span>
          </div>
        ))}
      </div>

      <div className="grid-2">
        <div className="panel">
          <div className="head">
            <h4>Flows F1–F10</h4>
            <span className="small muted">P0 must work · P1 next</span>
          </div>
          <div className="flows">
            {FLOWS.map((f) => (
              <span key={f.id} className={`flow ${f.p}`}>
                <b>{f.id}</b> {f.name}
              </span>
            ))}
          </div>
          <p className="eyebrow">Demo order</p>
          <div className="demo-order">
            {DEMO_ORDER.map((d, i) => (
              <span key={d.title} className="do-step">
                {i > 0 ? <span className="ff-arrow">→</span> : null}
                <b>{d.title}</b> <span className="muted small">{d.sub}</span>
              </span>
            ))}
          </div>
        </div>
        <div className="panel">
          <div className="head">
            <h4>Decision log</h4>
            <span className="small muted">
              {decided} decided · D14 open · D15 D19 D20 pending
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
      </div>

      <h3>What the arm sends</h3>
      <div className="streams">
        {STREAMS.map((s) => (
          <div key={s.name} className="stream">
            <code>{s.name}</code>
            <span className="small">{s.what}</span>
            <span className="cost">{s.size}</span>
          </div>
        ))}
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
        <More title="Device API (contract v0.1)" hint={`${DEVICE_API.length} device endpoints`}>
          <p className="small muted">{API_META}</p>
          <Table caption="Device API" head={["", "Path", "Does"]} rows={DEVICE_API.map((a) => [<code key="m">{a[0]}</code>, <code key="p">{a[1]}</code>, a[2]])} />
        </More>
        <More title="How Romulus is wired" hint="network + stack">
          <ul className="small">
            {V0_NETWORK.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
          <ul className="chips">
            {V0_STACK.map((s) => (
              <li key={s}>
                <span className="chip">{s}</span>
              </li>
            ))}
          </ul>
        </More>
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
      </div>
      <Sources items={ROMULUS_SRC} />
    </Section>
  );
}
