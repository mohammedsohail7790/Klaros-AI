"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ConfirmAction } from "@/components/dropshipping/parts";
import { Button } from "@/components/ui/Button";
import { ProblemReportEntry, decideProblemReport, describeError, listProblemReports } from "@/lib/dropshippingApi";

type Pending = { kind: "approve" | "reject"; entry: ProblemReportEntry } | null;

/** Shoppers' reports that something is wrong with an order (damaged, wrong or missing items), waiting for a decision. Loads on its own, so a failure here never blanks the page. */
export default function ProblemReports({ token, canDecide }: { token: string; canDecide: boolean }) {
  const [entries, setEntries] = useState<ProblemReportEntry[]>([]);
  const [pending, setPending] = useState<Pending>(null);

  const load = useCallback(async () => {
    try {
      setEntries((await listProblemReports(token, "PENDING")).problem_reports);
    } catch {
      setEntries([]);   // the rest of the page is complete without it
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function decide(kind: "approve" | "reject", entry: ProblemReportEntry, note: string) {
    try {
      await decideProblemReport(token, entry.id, kind, note);
      await load();
    } catch (err) {
      throw describeError(err, "That did not work.");
    }
  }

  if (entries.length === 0) return null;
  return (
    <section aria-label="Problems reported by shoppers">
      <h2 className="mb-2 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Reported by shoppers</h2>
      <p className="mb-3 text-sm text-muted">Something wrong with an order they received. Approving flags the order for a refund; Klaros does not move money, so refund it in Stripe.</p>
      <ul className="space-y-3">
        {entries.map((e) => (
          <li key={e.id} aria-label={`Report on ${e.order_number}`} className="rounded-2xl border border-border p-5">
            <div className="flex flex-wrap items-center gap-3"><Link href={`/dropshipping/orders/${e.order_id}`} className="font-medium text-foreground">{e.order_number}</Link></div>
            <p className="mt-2 whitespace-pre-wrap text-[15px]">{e.description}</p>
            {canDecide && (
              <div className="mt-4 flex flex-wrap gap-2">
                <Button size="sm" onClick={() => setPending({ kind: "approve", entry: e })}>Approve and flag for refund</Button>
                <Button size="sm" variant="secondary" onClick={() => setPending({ kind: "reject", entry: e })}>Not approved</Button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {pending?.kind === "approve" && (
        <ConfirmAction title={`Approve the report on ${pending.entry.order_number}?`} body="The order is flagged for a refund. You then refund it in Stripe." confirmLabel="Yes, approve" onConfirm={() => decide("approve", pending.entry, "")} onClose={() => setPending(null)} />
      )}
      {pending?.kind === "reject" && (
        <ConfirmAction danger title={`Do not approve the report on ${pending.entry.order_number}`} body="Say why. Keep it factual: the reason is kept with the report." fieldLabel="Why not" confirmLabel="Not approved" onConfirm={(t) => decide("reject", pending.entry, t)} onClose={() => setPending(null)} />
      )}
    </section>
  );
}
