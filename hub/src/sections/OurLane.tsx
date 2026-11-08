import { BY_PERSON, FEATURES, type BoxId, type PhaseLine } from "../content/features";
import { BENCHMARK, EDGE, ISAAC, LEROBOT, LLM_CHOICE, OUR_SCHEDULE, OUR_WINDOW, PODS, PROPOSAL_SRC, RESET } from "../content/proposal";
import { More, Section, Sources, Table, Txt } from "../ui";
import { BudgetBar, CanaryFlow, Icon, ResetLoop, Schedule, type IconName } from "../viz";
import { BOXES, MARK, SAY } from "./FeatureMap";

function KV({ rows }: { rows: { k: string; v: string }[] }) {
  return (
    <dl className="kv">
      {rows.map((r) => (
        <div key={r.k} style={{ display: "contents" }}>
          <dt>{r.k}</dt>
          <dd>{r.v}</dd>
        </div>
      ))}
    </dl>
  );
}

const JOBS: { icon: IconName; name: string; line: string }[] = [
  { icon: "cube", name: "Test bench", line: "LLM or VLA drives a sim arm" },
  { icon: "shield", name: "Safety twin", line: "check a move before it happens" },
  { icon: "doc", name: "Synthetic demos", line: "training data without teleop" },
  { icon: "alert", name: "Fault drills", line: "break it safely, score the watchdog" },
];

const RISKS: string[] = [
  "Sim overrates real success by 9–31 pts",
  "Isaac on RunPod: 2 h go/no-go, else MuJoCo",
  "Remote inference over Wi-Fi is unvalidated",
  "Servo register names: probe at startup",
  "Gripper overload may need a power cycle",
];

/** A box's title and color, as on the feature map. */
const boxOf = (id: BoxId) => BOXES.find((b) => b.id === id)!;

/** James's phase lines, grouped by phase in the order they appear. */
function byPhase(lines: PhaseLine[]) {
  const groups: { phase: string; texts: string[] }[] = [];
  for (const l of lines) {
    const g = groups.find((x) => x.phase === l.phase);
    if (g) g.texts.push(l.text);
    else groups.push({ phase: l.phase, texts: [l.text] });
  }
  return groups;
}

const JAMES_PHASES = byPhase(BY_PERSON.JAMES ?? []);
/** His claimed lines in the box sections: being built first, then done. */
const JAMES_CLAIMS = FEATURES.filter((f) => f.owners.includes("James")).sort((a, b) => Number(a.status === "done") - Number(b.status === "done"));
const JAMES_DONE = JAMES_CLAIMS.filter((f) => f.status === "done").length;

