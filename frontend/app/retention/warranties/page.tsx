"use client";

import { useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/ui/EmptyState";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Customer,
  Warranty,
  checkInWarranty,
  createWarranty,
  detectExpiringWarranties,
  listWarranties,
  searchCustomers,
} from "@/lib/api";

export default function WarrantiesPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [warranties, setWarranties] = useState<Warranty[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [checkInBusy, setCheckInBusy] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerResults, setCustomerResults] = useState<Customer[]>([]);
  const [selectedCustomer, setSelectedCustomer] = useState<Customer | null>(null);
  const [itemDescription, setItemDescription] = useState("");
  const [startDate, setStartDate] = useState(new Date().toISOString().slice(0, 10));
  const [expiryDate, setExpiryDate] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setWarranties((await listWarranties(token)).warranties);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load warranties.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDetectExpiring() {
    if (!token) return;
    setDetecting(true);
    setNotice(null);
    setError(null);
    try {
      const result = await detectExpiringWarranties(token);
      const total = result.newly_expiring_soon.length + result.newly_expired.length;
      setNotice(
        total === 0
          ? "Nothing newly expiring or expired."
          : `${result.newly_expiring_soon.length} newly expiring soon, ${result.newly_expired.length} newly expired.`
      );
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to check for expiring warranties.");
    } finally {
      setDetecting(false);
    }
  }

  async function handleSearchCustomer() {
    if (!token || !customerQuery.trim()) return;
    try {
      const result = await searchCustomers(token, { q: customerQuery.trim(), limit: 5 });
      setCustomerResults(result.customers);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to search customers.");
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !selectedCustomer || !itemDescription.trim() || !expiryDate) return;
    setSubmitting(true);
    setError(null);
    try {
      await createWarranty(token, {
        customer_id: selectedCustomer.id,
        item_description: itemDescription.trim(),
        start_date: startDate,
        expiry_date: expiryDate,
      });
      setSelectedCustomer(null);
      setCustomerQuery("");
      setCustomerResults([]);
      setItemDescription("");
      setExpiryDate("");
      setShowCreate(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create warranty.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCheckIn(warrantyId: string) {
    if (!token) return;
    setCheckInBusy(warrantyId);
    setError(null);
    try {
      await checkInWarranty(token, warrantyId);
      setNotice("Checked in.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to check in.");
    } finally {
      setCheckInBusy(null);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <header className="mb-1 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Warranties</h1>
          <div className="flex gap-2">
            <button
              disabled={detecting}
              onClick={handleDetectExpiring}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {detecting ? "Checking..." : "Check for expiring"}
            </button>
            <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
              New warranty
            </button>
          </div>
        </header>
        <p className="mb-6 text-sm text-muted">
          Coverage windows on completed jobs — flagged 30 days before expiry so a check-in becomes a retention
          touchpoint, not a missed one.
        </p>

        {notice && (
          <div className="mb-4 rounded-md border border-success/20 bg-success/[0.06] p-3 text-sm text-success">
            {notice}
          </div>
        )}
        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
        )}

        {authLoading || loading ? (
          <Skeleton />
        ) : warranties.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="No warranties on file yet."
            action={
              <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
                New warranty
              </button>
            }
          />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Item</th>
                  <th className="px-4 py-2">Expiry</th>
                  <th className="px-4 py-2">Last check-in</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {warranties.map((w) => (
                  <tr key={w.id} className="border-t border-border">
                    <td className="px-4 py-2">{w.item_description}</td>
                    <td className="px-4 py-2 text-muted">{w.expiry_date}</td>
                    <td className="px-4 py-2 text-muted">
                      {w.last_checked_in_at ? new Date(w.last_checked_in_at).toLocaleDateString() : "—"}
                    </td>
                    <td className="px-4 py-2">
                      <Badge status={w.status}>{w.status.replaceAll("_", " ")}</Badge>
                    </td>
                    <td className="px-4 py-2">
                      {w.status !== "CLAIMED" && (
                        <button
                          disabled={checkInBusy === w.id}
                          onClick={() => handleCheckIn(w.id)}
                          className="text-xs underline text-muted hover:text-foreground disabled:opacity-50"
                        >
                          {checkInBusy === w.id ? "Checking in..." : "Check in"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/60 backdrop-blur-sm px-4">
          <form
            onSubmit={handleCreate}
            className="w-full max-w-md space-y-3 rounded-lg border border-border bg-surface p-6"
          >
            <h2 className="font-display text-xl text-foreground">New warranty</h2>

            {selectedCustomer ? (
              <div className="flex items-center justify-between rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm">
                <span>{selectedCustomer.name}</span>
                <button type="button" onClick={() => setSelectedCustomer(null)} className="text-xs text-muted underline">
                  Change
                </button>
              </div>
            ) : (
              <div>
                <div className="flex gap-2">
                  <input
                    placeholder="Search customer by name..."
                    value={customerQuery}
                    onChange={(e) => setCustomerQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleSearchCustomer();
                      }
                    }}
                    className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
                  />
                  <button
                    type="button"
                    onClick={handleSearchCustomer}
                    className="rounded-md border border-border-strong px-3 py-2 text-sm hover:bg-surface-muted"
                  >
                    Search
                  </button>
                </div>
                {customerResults.length > 0 && (
                  <div className="mt-1 rounded-md border border-border-strong bg-surface">
                    {customerResults.map((c) => (
                      <button
                        type="button"
                        key={c.id}
                        onClick={() => {
                          setSelectedCustomer(c);
                          setCustomerResults([]);
                        }}
                        className="block w-full px-3 py-2 text-left text-sm hover:bg-surface-muted"
                      >
                        {c.name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            <input
              required
              placeholder="Item (e.g. HVAC unit, Water heater)"
              value={itemDescription}
              onChange={(e) => setItemDescription(e.target.value)}
              className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
            />
            <div>
              <label className="block text-xs text-muted">Start date</label>
              <input
                type="date"
                required
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-muted">Expiry date</label>
              <input
                type="date"
                required
                value={expiryDate}
                onChange={(e) => setExpiryDate(e.target.value)}
                className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
              />
            </div>
            {error && <p className="text-sm text-danger">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowCreate(false)} className="rounded-md px-3 py-1.5 text-sm text-muted">
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting || !selectedCustomer}
                className="klaros-btn-primary disabled:opacity-50"
              >
                {submitting ? "Creating..." : "Create"}
              </button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
