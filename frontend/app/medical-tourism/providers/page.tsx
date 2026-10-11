"use client";

import { useCallback, useEffect, useState } from "react";
import { Stethoscope } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  MedicalTourismProvider,
  createMedicalTourismProvider,
  listMedicalTourismProviders,
  setProviderVerification,
} from "@/lib/api";
import { VERIFICATION_LABEL, VERIFICATION_STATUSES } from "@/lib/mtLabels";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";

const STATUS_TABS = ["ALL", "ACTIVE", "INACTIVE"];

export default function MedicalTourismProvidersPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [providers, setProviders] = useState<MedicalTourismProvider[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("ALL");
  const [country, setCountry] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [verifying, setVerifying] = useState<MedicalTourismProvider | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listMedicalTourismProviders(token, {
        status: status === "ALL" ? undefined : status,
        country: country || undefined,
        limit: 50,
      });
      setProviders(result.providers);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load providers.");
    } finally {
      setLoading(false);
    }
  }, [token, status, country]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <PageHeader
          title={`Providers (${total})`}
          description="The tenant's Medical Tourism provider (hospital/clinic) directory."
          icon={Stethoscope}
          actions={
            <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
              New provider
            </button>
          }
        />

        <div className="mb-4 flex flex-wrap items-center gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`rounded-full border px-3 py-1 text-xs ${
                status === s ? "border-foreground bg-surface text-foreground" : "border-border-strong text-muted"
              }`}
            >
              {s}
            </button>
          ))}
          <input
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            placeholder="Country (e.g. TR)"
            className="ml-auto w-40 rounded-md border border-border-strong bg-surface-muted px-3 py-1.5 text-sm"
          />
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : providers.length === 0 ? (
          <EmptyState
            icon={Stethoscope}
            title="No providers yet."
            action={
              <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
                New provider
              </button>
            }
          />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Name</th>
                  <th className="px-4 py-2">Practitioner</th>
                  <th className="px-4 py-2">Location</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Verification</th>
                  <th className="px-4 py-2">Contact</th>
                </tr>
              </thead>
              <tbody>
                {providers.map((p) => (
                  <tr key={p.id} className="border-t border-border hover:bg-surface">
                    <td className="px-4 py-2 font-medium text-foreground">{p.name}</td>
                    <td className="px-4 py-2 text-muted">{p.practitioner_name || "—"}</td>
                    <td className="px-4 py-2 text-muted">
                      {[p.city, p.country].filter(Boolean).join(", ") || "—"}
                    </td>
                    <td className="px-4 py-2">
                      <Badge status={p.status}>{p.status}</Badge>
                    </td>
                    <td className="px-4 py-2 whitespace-nowrap">
                      <span className={p.verification_status === "VERIFIED" ? "font-medium text-success" : "text-muted"}>{VERIFICATION_LABEL[p.verification_status ?? "UNVERIFIED"] ?? p.verification_status}</span>
                      <button onClick={() => setVerifying(p)} className="ml-2 text-xs underline">Change</button>
                    </td>
                    <td className="px-4 py-2 text-muted">{p.contact_email || p.contact_phone || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {verifying && token && (
        <VerificationModal token={token} provider={verifying} onClose={() => setVerifying(null)} onSaved={() => { setVerifying(null); load(); }} />
      )}

      {showCreate && token && (
        <CreateProviderModal token={token} onClose={() => setShowCreate(false)} onCreated={load} />
      )}
    </AppShell>
  );
}

function CreateProviderModal({
  token,
  onClose,
  onCreated,
}: {
  token: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [country, setCountry] = useState("");
  const [practitionerName, setPractitionerName] = useState("");
  const [city, setCity] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [contactPhone, setContactPhone] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await createMedicalTourismProvider(token, {
        name,
        country,
        practitioner_name: practitionerName || undefined,
        city: city || undefined,
        contact_email: contactEmail || undefined,
        contact_phone: contactPhone || undefined,
        description: description || undefined,
      });
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create provider.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title="New provider" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <input required placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} className="klaros-input" />
        <input
          required
          placeholder="Country (ISO-2, e.g. TR)"
          value={country}
          maxLength={2}
          onChange={(e) => setCountry(e.target.value.toUpperCase())}
          className="klaros-input"
        />
        <input
          placeholder="Practitioner name"
          value={practitionerName}
          onChange={(e) => setPractitionerName(e.target.value)}
          className="klaros-input"
        />
        <input placeholder="City" value={city} onChange={(e) => setCity(e.target.value)} className="klaros-input" />
        <input
          placeholder="Contact email"
          value={contactEmail}
          onChange={(e) => setContactEmail(e.target.value)}
          className="klaros-input"
        />
        <input
          placeholder="Contact phone"
          value={contactPhone}
          onChange={(e) => setContactPhone(e.target.value)}
          className="klaros-input"
        />
        <textarea
          placeholder="Description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="klaros-input"
          rows={3}
        />
        {error && <p className="text-sm text-danger">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="klaros-btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="klaros-btn-primary">
            {submitting ? "Creating..." : "Create provider"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/**
 * Set a clinic's verification. A clinic can be marked Verified only with at least one verified, unexpired credential on record (the backend refuses otherwise),
 * and only an active, currently verified clinic can be matched or referred to.
 */
function VerificationModal({ token, provider, onClose, onSaved }: { token: string; provider: MedicalTourismProvider; onClose: () => void; onSaved: () => void }) {
  const [status, setStatus] = useState(provider.verification_status ?? "UNVERIFIED");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await setProviderVerification(token, provider.id, status, note || undefined);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to change the verification.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title={`Verification: ${provider.name}`} onClose={onClose}>
      {error && <div role="alert" className="mb-3 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>}
      <label className="block text-sm">Status
        <select aria-label="Verification status" value={status} onChange={(e) => setStatus(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5">
          {VERIFICATION_STATUSES.map((v) => <option key={v} value={v}>{VERIFICATION_LABEL[v]}</option>)}
        </select>
      </label>
      <label className="mt-3 block text-sm">Note (optional)
        <input value={note} onChange={(e) => setNote(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5" />
      </label>
      <p className="mt-2 text-xs text-muted-foreground">Verified needs at least one verified, unexpired credential. Only an active, verified clinic can be matched or referred to.</p>
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose} className="klaros-btn-secondary">Cancel</button>
        <button onClick={save} disabled={saving} className="klaros-btn-primary">{saving ? "Saving…" : "Save"}</button>
      </div>
    </Modal>
  );
}
