"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CalendarCheck, CheckCircle2, ClipboardList, Hourglass, LayoutDashboard, ShieldCheck, Users } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, MedicalTourismFunnel, getMedicalTourismFunnel } from "@/lib/api";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatCard } from "@/components/ui/StatCard";

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="mt-8">
      <h2 className="font-display text-lg text-foreground">{title}</h2>
      {hint && <p className="mt-0.5 text-sm text-muted">{hint}</p>}
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">{children}</div>
    </section>
  );
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/**
 * The pilot coordinator's front page: what the AI receptionist has handed over, what is waiting for a person, and whether the connection is healthy.
 * Counts only, straight from the backend funnel (mt_funnel.py); nothing is derived or invented in the browser, and no patient is named here.
 */
export default function MedicalTourismOverviewPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [funnel, setFunnel] = useState<MedicalTourismFunnel | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setFunnel(await getMedicalTourismFunnel(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load the overview.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const f = funnel;
  const failed = f ? f.delivery.halla_events_failed + f.delivery.halla_events_stuck_past_lease : 0;
  const escalations = f ? f.escalations_requested.halla + f.escalations_requested.klaros_safety_screen : 0;

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <PageHeader
          title="Overview"
          description="What the AI receptionist has handed to your team, what is waiting for a person, and whether the connection is healthy."
          icon={LayoutDashboard}
          actions={
            <Button size="sm" variant="secondary" onClick={() => load()}>
              Refresh
            </Button>
          }
        />

        {authLoading || (loading && !f) ? (
          <Skeleton />
        ) : error ? (
          <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>
            {error}
          </Alert>
        ) : f ? (
          <>
            <div className="space-y-2" aria-label="Needs attention">
              {failed > 0 && (
                <Alert variant="danger">
                  {plural(failed, "message", "messages")} from the AI receptionist did not finish arriving ({f.delivery.halla_events_failed} failed, {f.delivery.halla_events_stuck_past_lease} stuck). Some
                  enquiries may be missing or out of date until this is resolved.
                </Alert>
              )}
              {f.qualification.held_for_person > 0 && (
                <Alert
                  variant="warning"
                  action={
                    <Link href="/medical-tourism/leads" className="underline">
                      Open patient enquiries
                    </Link>
                  }
                >
                  {plural(f.qualification.held_for_person, "enquiry is", "enquiries are")} held for a person. The AI does not decide these.
                </Alert>
              )}
              {escalations > 0 && (
                <Alert variant="warning">
                  {plural(escalations, "escalation was", "escalations were")} requested. This means a person was asked to act, not that anyone has yet.
                </Alert>
              )}
              {f.delivery.outbound_blocked_by_consent > 0 && (
                <Alert variant="info">
                  {plural(f.delivery.outbound_blocked_by_consent, "outbound message was", "outbound messages were")} blocked because the patient has not agreed to be contacted.
                </Alert>
              )}
              {failed === 0 && f.qualification.held_for_person === 0 && escalations === 0 && f.delivery.outbound_blocked_by_consent === 0 && (
                <Alert variant="success">Nothing needs attention right now.</Alert>
              )}
            </div>

            <Section title="Enquiries" hint="Everything the AI receptionist has recorded for this business.">
              <StatCard label="Received" value={f.received} icon={Users} />
              <StatCard label="Qualified" value={f.qualification.qualified} icon={CheckCircle2} tone="success" note="A decision recorded by the AI or a person." />
              <StatCard label="Held for a person" value={f.qualification.held_for_person} icon={AlertTriangle} tone={f.qualification.held_for_person > 0 ? "warning" : "neutral"} />
              <StatCard label="Awaiting review" value={f.qualification.pending} icon={Hourglass} />
            </Section>

            <Section title="Consent" hint="What each patient has agreed to, using their most recent answer.">
              <StatCard label="May be contacted" value={f.consent.contact_granted} icon={ShieldCheck} tone="success" />
              <StatCard label="Details may be kept" value={f.consent.store_personal_data_granted} icon={ShieldCheck} tone="success" />
              <StatCard label="Health information may be kept" value={f.consent.store_medical_information_granted} icon={ShieldCheck} tone="success" />
              <StatCard
                label="Details withheld"
                value={f.consent.frozen}
                icon={ShieldCheck}
                tone={f.consent.frozen > 0 ? "warning" : "neutral"}
                note="Declined or withdrawn: personal details are not kept."
              />
              <StatCard label="No consent record" value={f.consent.no_evidence} icon={ClipboardList} tone={f.consent.no_evidence > 0 ? "warning" : "neutral"} />
            </Section>

            <Section title="Appointments" hint="Counted by their own status.">
              <StatCard label="Confirmed" value={f.appointments.confirmed} icon={CalendarCheck} tone="success" />
              <StatCard label="Requested, not booked" value={f.appointments.tentative_not_a_booking} icon={Hourglass} note="A request is not a booking." />
              <StatCard label="Completed" value={f.appointments.completed} icon={CheckCircle2} />
              <StatCard label="Cancelled or no-show" value={f.appointments.cancelled + f.appointments.no_show} icon={AlertTriangle} />
            </Section>

            <Section title="Connection to the AI receptionist" hint="Messages received from Halla AI.">
              <StatCard label="Processed" value={f.delivery.halla_events_processed} icon={CheckCircle2} tone="success" />
              <StatCard label="In progress" value={f.delivery.halla_events_in_flight} icon={Hourglass} />
              <StatCard label="Failed" value={f.delivery.halla_events_failed} icon={AlertTriangle} tone={f.delivery.halla_events_failed > 0 ? "danger" : "neutral"} />
              <StatCard label="Stuck" value={f.delivery.halla_events_stuck_past_lease} icon={AlertTriangle} tone={f.delivery.halla_events_stuck_past_lease > 0 ? "danger" : "neutral"} />
            </Section>

            <p className="mt-8 max-w-3xl text-xs text-muted-foreground">{f.definitions}</p>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
