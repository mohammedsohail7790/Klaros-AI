"use client";

import { useCallback, useEffect, useState } from "react";
import { Truck } from "lucide-react";
import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/ui/EmptyState";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { useAuth } from "@/lib/useAuth";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  Vendor,
  VendorBill,
  createVendor,
  listVendorBills,
  listVendors,
  recordVendorBill,
  recordVendorPayout,
} from "@/lib/api";

export default function VendorsPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [bills, setBills] = useState<VendorBill[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [billVendorId, setBillVendorId] = useState<string | null>(null);
  const [billAmount, setBillAmount] = useState("");
  const [billDueDate, setBillDueDate] = useState("");
  const [billBusy, setBillBusy] = useState(false);

  const [payoutBusy, setPayoutBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [vendorsResult, billsResult] = await Promise.all([listVendors(token), listVendorBills(token)]);
      setVendors(vendorsResult.vendors);
      setBills(billsResult.vendor_bills);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load vendors.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await createVendor(token, { name: name.trim(), email: email || undefined, phone: phone || undefined });
      setName("");
      setEmail("");
      setPhone("");
      setShowCreate(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create vendor.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRecordBill(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !billVendorId || !billAmount || !billDueDate) return;
    setBillBusy(true);
    setError(null);
    try {
      await recordVendorBill(token, { vendor_id: billVendorId, amount: billAmount, due_date: billDueDate });
      setBillVendorId(null);
      setBillAmount("");
      setBillDueDate("");
      toast.success("Bill recorded.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to record bill.");
    } finally {
      setBillBusy(false);
    }
  }

  async function handlePayout(bill: VendorBill) {
    if (!token) return;
    setPayoutBusy(bill.id);
    setError(null);
    try {
      const result = await recordVendorPayout(token, bill.id, bill.vendor_id);
      toast.success(
        result.status === "pending_approval"
          ? "Payout requires approval — a request was created and nothing has been paid yet."
          : "Payout recorded."
      );
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to record payout.");
    } finally {
      setPayoutBusy(null);
    }
  }

  const vendorName = (id: string) => vendors.find((v) => v.id === id)?.name ?? "—";

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <header className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="font-display text-2xl text-foreground">Vendors</h1>
            <p className="mt-1 text-sm text-muted">
              Subcontractors and suppliers — a bill linked to a job feeds real job costing automatically.
            </p>
          </div>
          <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
            New vendor
          </button>
        </header>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
        )}

        {authLoading || loading ? (
          <Skeleton />
        ) : vendors.length === 0 ? (
          <EmptyState
            icon={Truck}
            title="No vendors yet."
            action={
              <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
                New vendor
              </button>
            }
          />
        ) : (
          <>
            <h2 className="mb-3 font-medium">Vendors</h2>
            <div className="mb-8 klaros-table-wrap">
              <table className="klaros-table">
                <thead className="bg-surface text-muted">
                  <tr>
                    <th className="px-4 py-2">Name</th>
                    <th className="px-4 py-2">Email</th>
                    <th className="px-4 py-2">Phone</th>
                    <th className="px-4 py-2">Status</th>
                    <th className="px-4 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {vendors.map((v) => (
                    <tr key={v.id} className="border-t border-border">
                      <td className="px-4 py-2">{v.name}</td>
                      <td className="px-4 py-2 text-muted">{v.email ?? "—"}</td>
                      <td className="px-4 py-2 text-muted">{v.phone ?? "—"}</td>
                      <td className="px-4 py-2">
                        <Badge status={v.status}>{v.status}</Badge>
                      </td>
                      <td className="px-4 py-2">
                        <button
                          onClick={() => setBillVendorId(billVendorId === v.id ? null : v.id)}
                          className="text-xs underline text-muted hover:text-foreground"
                        >
                          Record bill
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {billVendorId && (
              <form
                onSubmit={handleRecordBill}
                className="mb-8 flex flex-wrap items-end gap-2 rounded-lg border border-border p-4"
              >
                <div>
                  <label className="block text-xs text-muted">Vendor</label>
                  <div className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm">
                    {vendorName(billVendorId)}
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-muted">Amount</label>
                  <input
                    value={billAmount}
                    onChange={(e) => setBillAmount(e.target.value)}
                    placeholder="0.00"
                    className="w-28 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs text-muted">Due date</label>
                  <input
                    type="date"
                    value={billDueDate}
                    onChange={(e) => setBillDueDate(e.target.value)}
                    className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                  />
                </div>
                <button
                  type="submit"
                  disabled={billBusy || !billAmount || !billDueDate}
                  className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                >
                  {billBusy ? "Recording..." : "Record bill"}
                </button>
                <button
                  type="button"
                  onClick={() => setBillVendorId(null)}
                  className="text-sm text-muted hover:underline"
                >
                  Cancel
                </button>
              </form>
            )}

            <h2 className="mb-3 font-medium">Bills</h2>
            {bills.length === 0 ? (
              <p className="text-sm text-muted">No vendor bills recorded yet.</p>
            ) : (
              <div className="klaros-table-wrap">
                <table className="klaros-table">
                  <thead className="bg-surface text-muted">
                    <tr>
                      <th className="px-4 py-2">Vendor</th>
                      <th className="px-4 py-2">Amount</th>
                      <th className="px-4 py-2">Due date</th>
                      <th className="px-4 py-2">Status</th>
                      <th className="px-4 py-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {bills.map((b) => (
                      <tr key={b.id} className="border-t border-border">
                        <td className="px-4 py-2">{vendorName(b.vendor_id)}</td>
                        <td className="px-4 py-2">${b.amount}</td>
                        <td className="px-4 py-2 text-muted">{b.due_date}</td>
                        <td className="px-4 py-2">
                          <Badge status={b.status}>{b.status}</Badge>
                        </td>
                        <td className="px-4 py-2">
                          {b.status !== "PAID" && (
                            <button
                              onClick={() => handlePayout(b)}
                              disabled={payoutBusy === b.id}
                              className="text-xs underline text-muted hover:text-foreground disabled:opacity-50"
                            >
                              {payoutBusy === b.id ? "Working..." : "Pay"}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>

      {showCreate && (
        <Modal title="New vendor" onClose={() => setShowCreate(false)}>
          <form onSubmit={handleCreate} className="space-y-3">
            <input
              required
              placeholder="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="klaros-input"
            />
            <input
              placeholder="Email (optional)"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="klaros-input"
            />
            <input
              placeholder="Phone (optional)"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              className="klaros-input"
            />
            {error && <p className="text-sm text-danger">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowCreate(false)} className="klaros-btn-secondary">
                Cancel
              </button>
              <button type="submit" disabled={submitting} className="klaros-btn-primary">
                {submitting ? "Creating..." : "Create vendor"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </AppShell>
  );
}
