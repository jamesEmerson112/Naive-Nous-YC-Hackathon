import { ARM_DESIGN, CLOCK, EVENT_SRC, HERMES_EVENT, NAIVE_EVENT, SCENARIOS } from "../content/event";
import { OPEN } from "../content/team";
import { More, Section, Sources, StatusChip, Table } from "../ui";
import { Timeline } from "../viz";

const EVENTS = [
  { at: "2026-11-07T19:30:00Z", when: "Sat 11:30 AM", label: "hacking starts" },
  { at: "2026-11-08T08:30:00Z", when: "Sun 12:30 AM", label: "overnight run starts" },
  { at: "2026-11-08T17:00:00Z", when: "Sun 9:00 AM", label: "collect proof" },
  { at: "2026-11-08T19:00:00Z", when: "Sun 11:00 AM", label: "hacking stops" },
  { at: "2026-11-08T21:00:00Z", when: "Sun 1:00 PM", label: "demos" },
  { at: "2026-11-11T22:00:00Z", when: "Wed 2:00 PM", label: "Hermes entry due" },
];

export function Event() {
  return (
    <Section id="event" title="Timeline" sub="All times PST. The marker moves with the clock.">
      <Timeline events={EVENTS} />
      <ul className="chips" aria-label="Credits">
        {NAIVE_EVENT.credits.map((c) => (
          <li key={c}>
            <span className="chip">{c}</span>
          </li>
        ))}
      </ul>
      <div className="grid-2">
        <More title="Naïve hackathon: rules and prizes" hint="Main + 3 sponsor tracks">
          <ul className="small">
            {NAIVE_EVENT.rules.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          <Table caption="Naïve prize tracks" head={["Track", "Prizes"]} rows={NAIVE_EVENT.tracks.map((t) => [<b key="t">{t[0]}</b>, t[1]])} />
        </More>
        <More title="Hermes plugin hackathon" hint="submit 3 things by Wed">
          <ol className="small">
            {HERMES_EVENT.submit.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ol>
          <p className="small muted">Judged on {HERMES_EVENT.judged}.</p>
          <ul className="small">
            {HERMES_EVENT.prizes.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </More>
        <More title="Earlier company ideas (A–D)" hint="before the arm">
          <Table caption="Scenarios" head={["", "Idea", "Sponsor fit"]} rows={SCENARIOS.map((s) => [<b key="i">{s.id} · {s.name}</b>, s.idea, s.sponsors])} />
          <p className="small muted">{CLOCK}</p>
        </More>
        <More title="Notes from the robotic-arm draft" hint="docs/robotic-arm.html">
          <ul className="small">
            {ARM_DESIGN.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </More>
      </div>
      <Sources items={EVENT_SRC} />
    </Section>
  );
}

export function Open() {
  return (
    <Section id="open" title="Open questions" sub="Who can settle each one.">
      <ul className="open-list">
        {OPEN.map((q) => (
          <li key={q.q} className={q.who.includes("next") ? "next" : ""}>
            <StatusChip s={q.status} />
            <span className="oq">{q.q}</span>
            <span className="who">{q.who}</span>
          </li>
        ))}
      </ul>
    </Section>
  );
}
