"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, Stethoscope } from "lucide-react";
import { ApiError, LeadOperations, getLeadOperations } from "@/lib/api";
import { when } from "@/lib/opsLabels";
import { StatusPill } from "./StatusPill";

const yesNo = (v: boolean | null) => (v === null ? null : v ? "Yes" : "No");

/**
 * The operating context of a lead that has industry details (a patient enquiry): what the
 * patient asked for, which configured providers could handle it and WHY, what has happened,
 * and the next step. Suggestions only — nothing is sent, booked or contacted from here. For a
 * lead without industry details the backend answers "none" and this renders nothing.
 */
export function LeadOperationsPanel({ token, leadId, refreshKey }: { token: string | null; leadId: string; refreshKey?: unknown }) {
  const [ops, setOps] = useState<LeadOperations | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setError(null);
    (async () => {
      try {
        const o = await getLeadOperations(token, leadId);
        if (!cancelled) setOps(o);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "We couldn't load this lead's operating details.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, leadId, refreshKey]);

  if (error) return <div role="alert" className="rounded-lg border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">{error}</div>;
  if (!ops) return null;

  const p = ops.patient;
  const facts: [string, string | null][] = [
    ["Treatment", p.treatment],
    ["Preferred destination", p.destination_country],
    ["Travel dates", p.travel_start ? `${p.travel_start}${p.travel_end ? ` → ${p.travel_end}` : ""}` : null],
    ["Has insurance", yesNo(p.has_insurance)],
    ["Insurance notes", p.insurance_notes],
    ["Medical history", p.medical_history_summary],
  ];
  const shown = facts.filter(([, v]) => v);
  const m = ops.matching;

  return (
    <section aria-labelledby="lead-ops-h" className="rounded-lg border border-border bg-surface p-6">
      <h2 id="lead-ops-h" className="flex items-center gap-2 font-display text-xl text-foreground">
        <Stethoscope className="h-5 w-5 text-accent" aria-hidden="true" /> Patient enquiry
      </h2>

      {shown.length > 0 ? (
        <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {shown.map(([k, v]) => (
            <div key={k}><dt className="klaros-label">{k}</dt><dd className="text-foreground">{v}</dd></div>
          ))}
        </dl>
      ) : (
        <p className="mt-2 text-sm text-muted">The patient didn't give treatment or destination details — Klaros matches on what the enquiry says.</p>
      )}

      <div className="mt-5 rounded-lg border border-accent/30 bg-accent-soft/60 p-4">
        <p className="klaros-label mb-1">Next action</p>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-foreground">{ops.next_action.text}</p>
          <div className="flex items-center gap-2">
            <StatusPill state={ops.next_action.state} />
            {ops.next_action.route && (
              <Link href={ops.next_action.route} className="klaros-btn-secondary text-sm">Open <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" /></Link>
            )}
          </div>
        </div>
      </div>

      <h3 className="mt-6 text-sm font-semibold text-foreground">Provider matches</h3>
      <p className="mt-0.5 text-xs text-muted">
        {m.basis.procedures.length > 0 ? `Matched on ${m.basis.procedures.join(", ")} (${m.basis.procedure_source})` : "No treatment identified"}
        {m.basis.destination_country ? ` · destination ${m.basis.destination_country}` : ""}. Suggestions only — nothing is sent or booked automatically.
      </p>
      {m.matches.length === 0 ? (
        <p className="mt-3 rounded-md border border-dashed border-border-strong p-3 text-sm text-muted">{m.none_reason}</p>
      ) : (
        <ul className="mt-3 space-y-3">
          {m.matches.map((x) => (
            <li key={x.provider_id} className="rounded-lg border border-border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium text-foreground">{x.name} <span className="font-normal text-muted">· {x.location || "location not set"}</span></p>
                <span className={x.fit === "STRONG" ? "rounded-full bg-success/10 px-2 py-0.5 text-xs font-medium text-success" : "rounded-full border border-border px-2 py-0.5 text-xs text-muted"}>
                  {x.fit === "STRONG" ? "Strong match" : "Partial match"}
                </span>
              </div>
              <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-xs text-muted">
                {x.reasons.map((r) => <li key={r}>{r}</li>)}
              </ul>
            </li>
          ))}
        </ul>
      )}

      <h3 className="mt-6 text-sm font-semibold text-foreground">Timeline</h3>
      <ol className="mt-2 space-y-2 border-l border-border pl-4" aria-label="Lead timeline">
        {ops.timeline.map((t, i) => (
          <li key={i} className="text-sm">
            <span className="text-foreground">{t.text}</span> <span className="text-xs text-muted-foreground">· {when(t.at)}</span>
          </li>
        ))}
        {ops.consultations.length === 0 && <li className="text-xs text-muted-foreground">No consultation scheduled yet.</li>}
      </ol>
    </section>
  );
}
