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
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
        <StageTracker stages={overview.stages} activeKey={operating ? "operate" : "launch"} />
        {error && <div className="mb-4"><Alert variant="danger">{error}</Alert></div>}
        {opsError && (
          <div className="mb-4">
            <Alert variant="warning" action={<Button size="sm" variant="secondary" onClick={() => reloadOps()}>Try again</Button>}>{opsError}</Alert>
          </div>
        )}

        {/* BUSINESS OVERVIEW */}
        <header className="klaros-card mb-6 p-5 sm:p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="klaros-label">{business.industry ?? "Your business"}</p>
              <h1 className="font-display text-3xl text-foreground sm:text-4xl">{business.name ?? business.summary ?? "Your business"}</h1>
              <p className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted">
                <span className="inline-flex items-center rounded-full border border-border px-2.5 py-0.5 text-xs font-medium text-foreground">{statusText}</span>
                <span>Stage: <span className="font-medium text-foreground">{stage}</span></span>
                {inProgress && <Link href="/business" className="underline underline-offset-2">Continue setup</Link>}
              </p>
            </div>
            <div className="flex gap-2">
              <Link href="/business/map" className="klaros-btn-secondary text-sm">Business map</Link>
              <Link href="/business/blueprint" className="klaros-btn-secondary text-sm">Blueprint</Link>
            </div>
          </div>
          <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-3">
            {business.summary && <div><dt className="klaros-label">What it is</dt><dd className="text-foreground">{business.summary}</dd></div>}
            {business.customers && <div><dt className="klaros-label">Customers</dt><dd className="text-foreground">{business.customers}</dd></div>}
            {business.business_model && <div><dt className="klaros-label">How it earns</dt><dd className="text-foreground">{business.business_model}</dd></div>}
          </dl>
        </header>

        {/* BUSINESS HEALTH */}
        <ConsoleSection id="health" title="Business health" hint="Where your business stands right now.">
          <HealthStrip tiles={model.health} />
        </ConsoleSection>

        {/* LAUNCH (first while preparing) */}
        {!operating && (
          <ConsoleSection id="launch" title="Launch readiness" hint="What is ready, and exactly what is holding up launch.">
            <LaunchPanel overview={overview} rows={model.launch} token={token} tenantId={user?.tenant_id} onLaunched={onLaunched} />
          </ConsoleSection>
        )}

        {/* ACTION CENTER + QUICK ACTIONS */}
        <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <ConsoleSection id="action-center" title="Action center" hint="What needs a person, most urgent first.">
            <ActionCenter items={model.actions} loading={!ops} />
          </ConsoleSection>
          <ConsoleSection id="quick-actions" title="Quick actions">
            <QuickActions actions={quick} />
          </ConsoleSection>
        </div>

        {/* ARCHITECTURE */}
        <ConsoleSection id="architecture" title="How your business operates" hint="The parts of your business and whether each one is in place.">
          <ArchitectureStrip nodes={model.chain} />
        </ConsoleSection>

        {/* LEADS + ACTIVITY */}
        <div className="grid gap-6 lg:grid-cols-2">
          <ConsoleSection id="leads" title="Leads to move forward" hint="Each with its next step." action={<Link href="/leads" className="klaros-btn-secondary text-sm">All leads</Link>}>
            <LeadsToAct token={token} />
          </ConsoleSection>
          <ConsoleSection id="activity" title="Recent activity" hint="What has actually happened, newest first.">
            {ops ? <ActivityTimeline items={ops.activity} /> : <Skeleton stats={0} rows={3} />}
          </ConsoleSection>
        </div>

        {/* LEAD FUNNEL + PIPELINE */}
        {ops && (
          <ConsoleSection id="pipeline" title="From website to customer" hint="How an enquiry moves through your business — and which parts are set up." action={<Link href="/business/workflows" className="klaros-btn-secondary text-sm">Automation</Link>}>
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
              <div className="klaros-card p-4">
                <h3 className="mb-3 text-sm font-semibold text-foreground">Where leads are</h3>
                <LeadFunnel leads={ops.leads} />
              </div>
              <LeadPipeline stages={ops.lead_pipeline} />
            </div>
          </ConsoleSection>
        )}

        {/* LAUNCH (after the day-to-day once live) */}
        {operating && (
          <ConsoleSection id="launch" title="Launch readiness" hint="Everything that makes this business complete.">
            <LaunchPanel overview={overview} rows={model.launch} token={token} tenantId={user?.tenant_id} onLaunched={onLaunched} />
          </ConsoleSection>
        )}
      </div>
    </AppShell>
  );
}
