import { useState } from "react";
import { HERO_STEPS, LANES, PEOPLE, PRD, ROMULUS_RULES, TEAM_SRC } from "../content/team";
import { TEAM_AS_OF } from "../content/meta";
import { More, Section, Sources, StatusChip, Txt } from "../ui";
import { Avatars, Steps } from "../viz";

function List({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <p className="eyebrow">{title}</p>
      <ul className="small">
        {items.map((x) => (
          <li key={x}>{x}</li>
        ))}
      </ul>
    </div>
  );
}

export function Lanes() {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Section id="lanes" title="Team lanes" sub={`Five people, five layers. Tentative, as of ${TEAM_AS_OF}. Tap a lane for its scope.`}>
      <div className="people" aria-label="People">
        {PEOPLE.map((p) => (
          <div key={p.who} className={`person l-${p.layer}`}>
            <span className={`avatar${p.layer === "neutral" ? " unknown" : ""}`}>{p.initial}</span>
            <span className="person-name">{p.who}</span>
            <span className="person-doing">{p.doing}</span>
          </div>
        ))}
      </div>

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
                    {l.overlaps}
                  </p>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      <h3>Kent's hero demo</h3>
      <Steps label="Hero demo" layer="heal" items={HERO_STEPS} />
      <div className="grid-2">
        <More title="Romulus design rules" hint="6 rules">
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
