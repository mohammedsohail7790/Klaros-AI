"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  DeadLetterRow,
  EventRow,
  EventWorkerMetrics,
  getEventWorkerMetrics,
  listDeadLetters,
  listEvents,
  replayDeadLetter,
} from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
const STATUS_TABS = ["ALL", "PUBLISHED", "PROCESSING", "RETRYING", "PROCESSED", "FAILED", "DEAD_LETTER"];

export default function EventsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState("ALL");
  const [events, setEvents] = useState<EventRow[]>([]);
  const [deadLetters, setDeadLetters] = useState<DeadLetterRow[]>([]);
  const [metrics, setMetrics] = useState<EventWorkerMetrics | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    setNotice(null);
    try {
      const result = await replayDeadLetter(token, deadLetterId);
      setNotice(`Replay result: ${result.result}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to replay.");
    } finally {
      setBusy(false);
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

        {notice && <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50/30 p-3 text-sm text-emerald-700">{notice}</div>}

        {authLoading || loading ? (
          <p className="text-sm text-muted">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : (
          <>
            {metrics && (
              <div className="mb-8">
                <h2 className="mb-3 text-sm font-medium text-muted">Worker metrics (real, in-process counters)</h2>
                <div className="grid grid-cols-2 gap-4 md:grid-cols-6">
                  <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                    <div className="text-xs text-muted">Processed</div>
                    <div className="mt-1 text-2xl font-semibold">{metrics.events_processed}</div>
                  </div>
                  <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                    <div className="text-xs text-muted">Failed (retrying)</div>
                    <div className="mt-1 text-2xl font-semibold">{metrics.events_failed}</div>
                  </div>
                  <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                    <div className="text-xs text-muted">Dead-lettered</div>
                    <div className="mt-1 text-2xl font-semibold">{metrics.events_dead_lettered}</div>
                  </div>
                  <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                    <div className="text-xs text-muted">Deduplicated</div>
                    <div className="mt-1 text-2xl font-semibold">{metrics.events_deduplicated}</div>
                  </div>
                  <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                    <div className="text-xs text-muted">Ticks</div>
                    <div className="mt-1 text-2xl font-semibold">{metrics.ticks}</div>
                  </div>
                  <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                    <div className="text-xs text-muted">Started</div>
                    <div className="mt-1 text-xs text-muted">
                      {metrics.started_at ? new Date(metrics.started_at).toLocaleTimeString() : "Not running in this process"}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {deadLetters.length > 0 && (
              <div className="mb-8">
                <h2 className="mb-3 text-sm font-medium text-red-700">Dead-lettered events ({deadLetters.length})</h2>
                <div className="overflow-x-auto rounded-lg border border-red-200">
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
              <p className="text-sm text-muted">No events.</p>
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
                      <tr key={e.event_id} className="border-t border-border">
                        <td className="px-4 py-2">{e.event_type}</td>
                        <td className="px-4 py-2">
                          <Badge status={e.status}>{e.status}</Badge>
                        </td>
                        <td className="px-4 py-2 text-muted">{e.retry_count}</td>
                        <td className="px-4 py-2 text-muted">{e.entity_type ?? "—"}</td>
                        <td className="px-4 py-2 text-muted">{new Date(e.created_at).toLocaleString()}</td>
                      </tr>
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
