import { useState } from "react";
import { LANES, OFF_MAIN, PEOPLE, PRD, ROMULUS_RULES, TEAM_SRC } from "../content/team";
import { DEMO_PLAN, HARDWARE, HARDWARE_NOTE } from "../content/heal";
import { TEAM_AS_OF } from "../content/meta";
import { More, Section, Sources, StatusChip, Table, Txt } from "../ui";
import { Avatars, Icon, Steps } from "../viz";

function List({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <p className="eyebrow">{title}</p>
      <ul className="small">
        {items.map((x) => (
          <li key={x}>
            <Txt t={x} />
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Lanes() {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Section id="lanes" title="Team lanes" sub={`Five people, five layers. As of ${TEAM_AS_OF}. Tap a lane for its scope.`}>
      <div className="people" aria-label="People">
        {PEOPLE.map((p) => (
          <div key={p.who} className={`person l-${p.layer}`}>
            <span className={`avatar${p.layer === "neutral" ? " unknown" : ""}`}>{p.initial}</span>
            <span className="person-name">
              {p.who} <span className="person-synced mono">{p.synced}</span>
            </span>
            <span className="person-doing">
              <b>Now</b> {p.doing}
            </span>
            <span className="person-doing">
              <b>Since 4 PM</b> {p.landed}
            </span>
          </div>
        ))}
      </div>

      <h3>Not on main yet</h3>
      <p className="sub muted small">Every branch on GitHub (there are no forks), plus what each person's status says is still local.</p>
      <Table
        caption="Work not on main"
        head={["Who", "Where", "What", "State"]}
        rows={OFF_MAIN.map((o) => [
          <b key="w">{o.who}</b>,
          <code key="b">{o.where}</code>,
          o.what,
          o.href ? (
            <a key="s" href={o.href} target="_blank" rel="noreferrer">
              <Txt t={o.state} />
            </a>
          ) : (
            <Txt key="s" t={o.state} />
          ),
        ])}
      />

      <div className="swim">
        {LANES.map((l) => {
          const isOpen = open === l.name;
          return (
            <div key={l.name} className={`lane-row l-${l.layer}${isOpen ? " open" : ""}`}>
              <button type="button" className="lane-btn" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : l.name)}>
                <span className="lane-name">{l.name}</span>
                <Avatars who={l.who} layer={l.layer} />
                <StatusChip s={l.status} />
                <span className="lane-pills">
                  {l.pills.map((p) => (
                    <span key={p} className="chip">
                      {p}
                    </span>
                  ))}
                </span>
              </button>
              {isOpen ? (
                <div className="lane-detail">
                  <p className="small muted">Owner: {l.owner}</p>
                  <div className="grid-3">
                    <List title="By Sunday 11 AM" items={l.sunday} />
                    <List title="Stretch" items={l.stretch} />
                    <List title="Needs" items={l.needs} />
                  </div>
                  <p className="small">
                    <span className="eyebrow">Overlaps </span>
                    <Txt t={l.overlaps} />
                  </p>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      <h3>On the bench</h3>
      <div className="hw">
        {HARDWARE.map((h) => (
          <div key={h.name} className={`hw-card l-${h.layer}`}>
            <span className="hw-icon">
              <Icon name={h.icon} size={22} />
            </span>
            <b>{h.name}</b>
            <span className="small muted">{h.role}</span>
            {h.note ? <span className="chip">{h.note}</span> : null}
          </div>
        ))}
      </div>
      <p className="small muted">{HARDWARE_NOTE}</p>

      <h3>Demo plan (being designed)</h3>
      <Steps label="Demo plan" layer="heal" items={DEMO_PLAN} />
      <div className="grid-2">
        <More title="Kent's first Romulus notes" hint="6 rules, before v0">
          <ul className="small">
            {ROMULUS_RULES.map((r) => (
              <li key={r}>
                <Txt t={r} />
              </li>
            ))}
          </ul>
        </More>
        <More title="Cloud-platform PRD and APIs" hint="Kent, pushed 2:23 PM">
          <p className="small">{PRD.objective}</p>
          <List title="Requirements" items={PRD.requirements} />
          <List title="APIs" items={PRD.apis} />
        </More>
      </div>
      <Sources items={TEAM_SRC} />
    </Section>
  );
}
