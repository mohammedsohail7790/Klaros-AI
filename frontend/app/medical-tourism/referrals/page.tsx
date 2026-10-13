"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { HandCoins } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  AwaitingCommission,
  CommissionStatus,
  DeskCommission,
  PipelineLead,
  createProviderCommission,
  getCommissionDesk,
  getMedicalTourismPipeline,
  transitionCommission,
} from "@/lib/api";
import { caseHref, patientName } from "@/lib/mtCase";
import { COMMISSION_REASON, COMMISSION_STATUS_HELP, COMMISSION_STATUS_LABEL, OUTCOME_LABEL } from "@/lib/mtLabels";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";

const CURRENCIES = ["USD", "EUR", "GBP", "AED", "SAR", "INR", "TRY"];

/** The next steps from each status, mirroring the backend's ordered rules (mt_commission.TRANSITIONS). The backend still refuses anything out of order. */
const STEPS: Record<CommissionStatus, { to: CommissionStatus; label: string; primary?: boolean }[]> = {
  PENDING: [{ to: "CONFIRMED", label: "Mark earned", primary: true }, { to: "CANCELLED", label: "Cancel" }],
  CONFIRMED: [{ to: "INVOICED", label: "Mark invoiced", primary: true }, { to: "DISPUTED", label: "Dispute" }, { to: "CANCELLED", label: "Cancel" }],
  INVOICED: [{ to: "PAID", label: "Mark paid", primary: true }, { to: "DISPUTED", label: "Dispute" }, { to: "CANCELLED", label: "Cancel" }],
  DISPUTED: [{ to: "CONFIRMED", label: "Settled: earned" }, { to: "INVOICED", label: "Settled: invoiced" }, { to: "CANCELLED", label: "Cancel" }],
  PAID: [{ to: "DISPUTED", label: "Dispute" }],
  CANCELLED: [],
};

const reasonOf = (err: unknown, fallback: string) => (err instanceof ApiError ? (err.code && COMMISSION_REASON[err.code]) || err.message : fallback);

const amountOf = (c: DeskCommission) =>
  c.computed_amount ? new Intl.NumberFormat(undefined, { style: "currency", currency: c.currency }).format(Number(c.computed_amount)) : c.commission_percentage ? `${c.commission_percentage}% of the treatment cost` : "To be set";

/**
 * The owner's commission desk. A commission is potential until the clinic's reported outcome (the one the agreement names) is on record with evidence; it then
 * moves through earned, invoiced and paid in that order. This page shows what is waiting for a commission and the one next step for each commission; the backend
 * enforces every rule, including that nothing can be marked earned without the evidence.
 */
