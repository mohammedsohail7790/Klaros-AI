"use client";

/** Operational analytics — lightweight, and only what can be computed from real records.
 * There is no revenue, order or traffic figure here because Klaros does not record those. */

import { BarChart3 } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { PageHeader } from "@/components/ui/PageHeader";
import { useOperations } from "@/components/business/useOperations";
import { ConsoleSection, LeadFunnel, SourceList } from "@/components/business/console";

function Stat({ label, value, hint }: { label: string; value: number | string; hint?: string }) {
  return (
    <div className="klaros-card p-4">
      <p className="klaros-label">{label}</p>
      <p className="mt-1 font-display text-3xl text-foreground">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
    </div>
  );
}

export default function AnalyticsPage() {
  const { token, user } = useAuth();
  const { ops, error, reload } = useOperations(token);
  const converted = ops?.leads.by_status["CONVERTED"] ?? 0;

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-5xl px-6 py-8">
        <PageHeader icon={BarChart3} title="Analytics" description="What your business is doing, measured from real records." />
        {error && <div className="mb-4"><Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => reload()}>Try again</Button>}>{error}</Alert></div>}
        {!ops && !error && <Skeleton rows={4} />}
        {ops && (
          <>
            <ConsoleSection id="leads" title="Leads">
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <Stat label="Total leads" value={ops.leads.total} />
                <Stat label="New in the last 7 days" value={ops.leads.new_7d} />
                <Stat label="Qualified or beyond" value={ops.leads.qualified} hint="Qualified, booked or converted" />
                <Stat label="Converted" value={converted} hint={ops.leads.total > 0 ? `${Math.round((converted / ops.leads.total) * 100)}% of all leads` : "No leads yet"} />
              </div>
              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                <div className="klaros-card p-4"><h3 className="mb-3 text-sm font-semibold text-foreground">By stage</h3><LeadFunnel leads={ops.leads} /></div>
                <div className="klaros-card p-4"><h3 className="mb-3 text-sm font-semibold text-foreground">By source</h3><SourceList leads={ops.leads} /><p className="mt-3 text-xs text-muted">Website enquiries: {ops.leads.website_enquiries}</p></div>
              </div>
            </ConsoleSection>

            {ops.module.metrics.length > 0 && (
              <ConsoleSection id="industry" title="Your industry">
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  {ops.module.metrics.map((m) => <Stat key={m.key} label={m.label} value={m.value ?? 0} />)}
                </div>
                {ops.module.breakdowns.some((b) => b.items.length > 0) && (
                  <div className="mt-4 grid gap-4 md:grid-cols-2">
                    {ops.module.breakdowns.filter((b) => b.items.length > 0).map((b) => (
                      <div key={b.key} className="klaros-card p-4">
                        <h3 className="mb-3 text-sm font-semibold text-foreground">{b.label}</h3>
                        <ul className="space-y-1 text-sm">
                          {b.items.map((i) => <li key={i.label} className="flex justify-between"><span className="text-muted">{i.label}</span><span className="font-medium text-foreground">{i.value}</span></li>)}
                        </ul>
                      </div>
                    ))}
                  </div>
                )}
              </ConsoleSection>
            )}

            <p className="text-xs text-muted">Revenue, orders and website traffic are not measured here — Klaros does not record them yet.</p>
          </>
        )}
      </div>
    </AppShell>
  );
}
