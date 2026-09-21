"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { Radio } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { StatCard } from "@/components/ui/StatCard";
import {
  ApiError,
  DeadLetterRow,
  EventDetail,
  EventRow,
  EventWorkerMetrics,
  getEventDetail,
  getEventWorkerMetrics,
  listDeadLetters,
  listEvents,
  replayDeadLetter,
} from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
const STATUS_TABS = ["ALL", "PUBLISHED", "PROCESSING", "RETRYING", "PROCESSED", "FAILED", "DEAD_LETTER"];

export default function EventsPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState("ALL");
  const [events, setEvents] = useState<EventRow[]>([]);
  const [deadLetters, setDeadLetters] = useState<DeadLetterRow[]>([]);
  const [metrics, setMetrics] = useState<EventWorkerMetrics | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<EventDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [eventsResult, deadLettersResult, metricsResult] = await Promise.all([
        listEvents(token, status === "ALL" ? undefined : status),
        listDeadLetters(token),
        getEventWorkerMetrics(token),
      ]);
      setEvents(eventsResult.events);
      setDeadLetters(deadLettersResult.dead_letters);
      setMetrics(metricsResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load events.");
    } finally {
      setLoading(false);
    }
  }, [token, status]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleReplay(deadLetterId: string) {
    if (!token) return;
    setBusy(true);
    try {
      const result = await replayDeadLetter(token, deadLetterId);
      toast.success(`Replay result: ${result.result}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to replay.");
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleDetail(eventId: string) {
    if (expandedId === eventId) {
      setExpandedId(null);
      return;
    }
    setExpandedId(eventId);
    setDetail(null);
    setDetailError(null);
    if (!token) return;
    setDetailLoading(true);
    try {
      setDetail(await getEventDetail(token, eventId));
    } catch (err) {
      setDetailError(err instanceof ApiError ? err.message : "Unable to load event detail.");
    } finally {
      setDetailLoading(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Event Worker</h1>
          <button onClick={load} className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted">
            Refresh
          </button>
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : (
          <>
            {metrics && (
              <div className="mb-8">
                <h2 className="mb-3 text-sm font-medium text-muted">Worker metrics (real, in-process counters)</h2>
                <div className="grid grid-cols-2 gap-4 md:grid-cols-6">
                  <StatCard label="Processed" value={metrics.events_processed} tone="success" />
                  <StatCard label="Failed (retrying)" value={metrics.events_failed} tone={metrics.events_failed > 0 ? "warning" : "neutral"} />
                  <StatCard label="Dead-lettered" value={metrics.events_dead_lettered} tone={metrics.events_dead_lettered > 0 ? "danger" : "neutral"} />
                  <StatCard label="Deduplicated" value={metrics.events_deduplicated} />
                  <StatCard label="Ticks" value={metrics.ticks} />
                  <StatCard
                    label="Started"
                    value={metrics.started_at ? new Date(metrics.started_at).toLocaleTimeString() : "Not running in this process"}
                    compact
                  />
                </div>
              </div>
            )}

            {deadLetters.length > 0 && (
              <div className="mb-8">
                <h2 className="mb-3 text-sm font-medium text-danger">Dead-lettered events ({deadLetters.length})</h2>
                <div className="overflow-x-auto rounded-lg border border-danger/25">
                  <table className="klaros-table">
                    <thead className="bg-surface text-muted">
                      <tr>
                        <th className="px-4 py-2">Event type</th>
                        <th className="px-4 py-2">Handler</th>
                        <th className="px-4 py-2">Reason</th>
                        <th className="px-4 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {deadLetters.map((d) => (
                        <tr key={d.dead_letter_id} className="border-t border-border">
                          <td className="px-4 py-2">{d.event_type}</td>
                          <td className="px-4 py-2 text-muted">{d.handler_name}</td>
                          <td className="max-w-sm truncate px-4 py-2 text-muted">{d.reason}</td>
                          <td className="px-4 py-2">
                            <button
                              disabled={busy}
                              onClick={() => handleReplay(d.dead_letter_id)}
                              className="text-xs underline text-muted hover:text-foreground"
                            >
                              Retry
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="mb-4 flex flex-wrap gap-2">
              {STATUS_TABS.map((s) => (
                <button
                  key={s}
                  onClick={() => setStatus(s)}
                  className={`rounded-full border px-3 py-1 text-xs ${status === s ? "border-foreground bg-surface text-foreground" : "border-border-strong text-muted"}`}
                >
                  {s}
                </button>
              ))}
            </div>

            {events.length === 0 ? (
              <EmptyState icon={Radio} title="No events." />
            ) : (
              <div className="klaros-table-wrap">
                <table className="klaros-table">
                  <thead className="bg-surface text-muted">
                    <tr>
                      <th className="px-4 py-2">Type</th>
                      <th className="px-4 py-2">Status</th>
                      <th className="px-4 py-2">Retries</th>
                      <th className="px-4 py-2">Entity</th>
                      <th className="px-4 py-2">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.map((e) => (
                      <Fragment key={e.event_id}>
                        <tr
                          onClick={() => handleToggleDetail(e.event_id)}
                          className="cursor-pointer border-t border-border hover:bg-surface-muted"
                        >
                          <td className="px-4 py-2">{e.event_type}</td>
                          <td className="px-4 py-2">
                            <Badge status={e.status}>{e.status}</Badge>
                          </td>
                          <td className="px-4 py-2 text-muted">{e.retry_count}</td>
                          <td className="px-4 py-2 text-muted">{e.entity_type ?? "—"}</td>
                          <td className="px-4 py-2 text-muted">{new Date(e.created_at).toLocaleString()}</td>
                        </tr>
                        {expandedId === e.event_id && (
                          <tr className="border-t border-border bg-surface-muted">
                            <td colSpan={5} className="px-4 py-3">
                              {detailLoading ? (
                                <Skeleton />
                              ) : detailError ? (
                                <p className="text-xs text-danger">{detailError}</p>
                              ) : detail ? (
                                <div className="space-y-3 text-xs">
                                  <div>
                                    <p className="mb-1 font-medium text-muted">Payload</p>
                                    <pre className="overflow-x-auto rounded-md border border-border bg-surface p-2">
                                      {JSON.stringify(detail.payload, null, 2)}
                                    </pre>
                                  </div>
                                  {detail.attempts.length > 0 && (
                                    <div>
                                      <p className="mb-1 font-medium text-muted">Handler attempts</p>
                                      <ul className="space-y-1">
                                        {detail.attempts.map((a, i) => (
                                          <li key={i} className="rounded-md border border-border bg-surface p-2">
                                            <span className="font-medium">{a.handler_name}</span> —{" "}
                                            <Badge status={a.status}>{a.status}</Badge> · {a.attempts} attempt(s)
                                            {a.last_error && (
                                              <p className="mt-1 text-danger">{a.last_error}</p>
                                            )}
                                          </li>
                                        ))}
                                      </ul>
                                    </div>
                                  )}
                                </div>
                              ) : null}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
