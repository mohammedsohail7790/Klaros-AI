"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, ServiceReminderRow, listServiceReminders, markDueReminders, updateReminderStatus } from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
export default function RemindersPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [reminders, setReminders] = useState<ServiceReminderRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
    try {
      const result = await markDueReminders(token);
      toast.success(`${result.due_reminder_ids.length} reminder(s) marked DUE.`);
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
          <h1 className="font-display text-2xl text-foreground">Service Reminders</h1>
          <button disabled={busy} onClick={handleMarkDue} className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50">
            Mark due reminders
          </button>
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : reminders.length === 0 ? (
          <EmptyState icon={AlertTriangle} title="No service reminders yet." />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
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
                  <tr key={r.id} className="border-t border-border">
                    <td className="px-4 py-2">{r.service_type || "—"}</td>
                    <td className="px-4 py-2 text-muted">{r.reminder_date}</td>
                    <td className="px-4 py-2 text-muted">{r.reason}</td>
                    <td className="px-4 py-2">
                      <Badge status={r.status}>{r.status}</Badge>
                    </td>
                    <td className="px-4 py-2 space-x-2">
                      {(r.status === "SCHEDULED" || r.status === "DUE") && (
                        <>
                          <button disabled={busy} onClick={() => handleStatus(r.id, "SENT")} className="text-xs underline text-muted hover:text-foreground">Send</button>
                          <button disabled={busy} onClick={() => handleStatus(r.id, "BOOKED")} className="text-xs underline text-success hover:text-foreground">Book</button>
                          <button disabled={busy} onClick={() => handleStatus(r.id, "CANCELLED")} className="text-xs underline text-danger hover:text-foreground">Cancel</button>
                        </>
                      )}
                      {r.status === "SENT" && (
                        <button disabled={busy} onClick={() => handleStatus(r.id, "RESPONDED")} className="text-xs underline text-muted hover:text-foreground">Mark responded</button>
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
