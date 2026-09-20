"use client";

import { useCallback, useEffect, useState } from "react";
import { Users } from "lucide-react";
import AppShell from "@/components/AppShell";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/useAuth";
import { ApiError, Worker, createWorker, listWorkers, updateWorkerStatus } from "@/lib/api";

const STATUSES = ["AVAILABLE", "BUSY", "OFFLINE", "ON_LEAVE", "INACTIVE"];

export default function WorkersPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [showInactive, setShowInactive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [role, setRole] = useState("");
  const [serviceTypes, setServiceTypes] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listWorkers(token, !showInactive);
      setWorkers(result.workers);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load workers.");
    } finally {
      setLoading(false);
    }
  }, [token, showInactive]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await createWorker(token, {
        name: name.trim(),
        email: email.trim() || undefined,
        phone: phone.trim() || undefined,
        role: role.trim() || undefined,
        service_types: serviceTypes
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      });
      setName("");
      setEmail("");
      setPhone("");
      setRole("");
      setServiceTypes("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to add worker.");
    } finally {
      setBusy(false);
    }
  }

  async function handleStatusChange(workerId: string, status: string) {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await updateWorkerStatus(token, workerId, status);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update status.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="font-display text-2xl text-foreground mb-2">Workers</h1>
        <p className="mb-6 text-sm text-muted">
          Field technicians available for job assignment and scheduling.
        </p>

        <form onSubmit={handleCreate} className="mb-6 flex flex-wrap items-end gap-2 rounded-lg border border-border bg-surface p-4">
          <div>
            <label className="block text-xs text-muted">Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Worker name"
              className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-muted">Email</label>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-muted">Phone</label>
            <input
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-muted">Role</label>
            <input
              value={role}
              onChange={(e) => setRole(e.target.value)}
              placeholder="e.g. HVAC Technician"
              className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-muted">Service types (comma-separated)</label>
            <input
              value={serviceTypes}
              onChange={(e) => setServiceTypes(e.target.value)}
              placeholder="HVAC, PLUMBING"
              className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
            />
          </div>
          <button
            type="submit"
            disabled={busy || !name.trim()}
            className="klaros-btn-primary disabled:opacity-50"
          >
            Add worker
          </button>
        </form>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
        )}

        <label className="mb-4 flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
          Show inactive workers
        </label>

        {authLoading || loading ? (
          <Skeleton />
        ) : workers.length === 0 ? (
          <EmptyState icon={Users} title="No workers yet. Add your first field technician above." />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Name</th>
                  <th className="px-4 py-2">Role</th>
                  <th className="px-4 py-2">Contact</th>
                  <th className="px-4 py-2">Service types</th>
                  <th className="px-4 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {workers.map((w) => (
                  <tr key={w.id} className="border-t border-border">
                    <td className="px-4 py-2 font-medium">{w.name}</td>
                    <td className="px-4 py-2 text-muted">{w.role ?? "—"}</td>
                    <td className="px-4 py-2 text-muted">
                      {w.email ?? "—"}
                      {w.phone && ` · ${w.phone}`}
                    </td>
                    <td className="px-4 py-2 text-muted">{w.service_types.join(", ") || "—"}</td>
                    <td className="px-4 py-2">
                      <div className="flex items-center gap-2">
                        <Badge status={w.status}>{w.status}</Badge>
                        <select
                          value={w.status}
                          disabled={busy}
                          onChange={(e) => handleStatusChange(w.id, e.target.value)}
                          className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-xs disabled:opacity-50"
                        >
                          {STATUSES.map((s) => (
                            <option key={s} value={s}>
                              {s}
                            </option>
                          ))}
                        </select>
                      </div>
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