function JamesLines() {
  return (
    <>
      <h3>
        James's lines in <code>features.txt</code>
      </h3>
      <div className="grid-2">
        <div className="panel">
          <div className="head">
            <h4>By phase</h4>
            <span className="small muted">from BY PERSON, IN PHASE ORDER</span>
          </div>
          <dl className="kv">
            {JAMES_PHASES.map((g) => (
              <div key={g.phase} style={{ display: "contents" }}>
                <dt>
                  <span className="chip next">{g.phase}</span>
                </dt>
                <dd>
                  <ul>
                    {g.texts.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                </dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="panel">
          <div className="head">
            <h4>Claimed in the boxes</h4>
            <span className="small muted">
              ✓ {JAMES_DONE} done · ● {JAMES_CLAIMS.length - JAMES_DONE} being built
            </span>
          </div>
          <ul className="fcells">
            {JAMES_CLAIMS.map((f) => (
              <li key={`${f.box}-${f.text}`} className={`fcell s-${f.status} l-${boxOf(f.box).layer}`} title={`${boxOf(f.box).title} · ${f.text} · ${SAY[f.status]}`}>
                <span className="fcell-mark" aria-hidden="true">
                  {MARK[f.status]}
                </span>
                <span className="fcell-text">{f.text}</span>
                <span className="fcell-who mono">{boxOf(f.box).title}</span>
                <span className="sr-only">{SAY[f.status]}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </>
  );
}

export function OurLane() {
  return (
    <Section
      id="ours"
      title="Sim + control"
      layer="sim"
      owner="James + Claude"
      status="tentative"
      sub={
        <>
          Our lane is under way. P1 runs in simulation: MuJoCo SO-101, the Hermes <code>so101-arm</code> plugin, a level-1 watchdog that parks the arm, approach-then-grasp, a fault factory and a ChatTS test. It is on branch <code>james/arm-sim</code>, not pushed yet. The real arm comes Sunday 7:30 AM.
        </>
      }
    >
      <h3>Our build plan</h3>
      <Schedule blocks={OUR_SCHEDULE} from={OUR_WINDOW.from} to={OUR_WINDOW.to} scale={OUR_WINDOW.scale} />
      <JamesLines />

      <div className="jobs">
        {JOBS.map((j) => (
          <div key={j.name} className="job l-sim">
            <span className="job-icon">
              <Icon name={j.icon} size={22} />
            </span>
            <b>{j.name}</b>
            <span className="small muted">{j.line}</span>
          </div>
        ))}
      </div>

      <div className="callout note leisaac">
        <span className="eyebrow">Worth a look</span>
        <span className="small">
          <a href="https://github.com/LightwheelAI/leisaac" target="_blank" rel="noreferrer">
            LeIsaac by LightwheelAI
          </a>
          : SO-101 already modeled in Isaac Lab, with a pick-and-place task, keyboard teleop, scripted demos and a LeRobot recorder.
        </span>
      </div>

      <div className="grid-wide">
        <div className="panel">
          <h3>Canary gate</h3>
          <p className="small muted">A new policy must beat the old one in sim before it touches the arm.</p>
          <CanaryFlow />
        </div>
        <div className="panel">
          <h3>Self-reset loop</h3>
          <p className="small muted">Each success is the next try's reset.</p>
          <ResetLoop />
          <More title="What HIL-SERL actually says" hint="scripted or human resets">
            <p className="small">{RESET.hilserl}</p>
            <p className="small">{RESET.serl}</p>
            <p className="small muted">{RESET.lerobot}</p>
            <ol className="small">
              {RESET.ours.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ol>
            <p className="small">
              <Txt t={RESET.oneLiner} />
            </p>
            <Sources items={RESET.src} />
          </More>
        </div>
      </div>

      <h3>Who should drive? The benchmark</h3>
      <div className="matrix" role="table" aria-label="Benchmark design">
        <div className="mx-row mx-head" role="row">
          <span role="columnheader">Condition</span>
          <span role="columnheader">OpenAI mini</span>
          <span role="columnheader">Qwen3-VL-8B</span>
        </div>
        {[
          ["VLA drives", false],
          ["LLM picks skills", true],
          ["LLM gives poses", true],
          ["Scripted baseline", false],
        ].map(([c, llm]) => (
          <div key={c as string} className="mx-row" role="row">
            <span role="rowheader">{c as string}</span>
            {llm ? (
              <>
                <span role="cell" className="mx-cell">not run</span>
                <span role="cell" className="mx-cell">not run</span>
              </>
            ) : (
              <span role="cell" className="mx-cell mx-span">not run · no LLM</span>
            )}
          </div>
        ))}
      </div>
      <ul className="chips">
        {BENCHMARK.metrics.map((m) => (
          <li key={m}>
            <span className="chip">{m}</span>
          </li>
        ))}
      </ul>
      <p className="small muted">{BENCHMARK.reference}</p>

      <h3>RunPod budget</h3>
      <BudgetBar
        cap={75}
        parts={[
          { label: "Isaac sim", lo: 5, hi: 10, layer: "sim" },
          { label: "training", lo: 3, hi: 5, layer: "sim" },
          { label: "vLLM", lo: 5, hi: 13, layer: "compute" },
        ]}
      />
      <More title="Pods and prices" hint="community / secure cloud">
        <Table caption="RunPod pods" head={["Pod", "GPU", "Price", "Hours", "Cost"]} rows={PODS.map((p) => [<b key="p">{p.pod}</b>, p.gpu, <span key="r" className="mono small">{p.price}</span>, p.hours, <span key="c" className="mono small">{p.cost}</span>])} />
      </More>

      <ul className="risks" aria-label="Risks">
        {RISKS.map((r) => (
          <li key={r}>
            <Icon name="alert" size={16} /> {r}
          </li>
        ))}
      </ul>

      <div className="grid-2">
        <More title="LeRobot 0.7 facts" hint="API, units, CLIs, telemetry">
          <KV rows={LEROBOT} />
        </More>
        <More title="Isaac Sim + LeIsaac facts" hint="GPU, versions, headless">
          <KV rows={ISAAC} />
        </More>
        <More title="Edge device facts" hint="Pi 5, Arduino, servo registers">
          <KV rows={EDGE} />
        </More>
        <More title="Model choices" hint="OpenAI vs open model">
          <KV rows={LLM_CHOICE} />
        </More>
      </div>
      <Sources items={PROPOSAL_SRC} />
    </Section>
  );
}
