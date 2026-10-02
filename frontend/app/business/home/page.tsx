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

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { Skeleton } from "@/components/ui/Skeleton";
import { AlertTriangle, Bot, Globe, Plug, Users, Workflow } from "lucide-react";
import { StageTracker } from "@/components/business/StageTracker";
import { StatusPill } from "@/components/business/StatusPill";
import { WebsiteActions } from "@/components/business/WebsiteActions";
import { useBuilderOverview } from "@/components/business/useBuilderOverview";
import { useOperations } from "@/components/business/useOperations";
import { ActivityFeed, ConsoleSection, LeadFunnel, LeadPipeline } from "@/components/business/console";
import { sourceLabel, when } from "@/lib/opsLabels";

function Stat({ label, value, hint }: { label: string; value: number | string; hint?: string }) {
  return (
    <div className="klaros-card p-4">
      <p className="klaros-label">{label}</p>
      <p className="mt-1 font-display text-3xl text-foreground">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
    </div>
  );
}

export default function BusinessHomePage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const { overview, error, reload } = useBuilderOverview(token);
  const { ops, error: opsError, reload: reloadOps } = useOperations(token);

  useEffect(() => {
    if (overview && !overview.journey) router.replace("/business");
  }, [overview, router]);

  if (!overview || !overview.journey) {
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

  const { business, launch, website, workforce, progress, next_actions } = overview;
  const operating = launch.launched;
  const inProgress = overview.journey.status !== "COMPLETED";
  const statusText = launch.launched
    ? launch.ready
      ? "Live — website published"
      : "Website published — launch checklist incomplete"
    : inProgress
      ? "Setting up"
      : "Set up — not launched yet";
  const topActions = next_actions.filter((a) => a.state !== "CONNECTED").slice(0, 6);
  const connected = new Set(overview.requirements.flatMap((r) => r.providers.filter((p) => p.state === "CONNECTED").map((p) => p.display_name)));
  const workforceWanted = overview.requirements.some((r) => r.workforce_addressable);
  const outstanding = launch.items.filter((i) => i.required && !i.done);

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-6xl px-6 py-8">
        <StageTracker stages={overview.stages} activeKey={operating ? "operate" : "launch"} />
        {error && <div className="mb-4"><Alert variant="danger">{error}</Alert></div>}

        {/* BUSINESS */}
        <header className="klaros-card mb-8 p-5 sm:p-6">
          <p className="klaros-label">{business.industry ?? "Your business"}</p>
          <h1 className="font-display text-3xl text-foreground">{business.name ?? business.summary ?? "Your business"}</h1>
          <p className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted">
            <span className="inline-flex items-center rounded-full border border-border px-2.5 py-0.5 text-xs font-medium text-foreground">{statusText}</span>
            <span className="font-medium text-foreground">{operating ? "Operate your business" : "Prepare your business"}</span>
            {inProgress && <Link href="/business" className="underline underline-offset-2">Continue setup</Link>}
          </p>
          <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-3">
            {business.summary && <div><dt className="klaros-label">What it is</dt><dd className="text-foreground">{business.summary}</dd></div>}
            {business.customers && <div><dt className="klaros-label">Customers</dt><dd className="text-foreground">{business.customers}</dd></div>}
            {business.business_model && <div><dt className="klaros-label">How it earns</dt><dd className="text-foreground">{business.business_model}</dd></div>}
          </dl>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link href="/business/map" className="klaros-btn-secondary text-sm">Business map</Link>
            <Link href="/business/blueprint" className="klaros-btn-secondary text-sm">Blueprint</Link>
            <Link href="/business/requirements" className="klaros-btn-secondary text-sm">Requirements</Link>
          </div>
        </header>

        {opsError && (
          <div className="mb-6">
            <Alert variant="warning" action={<Button size="sm" variant="secondary" onClick={() => reloadOps()}>Try again</Button>}>{opsError}</Alert>
          </div>
        )}

        {operating ? (
          <>
            {/* ATTENTION */}
            <ConsoleSection id="attention" title="Needs your attention" hint="What is waiting on you right now.">
              {!ops ? (
                <Skeleton stats={0} rows={2} />
              ) : ops.attention.length === 0 ? (
                <p className="klaros-card flex items-center gap-2 p-4 text-sm text-muted">Nothing is waiting on you right now.</p>
              ) : (
                <ul className="space-y-2">
                  {ops.attention.map((a) => (
                    <li key={a.id} className="klaros-card flex items-center justify-between gap-3 border-warning/30 p-4">
                      <span className="flex items-center gap-2 text-sm text-foreground"><AlertTriangle className="h-4 w-4 text-warning" aria-hidden="true" />{a.text}</span>
                      <Link href={a.route} className="klaros-btn-secondary shrink-0 text-sm">Open<span className="sr-only"> — {a.text}</span></Link>
                    </li>
                  ))}
                </ul>
              )}
            </ConsoleSection>

            {/* LEADS */}
            <ConsoleSection id="leads" title="Leads" hint="Everyone who has enquired — the first thing your business operates on." action={<Link href="/leads" className="klaros-btn-secondary text-sm">All leads</Link>}>
              {!ops ? (
                <Skeleton stats={4} rows={2} />
              ) : (
                <>
                  <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
                    <Stat label="Total leads" value={ops.leads.total} />
                    <Stat label="New this week" value={ops.leads.new_7d} />
                    <Stat label="Qualified or beyond" value={ops.leads.qualified} />
                    <Stat label="From your website" value={ops.leads.website_enquiries} />
                  </div>
                  <div className="grid gap-4 lg:grid-cols-2">
                    <div className="klaros-card p-4">
                      <h3 className="mb-3 text-sm font-semibold text-foreground">Where they are</h3>
                      <LeadFunnel leads={ops.leads} />
                    </div>
                    <div className="klaros-card p-4">
                      <h3 className="mb-3 text-sm font-semibold text-foreground">Waiting for a first response</h3>
                      {ops.leads.waiting.length === 0 ? (
                        <p className="text-sm text-muted">No new leads are waiting.</p>
                      ) : (
                        <ul className="divide-y divide-border">
                          {ops.leads.waiting.map((l) => (
                            <li key={l.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                              <Link href={`/leads/${l.id}`} className="font-medium text-foreground hover:underline">{l.name}</Link>
                              <span className="shrink-0 text-xs text-muted-foreground">{sourceLabel(l.source)} · {when(l.created_at)}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  </div>
                </>
              )}
            </ConsoleSection>

            {/* HOW A LEAD IS HANDLED */}
            <ConsoleSection id="pipeline" title="From website to customer" hint="How an enquiry moves through your business today — and which parts are set up." action={<Link href="/business/workflows" className="klaros-btn-secondary text-sm">Workflows</Link>}>
              {ops ? <LeadPipeline stages={ops.lead_pipeline} /> : <Skeleton stats={0} rows={4} />}
            </ConsoleSection>

            {/* MODULE METRICS */}
            {ops && ops.module.metrics.length > 0 && (
              <ConsoleSection id="operations" title="Operations" hint="Activity specific to your kind of business." action={<Link href="/business/analytics" className="klaros-btn-secondary text-sm">Analytics</Link>}>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  {ops.module.metrics.map((m) => (
                    <Link key={m.key} href={m.route ?? "/business/analytics"} className="klaros-card klaros-card-interactive block p-4">
                      <p className="klaros-label">{m.label}</p>
                      <p className="mt-1 font-display text-3xl text-foreground">{m.value ?? 0}</p>
                    </Link>
                  ))}
                </div>
              </ConsoleSection>
            )}
          </>
        ) : (
          <ConsoleSection id="launch" title="Launch progress" hint="What is ready, and exactly what is holding up launch.">
            <div className="grid gap-4 lg:grid-cols-2">
              <ol className="klaros-card divide-y divide-border" aria-label="Launch progress">
                {progress.map((p) => (
                  <li key={p.key} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                    <div className="min-w-0">
                      {p.route ? <Link href={p.route} className="font-medium text-foreground hover:underline">{p.label}</Link> : <span className="font-medium text-foreground">{p.label}</span>}
                      <p className="text-xs text-muted">{p.detail}</p>
                    </div>
                    <StatusPill state={p.status} />
                  </li>
                ))}
              </ol>
              <div className="klaros-card p-4" id="readiness">
                <h3 className="flex items-center justify-between gap-2 text-sm font-semibold text-foreground">Launch readiness <StatusPill state={launch.verdict} /></h3>
                <ul className="mt-3 space-y-3">
                  {launch.items.map((i) => (
                    <li key={i.key} className="text-sm">
                      <div className="flex items-start justify-between gap-2">
                        <span className="font-medium text-foreground">{i.label}{!i.required && <span className="ml-1.5 text-xs font-normal text-muted-foreground">(doesn't block launch)</span>}</span>
                        <StatusPill state={i.status} />
                      </div>
                      <p className="mt-0.5 text-xs text-muted">{i.detail}</p>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            <div className="mt-4 klaros-card p-4 text-sm text-muted">
              Publishing your website is what takes the business live — from then on, enquiries become leads you can operate on right here.
            </div>
          </ConsoleSection>
        )}

        {/* NEXT ACTIONS */}
        <ConsoleSection id="next" title="Next actions" hint="Generated from the current state of your business.">
          {topActions.length === 0 ? (
            <p className="text-sm text-muted">You're all caught up.</p>
          ) : (
            <ul className="grid gap-3 md:grid-cols-2">
              {topActions.map((a) => (
                <li key={a.id} className="klaros-card flex items-start justify-between gap-3 p-4">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-foreground">{a.title}</p>
                    <p className="mt-0.5 text-xs text-muted">{a.detail}</p>
                  </div>
                  {a.route ? (
                    <Link href={a.route} className="klaros-btn-secondary shrink-0 text-sm">Open<span className="sr-only"> {a.title}</span></Link>
                  ) : a.kind === "enable_vertical" ? (
                    <Link href="/business/requirements" className="klaros-btn-secondary shrink-0 text-sm">Review<span className="sr-only"> {a.title}</span></Link>
                  ) : (
                    <StatusPill state={a.state} />
                  )}
                </li>
              ))}
            </ul>
          )}
          {operating && outstanding.length > 0 && (
            <details className="mt-4 text-sm">
              <summary className="cursor-pointer text-muted hover:text-foreground">Still to finish for a complete launch ({outstanding.length})</summary>
              <ul className="mt-2 space-y-1.5">
                {outstanding.map((i) => (
                  <li key={i.key} className="flex items-start gap-2"><StatusPill state={i.status} /><span className="text-muted"><span className="font-medium text-foreground">{i.label}:</span> {i.detail}</span></li>
                ))}
              </ul>
            </details>
          )}
        </ConsoleSection>

        {/* THE REST OF THE BUSINESS */}
        <ConsoleSection id="business" title="Your business at a glance" hint="Website, AI workforce, integrations, data and automation.">
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            <article className="klaros-card flex flex-col gap-2 p-5">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground"><Globe className="h-4 w-4 text-accent" aria-hidden="true" />Website</h3>
              <StatusPill state={website.published ? "READY" : website.exists ? "CONFIGURATION_REQUIRED" : "NOT_READY"} />
              <p className="text-sm text-muted">
                {website.published ? "Published and taking enquiries." : website.exists ? "A draft exists — it isn't live yet." : "Not generated yet."}
                {website.pages.length > 0 && <> Pages: {website.pages.join(", ")}.</>}
              </p>
              <div className="mt-auto pt-2"><WebsiteActions website={website} tenantId={user?.tenant_id} /></div>
            </article>

            {workforceWanted && (
              <article className="klaros-card flex flex-col gap-2 p-5">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground"><Bot className="h-4 w-4 text-accent" aria-hidden="true" />AI workforce</h3>
                <StatusPill state={workforce.status === "CONNECTED" ? "CONNECTED" : workforce.adapter_implemented ? "NOT_CONNECTED" : "INTEGRATION_REQUIRED"} />
                <p className="text-sm text-muted">{workforce.message}</p>
                {ops && ops.agents.length > 0 && <p className="text-sm text-muted">{ops.agents.length} agent{ops.agents.length === 1 ? "" : "s"} configured inside Klaros.</p>}
                <Link href="/workforce" className="klaros-btn-secondary mt-auto self-start text-sm">View details</Link>
              </article>
            )}

            <article className="klaros-card flex flex-col gap-2 p-5">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground"><Plug className="h-4 w-4 text-accent" aria-hidden="true" />Integrations</h3>
              <p className="text-sm text-muted">{connected.size > 0 ? `${connected.size} connected: ${[...connected].join(", ")}.` : "0 connected."}</p>
              {ops && <p className="text-xs text-muted">{ops.integrations.filter((i) => i.state === "AVAILABLE").length} available to connect · {ops.integrations.filter((i) => i.state === "PLANNED" || i.state === "INTEGRATION_REQUIRED").length} not yet possible</p>}
              <Link href="/business/integrations" className="klaros-btn-secondary mt-auto self-start text-sm">Integration center</Link>
            </article>

            <article className="klaros-card flex flex-col gap-2 p-5">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground"><Users className="h-4 w-4 text-accent" aria-hidden="true" />Data</h3>
              {ops ? (
                <ul className="text-sm text-muted">
                  <li>Leads: <span className="text-foreground">{ops.leads.total}</span></li>
                  <li>Customers: <span className="text-foreground">{ops.customers}</span></li>
                  {ops.module.data.slice(0, 3).map((d) => <li key={d.key}>{d.label}: <span className="text-foreground">{d.count ?? 0}</span></li>)}
                </ul>
              ) : <p className="text-sm text-muted">Loading…</p>}
              <Link href="/business/data" className="klaros-btn-secondary mt-auto self-start text-sm">Data center</Link>
            </article>

            <article className="klaros-card flex flex-col gap-2 p-5">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground"><Workflow className="h-4 w-4 text-accent" aria-hidden="true" />Automation</h3>
              {ops ? (
                ops.workflows.length === 0 ? (
                  <p className="text-sm text-muted">No workflows yet — add one that alerts your team to each new lead.</p>
                ) : (
                  <p className="text-sm text-muted">{ops.workflows.filter((w) => w.status === "ENABLED").length} running of {ops.workflows.length}.</p>
                )
              ) : <p className="text-sm text-muted">Loading…</p>}
              <Link href="/business/workflows" className="klaros-btn-secondary mt-auto self-start text-sm">Workflows</Link>
            </article>

            <article className="klaros-card flex flex-col gap-2 p-5">
              <h3 className="text-sm font-semibold text-foreground">Analytics &amp; settings</h3>
              <p className="text-sm text-muted">Lead activity from real records, plus team, billing and account setup.</p>
              <div className="mt-auto flex flex-wrap gap-2 pt-2">
                <Link href="/business/analytics" className="klaros-btn-secondary text-sm">Analytics</Link>
                <Link href="/settings/team" className="klaros-btn-secondary text-sm">Settings</Link>
                <Link href="/onboarding" className="klaros-btn-secondary text-sm">Account setup</Link>
              </div>
            </article>
          </div>
        </ConsoleSection>

        {/* ACTIVITY */}
        <ConsoleSection id="activity" title="Recent activity" hint="What has actually happened, newest first.">
          {ops ? <ActivityFeed items={ops.activity} /> : <Skeleton stats={0} rows={3} />}
        </ConsoleSection>
      </div>
    </AppShell>
  );
}
