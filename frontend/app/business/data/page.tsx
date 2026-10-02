"use client";

/** Data center: the business data that really exists for this business, with real counts.
 * Anything the business needs that Klaros has no data model for is shown as
 * "Not configured" — never as an empty table of sample rows. */

import Link from "next/link";
import { Database } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { PageHeader } from "@/components/ui/PageHeader";
import { useBuilderOverview } from "@/components/business/useBuilderOverview";
import { useOperations } from "@/components/business/useOperations";
import { ConsoleSection } from "@/components/business/console";
import { StatusPill } from "@/components/business/StatusPill";

function Row({ label, count, route }: { label: string; count: number; route?: string | null }) {
  const inner = (
    <>
      <span className="text-sm text-foreground">{label}</span>
      <span className="flex items-center gap-3">
        <span className="font-display text-2xl text-foreground">{count}</span>
        {count === 0 && <span className="text-xs text-muted-foreground">nothing yet</span>}
      </span>
    </>
  );
  return route ? (
    <Link href={route} className="klaros-card klaros-card-interactive flex items-center justify-between p-4">{inner}</Link>
  ) : (
    <div className="klaros-card flex items-center justify-between p-4">{inner}</div>
  );
}

export default function DataCenterPage() {
  const { token, user } = useAuth();
  const { ops, error, reload } = useOperations(token);
  const { overview } = useBuilderOverview(token);
  const notConfigured = (overview?.requirements ?? []).filter((r) => r.klaros_support === "PLANNED" && r.required);

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-4xl px-6 py-8">
        <PageHeader icon={Database} title="Data" description="The records your business runs on. Counts come straight from your data." />
        {error && <div className="mb-4"><Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => reload()}>Try again</Button>}>{error}</Alert></div>}
        {!ops && !error && <Skeleton rows={4} />}
        {ops && (
          <>
            <ConsoleSection id="core" title="Customers & leads">
              <div className="grid gap-3 sm:grid-cols-2">
                <Row label="Leads" count={ops.leads.total} route="/leads" />
                <Row label="Customers" count={ops.customers} route="/customers" />
              </div>
            </ConsoleSection>

            {ops.module.data.length > 0 && (
              <ConsoleSection id="industry" title="Your industry data" hint="Records from the industry module enabled for your business.">
                <div className="grid gap-3 sm:grid-cols-2">
                  {ops.module.data.map((d) => <Row key={d.key} label={d.label} count={d.count ?? 0} route={d.route} />)}
                </div>
              </ConsoleSection>
            )}

            {notConfigured.length > 0 && (
              <ConsoleSection id="missing" title="Not configured" hint="Your business needs these, but Klaros has no data model for them yet — so there is nothing to show.">
                <ul className="grid gap-2 sm:grid-cols-2">
                  {notConfigured.map((r) => (
                    <li key={r.key} className="klaros-card flex items-center justify-between gap-2 border-dashed p-3 text-sm text-muted">
                      {r.label}
                      <StatusPill state="PLANNED" />
                    </li>
                  ))}
                </ul>
              </ConsoleSection>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
