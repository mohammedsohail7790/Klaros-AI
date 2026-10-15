"use client";

import { useEffect, useState } from "react";
import { ApiError, ClinicTerms as Terms, MedicalTourismProvider, getClinicTerms, saveClinicTerms } from "@/lib/api";
import { Modal } from "@/components/ui/Modal";

/**
 * What this clinic has agreed to pay you. Klaros reads these terms to create the commission when a referral is sent and to draft the invoice once the clinic has
 * reported the outcome. Nothing is assumed: leave it empty and no commission is created for this clinic. Enter only what is in your signed agreement.
 */
export function ClinicTerms({ token, provider, onClose, onSaved }: { token: string; provider: MedicalTourismProvider; onClose: () => void; onSaved: () => void }) {
  const [loaded, setLoaded] = useState(false);
  const [basis, setBasis] = useState<"" | "PERCENTAGE" | "FLAT">("");
  const [percentage, setPercentage] = useState("");
  const [flat, setFlat] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getClinicTerms(token, provider.id)
      .then(({ terms }) => {
        setBasis(terms.commission_basis ?? "");
        setPercentage(terms.commission_percentage ?? "");
        setFlat(terms.flat_amount ?? "");
        setCurrency(terms.currency ?? "USD");
        setReference(terms.agreement_reference ?? "");
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "Unable to load this clinic's terms."))
      .finally(() => setLoaded(true));
  }, [token, provider.id]);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await saveClinicTerms(token, provider.id, {
        commission_basis: basis || null,
        commission_percentage: basis === "PERCENTAGE" ? percentage : null,
        flat_amount: basis === "FLAT" ? flat : null,
        currency: basis ? currency : null,
        agreement_reference: reference || null,
      });
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't save. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  const field = "mt-1 w-full rounded-xl border border-border bg-surface px-3 py-2 text-[14px] text-foreground focus:border-accent focus:outline-none";
  return (
    <Modal title={`Terms: ${provider.name}`} onClose={onClose} size="lg">
      {error && <div role="alert" className="mb-3 rounded-xl border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>}
      {!loaded ? null : (
        <div className="space-y-4 text-[14px] text-foreground">
          <p className="text-muted">Enter only what your signed agreement says. With no terms, no commission is created for this clinic.</p>
          <label className="block">Commission
            <select aria-label="Commission type" className={field} value={basis} onChange={(e) => setBasis(e.target.value as typeof basis)}>
              <option value="">None agreed</option>
              <option value="PERCENTAGE">A percentage of the treatment price</option>
              <option value="FLAT">A flat fee per patient</option>
            </select>
          </label>
          {basis === "PERCENTAGE" && <label className="block">Percentage<input aria-label="Commission percentage" inputMode="decimal" className={field} value={percentage} onChange={(e) => setPercentage(e.target.value)} placeholder="e.g. 15" /></label>}
          {basis === "FLAT" && <label className="block">Flat fee<input aria-label="Flat fee" inputMode="decimal" className={field} value={flat} onChange={(e) => setFlat(e.target.value)} placeholder="e.g. 400" /></label>}
          <label className="block">Currency<input aria-label="Currency" maxLength={3} className={field} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></label>
          <label className="block">Where the signed agreement is kept<input aria-label="Agreement reference" className={field} value={reference} onChange={(e) => setReference(e.target.value)} placeholder="A reference, not the agreement itself" /></label>
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="text-muted hover:underline">Cancel</button>
            <button type="button" disabled={busy} onClick={save} className="klaros-btn-primary disabled:opacity-50">Save terms</button>
          </div>
        </div>
      )}
    </Modal>
  );
}
