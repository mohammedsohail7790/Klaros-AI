"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, CheckCircle2, Clock } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  HandoffTask,
  MedicalTourismFunnel,
  MedicalTourismPipeline,
  RevenueSummary,
  getMedicalTourismFunnel,
  getMedicalTourismPipeline,
  getMedicalTourismRevenue,
  listHandoffTasks,
} from "@/lib/api";
import { buildNeedsYou, todaySummary, waitingOnOthers } from "@/lib/mtCase";
import { STAGE_LABEL, STAGE_ORDER } from "@/lib/mtLabels";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";

const PRIORITY_STYLE: Record<number, string> = {
  1: "border-l-danger",
  2: "border-l-warning",
  3: "border-l-border-strong",
};
const PRIORITY_WORD: Record<number, string> = { 1: "Urgent", 2: "Today", 3: "When you can" };

function greeting(now: Date): string {
  const h = now.getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

const money = (amount: string, currency: string) => {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(Number(amount));
  } catch {
    return `${amount} ${currency}`;
  }
};

/**
 * Today: the owner's first screen. It answers "what needs me right now?" with a prioritised list of named patients and one clear action each, then shows where
 * everyone is and what the business has earned. Everything is read from facts the backend already derived (pipeline, tasks, revenue); the browser invents nothing.
 */
