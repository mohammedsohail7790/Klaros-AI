"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  MedicalTourismProvider,
  MedicalTourismProviderCredential,
  addProviderCredential,
  listProviderCredentials,
  setProviderVerification,
  verifyProviderCredential,
} from "@/lib/api";
import { VERIFICATION_LABEL, VERIFICATION_STATUSES, referableNote } from "@/lib/mtLabels";
import { Badge } from "@/components/ui/Badge";
import { Modal } from "@/components/ui/Modal";

const CREDENTIAL_TYPES = ["Operating licence", "Hospital accreditation", "Specialist licence", "Professional indemnity insurance", "Quality certification"];

const today = () => new Date().toISOString().slice(0, 10);
const isExpired = (c: MedicalTourismProviderCredential) => !!c.expiry_date && c.expiry_date < today();
/** A credential counts toward verification only once a person has checked it and it has not expired. */
export const counts = (c: MedicalTourismProviderCredential) => c.status === "VERIFIED" && !isExpired(c);

/**
 * A clinic becomes referable in three steps the owner can see: add what the clinic says it holds, record that you checked it with the issuing authority, then
 * mark the clinic Verified. The backend enforces the rule (a clinic is Verified only with at least one checked, unexpired credential); this dialog makes that path
 * possible and says what is missing.
 */
export function ClinicVerification({ token, provider, onClose, onSaved }: { token: string; provider: MedicalTourismProvider; onClose: () => void; onSaved: () => void }) {
  const [credentials, setCredentials] = useState<MedicalTourismProviderCredential[] | null>(null);
  const [status, setStatus] = useState(provider.verification_status ?? "UNVERIFIED");
  const [note, setNote] = useState("");
  const [type, setType] = useState(CREDENTIAL_TYPES[0]);
  const [authority, setAuthority] = useState("");
  const [number, setNumber] = useState("");
  const [expiry, setExpiry] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setCredentials((await listProviderCredentials(token, provider.id)).credentials);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load this clinic's credentials.");
    }
  }, [token, provider.id]);

  useEffect(() => {
    load();
  }, [load]);

  async function run(fn: () => Promise<unknown>, fail: string): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      await fn();
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : fail);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const ok = await run(
      () =>
        addProviderCredential(token, provider.id, {
          credential_type: type,
          ...(authority.trim() ? { issuing_authority: authority.trim() } : {}),
          ...(number.trim() ? { credential_number: number.trim() } : {}),
          ...(expiry ? { expiry_date: expiry } : {}),
        }),
      "Unable to add the credential.",
    );
    if (ok) {
      setAuthority("");
      setNumber("");
      setExpiry("");
      await load();
    }
  }

  async function check(c: MedicalTourismProviderCredential) {
    if (await run(() => verifyProviderCredential(token, provider.id, c.id), "Unable to record the check.")) await load();
  }

  async function save(next: string) {
    if (await run(() => setProviderVerification(token, provider.id, next, note || undefined), "Unable to change the verification.")) onSaved();
  }

  const ready = (credentials ?? []).some(counts);
  const note_ = referableNote("ACTIVE", status);

  return (
    <Modal title={`Verification: ${provider.name}`} onClose={onClose} size="lg">
      {error && (
        <div role="alert" className="mb-3 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">
          {error}
        </div>
      )}

      <section aria-label="Credentials">
        <h3 className="font-medium text-foreground">1. Credentials on record</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">What the clinic says it holds. Add each one, then record that you checked it with the issuing authority.</p>
        {credentials === null ? (
          <p className="mt-2 text-sm text-muted">Loading…</p>
        ) : credentials.length === 0 ? (
          <p className="mt-2 text-sm text-muted">None yet. Add at least one below.</p>
        ) : (
          <ul className="mt-2 divide-y divide-border rounded-md border border-border">
            {credentials.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="font-medium text-foreground">{c.credential_type}</div>
                  <div className="text-xs text-muted">
                    {[c.issuing_authority, c.credential_number && `No. ${c.credential_number}`, c.expiry_date && `expires ${c.expiry_date}`].filter(Boolean).join(" · ") || "No further details"}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {isExpired(c) ? <Badge status="EXPIRED">Expired</Badge> : <Badge status={c.status}>{c.status === "VERIFIED" ? "Checked" : c.status === "PENDING" ? "Not checked yet" : c.status}</Badge>}
                  {c.status === "PENDING" && !isExpired(c) && (
                    <button type="button" disabled={busy} onClick={() => check(c)} className="klaros-btn-secondary" aria-label={`I checked ${c.credential_type}`}>
                      I checked this
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}

        <form onSubmit={add} className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2" aria-label="Add a credential">
          <label className="block text-xs">
            Type
            <select aria-label="Credential type" value={type} onChange={(e) => setType(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5 text-sm">
              {CREDENTIAL_TYPES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className="block text-xs">
            Issued by
            <input aria-label="Issuing authority" value={authority} onChange={(e) => setAuthority(e.target.value)} placeholder="e.g. Ministry of Health" className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5 text-sm" />
          </label>
          <label className="block text-xs">
            Reference number
            <input aria-label="Credential number" value={number} onChange={(e) => setNumber(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5 text-sm" />
          </label>
          <label className="block text-xs">
            Expires on
            <input aria-label="Expiry date" type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5 text-sm" />
          </label>
          <div className="sm:col-span-2">
            <button type="submit" disabled={busy} className="klaros-btn-secondary">
              Add credential
            </button>
          </div>
        </form>
      </section>

      <section aria-label="Clinic status" className="mt-5 border-t border-border pt-4">
        <h3 className="font-medium text-foreground">2. Clinic status</h3>
        {ready && status !== "VERIFIED" && (
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-md bg-success/10 p-3 text-sm text-success">
            <span>A checked, unexpired credential is on record. This clinic can now be marked Verified.</span>
            <button type="button" disabled={busy} onClick={() => save("VERIFIED")} className="klaros-btn-primary">
              Mark clinic as verified
            </button>
          </div>
        )}
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            Status
            <select aria-label="Verification status" value={status} onChange={(e) => setStatus(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5">
              {VERIFICATION_STATUSES.map((v) => (
                <option key={v} value={v}>
                  {VERIFICATION_LABEL[v]}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            Note (optional)
            <input aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5" />
          </label>
        </div>
        {(ready || status !== "VERIFIED") && <p className={`mt-2 text-xs ${note_.ok ? "text-success" : "text-muted-foreground"}`}>{note_.text}</p>}
        {!ready && status === "VERIFIED" && <p className="mt-2 text-xs text-warning">Add a credential and record that you checked it before this can be saved as Verified.</p>}
      </section>

      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose} className="klaros-btn-secondary">
          Close
        </button>
        <button onClick={() => save(status)} disabled={busy} className="klaros-btn-primary">
          {busy ? "Saving…" : "Save status"}
        </button>
      </div>
    </Modal>
  );
}
