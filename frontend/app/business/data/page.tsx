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
  const body = (
    <div className="flex h-full flex-col justify-between rounded-3xl bg-surface-muted p-6 transition-all duration-500 ease-apple hover:bg-surface hover:shadow-raised">
      <div>
        <p className="text-[15px] font-medium text-foreground">{label}</p>
        {hint && <p className="mt-1 text-[13px] leading-snug text-muted">{hint}</p>}
      </div>
      <div className="mt-6 flex items-end justify-between gap-3">
        <span className={text ? "text-[17px] font-semibold tracking-tight text-foreground" : "font-display text-[2.5rem] font-semibold leading-none tracking-[-0.035em] text-foreground"}>{text ?? count}</span>
        {count === 0 && <span className="text-[13px] text-muted-foreground">nothing yet</span>}
      </div>
    </div>
  );
  return route ? (
    <Link href={route} className="block">{body}</Link>
  ) : (
    body
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
      <div className="mx-auto w-full max-w-5xl px-5 py-8 sm:px-8 sm:py-12">
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
                    <li key={r.key} className="klaros-card flex items-center justify-between gap-2 p-3 text-sm text-muted">
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
