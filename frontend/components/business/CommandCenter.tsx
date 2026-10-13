"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Activity, AlertTriangle, ArrowRight, BarChart3, ChevronRight, ExternalLink, Globe, Headset, Info, Plug, Rocket, Target, Users, Workflow, XOctagon, Zap, type LucideIcon } from "lucide-react";
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
import { Group, GroupRow, ProgressRing, Tile } from "@/components/ui/layout";
import { cn } from "@/lib/cn";
import { leadStatusLabel, sourceLabel, when } from "@/lib/opsLabels";
import { StatusPill } from "./StatusPill";
import { ActionItem, ChainNode, HealthTile, LaunchRow, Severity } from "./homeModel";

const SEVERITY: Record<Severity, { icon: typeof Info; tone: string; chip: string; label: string }> = {
  danger: { icon: XOctagon, tone: "text-danger", chip: "bg-danger/10", label: "Needs attention" },
  warning: { icon: AlertTriangle, tone: "text-warning", chip: "bg-warning/10", label: "Waiting on you" },
  info: { icon: Info, tone: "text-accent", chip: "bg-accent-soft", label: "Set-up step" },
};

const TILE_ICON: Record<string, { icon: LucideIcon; chip: string }> = {
  website: { icon: Globe, chip: "bg-sky-50 text-sky-600" },
  workforce: { icon: Headset, chip: "bg-accent-soft text-accent" },
  leads: { icon: Users, chip: "bg-emerald-50 text-emerald-600" },
  opportunities: { icon: Target, chip: "bg-amber-50 text-amber-600" },
  pending: { icon: Zap, chip: "bg-rose-50 text-rose-600" },
  workflows: { icon: Workflow, chip: "bg-indigo-50 text-indigo-600" },
  integrations: { icon: Plug, chip: "bg-teal-50 text-teal-600" },
};

