"use client";

/**
 * AI Workforce — the Klaros side of the AI workforce platform (Halla AI).
 *
 * Halla is a SEPARATE platform that provides the interaction layer: voice, calls,
 * conversations, qualification, booking, support. Klaros supplies the business context and
 * rules, and records what comes back. This page reports exactly what the integration boundary
 * says — "Connected" appears only when the adapter itself reports it — and shows no control
 * that cannot do anything: a button that does nothing would be a lie.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Bot, CalendarClock, Check, CircleDashed, ExternalLink, Lock, MessageSquareReply, PhoneCall } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, WorkforceSetup, getBuilderOverview, getWorkforceSetup } from "@/lib/api";
import { StatusPill } from "@/components/business/StatusPill";
import { useOperations } from "@/components/business/useOperations";
import { actionLabel, agentStatusLabel, autonomyLabel, when } from "@/lib/opsLabels";
import { BuilderContextBar } from "@/components/business/BuilderContextBar";
import { HallaConnectionPanel } from "@/components/business/HallaConnection";
import { workforceLabel } from "@/components/business/homeModel";
import { cn } from "@/lib/cn";

const MEMBER_ICON: Record<string, typeof Bot> = { receptionist: PhoneCall, followup: MessageSquareReply, scheduling: CalendarClock };

function List({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return <p className="text-sm text-muted">{empty}</p>;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {items.map((x) => (
        <li key={x} className="rounded-full border border-border bg-surface px-2.5 py-0.5 text-xs text-foreground">{x}</li>
      ))}
    </ul>
  );
}

export default function WorkforcePage() {
  const { token, user } = useAuth();
  const [setup, setSetup] = useState<WorkforceSetup | null>(null);
  const [needs, setNeeds] = useState<string[]>([]);
  const { ops } = useOperations(token);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      setSetup(await getWorkforceSetup(token));
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 403
          ? "You don't have permission to view the AI workforce."
          : err instanceof ApiError
            ? err.message
            : "We couldn't check your AI workforce just now. Please try again."
      );
      return;
    }
    // Which of this business's own requirements an AI workforce could serve (optional context).
    try {
      const o = await getBuilderOverview(token);
      setNeeds(o.requirements.filter((r) => r.workforce_addressable).map((r) => r.label));
    } catch {
      setNeeds([]);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const status = setup?.status;
  const connected = status?.status === "CONNECTED";
  const wl = status ? workforceLabel({ ...status, capabilities: [] } as Parameters<typeof workforceLabel>[0]) : null;
  const pill = wl?.state ?? null;

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-8">
        <BuilderContextBar token={token} activeKey="workforce" />
        <PageHeader icon={Bot} title="AI Workforce" description="Your AI team handles customer conversations and operational tasks." />
        {error && (
          <div className="mb-4">
            <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={load}>Retry</Button>}>{error}</Alert>
          </div>
        )}
        {!setup && !error && <Skeleton rows={4} />}
        {setup && status && (
          <>
            {/* WHO DOES WHAT */}
            <section aria-label="Klaros and Halla" className="mb-6 grid gap-3 sm:grid-cols-2">
              <div className="klaros-card p-4">
                <p className="klaros-label">Klaros — the business</p>
                <p className="mt-1 text-sm text-foreground">Knows your business, customers, leads and rules. Decides what happens next and records everything.</p>
              </div>
              <div className="klaros-card p-4">
                <p className="klaros-label">Halla AI — the interaction layer</p>
                <p className="mt-1 text-sm text-foreground">A separate platform that speaks with customers: voice, calls, conversations, qualification, booking and support.</p>
              </div>
            </section>

            {/* WORKFORCE STATUS */}
            <section aria-labelledby="wf-state" className="mb-8">
              <h2 id="wf-state" className="klaros-label mb-2">Workforce status</h2>
              <div className="klaros-card p-5">
                <div className="flex flex-wrap items-center gap-3">
                  <p className="text-base font-semibold capitalize text-foreground">{status.provider} AI</p>
                  {pill && <StatusPill state={pill} />}
                  <span className="rounded-full border border-border px-2 py-0.5 text-xs text-muted">External platform</span>
                  {status.mode === "development" && <span className="rounded-full border border-dashed border-border-strong px-2 py-0.5 text-xs text-muted">Development simulator — not live</span>}
                </div>
                <p className="mt-2 text-sm text-foreground">{connected ? "Your AI workforce is connected." : "Connect Halla to enable AI workforce capabilities."}</p>
                <p className="mt-1 text-sm text-muted">{status.message}</p>
                {!status.adapter_implemented && (
                  <div className="mt-4 rounded-lg border border-dashed border-border-strong bg-surface-muted p-4 text-sm">
                    <p className="font-medium text-foreground">Integration required</p>
                    <p className="mt-1 text-muted">
                      Halla AI is a separate product with its own voice and calling stack. Klaros has defined how the two will connect, but the
                      connection itself isn&apos;t built yet — so there is nothing to sign in to or configure here today.
                    </p>
                  </div>
                )}
                <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-3">
                  <div><dt className="klaros-label">Halla connection</dt><dd className="text-foreground">{wl?.label}</dd></div>
                  <div><dt className="klaros-label">Agent</dt><dd className="text-foreground">{status.agent_id ? "Deployed" : "None deployed"}</dd></div>
                  <div><dt className="klaros-label">Channels</dt><dd className="text-foreground">{setup.channels.length ? setup.channels.join(", ") : "None configured"}</dd></div>
                </dl>
                {ops && (
                  <p className="mt-4 text-sm text-muted">
                    {ops.ai.interactions === 0
                      ? "No AI conversations have been recorded."
                      : `${ops.ai.interactions} conversation${ops.ai.interactions === 1 ? "" : "s"} recorded · ${ops.ai.qualified} qualified · ${ops.ai.escalated} escalated to a person.`}
                  </p>
                )}
                <a href="https://hallaai.com" target="_blank" rel="noopener noreferrer" className="klaros-btn-secondary mt-4 inline-flex items-center gap-1.5 text-sm">
                  About Halla AI <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                  <span className="sr-only"> (opens in a new tab)</span>
                </a>
              </div>
            </section>

            {/* CONNECT (only where the real Halla adapter is enabled) */}
            <HallaConnectionPanel token={token} setup={setup} onChanged={load} />

            {/* WORKFORCE MEMBERS */}
            <section aria-labelledby="wf-members" className="mb-8">
              <h2 id="wf-members" className="klaros-label mb-1">Workforce members</h2>
              <p className="mb-3 text-xs text-muted">What each AI team member does once Halla is connected and configured. None is active until then.</p>
              <ul className="grid gap-3 md:grid-cols-3">
                {setup.members.map((m) => {
                  const Icon = MEMBER_ICON[m.key] ?? Bot;
                  return (
                    <li key={m.key} className="klaros-card flex flex-col gap-2 p-4">
                      <div className="flex items-start justify-between gap-2">
                        <Icon className="h-5 w-5 text-accent" aria-hidden="true" />
                        <span className={cn("rounded-full border px-2 py-0.5 text-[11px] font-medium", m.status === "ACTIVE" ? "border-success/25 bg-success/[0.08] text-success" : "border-dashed border-border-strong text-muted")}>{m.status_label}</span>
                      </div>
                      <p className="text-sm font-semibold text-foreground">{m.name}</p>
                      <p className="text-xs text-muted">{m.purpose}</p>
                      <p className="mt-auto text-xs text-muted-foreground">{m.capabilities.join(" · ")}</p>
                    </li>
                  );
                })}
              </ul>
            </section>

            {/* SETUP */}
            <section aria-labelledby="wf-setup" className="mb-8">
              <h2 id="wf-setup" className="klaros-label mb-1">Set up your AI workforce</h2>
              <p className="mb-3 text-xs text-muted">Klaros prepares the business side now. The steps that need Halla stay locked until it is connected.</p>
              <ol className="klaros-card divide-y divide-border">
                {setup.steps.map((st, i) => (
                  <li key={st.key} className="flex items-start gap-3 px-4 py-3">
                    <span className={cn("mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-medium", st.state === "DONE" ? "bg-success/10 text-success" : st.state === "READY" ? "bg-accent-soft text-accent-hover" : "bg-surface-muted text-muted")} aria-hidden="true">
                      {st.state === "DONE" ? <Check className="h-3.5 w-3.5" /> : st.state === "BLOCKED" ? <Lock className="h-3 w-3" /> : i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-foreground">{st.label}</p>
                      <p className="text-xs text-muted">{st.detail}</p>
                    </div>
                    <span className="shrink-0 text-xs text-muted-foreground">{st.state === "DONE" ? "Done" : st.state === "READY" ? "Prepared" : "Locked"}</span>
                  </li>
                ))}
              </ol>

              <div className="mt-4 space-y-2">
                <details className="klaros-card p-4" open>
                  <summary className="cursor-pointer text-sm font-medium text-foreground">Business context Klaros will send to Halla</summary>
                  <dl className="mt-3 grid gap-4 text-sm sm:grid-cols-2">
                    <div><dt className="klaros-label">Business</dt><dd className="text-foreground">{setup.context.business_name ?? "—"}{setup.context.industry ? ` · ${setup.context.industry}` : ""}</dd></div>
                    <div><dt className="klaros-label">Customers</dt><dd className="text-foreground">{setup.context.customers ?? "—"}</dd></div>
                    <div className="sm:col-span-2"><dt className="klaros-label mb-1">Services</dt><dd><List items={setup.context.services} empty="No services configured yet." /></dd></div>
                    <div className="sm:col-span-2"><dt className="klaros-label mb-1">Markets</dt><dd><List items={setup.context.markets} empty="No markets configured yet." /></dd></div>
                  </dl>
                </details>
                <details className="klaros-card p-4">
                  <summary className="cursor-pointer text-sm font-medium text-foreground">Qualification rules</summary>
                  <p className="mb-2 mt-3 text-xs text-muted">What Halla would ask every customer before handing the lead to Klaros.</p>
                  <List items={setup.context.qualification_fields} empty="None." />
                </details>
                <details className="klaros-card p-4">
                  <summary className="cursor-pointer text-sm font-medium text-foreground">Escalation rules</summary>
                  <p className="mb-2 mt-3 text-xs text-muted">Situations where Halla hands the conversation to a person.</p>
                  <List items={setup.context.escalation_triggers} empty="None." />
                </details>
                <details className="klaros-card p-4">
                  <summary className="cursor-pointer text-sm font-medium text-foreground">Booking rules</summary>
                  <p className="mb-2 mt-3 text-xs text-muted">How appointments must be made.</p>
                  <List items={setup.context.booking_rules} empty="None." />
                </details>
              </div>
            </section>

            {needs.length > 0 && (
              <section aria-labelledby="wf-needs" className="mb-8">
                <h2 id="wf-needs" className="klaros-label mb-2">Parts of your business it would serve</h2>
                <ul className="flex flex-wrap gap-2">
                  {needs.map((n) => (
                    <li key={n} className="rounded-full border border-border bg-surface px-3 py-1 text-sm text-foreground">{n}</li>
                  ))}
                </ul>
              </section>
            )}

            <section aria-labelledby="wf-agents" className="mb-8">
              <h2 id="wf-agents" className="klaros-label mb-1">Agents working inside Klaros</h2>
              <p className="mb-3 text-xs text-muted">These are Klaros's own agents — separate from Halla. Each can only use the tools it has been given, and every action is logged.</p>
              {!ops ? (
                <p className="text-sm text-muted">Loading agents…</p>
              ) : ops.agents.length === 0 ? (
                <p className="klaros-card flex flex-wrap items-center justify-between gap-2 border-dashed p-4 text-sm text-muted">
                  No agents are configured yet.
                  <Link href="/agents/new" className="klaros-btn-secondary text-sm">Create an agent</Link>
                </p>
              ) : (
                <ul className="space-y-3">
                  {ops.agents.map((a) => (
                    <li key={a.id} className="klaros-card p-4">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <Link href={`/agents/${a.id}`} className="text-sm font-semibold text-foreground hover:underline">{a.name}</Link>
                        <StatusPill state={a.status === "ACTIVE" ? "READY" : "CONFIGURATION_REQUIRED"} />
                      </div>
                      <p className="mt-0.5 text-xs text-muted">{a.purpose || "No purpose written yet."}</p>
                      <dl className="mt-3 grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
                        <div><dt className="klaros-label">Status</dt><dd className="text-foreground">{agentStatusLabel(a.status)}</dd></div>
                        <div><dt className="klaros-label">How it acts</dt><dd className="text-foreground">{autonomyLabel(a.autonomy_tier)}</dd></div>
                        <div className="sm:col-span-2"><dt className="klaros-label">Can use</dt><dd className="text-foreground">{a.tools.length ? a.tools.map(actionLabel).join(" · ") : "No tools granted — it cannot act."}</dd></div>
                        <div className="sm:col-span-2"><dt className="klaros-label">History</dt><dd className="text-foreground">{a.executions === 0 ? "Hasn't run yet." : `${a.executions} run${a.executions === 1 ? "" : "s"}${a.last_execution ? ` · last ${a.last_execution.status.toLowerCase()} ${when(a.last_execution.at)}` : ""}`}</dd></div>
                      </dl>
                    </li>
                  ))}
                </ul>
              )}
            </section>

          </>
        )}
      </div>
    </AppShell>
  );
}
