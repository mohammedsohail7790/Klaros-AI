"use client";

/**
 * AI Workforce — the Klaros-side view of the external AI workforce platform
 * (Halla AI). Klaros does not contain the workforce; this page only reports
 * what the integration boundary says. "Connected" appears only when the
 * adapter itself reports CONNECTED, and no connect/deploy control is shown
 * while no adapter exists — a button that does nothing would be a lie.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Bot,
  CalendarCheck,
  ExternalLink,
  LifeBuoy,
  Megaphone,
  Phone,
  PhoneIncoming,
  PhoneOutgoing,
  UserCheck,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, WorkforceStatus, getBuilderOverview, getWorkforceStatus } from "@/lib/api";
import { StatusPill } from "@/components/business/StatusPill";
import Link from "next/link";
import { useOperations } from "@/components/business/useOperations";
import { actionLabel, agentStatusLabel, autonomyLabel, when } from "@/lib/opsLabels";
import { BuilderContextBar } from "@/components/business/BuilderContextBar";

const ICONS: Record<string, typeof Phone> = {
  voice: Phone,
  incoming_calls: PhoneIncoming,
  outgoing_calls: PhoneOutgoing,
  lead_qualification: UserCheck,
  appointment_booking: CalendarCheck,
  customer_support: LifeBuoy,
  outbound_communication: Megaphone,
};

export default function WorkforcePage() {
  const { token, user } = useAuth();
  const [status, setStatus] = useState<WorkforceStatus | null>(null);
  const [needs, setNeeds] = useState<string[]>([]);
  const { ops } = useOperations(token);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      setStatus(await getWorkforceStatus(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't check your AI workforce just now. Please try again.");
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

  const connected = status?.status === "CONNECTED";
  const pill = !status ? null : connected ? "CONNECTED" : status.status === "CONFIGURATION_REQUIRED" ? "CONFIGURATION_REQUIRED" : status.adapter_implemented ? "NOT_CONNECTED" : "INTEGRATION_REQUIRED";

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-3xl px-6 py-8">
        <BuilderContextBar token={token} activeKey="workforce" />
        <PageHeader
          icon={Bot}
          title="AI workforce"
          description="The team that talks to your customers — by phone and message — while Klaros runs the business behind them."
        />
        {error && (
          <div className="mb-4">
            <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={load}>Retry</Button>}>{error}</Alert>
          </div>
        )}
        {!status && !error && <Skeleton rows={4} />}
        {status && (
          <>
            <section aria-labelledby="wf-state" className="klaros-card mb-6 p-5">
              <h2 id="wf-state" className="klaros-label mb-2">Current state</h2>
              <div className="flex flex-wrap items-center gap-3">
                <p className="text-base font-semibold capitalize text-foreground">{status.provider} AI</p>
                {pill && <StatusPill state={pill} />}
                <span className="rounded-full border border-border px-2 py-0.5 text-xs text-muted">External platform</span>
              </div>
              <p className="mt-2 text-sm text-foreground">
                {connected ? "Your AI workforce is connected." : "Connect Halla to enable AI workforce capabilities."}
              </p>
              <p className="mt-1 text-sm text-muted">{status.message}</p>

              {!status.adapter_implemented && (
                <div className="mt-4 rounded-lg border border-dashed border-border-strong bg-surface-muted p-4 text-sm">
                  <p className="font-medium text-foreground">Integration required</p>
                  <p className="mt-1 text-muted">
                    Halla AI is a separate product with its own voice and calling stack. Klaros has defined how the two will connect, but the
                    connection itself isn't built yet — so there is nothing to sign in to or configure here today. Everything below describes
                    what the workforce will do once it is connected.
                  </p>
                </div>
              )}
              <a href="https://hallaai.com" target="_blank" rel="noopener noreferrer" className="klaros-btn-secondary mt-4 inline-flex items-center gap-1.5 text-sm">
                About Halla AI <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            </section>

            {needs.length > 0 && (
              <section aria-labelledby="wf-needs" className="mb-6">
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

            <section aria-labelledby="wf-does">
              <h2 id="wf-does" className="klaros-label mb-3">What an AI workforce does</h2>
              <ul className="grid gap-3 sm:grid-cols-2">
                {status.capabilities.map((c) => {
                  const Icon = ICONS[c.key] ?? Bot;
                  return (
                    <li key={c.key} className="klaros-card flex gap-3 p-4">
                      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-accent" aria-hidden="true" />
                      <div>
                        <p className="text-sm font-medium text-foreground">{c.label}</p>
                        <p className="text-xs text-muted">{c.description}</p>
                        <StatusPill state={connected ? "CONNECTED" : status.adapter_implemented ? "NOT_CONNECTED" : "INTEGRATION_REQUIRED"} className="mt-1.5" />
                      </div>
                    </li>
                  );
                })}
              </ul>
            </section>
          </>
        )}
      </div>
    </AppShell>
  );
}
