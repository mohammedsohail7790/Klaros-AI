"use client";

import { useCallback, useEffect, useState } from "react";
import { Stethoscope } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  MedicalTourismProvider,
  createMedicalTourismProvider,
  ClinicSourcingResult,
  listMedicalTourismProviders,
  runClinicSourcing,
} from "@/lib/api";
import { VERIFICATION_LABEL, countryName, referableNote } from "@/lib/mtLabels";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { ClinicVerification } from "@/components/mt/ClinicVerification";
import { ClinicTerms } from "@/components/mt/ClinicTerms";
import { Hero, Page, StatusSummary } from "@/components/ui/layout";

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
  const [finding, setFinding] = useState(false);
  const [findCountries, setFindCountries] = useState("TR, TH, KR, IN");
  const [found, setFound] = useState<ClinicSourcingResult | null>(null);
  const [verifying, setVerifying] = useState<MedicalTourismProvider | null>(null);
  const [pricing, setPricing] = useState<MedicalTourismProvider | null>(null);

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

  async function findClinics() {
    if (!token || finding) return;
    setFinding(true);
    setError(null);
    try {
      const codes = findCountries.split(/[ ,]+/).map((c) => c.trim().toUpperCase()).filter(Boolean);
      setFound(await runClinicSourcing(token, codes, []));
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't search for clinics.");
    } finally {
      setFinding(false);
    }
  }

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell user={user}>
      <Page>
        <Hero
          title={`Providers (${total})`}
          lede="The hospitals and clinics you refer patients to. Only a verified clinic can receive a referral."
          actions={
            <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
              New provider
            </button>
          }
        />

        <StatusSummary rows={providers.map((p) => ({ status: p.verification_status ?? "UNVERIFIED" }))} noun="clinics" />

        <section aria-label="Find clinics automatically" className="mb-12 rounded-3xl bg-surface-muted p-7 sm:p-9">
          <h2 className="font-display text-2xl font-semibold tracking-[-0.02em] text-foreground">Find clinics automatically</h2>
          <p className="mt-2 max-w-2xl text-[15px] leading-snug text-muted">
            Klaros reads public pages and adds what it finds as unverified, with the source link. It contacts no one. You confirm each clinic&apos;s accreditation before it can receive a referral.
          </p>
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <input
              value={findCountries}
              onChange={(e) => setFindCountries(e.target.value)}
              aria-label="Country codes to search"
              placeholder="TR, TH, KR, IN"
              className="klaros-input !w-56 !rounded-full"
            />
            <button onClick={findClinics} disabled={finding} className="klaros-btn-primary">
              {finding ? "Searching…" : "Search the web"}
            </button>
          </div>
          {found && (
            <p className="mt-4 text-[15px] text-foreground" role="status">
              {found.status === "ok"
                ? `Added ${found.added.length} clinic${found.added.length === 1 ? "" : "s"} for review (${found.skipped_duplicate} already on your list).`
                : found.notes.join(" ")}
            </p>
          )}
        </section>

        <div className="mb-5 flex flex-wrap items-center gap-2">
          {STATUS_TABS.map((s) => (
            <button key={s} onClick={() => setStatus(s)} className={`klaros-filter-tab ${status === s ? "klaros-filter-tab-active" : "klaros-filter-tab-inactive"}`}>
              {s}
            </button>
          ))}
          <input
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            placeholder="Country (e.g. TR)"
            className="klaros-input ml-auto !w-44 !rounded-full"
          />
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-3xl bg-danger/[0.06] p-6 text-[15px] text-danger">
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
          <ul className="divide-y divide-border/70 overflow-hidden rounded-3xl bg-surface-muted" aria-label="Providers">
            {providers.map((p) => {
              const note = referableNote(p.status, p.verification_status);
              const verified = p.verification_status === "VERIFIED";
              return (
                <li key={p.id} className="flex flex-col gap-3 px-6 py-5 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <p className="text-[17px] font-semibold tracking-tight text-foreground">{p.name}</p>
                    <p className="mt-0.5 text-[14px] text-muted">
                      {[p.practitioner_name, [p.city, countryName(p.country)].filter(Boolean).join(", ")].filter(Boolean).join(" · ") || "—"}
                    </p>
                    <p className="mt-0.5 text-[13px] text-muted-foreground">{p.contact_email || p.contact_phone || "No contact on file"}</p>
                  </div>
                  <div className="flex shrink-0 flex-col items-start gap-1.5 sm:items-end">
                    <div className="flex items-center gap-2">
                      <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${verified ? "bg-success/10 text-success" : "bg-surface text-muted"}`}>
                        <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${verified ? "bg-success" : "bg-muted-foreground"}`} />
                        {VERIFICATION_LABEL[p.verification_status ?? "UNVERIFIED"] ?? p.verification_status}
                      </span>
                      <Badge status={p.status}>{p.status}</Badge>
                      <button onClick={() => setVerifying(p)} className="text-[14px] text-accent hover:underline">
                        Change
                      </button>
                      <button onClick={() => setPricing(p)} className="text-[14px] text-accent hover:underline">
                        Terms
                      </button>
                    </div>
                    <p className={`text-[13px] ${note.ok ? "text-success" : "text-muted-foreground"}`}>{note.text}</p>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Page>

      {verifying && token && (
        <ClinicVerification token={token} provider={verifying} onClose={() => setVerifying(null)} onSaved={() => { setVerifying(null); load(); }} />
      )}

      {pricing && token && <ClinicTerms token={token} provider={pricing} onClose={() => setPricing(null)} onSaved={() => setPricing(null)} />}

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
