"use client";

import Link from "next/link";
import { ArrowDown, CircleDot, Cog, Hand, Sparkles, Zap } from "lucide-react";
import type { BusinessOperations, OpsPipelineStage, OpsWorkflow } from "@/lib/api";
import { LEAD_STATUS_ORDER, actionLabel, leadStatusLabel, sourceLabel, triggerLabel, when } from "@/lib/opsLabels";
import { StatusPill } from "./StatusPill";
import { WorkflowDetailToggle } from "./WorkflowDetail";

export function ConsoleSection({ id, title, hint, children, action }: { id: string; title: string; hint?: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-h`} className="mb-8 scroll-mt-20">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id={`${id}-h`} className="font-display text-xl text-foreground">{title}</h2>
          {hint && <p className="mt-0.5 text-sm text-muted">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

/** Lead counts by status — a horizontal bar per status, every number a real count. */
export function LeadFunnel({ leads }: { leads: BusinessOperations["leads"] }) {
  const rows = LEAD_STATUS_ORDER.filter((s) => (leads.by_status[s] ?? 0) > 0);
  const max = Math.max(1, ...rows.map((s) => leads.by_status[s]));
  if (leads.total === 0) return <p className="text-sm text-muted">No leads yet. They appear here as soon as someone submits your website form.</p>;
  return (
    <ul className="space-y-2" aria-label="Leads by status">
      {rows.map((s) => (
        <li key={s} className="grid grid-cols-[7rem_1fr_2rem] items-center gap-3 text-sm">
          <span className="text-muted">{leadStatusLabel(s)}</span>
          <span className="h-2.5 overflow-hidden rounded-full bg-surface-muted" aria-hidden="true">
            <span className="block h-full rounded-full bg-accent" style={{ width: `${(leads.by_status[s] / max) * 100}%` }} />
          </span>
          <span className="text-right font-medium text-foreground">{leads.by_status[s]}</span>
        </li>
      ))}
    </ul>
  );
}

export function SourceList({ leads }: { leads: BusinessOperations["leads"] }) {
  const rows = Object.entries(leads.by_source).sort((a, b) => b[1] - a[1]);
  if (rows.length === 0) return <p className="text-sm text-muted">No leads yet.</p>;
  return (
    <ul className="space-y-1 text-sm" aria-label="Leads by source">
      {rows.map(([s, n]) => (
        <li key={s} className="flex justify-between"><span className="text-muted">{sourceLabel(s)}</span><span className="font-medium text-foreground">{n}</span></li>
      ))}
    </ul>
  );
}

const KIND: Record<OpsPipelineStage["kind"], { label: string; icon: typeof Cog }> = {
  SYSTEM: { label: "Built in", icon: Cog },
  AUTOMATED: { label: "Automated", icon: Zap },
  ASSISTED: { label: "AI-assisted", icon: Sparkles },
  MANUAL: { label: "Your team", icon: Hand },
};

/** "Website → Lead → … " as it really works today: each stage says whether it is built in,
 * automated by a workflow, assisted by AI, or done by a person — and whether it's set up. */
export function LeadPipeline({ stages }: { stages: OpsPipelineStage[] }) {
  return (
    <ol className="klaros-card divide-y divide-border" aria-label="How a lead is handled">
      {stages.map((s, i) => {
        const K = KIND[s.kind];
        return (
          <li key={s.key} className="flex items-start gap-3 px-4 py-3">
            <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-muted text-xs font-medium text-muted" aria-hidden="true">{i + 1}</span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                {s.route ? <Link href={s.route} className="text-sm font-medium text-foreground hover:underline">{s.label}</Link> : <span className="text-sm font-medium text-foreground">{s.label}</span>}
                <span className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[11px] text-muted"><K.icon className="h-3 w-3" aria-hidden="true" />{K.label}</span>
                <StatusPill state={s.state} />
              </div>
              <p className="mt-0.5 text-xs text-muted">{s.detail}</p>
            </div>
            {i < stages.length - 1 && <ArrowDown className="mt-1 hidden h-4 w-4 shrink-0 text-border-strong sm:block" aria-hidden="true" />}
          </li>
        );
      })}
    </ol>
  );
}

function nextStep(w: OpsWorkflow): string {
  if (w.status !== "ENABLED") return "Enable it to start.";
  if (w.last_run?.status === "FAILED") return "Check why the last run failed.";
  return `Waits for its trigger — ${triggerLabel(w.trigger_event)}.`;
}

export function WorkflowList({ workflows, token = null }: { workflows: OpsWorkflow[]; token?: string | null }) {
  if (workflows.length === 0) return <p className="text-sm text-muted">No workflows yet.</p>;
  return (
    <ul className="space-y-3" aria-label="Workflows">
      {workflows.map((w) => {
        const failing = w.last_run?.status === "FAILED";
        return (
          <li key={w.id} className="klaros-card p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-semibold text-foreground">{w.name}</p>
              <div className="flex items-center gap-2">
                {failing && <span className="inline-flex items-center rounded-full border border-danger/25 bg-danger/[0.06] px-2 py-0.5 text-[11px] font-medium text-danger">Last run failed</span>}
                <StatusPill state={w.status === "ENABLED" ? "READY" : "CONFIGURATION_REQUIRED"} />
              </div>
            </div>
            {w.description && <p className="mt-0.5 text-xs text-muted">{w.description}</p>}
            <ol className="mt-3 flex flex-wrap items-center gap-2 text-xs" aria-label={`Steps of ${w.name}`}>
              <li className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2.5 py-1 text-accent-hover"><CircleDot className="h-3 w-3" aria-hidden="true" />{triggerLabel(w.trigger_event)}</li>
              {w.actions.map((a, i) => (
                <li key={a + i} className="inline-flex items-center gap-2"><span aria-hidden="true" className="text-muted-foreground">→</span><span className="rounded-full border border-border px-2.5 py-1 text-foreground">{actionLabel(a)}</span></li>
              ))}
            </ol>
            <p className="mt-3 text-xs text-muted">
              {w.status !== "ENABLED" ? "Not running — enable it to start." : w.runs === 0 ? "Hasn't run yet — it runs the next time its trigger happens." : `Ran ${w.runs} time${w.runs === 1 ? "" : "s"}${w.failed_runs ? `, ${w.failed_runs} failed` : ""}${w.last_run ? ` · last run ${when(w.last_run.at)} (${w.last_run.status.toLowerCase()})` : ""}.`}
            </p>
            <p className="mt-1 text-xs text-foreground"><span className="text-muted">Next:</span> {nextStep(w)}</p>
            {token && <WorkflowDetailToggle token={token} id={w.id} name={w.name} />}
          </li>
        );
      })}
    </ul>
  );
}

export function ActivityFeed({ items }: { items: BusinessOperations["activity"] }) {
  if (items.length === 0) return <p className="text-sm text-muted">Nothing has happened yet. Activity appears here as your business runs.</p>;
  return (
    <ul className="klaros-card divide-y divide-border" aria-label="Recent activity">
      {items.map((a, i) => (
        <li key={i} className="flex items-start justify-between gap-3 px-4 py-2.5 text-sm">
          {a.route ? <Link href={a.route} className="text-foreground hover:underline">{a.text}</Link> : <span className="text-foreground">{a.text}</span>}
          <span className="shrink-0 text-xs text-muted-foreground">{when(a.at)}</span>
        </li>
      ))}
    </ul>
  );
}
