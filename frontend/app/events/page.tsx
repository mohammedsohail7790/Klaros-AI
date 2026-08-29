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
          <h1 className="text-xl font-semibold">Event Worker</h1>
          <button onClick={load} className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900">
            Refresh
          </button>
        </div>

        {notice && <div className="mb-4 rounded-md border border-emerald-900 bg-emerald-950/30 p-3 text-sm text-emerald-300">{notice}</div>}

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : (
          <>
            {metrics && (
              <div className="mb-8">
                <h2 className="mb-3 text-sm font-medium text-neutral-300">Worker metrics (real, in-process counters)</h2>
                <div className="grid grid-cols-2 gap-4 md:grid-cols-6">
                  <div className="rounded-lg border border-neutral-800 p-4">
                    <div className="text-xs text-neutral-500">Processed</div>
                    <div className="mt-1 text-2xl font-semibold">{metrics.events_processed}</div>
                  </div>
                  <div className="rounded-lg border border-neutral-800 p-4">
                    <div className="text-xs text-neutral-500">Failed (retrying)</div>
                    <div className="mt-1 text-2xl font-semibold">{metrics.events_failed}</div>
                  </div>
                  <div className="rounded-lg border border-neutral-800 p-4">
                    <div className="text-xs text-neutral-500">Dead-lettered</div>
                    <div className="mt-1 text-2xl font-semibold">{metrics.events_dead_lettered}</div>
                  </div>
                  <div className="rounded-lg border border-neutral-800 p-4">
                    <div className="text-xs text-neutral-500">Deduplicated</div>
                    <div className="mt-1 text-2xl font-semibold">{metrics.events_deduplicated}</div>
                  </div>
                  <div className="rounded-lg border border-neutral-800 p-4">
                    <div className="text-xs text-neutral-500">Ticks</div>
                    <div className="mt-1 text-2xl font-semibold">{metrics.ticks}</div>
                  </div>
                  <div className="rounded-lg border border-neutral-800 p-4">
                    <div className="text-xs text-neutral-500">Started</div>
                    <div className="mt-1 text-xs text-neutral-400">
                      {metrics.started_at ? new Date(metrics.started_at).toLocaleTimeString() : "Not running in this process"}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {deadLetters.length > 0 && (
              <div className="mb-8">
                <h2 className="mb-3 text-sm font-medium text-red-300">Dead-lettered events ({deadLetters.length})</h2>
                <div className="overflow-x-auto rounded-lg border border-red-900">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-neutral-950 text-neutral-500">
                      <tr>
                        <th className="px-4 py-2">Event type</th>
                        <th className="px-4 py-2">Handler</th>
                        <th className="px-4 py-2">Reason</th>
                        <th className="px-4 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {deadLetters.map((d) => (
                        <tr key={d.dead_letter_id} className="border-t border-neutral-900">
                          <td className="px-4 py-2">{d.event_type}</td>
                          <td className="px-4 py-2 text-neutral-400">{d.handler_name}</td>
                          <td className="max-w-sm truncate px-4 py-2 text-neutral-500">{d.reason}</td>
                          <td className="px-4 py-2">
                            <button
                              disabled={busy}
                              onClick={() => handleReplay(d.dead_letter_id)}
                              className="text-xs underline text-neutral-400 hover:text-white"
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
                  className={`rounded-full border px-3 py-1 text-xs ${status === s ? "border-white bg-white text-black" : "border-neutral-700 text-neutral-400"}`}
                >
                  {s}
                </button>
              ))}
            </div>

            {events.length === 0 ? (
              <p className="text-sm text-neutral-500">No events.</p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-neutral-800">
                <table className="w-full text-left text-sm">
                  <thead className="bg-neutral-950 text-neutral-500">
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
                      <tr key={e.event_id} className="border-t border-neutral-900">
                        <td className="px-4 py-2">{e.event_type}</td>
                        <td className="px-4 py-2">
                          <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">{e.status}</span>
                        </td>
                        <td className="px-4 py-2 text-neutral-400">{e.retry_count}</td>
                        <td className="px-4 py-2 text-neutral-500">{e.entity_type ?? "—"}</td>
                        <td className="px-4 py-2 text-neutral-500">{new Date(e.created_at).toLocaleString()}</td>
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
