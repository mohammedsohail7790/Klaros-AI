"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Send } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  MedicalTourismProvider,
  PipelineLead,
  ProviderReferral,
  getMedicalTourismPipeline,
  listMedicalTourismProviders,
  listProviderReferrals,
} from "@/lib/api";
import { NewReferral, ReferralDetail } from "@/components/mt/ReferralDialogs";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Hero } from "@/components/ui/layout";
import { Skeleton } from "@/components/ui/Skeleton";

/**
 * Referrals to a clinic. A coordinator records the PATIENT'S decision for ONE clinic (never granting it for them), then sends. The page shows, from the
 * backend's own check, whether a send is allowed right now and, if not, why in plain words. The browser is not the control: the backend refuses a send that
 * the patient has not approved, whatever this page does.
 */
export default function ProviderReferralsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [referrals, setReferrals] = useState<ProviderReferral[]>([]);
  const [providers, setProviders] = useState<MedicalTourismProvider[]>([]);
  const [patients, setPatients] = useState<PipelineLead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [r, p, pipe] = await Promise.all([listProviderReferrals(token), listMedicalTourismProviders(token, { limit: 100 }), getMedicalTourismPipeline(token)]);
      setReferrals(r.referrals);
      setProviders(p.providers);
      setPatients(pipe.leads);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load referrals.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const providerName = useMemo(() => Object.fromEntries(providers.map((p) => [p.id, p.name])), [providers]);
  const patientName = useMemo(() => Object.fromEntries(patients.map((p) => [p.lead_id, p.name ?? "Name withheld"])), [patients]);

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <Hero
          title="Referrals"
          lede="Send a patient's enquiry to a verified clinic, only after the patient has approved that clinic."
          actions={<Button size="sm" onClick={() => setCreating(true)}>New referral</Button>}
        />
        {authLoading || (loading && referrals.length === 0) ? (
          <Skeleton />
        ) : error ? (
          <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>{error}</Alert>
        ) : referrals.length === 0 ? (
          <EmptyState icon={Send} title="No referrals yet." action={<Button size="sm" onClick={() => setCreating(true)}>New referral</Button>} />
        ) : (
          <ul className="divide-y divide-border/70 overflow-hidden rounded-3xl bg-surface-muted" aria-label="Referrals">
            {referrals.map((r) => (
              <li key={r.id} className="flex flex-col gap-3 px-6 py-5 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <Link href={`/medical-tourism/patients/${r.lead_id}`} className="text-[17px] font-semibold tracking-tight text-foreground hover:underline">
                    {patientName[r.lead_id] ?? "Patient"}
                  </Link>
                  <p className="mt-0.5 text-[14px] text-muted">{providerName[r.provider_id] ?? "Clinic"}</p>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <Badge status={r.status}>{r.status.replace("_", " ")}</Badge>
                  <Button size="sm" variant="secondary" onClick={() => setOpenId(r.id)}>Open</Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {creating && token && (
        <NewReferral token={token} patients={patients} providers={providers} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setOpenId(id); load(); }} />
      )}
      {openId && token && <ReferralDetail token={token} id={openId} providerName={providerName} onClose={() => { setOpenId(null); load(); }} />}
    </AppShell>
  );
}
