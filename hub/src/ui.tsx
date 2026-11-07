import type { ReactNode } from "react";
import { LAYER_NAME, STATUS_HELP, type Layer, type Src, type Status } from "./content/meta";

export function StatusChip({ s }: { s: Status }) {
  return (
    <span className={`chip status ${s}`} title={STATUS_HELP[s]}>
      {s === "ref" ? "reference" : s}
    </span>
  );
}

export function LayerChip({ layer, label }: { layer: Layer; label?: string }) {
  return (
    <span className={`chip layer l-${layer}`}>
      <span className="dot" aria-hidden="true" />
      {label ?? LAYER_NAME[layer]}
    </span>
  );
}

export function Sources({ items, lead = "Sources" }: { items: Src[]; lead?: string }) {
  if (items.length === 0) return null;
  return (
    <p className="src">
      <span>{lead}:</span>
      {items.map((s) =>
        s.href ? (
          <a key={s.label + s.href} href={s.href} target="_blank" rel="noreferrer">
            {s.label}
          </a>
        ) : (
          <span key={s.label}>{s.label}</span>
        ),
      )}
    </p>
  );
}

export function More({ title, hint, children, open }: { title: string; hint?: string; children: ReactNode; open?: boolean }) {
  return (
    <details className="more" open={open}>
      <summary>
        <span>{title}</span>
        {hint ? <span className="hint">{hint}</span> : null}
      </summary>
      <div className="body">{children}</div>
    </details>
  );
}

export function Section({
  id,
  title,
  sub,
  layer,
  owner,
  status,
  children,
}: {
  id: string;
  title: string;
  sub?: ReactNode;
  layer?: Layer;
  owner?: string;
  status?: Status;
  children: ReactNode;
}) {
  return (
    <section id={id} className="sec" aria-labelledby={`${id}-h`}>
      <div className="sec-head">
        <div className="row">
          <h2 id={`${id}-h`}>{title}</h2>
          {layer ? <LayerChip layer={layer} label={owner} /> : null}
          {status ? <StatusChip s={status} /> : null}
        </div>
        {sub ? <p className="sub">{sub}</p> : null}
      </div>
      {children}
    </section>
  );
}

/** Wide tables scroll inside their own box so the page never scrolls sideways. */
export function Table({ head, rows, caption }: { head: string[]; rows: ReactNode[][]; caption?: string }) {
  return (
    <div className="tbl-wrap" role="region" aria-label={caption ?? head.join(", ")} tabIndex={0}>
      <table>
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Renders *(inferred)* markers from content strings in muted italics. */
export function Txt({ t }: { t: string }) {
  const parts = t.split(/(\(inferred\))/g);
  return (
    <>
      {parts.map((p, i) =>
        p === "(inferred)" ? (
          <span key={i} className="inferred">
            {" "}
            (inferred)
          </span>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}
