"use client";

/**
 * Business Home — the operating console.
 *
 * Before the website is live it is "Prepare your business": what is ready, exactly what is
 * holding launch up, and what to do next. Once the website is published it becomes "Operate
 * your business": what needs attention, the leads coming in, how each lead is handled, what
 * the team and AI workforce are doing, and what has happened. Every figure comes from the
 * backend's derived read-models (overview + operations); an empty business shows zeros and
 * "nothing yet" — never sample data. No state is shown that isn't true.
 */

import { useCallback, useEffect, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { Hero, Page } from "@/components/ui/layout";
import { Skeleton } from "@/components/ui/Skeleton";
import { StageTracker } from "@/components/business/StageTracker";
import { useBuilderOverview } from "@/components/business/useBuilderOverview";
import { useOperations } from "@/components/business/useOperations";
import { ConsoleSection, LeadFunnel, LeadPipeline } from "@/components/business/console";
import { ActionCenter, ActivityTimeline, ArchitectureStrip, HealthStrip, LaunchPanel, LeadsToAct, QuickActions } from "@/components/business/CommandCenter";
import { buildActionCenter, buildArchitectureChain, buildHealth, buildLaunchRows } from "@/components/business/homeModel";

export default function BusinessHomePage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const { overview, error, reload } = useBuilderOverview(token);
  const { ops, error: opsError, reload: reloadOps } = useOperations(token);

  useEffect(() => {
    if (overview && !overview.journey) router.replace("/business");
  }, [overview, router]);

  const onLaunched = useCallback(() => {
    reload();
    reloadOps();
  }, [reload, reloadOps]);

  const model = useMemo(() => {
    if (!overview || !overview.journey) return null;
    return {
      actions: buildActionCenter(overview, ops),
      health: buildHealth(overview, ops),
      chain: buildArchitectureChain(overview, ops),
      launch: buildLaunchRows(overview, ops),
    };
  }, [overview, ops]);

  if (!overview || !overview.journey || !model) {
    return (
      <AppShell user={user}>
        <div className="mx-auto max-w-6xl px-6 py-10">
          {error ? (
            <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => reload()}>Try again</Button>}>
              {error}
            </Alert>
          ) : (
            <Skeleton rows={5} />
          )}
        </div>
      </AppShell>
    );
  }

  const { business, launch } = overview;
  const operating = launch.launched;
  const inProgress = overview.journey.status !== "COMPLETED";
  const stage = operating ? "Operating" : inProgress ? "Setting up" : "Ready to launch";
  const statusText = launch.launched
    ? launch.ready
      ? "Live"
      : "Live — launch checklist incomplete"
    : inProgress
      ? "Setting up"
      : "Not launched yet";
  const quick = [
    { label: "View leads", href: "/leads" },
    { label: overview.website.exists ? "Open website" : "Build website", href: "/website" },
    { label: "Configure AI workforce", href: "/workforce" },
    { label: "Connect integration", href: "/business/integrations" },
    { label: "Create workflow", href: "/business/workflows" },
    ...(ops?.module.metrics.slice(0, 2).filter((m) => m.route).map((m) => ({ label: `Open ${m.label.toLowerCase()}`, href: m.route as string })) ?? []),
  ];

  return (
    <AppShell user={user}>
      <Page>
        <StageTracker stages={overview.stages} activeKey={operating ? "operate" : "launch"} />
        {error && <div className="mb-6"><Alert variant="danger">{error}</Alert></div>}
        {opsError && (
          <div className="mb-6">
            <Alert variant="warning" action={<Button size="sm" variant="secondary" onClick={() => reloadOps()}>Try again</Button>}>{opsError}</Alert>
          </div>
        )}

        <Hero
          eyebrow={
            <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
              <span>{business.industry ?? "Your business"}</span>
              <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${operating ? "bg-success/10 text-success" : "bg-surface-muted text-muted"}`}>
                <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${operating ? "bg-success" : "bg-muted-foreground"}`} />
                {statusText}
              </span>
              <span className="text-muted-foreground">Stage: <span className="font-medium text-foreground">{stage}</span></span>
            </span>
          }
          title={business.name ?? business.summary ?? "Your business"}
          lede={business.summary && business.summary !== business.name ? business.summary : undefined}
          actions={
            <>
              {inProgress && <Link href="/business" className="klaros-btn-primary">Continue setup</Link>}
              <Link href="/business/map" className="klaros-btn-secondary">Business map</Link>
              <Link href="/business/blueprint" className="klaros-btn-secondary">Blueprint</Link>
            </>
          }
        />

        {/* UP NEXT */}
        <ConsoleSection id="action-center" title="Action center" hint="What needs a person, most urgent first.">
          <ActionCenter items={model.actions} loading={!ops} />
        </ConsoleSection>

        {/* HEALTH */}
        <ConsoleSection id="health" title="Business health" hint="Where your business stands right now.">
          <HealthStrip tiles={model.health} />
        </ConsoleSection>

        <div className="grid gap-x-10 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
          {/* MAIN COLUMN */}
          <div className="min-w-0">
            <ConsoleSection id="leads" title="Leads to move forward" hint="Each with its next step." action={<Link href="/leads" className="text-[15px] text-accent hover:underline">All leads &rsaquo;</Link>}>
              <LeadsToAct token={token} />
            </ConsoleSection>

            {ops && (
              <ConsoleSection id="pipeline" title="From website to customer" hint="How an enquiry moves through your business, and which parts are set up." action={<Link href="/business/workflows" className="text-[15px] text-accent hover:underline">Workflows &rsaquo;</Link>}>
                <div className="mb-4 rounded-3xl bg-surface-muted p-6">
                  <h3 className="mb-4 text-[15px] font-semibold text-foreground">Where leads are</h3>
                  <LeadFunnel leads={ops.leads} />
                </div>
                <LeadPipeline stages={ops.lead_pipeline} />
              </ConsoleSection>
            )}

            <ConsoleSection id="activity" title="Recent activity" hint="What has actually happened, newest first.">
              {ops ? <ActivityTimeline items={ops.activity} /> : <Skeleton stats={0} rows={3} />}
            </ConsoleSection>
          </div>

          {/* SIDE RAIL */}
          <aside className="min-w-0 lg:sticky lg:top-24 lg:self-start">
            <ConsoleSection id="launch" title="Launch readiness" hint={operating ? "Everything that makes this business complete." : "What is ready, and what is holding up launch."}>
              <LaunchPanel overview={overview} rows={model.launch} token={token} tenantId={user?.tenant_id} onLaunched={onLaunched} />
            </ConsoleSection>
            <ConsoleSection id="quick-actions" title="Quick actions">
              <QuickActions actions={quick} />
            </ConsoleSection>
            <ConsoleSection id="architecture" title="How your business operates" hint="The parts of your business and whether each is in place.">
              <ArchitectureStrip nodes={model.chain} />
            </ConsoleSection>
          </aside>
        </div>
      </Page>
    </AppShell>
  );
}
