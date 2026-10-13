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
import { Hero, Page } from "@/components/ui/layout";

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
      <Page>
        <Hero
          eyebrow={clock ? clock.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }) : " "}
          title={`${clock ? greeting(clock) : "Welcome"}${firstName ? `, ${firstName}` : ""}`}
          lede={pipeline ? todaySummary(needs) : undefined}
          actions={
            <Button size="sm" variant="secondary" onClick={() => load()}>
              Refresh
            </Button>
          }
        />

        {authLoading || (loading && !pipeline) ? (
          <Skeleton />
        ) : error ? (
          <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>
            {error}
          </Alert>
        ) : pipeline ? (
          <>
            {failedMessages > 0 && (
              <div className="mb-8">
                <Alert variant="danger">
                  {failedMessages} {failedMessages === 1 ? "message" : "messages"} from the AI receptionist did not finish arriving, so some enquiries may be missing or out of date.
                </Alert>
              </div>
            )}

            <section aria-label="Needs you" className="mb-14">
              <h2 className="mb-4 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Needs you</h2>
              {needs.length === 0 ? (
                <div className="flex items-center gap-4 rounded-3xl bg-surface-muted px-7 py-8 text-[17px] text-muted">
                  <CheckCircle2 className="h-6 w-6 shrink-0 text-success" aria-hidden="true" />
                  {total === 0 ? "No patient enquiries yet. They appear here as soon as the AI receptionist or your website records one." : "Every patient is either moving along or waiting on a clinic."}
                </div>
              ) : (
                <ul className="space-y-3">
                  {needs.map((n, idx) => (
                    <li key={n.leadId} className={idx === 0 ? "klaros-dark flex flex-col gap-5 rounded-3xl p-7 sm:flex-row sm:items-center sm:justify-between sm:p-8" : "flex flex-col gap-4 rounded-3xl bg-surface-muted p-6 sm:flex-row sm:items-center sm:justify-between"}>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                          <span className={idx === 0 ? "font-display text-2xl font-semibold tracking-tight" : "text-[17px] font-semibold text-foreground"}>{n.patient}</span>
                          {n.context && <span className="text-[15px] text-muted">{n.context}</span>}
                          <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${n.priority === 1 ? "bg-danger/10 text-danger" : n.priority === 2 ? "bg-warning/10 text-warning" : "bg-surface text-muted"}`}>
                            <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${n.priority === 1 ? "bg-danger" : n.priority === 2 ? "bg-warning" : "bg-muted-foreground"}`} />
                            {PRIORITY_WORD[n.priority]}
                          </span>
                          {n.openTasks > 1 && <span className="text-xs text-muted-foreground">{n.openTasks} tasks</span>}
                        </div>
                        <p className={idx === 0 ? "mt-2 text-[17px] leading-snug text-muted" : "mt-1 text-[15px] leading-snug text-muted"}>{n.headline}</p>
                      </div>
                      <Link href={n.href} className={idx === 0 ? "klaros-btn-primary shrink-0 self-start !px-6 !py-3 text-[15px] sm:self-center" : "klaros-btn-primary shrink-0 self-start sm:self-center"}>
                        {n.cta}
                        <ArrowRight className="h-4 w-4" aria-hidden="true" />
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <div className="grid gap-x-12 gap-y-4 lg:grid-cols-2">
              <section aria-label="Where your patients are" className="mb-12">
                <h2 className="mb-4 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Where your patients are</h2>
                <ul className="divide-y divide-border/70 overflow-hidden rounded-3xl bg-surface-muted">
                  {STAGE_ORDER.map((s, i) => {
                    const n = pipeline.counts[s] ?? 0;
                    return (
                      <li key={s}>
                        <Link href={`/medical-tourism/pipeline?stage=${s}`} className="group flex items-center gap-4 px-5 py-3.5 text-[15px] transition-colors duration-300 hover:bg-black/[0.03]">
                          <span className="w-4 text-right text-xs tabular-nums text-muted-foreground">{i + 1}</span>
                          <span className="w-32 shrink-0 text-foreground">{STAGE_LABEL[s]}</span>
                          <span className="h-2 flex-1 overflow-hidden rounded-full bg-border/60" aria-hidden="true">
                            <span className="block h-full rounded-full bg-accent transition-[width] duration-700 ease-apple" style={{ width: total > 0 ? `${Math.max(n > 0 ? 6 : 0, (n / total) * 100)}%` : "0%" }} />
                          </span>
                          <span className="w-6 text-right font-medium tabular-nums text-foreground">{n}</span>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
                <Link href="/medical-tourism/pipeline" className="mt-3 inline-block text-[15px] text-accent hover:underline">
                  Open the patient board &rsaquo;
                </Link>
              </section>

              <div>
                <section aria-label="Money" className="mb-12">
                  <h2 className="mb-4 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Money</h2>
                  {currencies.length === 0 ? (
                    <p className="rounded-3xl bg-surface-muted px-6 py-6 text-[15px] leading-snug text-muted">No commission is on record yet. A commission only counts once a clinic&apos;s outcome is confirmed with evidence.</p>
                  ) : (
                    currencies.map(([cur, by]) => (
                      <div key={cur} className="mb-3 rounded-3xl bg-surface-muted p-6">
                        <div className="text-xs font-medium text-muted-foreground">{cur}</div>
                        <dl className="mt-3 grid grid-cols-2 gap-5 sm:grid-cols-4">
                          {(
                            [
                              ["Potential", by.PENDING],
                              ["Earned", by.CONFIRMED],
                              ["Invoiced", by.INVOICED],
                              ["Paid", by.PAID],
                            ] as const
                          ).map(([label, v]) => (
                            <div key={label}>
                              <dt className="text-[13px] text-muted">{label}</dt>
                              <dd className="mt-1 font-display font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">{money(v?.amount ?? "0", cur)}</dd>
                            </div>
                          ))}
                        </dl>
                      </div>
                    ))
                  )}
                  <p className="mt-3 text-[13px] text-muted-foreground">Potential is not earned: it becomes revenue only when confirmed.</p>
                </section>

                {waiting.length > 0 && (
                  <section aria-label="Waiting on clinics" className="mb-12">
                    <h2 className="mb-4 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Waiting on clinics</h2>
                    <ul className="divide-y divide-border/70 overflow-hidden rounded-3xl bg-surface-muted">
                      {waiting.map((w) => (
                        <li key={w.leadId} className="flex items-center gap-3 px-5 py-4 text-[15px] text-muted">
                          <Clock className="h-4 w-4 shrink-0" aria-hidden="true" />
                          <Link href={`/medical-tourism/patients/${w.leadId}`} className="font-medium text-foreground hover:underline">
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
      </Page>
    </AppShell>
  );
}
