"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, ClinicQuoteRow, LeadQuotes, MedicalTourismProvider, getLeadQuotes, listMedicalTourismProviders, recordLeadQuote, withdrawLeadQuote } from "@/lib/api";
import { Modal } from "@/components/ui/Modal";

const money = (amount: string, currency: string) => {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(Number(amount));
  } catch {
    return `${amount} ${currency}`;
  }
};

/** What a clinic would pay us on a quote, for managers: "about USD 800 (20%)". Never shown to a patient. */
function commissionLine(q: ClinicQuoteRow): string | null {
  const c = q.commission;
  if (!c || !c.agreed) return null;
  const share = c.basis === "PERCENTAGE" && c.percentage ? ` (${Number(c.percentage)}%)` : "";
  return c.estimated && c.estimated_currency ? `About ${money(c.estimated, c.estimated_currency)}${share}` : `${c.basis === "FLAT" ? "Flat fee" : "Percentage"} agreed${share}`;
}

function QuoteDialog({ token, leadId, onClose, onSaved }: { token: string; leadId: string; onClose: () => void; onSaved: (q: LeadQuotes) => void }) {
  const [providers, setProviders] = useState<MedicalTourismProvider[]>([]);
  const [providerId, setProviderId] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [includes, setIncludes] = useState("");
  const [excludes, setExcludes] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listMedicalTourismProviders(token, { limit: 100 }).then((r) => setProviders(r.providers.filter((p) => p.status === "ACTIVE"))).catch(() => setProviders([]));
  }, [token]);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      onSaved(await recordLeadQuote(token, leadId, { provider_id: providerId, amount: amount.trim(), currency: currency.trim(), includes: includes.trim(), excludes: excludes.trim() || undefined, valid_until: validUntil || undefined, reference: reference.trim() || undefined }));
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : "";
      setError(detail || "That didn't save. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  const field = "mt-1 w-full rounded-xl border border-border bg-surface px-3 py-2 text-[14px] text-foreground focus:border-accent focus:outline-none";
  return (
    <Modal title="Add a clinic's quote" onClose={onClose} size="lg">
      {error && <div role="alert" className="mb-3 rounded-xl border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>}
      <div className="space-y-4 text-[14px] text-foreground">
        <p className="text-muted">Record what the clinic quoted for this patient, in writing. A newer quote from the same clinic replaces the earlier one.</p>
        <label className="block">Clinic
          <select aria-label="Clinic" className={field} value={providerId} onChange={(e) => setProviderId(e.target.value)}>
            <option value="">Choose a clinic</option>
            {providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <div className="grid grid-cols-3 gap-3">
          <label className="col-span-2 block">Quoted price<input aria-label="Quoted price" inputMode="decimal" className={field} value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="4500" /></label>
          <label className="block">Currency<input aria-label="Currency" maxLength={3} className={field} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></label>
        </div>
        <label className="block">What the price includes<textarea aria-label="What the price includes" rows={3} className={field} value={includes} onChange={(e) => setIncludes(e.target.value)} placeholder="Surgery, hospital stay, medication, transfers, follow-up" /></label>
        <label className="block">What it does not include (optional)<textarea aria-label="What it does not include" rows={2} className={field} value={excludes} onChange={(e) => setExcludes(e.target.value)} /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">Valid until (optional)<input aria-label="Valid until" type="date" className={field} value={validUntil} onChange={(e) => setValidUntil(e.target.value)} /></label>
          <label className="block">Where the written quote is kept (optional)<input aria-label="Quote reference" className={field} value={reference} onChange={(e) => setReference(e.target.value)} placeholder="Email from the clinic, 12 Nov" /></label>
        </div>
        <div className="flex justify-end gap-3 pt-2">
          <button type="button" onClick={onClose} className="text-muted hover:underline">Cancel</button>
          <button type="button" disabled={busy || !providerId} onClick={save} className="klaros-btn-primary disabled:opacity-50">Save quote</button>
        </div>
      </div>
    </Modal>
  );
}

/**
 * Clinic quotes for one patient, side by side. The backend orders them by price (never by commission) and says, for each clinic, whether we receive a commission from it;
 * only managers also see the amounts. The note under the list is the sentence to share with the patient.
 */
export function ClinicQuotes({ token, leadId }: { token: string | null; leadId: string }) {
  const [data, setData] = useState<LeadQuotes | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setData(await getLeadQuotes(token, leadId));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't load the quotes.");
    }
  }, [token, leadId]);

  useEffect(() => {
    load();
  }, [load]);

  async function withdraw(id: string) {
    if (!token) return;
    setBusyId(id);
    try {
      setData(await withdrawLeadQuote(token, id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't save. Nothing was changed.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section aria-label="Clinic quotes" className="klaros-card p-6 sm:p-7">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Clinic quotes</h2>
        <button type="button" onClick={() => setAdding(true)} className="klaros-btn-secondary shrink-0">Add a quote</button>
      </div>
      {error && <p role="alert" className="mt-3 text-[14px] text-danger">{error}</p>}
      {data && data.quotes.length === 0 && !error && (
        <p className="mt-3 text-[15px] text-muted">No quotes yet. Ask each shortlisted clinic for a written quote that lists what is included, then record it here to compare them side by side.</p>
      )}
      {data && data.quotes.length > 0 && (
        <>
          <ul className="mt-4 space-y-3" aria-label="Quotes, cheapest first">
            {data.quotes.map((q) => {
              const line = commissionLine(q);
              return (
                <li key={q.id} className="rounded-2xl bg-surface-muted p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[16px] font-semibold text-foreground">{q.provider_name}</span>
                        {q.lowest_in_currency && data.quotes.length > 1 && <span className="rounded-full bg-success/10 px-2 py-0.5 text-[12px] text-success">Lowest price</span>}
                        <span className={`rounded-full px-2 py-0.5 text-[12px] ${q.verified ? "bg-success/10 text-success" : "bg-warning/10 text-warning"}`}>{q.verified ? "Verified clinic" : "Not verified yet"}</span>
                        {q.expired && <span className="rounded-full bg-danger/10 px-2 py-0.5 text-[12px] text-danger">Quote expired</span>}
                      </div>
                      {q.procedure && <p className="mt-0.5 text-[13px] text-muted">{q.procedure}</p>}
                    </div>
                    <div className="text-right">
                      <div className="font-display text-[1.6rem] font-semibold leading-none tracking-[-0.02em] text-foreground">{money(q.amount, q.currency)}</div>
                      {q.valid_until && <div className="mt-1 text-[12px] text-muted">Valid until {q.valid_until}</div>}
                    </div>
                  </div>
                  <p className="mt-3 text-[14px] text-foreground"><span className="font-medium">Includes:</span> {q.includes}</p>
                  {q.excludes && <p className="mt-1 text-[14px] text-foreground"><span className="font-medium">Does not include:</span> {q.excludes}</p>}
                  {q.reference && <p className="mt-1 text-[13px] text-muted">Written quote: {q.reference}</p>}
                  <p className="mt-3 text-[13px] text-muted">{q.disclosure}</p>
                  {line && <p className="mt-1 text-[13px] text-muted-foreground">Only you see this: {line}</p>}
                  <button type="button" disabled={busyId === q.id} onClick={() => withdraw(q.id)} className="mt-3 text-[13px] text-accent hover:underline disabled:opacity-50">Withdraw this quote</button>
                </li>
              );
            })}
          </ul>
          {data.currencies.length > 1 && <p className="mt-3 text-[13px] text-muted">Prices are in different currencies, so each is compared only within its own currency.</p>}
          <p className="mt-4 rounded-2xl bg-accent/5 px-4 py-3 text-[14px] leading-snug text-foreground">{data.general_disclosure}</p>
        </>
      )}
      {adding && token && <QuoteDialog token={token} leadId={leadId} onClose={() => setAdding(false)} onSaved={(q) => { setData(q); setAdding(false); }} />}
    </section>
  );
}
