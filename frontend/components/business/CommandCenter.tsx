"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, ChevronRight, ExternalLink, Info, Rocket, XOctagon } from "lucide-react";
import {
  ApiError,
  BuilderOverview,
  BusinessOperations,
  LeadBoardRow,
  getLeadBoard,
  getMyWebsite,
  listWebsiteVersions,
  publishWebsiteVersion,
} from "@/lib/api";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/cn";
import { leadStatusLabel, sourceLabel, when } from "@/lib/opsLabels";
import { StatusPill } from "./StatusPill";
import { ActionItem, ChainNode, HealthTile, LaunchRow, Severity } from "./homeModel";

const SEVERITY: Record<Severity, { icon: typeof Info; tone: string; label: string }> = {
  danger: { icon: XOctagon, tone: "text-danger", label: "Needs attention" },
  warning: { icon: AlertTriangle, tone: "text-warning", label: "Waiting on you" },
  info: { icon: Info, tone: "text-muted-foreground", label: "Set-up step" },
};

/** One row per thing that needs a person, most urgent first. */
export function ActionCenter({ items, loading }: { items: ActionItem[]; loading?: boolean }) {
  if (loading) return <Skeleton stats={0} rows={3} />;
  if (items.length === 0) {
    return <p className="klaros-card p-5 text-sm text-muted">Nothing is waiting on you right now. New leads and issues appear here the moment they need a person.</p>;
  }
  return (
    <ul className="klaros-card divide-y divide-border" aria-label="Action center">
      {items.map((a) => {
        const S = SEVERITY[a.severity];
        return (
          <li key={a.id} className="flex items-center gap-3 px-4 py-3">
            <S.icon className={cn("h-4 w-4 shrink-0", S.tone)} aria-hidden="true" />
            <span className="sr-only">{S.label}: </span>
            <p className="min-w-0 flex-1 text-sm text-foreground">{a.text}</p>
            <Link href={a.route} className="klaros-btn-secondary shrink-0 !px-3 !py-1 text-xs">
              {a.cta}
              <span className="sr-only"> — {a.text}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

export function HealthStrip({ tiles }: { tiles: HealthTile[] }) {
  return (
    <ul className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7" aria-label="Business health">
      {tiles.map((t) => (
        <li key={t.key}>
          <Link href={t.route} className="klaros-card klaros-card-interactive block h-full p-3.5">
            <p className="klaros-label">{t.label}</p>
            <p className="mt-1 font-display text-xl leading-tight text-foreground">{t.value}</p>
            {t.hint && <p className="mt-0.5 text-xs text-muted">{t.hint}</p>}
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function QuickActions({ actions }: { actions: { label: string; href: string }[] }) {
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Quick actions">
      {actions.map((a) => (
        <li key={a.label}>
          <Link href={a.href} className="klaros-btn-secondary !px-3 !py-1.5 text-sm">
            {a.label}
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** The business as a chain of real, linked parts. */
export function ArchitectureStrip({ nodes }: { nodes: ChainNode[] }) {
  return (
    <div className="klaros-card p-4">
      <ol className="flex flex-col gap-2 lg:flex-row lg:items-stretch lg:gap-0" aria-label="How your business operates">
        {nodes.map((n, i) => (
          <li key={n.key} className="flex flex-col lg:flex-1 lg:flex-row lg:items-stretch">
            <Link href={n.route} className="block flex-1 rounded-lg border border-border bg-surface-muted/50 p-3 transition-colors hover:border-border-strong hover:bg-surface-muted">
              <p className="text-sm font-medium text-foreground">{n.label}</p>
              <p className="mt-0.5 text-xs text-muted">{n.detail}</p>
              <div className="mt-2"><StatusPill state={n.state} /></div>
            </Link>
            {i < nodes.length - 1 && (
              <span className="flex items-center justify-center py-0.5 text-border-strong lg:px-1.5 lg:py-0" aria-hidden="true">
                <ArrowRight className="hidden h-4 w-4 lg:block" />
                <ArrowRight className="h-4 w-4 rotate-90 lg:hidden" />
              </span>
            )}
          </li>
        ))}
      </ol>
      <div className="mt-3 text-right">
        <Link href="/business/map" className="inline-flex items-center gap-1 text-xs font-medium text-accent-hover hover:underline">
          Open the Business Map <ChevronRight className="h-3 w-3" aria-hidden="true" />
        </Link>
      </div>
    </div>
  );
}

const DOT: Record<string, string> = { lead: "bg-accent", website: "bg-success", workflow: "bg-accent-2", agent: "bg-accent-2", ai: "bg-accent-2", action: "bg-muted-foreground", integration: "bg-success", data: "bg-muted-foreground", consultation: "bg-accent" };

function clock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const today = new Date();
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === today.toDateString() ? time : `${d.toLocaleDateString(undefined, { day: "numeric", month: "short" })} ${time}`;
}

export function ActivityTimeline({ items }: { items: BusinessOperations["activity"] }) {
  if (items.length === 0) return <p className="klaros-card p-5 text-sm text-muted">Nothing has happened yet. Activity appears here as your business runs.</p>;
  return (
    <ol className="klaros-card px-4 py-3" aria-label="Recent activity">
      {items.slice(0, 8).map((a, i) => (
        <li key={`${a.at}-${i}`} className="relative flex gap-3 pb-3 last:pb-0">
          {i < Math.min(items.length, 8) - 1 && <span className="absolute left-[3px] top-3 h-full w-px bg-border" aria-hidden="true" />}
          <span className={cn("relative mt-1.5 h-[7px] w-[7px] shrink-0 rounded-full", DOT[a.kind] ?? "bg-muted-foreground")} aria-hidden="true" />
          <div className="min-w-0 flex-1 text-sm">
            {a.route ? <Link href={a.route} className="text-foreground hover:underline">{a.text}</Link> : <span className="text-foreground">{a.text}</span>}
            <p className="text-xs text-muted-foreground">{clock(a.at)}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

/** The leads that have something to do next — straight from the batched lead board. */
export function LeadsToAct({ token }: { token: string | null }) {
  const [rows, setRows] = useState<LeadBoardRow[] | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const b = await getLeadBoard(token, { limit: 20 });
        if (!cancelled) setRows(b.leads.filter((l) => l.next_action).slice(0, 5));
      } catch {
        if (!cancelled) setError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);
  if (error) return <p className="klaros-card p-5 text-sm text-muted">We couldn't load your leads just now.</p>;
  if (!rows) return <Skeleton stats={0} rows={3} />;
  if (rows.length === 0) return <p className="klaros-card p-5 text-sm text-muted">No lead needs a next step right now.</p>;
  return (
    <ul className="klaros-card divide-y divide-border" aria-label="Leads with a next step">
      {rows.map((l) => (
        <li key={l.id} className="flex items-start justify-between gap-3 px-4 py-3">
          <div className="min-w-0">
            <Link href={`/leads/${l.id}`} className="text-sm font-medium text-foreground hover:underline">{l.name}</Link>
            <p className="text-xs text-muted">{[l.service, sourceLabel(l.source), leadStatusLabel(l.status)].filter(Boolean).join(" · ")}</p>
            {l.next_action && <p className="mt-1 text-xs text-foreground"><span className="text-muted">Next:</span> {l.next_action.text}</p>}
          </div>
          <span className="shrink-0 text-xs text-muted-foreground">{when(l.created_at)}</span>
        </li>
      ))}
    </ul>
  );
}

/** Launch readiness. The verdict comes from the backend; "Launch Business" is a controlled
 * state transition — it publishes the existing website draft through the existing API,
 * after an explicit confirmation. Nothing else happens. */
export function LaunchPanel({
  overview,
  rows,
  token,
  tenantId,
  onLaunched,
}: {
  overview: BuilderOverview;
  rows: LaunchRow[];
  token: string | null;
  tenantId?: string | null;
  onLaunched: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const launched = overview.launch.launched;
  const others = rows.filter((r) => r.blocking && r.key !== "website");
  const website = overview.website;
  const canLaunch = !launched && website.exists && others.length === 0;
  const reason = launched
    ? null
    : !website.exists
      ? "Generate your website first — launching publishes it."
      : others.length > 0
        ? `Finish ${others.length === 1 ? "this first" : `these ${others.length} first`}: ${others.map((o) => o.group.toLowerCase()).join(", ")}.`
        : null;

  const launch = useCallback(async () => {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const site = await getMyWebsite(token);
      if (!site) throw new Error("no website");
      const versions = await listWebsiteVersions(token, site.id);
      const draft = [...versions].reverse().find((v) => v.status === "DRAFT") ?? versions[versions.length - 1];
      if (!draft) throw new Error("no version");
      await publishWebsiteVersion(token, site.id, draft.id);
      setConfirming(false);
      onLaunched();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't launch just now. Nothing was changed — try again.");
    } finally {
      setBusy(false);
    }
  }, [token, onLaunched]);

  return (
    <div className="klaros-card overflow-hidden">
      <ul className="divide-y divide-border" aria-label="Launch readiness">
        {rows.map((r) => (
          <li key={r.key} className="flex items-start justify-between gap-3 px-4 py-3">
            <div className="min-w-0">
              <p className="klaros-label">{r.group}</p>
              <p className="text-sm font-medium text-foreground">
                {r.route ? <Link href={r.route} className="hover:underline">{r.value}</Link> : r.value}
              </p>
              {r.detail && <p className="mt-0.5 text-xs text-muted">{r.detail}</p>}
            </div>
            <StatusPill state={r.state} className="mt-1" />
          </li>
        ))}
      </ul>
      <div className="border-t border-border bg-surface-muted/50 px-4 py-4">
        {error && <div className="mb-3"><Alert variant="danger">{error}</Alert></div>}
        {launched ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-foreground"><span className="font-medium">Your business is live.</span> <span className="text-muted">Enquiries from your website become leads.</span></p>
            {tenantId && (
              <a href={`/w/${tenantId}`} target="_blank" rel="noopener noreferrer" className="klaros-btn-secondary inline-flex items-center gap-1.5 text-sm">
                View live website <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            )}
          </div>
        ) : confirming ? (
          <div role="alertdialog" aria-labelledby="launch-confirm" className="space-y-3">
            <p id="launch-confirm" className="text-sm text-foreground">Launching publishes your website at its public address, so visitors can send enquiries. You can unpublish it later.</p>
            <div className="flex gap-2">
              <Button onClick={launch} disabled={busy}>{busy ? "Launching…" : "Yes, launch"}</Button>
              <Button variant="secondary" onClick={() => setConfirming(false)} disabled={busy}>Cancel</Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => setConfirming(true)} disabled={!canLaunch} aria-describedby={reason ? "launch-reason" : undefined}>
              <Rocket className="h-4 w-4" aria-hidden="true" /> Launch Business
            </Button>
            {reason && <p id="launch-reason" className="text-xs text-muted">{reason}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
