"use client";

import { useState } from "react";
import { ApiError, MedicalTourismProvider, updateClinicContact } from "@/lib/api";
import { Modal } from "@/components/ui/Modal";

/** Where a clinic can be reached. Use the address the clinic itself publishes for international patients (its own website's contact page), never one found on a third-party listing. */
export function ClinicContact({ token, provider, onClose, onSaved }: { token: string; provider: MedicalTourismProvider; onClose: () => void; onSaved: () => void }) {
  const [email, setEmail] = useState(provider.contact_email ?? "");
  const [phone, setPhone] = useState(provider.contact_phone ?? "");
  const [city, setCity] = useState(provider.city ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await updateClinicContact(token, provider.id, { contact_email: email.trim(), contact_phone: phone.trim(), city: city.trim() });
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't save. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  const field = "mt-1 w-full rounded-xl border border-border bg-surface px-3 py-2 text-[14px] text-foreground focus:border-accent focus:outline-none";
  return (
    <Modal title={`Contact: ${provider.name}`} onClose={onClose} size="lg">
      {error && <div role="alert" className="mb-3 rounded-xl border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>}
      <div className="space-y-4 text-[14px] text-foreground">
        <p className="text-muted">Use the email the clinic itself publishes for international patients. Nothing is sent to it until you approve an invitation.</p>
        <label className="block">Email<input aria-label="Clinic email" type="email" className={field} value={email} onChange={(e) => setEmail(e.target.value)} placeholder="international@clinic.example" /></label>
        <label className="block">Phone<input aria-label="Clinic phone" className={field} value={phone} onChange={(e) => setPhone(e.target.value)} /></label>
        <label className="block">City<input aria-label="Clinic city" className={field} value={city} onChange={(e) => setCity(e.target.value)} /></label>
        <div className="flex justify-end gap-3 pt-2">
          <button type="button" onClick={onClose} className="text-muted hover:underline">Cancel</button>
          <button type="button" disabled={busy} onClick={save} className="klaros-btn-primary disabled:opacity-50">Save contact</button>
        </div>
      </div>
    </Modal>
  );
}
