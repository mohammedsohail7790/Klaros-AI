"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, ServiceReminderRow, listServiceReminders, markDueReminders, updateReminderStatus } from "@/lib/api";

export default function RemindersPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [reminders, setReminders] = useState<ServiceReminderRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setReminders((await listServiceReminders(token)).reminders);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load reminders.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleMarkDue() {
    if (!token) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await markDueReminders(token);
      setNotice(`${result.due_reminder_ids.length} reminder(s) marked DUE.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to mark reminders due.");
    } finally {
      setBusy(false);
    }
  }

  async function handleStatus(id: string, status: string) {
    if (!token) return;
    setBusy(true);
    try {
      await updateReminderStatus(token, id, status);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update reminder.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-xl font-semibold">Service Reminders</h1>
          <button disabled={busy} onClick={handleMarkDue} className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900 disabled:opacity-50">
            Mark due reminders
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
        ) : reminders.length === 0 ? (
          <p className="text-sm text-neutral-500">No service reminders yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-neutral-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-neutral-950 text-neutral-500">
                <tr>
                  <th className="px-4 py-2">Service</th>
                  <th className="px-4 py-2">Due date</th>
                  <th className="px-4 py-2">Reason</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {reminders.map((r) => (
                  <tr key={r.id} className="border-t border-neutral-900">
                    <td className="px-4 py-2">{r.service_type || "—"}</td>
                    <td className="px-4 py-2 text-neutral-400">{r.reminder_date}</td>
                    <td className="px-4 py-2 text-neutral-500">{r.reason}</td>
                    <td className="px-4 py-2">
                      <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">{r.status}</span>
                    </td>
                    <td className="px-4 py-2 space-x-2">
                      {(r.status === "SCHEDULED" || r.status === "DUE") && (
                        <>
                          <button disabled={busy} onClick={() => handleStatus(r.id, "SENT")} className="text-xs underline text-neutral-400 hover:text-white">Send</button>
                          <button disabled={busy} onClick={() => handleStatus(r.id, "BOOKED")} className="text-xs underline text-emerald-400 hover:text-white">Book</button>
                          <button disabled={busy} onClick={() => handleStatus(r.id, "CANCELLED")} className="text-xs underline text-red-400 hover:text-white">Cancel</button>
                        </>
                      )}
                      {r.status === "SENT" && (
                        <button disabled={busy} onClick={() => handleStatus(r.id, "RESPONDED")} className="text-xs underline text-neutral-400 hover:text-white">Mark responded</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
