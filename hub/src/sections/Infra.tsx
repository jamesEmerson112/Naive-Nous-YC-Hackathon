import { BESIDE, PLACEMENTS, RACK, RACK_SRC } from "../content/infra";
import { LayerChip, More, Section, Sources, StatusChip, Table } from "../ui";
import { Bridge } from "../viz";

export function Infra() {
  return (
    <Section id="infra" title="Infrastructure" sub="Top: the business in the cloud. Bottom: the metal on the bench. Right column: real ports and rates.">
      <div className="rack">
        {RACK.map((r) => (
          <div key={r.u} style={{ display: "contents" }}>
            <div className={`rack-row l-${r.layer}`}>
              <div className="rack-unit">
                <span className="u">{r.u}</span>
                <span className="name">{r.name}</span>
                <span className="where">{r.where}</span>
              </div>
              <div className="rack-body">
                <ul className="chips">
                  {r.chips.map((c) => (
                    <li key={c}>
                      <span className="chip">{c}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="rack-ports">
                {r.ports.map((p) => (
                  <span key={p}>{p}</span>
                ))}
              </div>
            </div>
            {r.flowBelow ? (
              <div className="rack-flow" aria-label={`Between ${r.name} and the layer below`}>
                <span className="flow-arrows" aria-hidden="true">
                  <span>↓</span>
                  <span>↑</span>
                </span>
                <span>{r.flowBelow}</span>
              </div>
            ) : null}
          </div>
        ))}
      </div>
      <Sources items={RACK_SRC} />

      <div className="panel bridge-panel">
        <div className="head">
          <h3>Bench ↔ cloud bridge</h3>
          <span className="chip next">next up · James + Claude</span>
          <StatusChip s="open" />
        </div>
        <Bridge />
        <p className="small muted">Neither cloud can reach the arm. The bench connects out, or a tunnel lets them in. We build it with pure APIs.</p>
      </div>
      <div>
        <div className="beside">
          {BESIDE.map((b) => (
            <div key={b.title} className={`panel tint l-${b.layer}`}>
              <div className="head">
                <h4>{b.title}</h4>
                <StatusChip s={b.status} />
              </div>
              <ul className="small">
                {b.lines.slice(0, 3).map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>

      <More title="Where each piece runs" hint={`${PLACEMENTS.length} pieces · table`}>
        <Table
          caption="Where each piece runs"
          head={["Piece", "Runs on", "Lane", "Status", "Note"]}
          rows={PLACEMENTS.map((p) => [
            <b key="w">{p.what}</b>,
            p.where,
            <LayerChip key="l" layer={p.layer} label={p.lane} />,
            <StatusChip key="s" s={p.status} />,
            <span key="n" className="muted">{p.note}</span>,
          ])}
        />
      </More>
    </Section>
  );
}