/** What needs a person, most urgent first: the top item is the "up next" card, the rest are a grouped list. */
export function ActionCenter({ items, loading }: { items: ActionItem[]; loading?: boolean }) {
  if (loading) return <Skeleton stats={0} rows={3} />;
  if (items.length === 0) {
    return (
      <div className="rounded-3xl bg-surface-muted px-8 py-10 text-center">
        <p className="font-display text-2xl font-semibold tracking-tight text-foreground">You&apos;re all caught up.</p>
        <p className="mx-auto mt-2 max-w-md text-[15px] text-muted">Nothing is waiting on you right now. New leads and issues appear here the moment they need a person.</p>
      </div>
    );
  }
  const [first, ...rest] = items;
  const S = SEVERITY[first.severity];
  return (
    <ul className="space-y-3" aria-label="Action center">
      <li className="klaros-dark flex flex-col gap-6 rounded-3xl p-7 sm:flex-row sm:items-center sm:justify-between sm:p-9">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-[13px] font-medium text-muted">
            <S.icon className={cn("h-4 w-4", S.tone)} aria-hidden="true" /> Up next · {S.label}
          </p>
          <p className="mt-3 text-balance font-display text-2xl font-semibold leading-tight tracking-[-0.02em] sm:text-[1.9rem]">{first.text}</p>
        </div>
        <Link href={first.route} className="klaros-btn-primary shrink-0 self-start !px-6 !py-3 text-[15px] sm:self-center">
          {first.cta}
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
          <span className="sr-only"> — {first.text}</span>
        </Link>
      </li>
      {rest.map((a) => {
        const R = SEVERITY[a.severity];
        return (
          <li key={a.id} className="flex items-center gap-3.5 rounded-2xl bg-surface-muted px-5 py-4">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface">
              <R.icon className={cn("h-[18px] w-[18px]", R.tone)} aria-hidden="true" />
            </span>
            <p className="min-w-0 flex-1 text-[15px] text-foreground">{a.text}</p>
            <Link href={a.route} className="shrink-0 text-[15px] text-accent hover:underline">
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
    <ul className="grid grid-cols-2 gap-3 md:grid-cols-4" aria-label="Business health">
      {tiles.map((t) => {
        const meta = TILE_ICON[t.key] ?? { icon: Activity, chip: "" };
        return (
          <li key={t.key}>
            <Tile label={t.label} value={t.value} hint={t.hint} href={t.route} icon={meta.icon} />
          </li>
        );
      })}
    </ul>
  );
}

export function QuickActions({ actions }: { actions: { label: string; href: string }[] }) {
  return (
    <Group label="Quick actions">
      {actions.map((a) => (
        <GroupRow key={a.label} title={a.label} href={a.href} />
      ))}
    </Group>
  );
}

/** The business as a chain of real, linked parts, shown as a vertical stepper. */
export function ArchitectureStrip({ nodes }: { nodes: ChainNode[] }) {
  return (
    <div>
      <ol className="relative space-y-1" aria-label="How your business operates">
        {nodes.map((n, i) => (
          <li key={n.key} className="relative flex gap-4">
            <div className="flex flex-col items-center">
              <span className="mt-4 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-muted text-xs font-semibold text-muted">{i + 1}</span>
              {i < nodes.length - 1 && <span className="mt-1 w-px flex-1 bg-border" aria-hidden="true" />}
            </div>
            <Link href={n.route} className="group mb-1 flex min-w-0 flex-1 items-center justify-between gap-3 rounded-2xl px-4 py-3 transition-colors duration-300 hover:bg-surface-muted">
              <span className="min-w-0">
                <span className="block text-[15px] font-medium text-foreground">{n.label}</span>
                <span className="mt-0.5 block text-[13px] text-muted">{n.detail}</span>
              </span>
              <StatusPill state={n.state} />
            </Link>
          </li>
        ))}
      </ol>
      <Link href="/business/map" className="mt-3 inline-flex items-center gap-1 text-[15px] text-accent hover:underline">
        Open the Business Map <ChevronRight className="h-4 w-4" aria-hidden="true" />
      </Link>
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
  if (items.length === 0) return <p className="rounded-3xl bg-surface-muted px-6 py-8 text-[15px] text-muted">Nothing has happened yet. Activity appears here as your business runs.</p>;
  return (
    <Group label="Recent activity">
      {items.slice(0, 8).map((a, i) => (
        <GroupRow key={`${a.at}-${i}`} title={<span className="whitespace-normal">{a.text}</span>} href={a.route ?? undefined} trailing={clock(a.at)} />
      ))}
    </Group>
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
  if (error) return <p className="rounded-3xl bg-surface-muted px-6 py-8 text-[15px] text-muted">We couldn't load your leads just now.</p>;
  if (!rows) return <Skeleton stats={0} rows={3} />;
  if (rows.length === 0) return <p className="rounded-3xl bg-surface-muted px-6 py-8 text-[15px] text-muted">No lead needs a next step right now.</p>;
  return (
    <Group label="Leads with a next step">
      {rows.map((l) => (
        <GroupRow
          key={l.id}
          title={l.name}
          href={`/leads/${l.id}`}
          detail={
            <>
              {[l.service, sourceLabel(l.source), leadStatusLabel(l.status)].filter(Boolean).join(" · ")}
              {l.next_action && (
                <span className="mt-1 block text-foreground">
                  <span className="text-muted">Next:</span> {l.next_action.text}
                </span>
              )}
            </>
          }
          trailing={when(l.created_at)}
        />
      ))}
    </Group>
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

  const doneCount = rows.filter((r) => r.state === "READY" || r.state === "CONNECTED").length;
  return (
    <div className="overflow-hidden rounded-3xl bg-surface-muted">
      <div className="flex items-center gap-5 px-6 py-6">
        <ProgressRing done={doneCount} total={rows.length} />
        <p className="text-[15px] leading-snug text-foreground">
          <span className="font-display text-xl font-semibold tracking-tight">Launch readiness</span>
          <br />
          <span className="text-muted">{doneCount} of {rows.length} checks complete</span>
        </p>
      </div>
      <ul className="divide-y divide-border/70 border-t border-border/70" aria-label="Launch readiness">
        {rows.map((r) => (
          <li key={r.key} className="flex items-start justify-between gap-3 px-6 py-3.5">
            <div className="min-w-0">
              <p className="text-xs text-muted-foreground">{r.group}</p>
              <p className="text-[15px] font-medium text-foreground">
                {r.route ? <Link href={r.route} className="hover:underline">{r.value}</Link> : r.value}
              </p>
              {r.detail && <p className="mt-0.5 text-[13px] text-muted">{r.detail}</p>}
            </div>
            <StatusPill state={r.state} className="mt-1" />
          </li>
        ))}
      </ul>
      <div className="border-t border-border/70 px-6 py-5">
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
