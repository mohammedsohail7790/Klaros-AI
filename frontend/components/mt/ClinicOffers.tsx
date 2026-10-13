"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  MedicalTourismOffering,
  MedicalTourismProcedure,
  MedicalTourismProvider,
  createMedicalTourismOffering,
  listMedicalTourismOfferings,
  listMedicalTourismProcedures,
  listMedicalTourismProviders,
} from "@/lib/api";
import { VERIFICATION_LABEL } from "@/lib/mtLabels";
import { Alert } from "@/components/ui/Alert";
import { Modal } from "@/components/ui/Modal";

const CURRENCIES = ["USD", "EUR", "GBP", "AED", "SAR", "INR", "TRY"];

/**
 * What each clinic really offers. A treatment estimate belongs to a clinic and a procedure; it is optional, and it is only ever a price the clinic has approved:
 * Klaros never fills one in. Offers are what the matching step and the patient conversation draw on.
 */
export function ClinicOffers({ token, refreshKey = 0 }: { token: string; refreshKey?: number }) {
  const [offers, setOffers] = useState<MedicalTourismOffering[]>([]);
  const [providers, setProviders] = useState<MedicalTourismProvider[]>([]);
  const [procedures, setProcedures] = useState<MedicalTourismProcedure[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      const [o, p, pr] = await Promise.all([
        listMedicalTourismOfferings(token, { limit: 100 }),
        listMedicalTourismProviders(token, { limit: 100 }),
        listMedicalTourismProcedures(token, { limit: 100 }),
      ]);
      setOffers(o.offerings);
      setProviders(p.providers);
      setProcedures(pr.procedures);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load what clinics offer.");
    } finally {
      setLoaded(true);
    }
  }, [token]);

  // `refreshKey` changes when the page around this adds a procedure, so the
  // "Add an offer" button and its lists pick it up without a page reload.
  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const providerName = useMemo(() => Object.fromEntries(providers.map((p) => [p.id, p.name])), [providers]);
  const procedureName = useMemo(() => Object.fromEntries(procedures.map((p) => [p.id, p.name])), [procedures]);

  return (
    <section aria-label="What clinics offer" className="mt-10">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-xl font-semibold tracking-tight text-foreground">What clinics offer</h2>
          <p className="mt-0.5 text-sm text-muted">Which procedures each clinic really performs. A price is optional and should only be one the clinic has approved.</p>
        </div>
        <button type="button" onClick={() => setAdding(true)} disabled={providers.length === 0 || procedures.length === 0} className="klaros-btn-primary">
          Add an offer
        </button>
      </div>

      {error && (
        <div className="mt-3">
          <Alert variant="danger">{error}</Alert>
        </div>
      )}

      {loaded && !error && providers.length === 0 ? (
        <p className="mt-3 text-sm text-muted">Add a clinic first (Providers), then record what it offers.</p>
      ) : loaded && !error && offers.length === 0 ? (
        <p className="mt-3 text-sm text-muted">No offers recorded yet.</p>
      ) : offers.length > 0 ? (
        <div className="klaros-table-wrap mt-3">
          <table className="klaros-table">
            <thead className="bg-surface text-muted">
              <tr>
                <th className="px-4 py-2">Clinic</th>
                <th className="px-4 py-2">Procedure</th>
                <th className="px-4 py-2">Approved price</th>
              </tr>
            </thead>
            <tbody>
              {offers.map((o) => (
                <tr key={o.id} className="border-t border-border">
                  <td className="px-4 py-2 text-foreground">{providerName[o.provider_id] ?? "Clinic"}</td>
                  <td className="px-4 py-2 text-muted">{procedureName[o.procedure_id] ?? "Procedure"}</td>
                  <td className="px-4 py-2 text-muted">{o.estimated_price ? `${o.estimated_price} ${o.currency ?? ""}`.trim() : "Not given"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {adding && (
        <AddOffer
          token={token}
          providers={providers}
          procedures={procedures}
          onClose={() => setAdding(false)}
          onAdded={() => {
            setAdding(false);
            load();
          }}
        />
      )}
    </section>
  );
}

function AddOffer({ token, providers, procedures, onClose, onAdded }: { token: string; providers: MedicalTourismProvider[]; procedures: MedicalTourismProcedure[]; onClose: () => void; onAdded: () => void }) {
  const [provider, setProvider] = useState("");
  const [procedure, setProcedure] = useState("");
  const [price, setPrice] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const priceOk = price.trim() === "" || /^\d+(\.\d{1,2})?$/.test(price.trim());

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await createMedicalTourismOffering(token, { provider_id: provider, procedure_id: procedure, ...(price.trim() ? { estimated_price: price.trim(), currency } : {}) });
      onAdded();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to add the offer.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Add an offer" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        {error && (
          <div role="alert" className="rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">
            {error}
          </div>
        )}
        <label className="block text-sm">
          Clinic
          <select aria-label="Clinic" value={provider} onChange={(e) => setProvider(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5">
            <option value="">Choose…</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({VERIFICATION_LABEL[p.verification_status ?? "UNVERIFIED"] ?? p.verification_status})
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          Procedure
          <select aria-label="Procedure" value={procedure} onChange={(e) => setProcedure(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5">
            <option value="">Choose…</option>
            {procedures.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <div className="grid grid-cols-3 gap-2">
          <label className="col-span-2 block text-sm">
            Price the clinic has approved (optional)
            <input aria-label="Approved price" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="e.g. 1800" className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5" />
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
        {!priceOk && <p className="text-xs text-danger">Enter the price as a number, for example 1800 or 1800.50.</p>}
        <p className="text-xs text-muted-foreground">Leave the price blank unless the clinic gave you one in writing. Klaros never estimates a price for you.</p>
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="klaros-btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={busy || !provider || !procedure || !priceOk} className="klaros-btn-primary">
            {busy ? "Adding…" : "Add offer"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
