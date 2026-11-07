import { useState } from "react";
import { CONNECT, CONNECT_STEPS, HARNESS_SPLIT, HERMES, HOST_RULES, NAIVE, NAIVE_FINDINGS, NAIVE_N, NAIVE_ORG, type Term } from "../content/stack";
import { repo } from "../content/meta";
import { More, Section, Sources, StatusChip, Txt } from "../ui";
import { Icon } from "../viz";

function Board({ title, layer, items }: { title: string; layer: "naive" | "hermes"; items: Term[] }) {
  const [sel, setSel] = useState(0);
  const t = items[sel];
  return (
    <div className={`panel l-${layer} host`}>
      <div className="head">
        <h3>{title}</h3>
        <span className="small muted">{items.length} parts · tap one</span>
      </div>
      <div className="tiles" role="group" aria-label={`${title} parts`}>
        {items.map((x, k) => (
          <button key={x.term} type="button" className="tile" aria-pressed={sel === k} onClick={() => setSel(k)}>
            {x.term}
          </button>
        ))}
      </div>
      <div className="tile-detail" aria-live="polite">
        <b>{t.term}</b>
        <span>{t.what}</span>
        <span className="muted">
          <b>Here:</b> <Txt t={t.use} />
        </span>
        <Sources items={[t.src]} lead="Source" />
      </div>
    </div>
  );
}

export function Hosts() {
  return (
    <Section id="hosts" title="The hosts' stacks" sub="Naïve and Nous Research host the hackathons, so we build on their parts.">
      <div className="grid-2">
        <Board title="Naïve · the office" layer="naive" items={NAIVE} />
        <Board title="Hermes · the hands" layer="hermes" items={HERMES} />
      </div>

      <h3>Our Naïve org today</h3>
      <div className="org" aria-label="Our Naïve org">
        {NAIVE_ORG.map((o) => (
          <div key={o.k} className="org-tile l-naive">
            <span className="eyebrow">{o.k}</span>
            <b>{o.v}</b>
          </div>
        ))}
      </div>
      <div className="grid-2">
        <div className="panel">
          <h4>What Kent found</h4>
          <ul className="small">
            {NAIVE_FINDINGS.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </div>
        <div className="panel">
          <h4>Org decisions</h4>
          <ul className="terms">
            {NAIVE_N.map((n) => (
              <li key={n.id}>
                <span className="term">
                  {n.id} · {n.q}
                </span>
                <span className="what">{n.d}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <More title="Connect your own Claude Code to Naïve" hint="3 steps · your own token, local scope only">
        <ol className="small">
          {CONNECT_STEPS.map((c) => (
            <li key={c}>
              <code>{c}</code>
            </li>
          ))}
        </ol>
      </More>
      <Sources items={[repo("docs/naive/company-setup.md", "Kent's Naïve company setup")]} />

      <h3>Hermes inside Naïve</h3>
      <div className="nest l-naive" aria-label="What Naïve owns contains what Hermes owns">
        <div className="nest-label">
          <Icon name="building" /> Naïve owns everything beneath the harness
        </div>
        <ul className="chips">
          {["durable loop", "budget gate", "tool policy", "event log + ledger", "micro-VM", "personas · MCP · apps"].map((c) => (
            <li key={c}>
              <span className="chip">{c}</span>
            </li>
          ))}
        </ul>
        <div className="nest-inner l-hermes">
          <div className="nest-label">
            <Icon name="plug" /> Hermes owns
          </div>
          <ul className="chips">
            {["turn loop", "own file · shell · search tools"].map((c) => (
              <li key={c}>
                <span className="chip">{c}</span>
              </li>
            ))}
          </ul>
        </div>
        <p className="small">
          <StatusChip s="tentative" /> <span className="muted">preview, no approvals or streaming. That's why the arm's Hermes lives on the bench.</span>
        </p>
      </div>
      <Sources items={HARNESS_SPLIT.src} />

      <div className="grid-2">
        <More title="How the two connect" hint={`${CONNECT.length} points`}>
          <ul className="terms">
            {CONNECT.map((c) => (
              <li key={c.point}>
                <span className="term">{c.point}</span>
                <span className="what">
                  <Txt t={c.detail} />
                </span>
                <Sources items={c.src} lead="Source" />
              </li>
            ))}
          </ul>
        </More>
        <More title="Rules from the hosts" hint="SDK required · plugin catalog rules">
          <ul className="terms">
            {HOST_RULES.map((c) => (
              <li key={c.point}>
                <span className="term">{c.point}</span>
                <span className="what">{c.detail}</span>
                <Sources items={c.src} lead="Source" />
              </li>
            ))}
          </ul>
        </More>
      </div>
    </Section>
  );
}