export default function TodayPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [pipeline, setPipeline] = useState<MedicalTourismPipeline | null>(null);
  const [tasks, setTasks] = useState<HandoffTask[]>([]);
  const [revenue, setRevenue] = useState<RevenueSummary | null>(null);
  const [funnel, setFunnel] = useState<MedicalTourismFunnel | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The date and greeting depend on the viewer's clock and locale, so they are set after mount; rendering them on the server would not match the browser.
  const [clock, setClock] = useState<Date | null>(null);
  useEffect(() => setClock(new Date()), []);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [p, t, r, f] = await Promise.all([
        getMedicalTourismPipeline(token),
        listHandoffTasks(token, { status: "OPEN" }),
        getMedicalTourismRevenue(token),
        getMedicalTourismFunnel(token),
      ]);
      setPipeline(p);
      setTasks(t.tasks);
      setRevenue(r);
      setFunnel(f);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load Today.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const now = clock?.getTime() ?? Date.now();
  const needs = useMemo(() => (pipeline ? buildNeedsYou(pipeline.leads, tasks, now) : []), [pipeline, tasks, now]);
  const waiting = useMemo(() => (pipeline ? waitingOnOthers(pipeline.leads, now) : []), [pipeline, now]);
  const firstName = user?.full_name?.split(" ")[0];
  const failedMessages = funnel ? funnel.delivery.halla_events_failed + funnel.delivery.halla_events_stuck_past_lease : 0;
  const total = pipeline?.leads.length ?? 0;
  const currencies = revenue ? Object.entries(revenue.by_currency) : [];

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-5xl px-6 py-8 sm:px-8">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="min-h-5 text-sm text-muted">{clock ? clock.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }) : ""}</p>
            <h1 className="text-3xl font-semibold tracking-tight text-foreground">
              {clock ? greeting(clock) : "Welcome"}
              {firstName ? `, ${firstName}` : ""}
            </h1>
            {pipeline && <p className="mt-1 text-base text-muted">{todaySummary(needs)}</p>}
          </div>
          <Button size="sm" variant="secondary" onClick={() => load()}>
            Refresh
          </Button>
        </header>

        {authLoading || (loading && !pipeline) ? (
          <div className="mt-8">
            <Skeleton />
          </div>
        ) : error ? (
          <div className="mt-8">
            <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>
              {error}
            </Alert>
          </div>
        ) : pipeline ? (
          <>
            {failedMessages > 0 && (
              <div className="mt-6">
                <Alert variant="danger">
                  {failedMessages} {failedMessages === 1 ? "message" : "messages"} from the AI receptionist did not finish arriving, so some enquiries may be missing or out of date.
                </Alert>
              </div>
            )}

            <section aria-label="Needs you" className="mt-8">
              <h2 className="text-xl font-semibold tracking-tight text-foreground">Needs you</h2>
              {needs.length === 0 ? (
                <div className="klaros-card mt-3 flex items-center gap-3 p-5 text-sm text-muted">
                  <CheckCircle2 className="h-5 w-5 text-success" aria-hidden="true" />
                  {total === 0 ? "No patient enquiries yet. They appear here as soon as the AI receptionist or your website records one." : "Every patient is either moving along or waiting on a clinic."}
                </div>
              ) : (
                <ul className="mt-3 space-y-2">
                  {needs.map((n) => (
                    <li key={n.leadId} className={`klaros-card flex flex-col gap-3 border-l-4 p-4 sm:flex-row sm:items-center sm:justify-between ${PRIORITY_STYLE[n.priority]}`}>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium text-foreground">{n.patient}</span>
                          {n.context && <span className="text-sm text-muted">· {n.context}</span>}
                          <span className="rounded-full border border-border-strong px-2 py-0.5 text-xs text-muted">{PRIORITY_WORD[n.priority]}</span>
                          {n.openTasks > 1 && <span className="text-xs text-muted-foreground">{n.openTasks} tasks</span>}
                        </div>
                        <p className="mt-1 text-sm text-muted">{n.headline}</p>
                      </div>
                      <Link
                        href={n.href}
                        className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-xl bg-accent px-3.5 py-2 text-sm font-medium text-accent-foreground hover:bg-accent-hover"
                      >
                        {n.cta}
                        <ArrowRight className="h-4 w-4" aria-hidden="true" />
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <div className="mt-10 grid gap-8 lg:grid-cols-2">
              <section aria-label="Where your patients are">
                <h2 className="text-xl font-semibold tracking-tight text-foreground">Where your patients are</h2>
                <ul className="mt-3 space-y-1.5">
                  {STAGE_ORDER.map((s, i) => {
                    const n = pipeline.counts[s] ?? 0;
                    return (
                      <li key={s}>
                        <Link href={`/medical-tourism/pipeline?stage=${s}`} className="group flex items-center gap-3 rounded-xl px-2 py-1.5 text-sm hover:bg-surface-muted">
                          <span className="w-5 text-right text-xs text-muted-foreground">{i + 1}</span>
                          <span className="w-36 shrink-0 text-foreground">{STAGE_LABEL[s]}</span>
                          <span className="h-2 flex-1 overflow-hidden rounded-full bg-surface-muted">
                            <span className="block h-full rounded-full bg-accent" style={{ width: total > 0 ? `${Math.max(n > 0 ? 6 : 0, (n / total) * 100)}%` : "0%" }} />
                          </span>
                          <span className="w-6 text-right tabular-nums text-muted">{n}</span>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
                <Link href="/medical-tourism/pipeline" className="mt-2 inline-block text-sm text-accent hover:text-accent-hover">
                  Open the patient board
                </Link>
              </section>

              <div className="space-y-8">
                <section aria-label="Money">
                  <h2 className="text-xl font-semibold tracking-tight text-foreground">Money</h2>
                  {currencies.length === 0 ? (
                    <p className="mt-3 text-sm text-muted">No commission is on record yet. A commission only counts once a clinic's outcome is confirmed with evidence.</p>
                  ) : (
                    currencies.map(([cur, by]) => (
                      <div key={cur} className="klaros-card mt-3 p-4">
                        <div className="text-xs text-muted-foreground">{cur}</div>
                        <dl className="mt-2 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                          {(
                            [
                              ["Potential", by.PENDING],
                              ["Earned", by.CONFIRMED],
                              ["Invoiced", by.INVOICED],
                              ["Paid", by.PAID],
                            ] as const
                          ).map(([label, v]) => (
                            <div key={label}>
                              <dt className="text-muted">{label}</dt>
                              <dd className="mt-0.5 text-lg text-foreground">{money(v?.amount ?? "0", cur)}</dd>
                            </div>
                          ))}
                        </dl>
                      </div>
                    ))
                  )}
                  <p className="mt-2 text-xs text-muted-foreground">Potential is not earned: it becomes revenue only when confirmed.</p>
                </section>

                {waiting.length > 0 && (
                  <section aria-label="Waiting on clinics">
                    <h2 className="text-xl font-semibold tracking-tight text-foreground">Waiting on clinics</h2>
                    <ul className="mt-3 space-y-1.5 text-sm">
                      {waiting.map((w) => (
                        <li key={w.leadId} className="flex items-center gap-2 text-muted">
                          <Clock className="h-4 w-4 shrink-0" aria-hidden="true" />
                          <Link href={`/medical-tourism/patients/${w.leadId}`} className="text-foreground hover:underline">
                            {w.patient}
                          </Link>
                          <span>
                            · with {w.clinic}
                            {w.sentAgo ? `, sent ${w.sentAgo}` : ""}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
              </div>
            </div>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
