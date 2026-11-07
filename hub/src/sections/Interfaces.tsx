import { useState } from "react";
import { API, FAULTS, INTERFACE_SRC, PLUGIN_WIRING, TELEMETRY } from "../content/proposal";
import { More, Section, Sources, Table } from "../ui";
import { Icon, type IconName } from "../viz";

const FAULT_ICON: Record<string, IconName> = {
  grasp_slip: "cube",
  camera_lost: "eye",
  collision_stall: "alert",
  overheat: "alert",
  bus_fault: "plug",
  gripper_overload: "arm",
  calib_drift: "gate",
  bad_policy: "bug",
};

export function Interfaces() {
  const [sel, setSel] = useState(0);
  const f = FAULTS[sel];
  return (
    <Section id="interfaces" title="Self-healing interfaces" layer="heal" owner="draft for Romulus" status="proposed" sub="What the arm could hand Romulus: faults it can spot, and safe ways to fix them. Tap a fault.">
      <div className="faults" role="group" aria-label="Faults">
        {FAULTS.map((x, k) => (
          <button key={x[0]} type="button" className="fault" aria-pressed={sel === k} onClick={() => setSel(k)}>
            <Icon name={FAULT_ICON[x[0]] ?? "alert"} />
            <code>{x[0]}</code>
          </button>
        ))}
      </div>
      <div key={f[0]} className="fault-flow" aria-live="polite">
        <div>
          <span className="eyebrow">Spot it</span>
          <span>{f[1]}</span>
        </div>
        <span className="ff-arrow" aria-hidden="true">→</span>
        <div>
          <span className="eyebrow">Drill it safely</span>
          <span>{f[2]}</span>
        </div>
        <span className="ff-arrow" aria-hidden="true">→</span>
        <div>
          <span className="eyebrow">Fix it</span>
          <span>{f[3]}</span>
        </div>
        <span className="ff-arrow" aria-hidden="true">→</span>
        <div className="ff-human">
          <span className="eyebrow">Call a person when</span>
          <span>{f[4]}</span>
        </div>
      </div>
      <p className="small muted">Thresholds are placeholders until calibrated on good runs. The model never sits in the control loop.</p>

      <div className="grid-2">
        <More title="Arm host API and Hermes tools" hint={`${API.length} endpoints`}>
          <Table caption="Arm host API" head={["Endpoint", "Hermes tool", "Does"]} rows={API.map((a) => [<code key="e">{a[0]}</code>, <code key="t">{a[1]}</code>, a[2]])} />
        </More>
        <More title="Telemetry log and plugin wiring" hint="JSONL fields · hooks · webhook">
          <ul className="small">
            {TELEMETRY.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
          <ul className="small">
            {PLUGIN_WIRING.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </More>
      </div>
      <Sources items={INTERFACE_SRC} />
    </Section>
  );
}
