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

function Row({ label, count, route, text, hint }: { label: string; count?: number; route?: string | null; text?: string; hint?: string }) {
  const inner = (
    <>
      <span className="min-w-0">
        <span className="block text-sm text-foreground">{label}</span>
        {hint && <span className="block text-xs text-muted">{hint}</span>}
      </span>
      <span className="flex shrink-0 items-center gap-3">
        <span className={text ? "text-sm font-medium text-foreground" : "font-display text-2xl text-foreground"}>{text ?? count}</span>
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
  const runs = ops?.workflows.reduce((n, w) => n + w.runs, 0) ?? 0;
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
                <Row label="Leads" hint="Everyone who has enquired" count={ops.leads.total} route="/leads" />
                <Row label="Website enquiries" hint="Leads that came from your website form" count={ops.leads.website_enquiries} route="/leads?source=WEB" />
                <Row label="Customers" hint="People you have worked with" count={ops.customers} route="/customers" />
                <Row label="AI conversations" hint={ops.ai.interactions === 0 ? "None recorded — no AI workforce is reporting yet" : "Conversations your AI workforce reported"} count={ops.ai.interactions} route="/workforce" />
              </div>
            </ConsoleSection>

            {ops.module.data.length > 0 && (
              <ConsoleSection id="industry" title="Your industry data" hint="Records from the industry module enabled for your business.">
                <div className="grid gap-3 sm:grid-cols-2">
                  {ops.module.data.map((d) => <Row key={d.key} label={d.label} count={d.count ?? 0} route={d.route} />)}
                </div>
              </ConsoleSection>
            )}

            <ConsoleSection id="definition" title="Your business definition" hint="What Klaros built your business from.">
              <div className="grid gap-3 sm:grid-cols-2">
                <Row label="Blueprint" hint="Your confirmed business plan" text={overview?.blueprint ? `Version ${overview.blueprint.version}` : "Not confirmed"} route="/business/blueprint" />
                <Row label="Requirements" hint="What your business needs to run" count={overview?.requirements.length ?? 0} route="/business/requirements" />
              </div>
            </ConsoleSection>

            <ConsoleSection id="automation" title="Automation">
              <div className="grid gap-3 sm:grid-cols-2">
                <Row label="Workflows" hint="Automations that run on their own" count={ops.workflows.length} route="/business/workflows" />
                <Row label="Workflow runs" hint="Every time a workflow ran" count={runs} route="/business/workflows" />
              </div>
            </ConsoleSection>

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