export default function CommissionDeskPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [commissions, setCommissions] = useState<DeskCommission[]>([]);
  const [awaiting, setAwaiting] = useState<AwaitingCommission[]>([]);
  const [patients, setPatients] = useState<PipelineLead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [setup, setSetup] = useState<AwaitingCommission | null>(null);
  const [step, setStep] = useState<{ c: DeskCommission; to: CommissionStatus; label: string } | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const desk = await getCommissionDesk(token);
      setCommissions(desk.commissions);
      setAwaiting(desk.awaiting);
      // names are a convenience: without them a patient is simply shown as "Patient"
      getMedicalTourismPipeline(token).then((p) => setPatients(p.leads ?? [])).catch(() => setPatients([]));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load commissions.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const who = useMemo(() => {
    const by = new Map(patients.map((p) => [p.lead_id, p]));
    return (leadId: string | null) => (leadId && by.get(leadId) ? patientName(by.get(leadId)!) : "Patient");
  }, [patients]);

  return (
    <AppShell user={user}>
      <div className="px-6 py-8 sm:px-8">
        <PageHeader
          title="Commissions"
          description="What clinics owe you for referrals. A commission is only potential until the clinic reports the outcome your agreement names, with evidence."
          icon={HandCoins}
          actions={
            <Button size="sm" variant="secondary" onClick={() => load()}>
              Refresh
            </Button>
          }
        />
        {notice && (
          <Alert variant="success" className="mb-3">
            {notice}
          </Alert>
        )}
        {error && (
          <Alert variant="danger" className="mb-3" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>
            {error}
          </Alert>
        )}

        {authLoading || (loading && commissions.length === 0 && awaiting.length === 0) ? (
          <Skeleton />
        ) : (
          <>
            {awaiting.length > 0 && (
              <section aria-label="Ready for a commission" className="mb-8">
                <h2 className="text-xl font-semibold tracking-tight text-foreground">Ready for a commission</h2>
                <p className="mt-0.5 text-sm text-muted">The clinic has reported an outcome for these referrals. Record the commission you agreed.</p>
                <ul className="mt-3 space-y-2">
                  {awaiting.map((a) => (
                    <li key={a.referral_id} className="klaros-card flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <Link href={caseHref(a.lead_id)} className="font-medium text-foreground hover:underline">
                          {who(a.lead_id)}
                        </Link>
                        <span className="text-sm text-muted"> · {a.provider_name ?? "Clinic"}</span>
                        <p className="mt-0.5 text-sm text-muted">
                          {OUTCOME_LABEL[a.outcome] ?? a.outcome}
                          {a.outcome_evidence ? ` · evidence: ${a.outcome_evidence}` : ""}
                        </p>
                      </div>
                      <Button size="sm" onClick={() => setSetup(a)}>
                        Set up commission
                      </Button>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section aria-label="Commissions">
              <h2 className="text-xl font-semibold tracking-tight text-foreground">Commissions</h2>
              {commissions.length === 0 ? (
                <p className="mt-2 text-sm text-muted">
                  No commissions yet. Once a clinic reports an outcome for a referral, it appears above so you can record the commission.
                </p>
              ) : (
                <div className="klaros-table-wrap mt-3">
                  <table className="klaros-table">
                    <thead className="bg-surface text-muted">
                      <tr>
                        <th className="px-4 py-2">Patient and clinic</th>
                        <th className="px-4 py-2">Outcome</th>
                        <th className="px-4 py-2">Amount</th>
                        <th className="px-4 py-2">Status</th>
                        <th className="px-4 py-2" />
                      </tr>
                    </thead>
                    <tbody>
                      {commissions.map((c) => (
                        <tr key={c.id} className="border-t border-border align-top">
                          <td className="px-4 py-2">
                            {c.lead_id ? (
                              <Link href={caseHref(c.lead_id)} className="font-medium text-foreground hover:underline">
                                {who(c.lead_id)}
                              </Link>
                            ) : (
                              <span className="font-medium text-foreground">Patient</span>
                            )}
                            <div className="text-xs text-muted">{c.provider_name ?? "Clinic"}</div>
                          </td>
                          <td className="px-4 py-2 text-muted">
                            {c.outcome ? OUTCOME_LABEL[c.outcome] ?? c.outcome : "Not reported"}
                            {c.outcome_evidence && <div className="text-xs">{c.outcome_evidence}</div>}
                            <div className="text-xs text-muted-foreground">Agreement needs: {OUTCOME_LABEL[c.qualifying_outcome] ?? c.qualifying_outcome}</div>
                          </td>
                          <td className="px-4 py-2 text-foreground">{amountOf(c)}</td>
                          <td className="px-4 py-2">
                            <Badge status={c.status}>{COMMISSION_STATUS_LABEL[c.status] ?? c.status}</Badge>
                            <div className="mt-1 max-w-xs text-xs text-muted-foreground">{COMMISSION_STATUS_HELP[c.status]}</div>
                          </td>
                          <td className="px-4 py-2 text-right">
                            <div className="flex flex-wrap justify-end gap-1.5">
                              {(STEPS[c.status] ?? []).map((s) => (
                                <Button key={s.to} size="sm" variant={s.primary ? "primary" : "secondary"} onClick={() => setStep({ c, to: s.to, label: s.label })}>
                                  {s.label}
                                </Button>
                              ))}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        )}
      </div>

      {setup && token && (
        <SetUpCommission
          token={token}
          item={setup}
          patient={who(setup.lead_id)}
          onClose={() => setSetup(null)}
          onDone={() => {
            setSetup(null);
            setNotice("Commission recorded as potential. It becomes earned once you mark it, which needs the clinic's reported outcome.");
            load();
          }}
        />
      )}
      {step && token && (
        <StepDialog
          token={token}
          c={step.c}
          to={step.to}
          label={step.label}
          onClose={() => setStep(null)}
          onDone={(msg) => {
            setStep(null);
            setNotice(msg);
            load();
          }}
        />
      )}
    </AppShell>
  );
}

function SetUpCommission({ token, item, patient, onClose, onDone }: { token: string; item: AwaitingCommission; patient: string; onClose: () => void; onDone: () => void }) {
  const [currency, setCurrency] = useState("USD");
  const [basis, setBasis] = useState<"FLAT" | "PERCENTAGE">("FLAT");
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [qualifying, setQualifying] = useState("TREATMENT_COMPLETED");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const number = /^\d+(\.\d{1,2})?$/;
  const ok = number.test(amount.trim()) && (basis === "FLAT" || reference.trim() === "" || number.test(reference.trim()));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await createProviderCommission(token, item.referral_id, {
        currency,
        basis,
        qualifying_outcome: qualifying,
        ...(basis === "FLAT" ? { flat_amount: amount.trim() } : { commission_percentage: amount.trim(), ...(reference.trim() ? { reference_amount: reference.trim() } : {}) }),
      });
      onDone();
    } catch (err) {
      setError(reasonOf(err, "Unable to record the commission."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Commission: ${patient}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        {error && (
          <div role="alert" className="rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">
            {error}
          </div>
        )}
        <p className="text-sm text-muted">
          Referral to {item.provider_name ?? "the clinic"}. Enter what your agreement with the clinic says; Klaros does not guess it.
        </p>
        <fieldset className="flex gap-4 text-sm">
          <legend className="sr-only">How the commission is calculated</legend>
          <label className="flex items-center gap-1.5">
            <input type="radio" name="basis" checked={basis === "FLAT"} onChange={() => setBasis("FLAT")} /> Fixed fee
          </label>
          <label className="flex items-center gap-1.5">
            <input type="radio" name="basis" checked={basis === "PERCENTAGE"} onChange={() => setBasis("PERCENTAGE")} /> Percentage of treatment cost
          </label>
        </fieldset>
        <div className="grid grid-cols-3 gap-2">
          <label className="col-span-2 block text-sm">
            {basis === "FLAT" ? "Fee" : "Percentage"}
            <input aria-label={basis === "FLAT" ? "Fee" : "Percentage"} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder={basis === "FLAT" ? "e.g. 650" : "e.g. 10"} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5" />
          </label>
          <label className="block text-sm">
            Currency
            <select aria-label="Currency" value={currency} onChange={(e) => setCurrency(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5">
              {CURRENCIES.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
        </div>
        {basis === "PERCENTAGE" && (
          <label className="block text-sm">
            Treatment cost it applies to (optional)
            <input aria-label="Treatment cost" inputMode="decimal" value={reference} onChange={(e) => setReference(e.target.value)} placeholder="Leave blank until the clinic tells you" className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5" />
          </label>
        )}
        <label className="block text-sm">
          The outcome your agreement says earns it
          <select aria-label="Qualifying outcome" value={qualifying} onChange={(e) => setQualifying(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5">
            {Object.entries(OUTCOME_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" size="sm" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={busy || !ok}>
            {busy ? "Saving…" : "Record commission"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function StepDialog({ token, c, to, label, onClose, onDone }: { token: string; c: DeskCommission; to: CommissionStatus; label: string; onClose: () => void; onDone: (message: string) => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const needsNote = to === "DISPUTED" || to === "CANCELLED";

  async function go() {
    setBusy(true);
    setError(null);
    try {
      await transitionCommission(token, c.id, to, note.trim() || undefined);
      onDone(`Commission is now ${COMMISSION_STATUS_LABEL[to].toLowerCase()}.`);
    } catch (err) {
      setError(reasonOf(err, "That did not work."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={label} onClose={onClose}>
      {error && (
        <div role="alert" className="mb-3 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">
          {error}
        </div>
      )}
      <p className="text-sm text-muted">
        {to === "CONFIRMED"
          ? "Mark this commission as earned. Klaros checks that the clinic has reported the outcome your agreement names, with evidence."
          : `This moves the commission from ${COMMISSION_STATUS_LABEL[c.status].toLowerCase()} to ${COMMISSION_STATUS_LABEL[to].toLowerCase()}.`}
      </p>
      <label className="mt-3 block text-sm">
        {needsNote ? "Reason (recommended)" : "Note (optional)"}
        <input aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5" />
      </label>
      <div className="mt-4 flex justify-end gap-2">
        <Button size="sm" variant="secondary" onClick={onClose}>
          Back
        </Button>
        <Button size="sm" disabled={busy} onClick={go}>
          {busy ? "Saving…" : label}
        </Button>
      </div>
    </Modal>
  );
}
