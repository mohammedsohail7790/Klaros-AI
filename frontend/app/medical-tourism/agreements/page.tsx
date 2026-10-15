"use client";

import { useCallback, useEffect, useState } from "react";
import { Handshake } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, ClinicAgreementRow, MedicalTourismProvider, getClinicAgreements } from "@/lib/api";
import { ClinicTerms } from "@/components/mt/ClinicTerms";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";

function describe(row: ClinicAgreementRow): string {
  const t = row.terms;
  if (t.commission_basis === "PERCENTAGE") return `${Number(t.commission_percentage)}% of the treatment price (${t.currency})`;
  if (t.commission_basis === "FLAT") return `${t.currency} ${Number(t.flat_amount)} per patient`;
  return "No commission agreed yet";
}

/** The commission side of Sales: which clinics have agreed to pay what. A clinic with no terms creates no commission, so it is listed plainly rather than hidden. */
export default function ClinicAgreementsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [rows, setRows] = useState<ClinicAgreementRow[] | null>(null);
  const [counts, setCounts] = useState<{ with: number; without: number }>({ with: 0, without: 0 });
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<ClinicAgreementRow | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const r = await getClinicAgreements(token);
      setRows(r.clinics);
      setCounts({ with: r.with_terms, without: r.without_terms });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load clinic agreements.");
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <PageHeader title="Clinic agreements" description="What each clinic has agreed to pay you. Your income is the commission clinics pay for the patients you refer." icon={Handshake} />
        {error ? (
          <p role="alert" className="text-danger">{error}</p>
        ) : authLoading || rows === null ? (
          <Skeleton />
        ) : rows.length === 0 ? (
          <EmptyState title="No clinics yet. Add a clinic on the Providers page, then record the commission it has agreed to pay." />
        ) : (
          <>
            <p className="mb-4 text-[15px] text-muted">
              {counts.with} of {rows.length} clinics have commission terms.{counts.without > 0 ? " A clinic without terms creates no commission when you refer a patient to it." : ""}
            </p>
            <ul className="divide-y divide-border/70 overflow-hidden rounded-3xl bg-surface-muted" aria-label="Clinic agreements">
              {rows.map((r) => (
                <li key={r.provider_id} className="flex flex-col gap-2 px-6 py-5 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <p className="text-[17px] font-semibold tracking-tight text-foreground">{r.name}</p>
                    <p className={`mt-0.5 text-[14px] ${r.terms.commission_basis ? "text-foreground" : "text-muted"}`}>{describe(r)}</p>
                    <p className="mt-0.5 text-[13px] text-muted-foreground">
                      {r.terms.agreement_reference ? `Agreement: ${r.terms.agreement_reference} · ` : ""}{r.referrals_sent} referral{r.referrals_sent === 1 ? "" : "s"} sent
                    </p>
                  </div>
                  <button onClick={() => setEditing(r)} className="shrink-0 text-[14px] text-accent hover:underline">{r.terms.commission_basis ? "Edit terms" : "Set terms"}</button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
      {editing && token && (
        <ClinicTerms token={token} provider={{ id: editing.provider_id, name: editing.name } as MedicalTourismProvider} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />
      )}
    </AppShell>
  );
}
