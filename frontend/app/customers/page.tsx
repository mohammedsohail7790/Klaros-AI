"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, Customer, createCustomer, searchCustomers } from "@/lib/api";

export default function CustomersPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await searchCustomers(token, { q: q || undefined, limit: 50 });
      setCustomers(result.customers);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load customers.");
    } finally {
      setLoading(false);
    }
  }, [token, q]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <header className="mb-6 flex items-center justify-between">
          <h1 className="text-xl font-semibold">Customers ({total})</h1>
          <button
            onClick={() => setShowCreate(true)}
            className="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-black hover:bg-neutral-200"
          >
            New customer
          </button>
        </header>

        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name or email..."
          className="mb-4 w-72 rounded-md border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-sm"
        />

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading customers...</p>
        ) : error ? (
          <div className="rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : customers.length === 0 ? (
          <p className="text-sm text-neutral-500">No customers yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-neutral-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-neutral-950 text-neutral-500">
                <tr>
                  <th className="px-4 py-2">Name</th>
                  <th className="px-4 py-2">Email</th>
                  <th className="px-4 py-2">Phone</th>
                  <th className="px-4 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {customers.map((c) => (
                  <tr key={c.id} className="border-t border-neutral-900 hover:bg-neutral-950">
                    <td className="px-4 py-2">
                      <Link href={`/customers/${c.id}`} className="hover:underline">
                        {c.name}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-neutral-400">{c.email ?? "—"}</td>
                    <td className="px-4 py-2 text-neutral-400">{c.phone ?? "—"}</td>
                    <td className="px-4 py-2">
                      <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">
                        {c.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate && token && (
        <CreateCustomerModal token={token} onClose={() => setShowCreate(false)} onCreated={load} />
      )}
    </AppShell>
  );
}

function CreateCustomerModal({
  token,
  onClose,
  onCreated,
}: {
  token: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await createCustomer(token, { name, email: email || undefined, phone: phone || undefined });
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create customer.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-black/60 px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-md space-y-3 rounded-lg border border-neutral-800 bg-neutral-950 p-6"
      >
        <h2 className="text-lg font-semibold">New customer</h2>
        <input
          required
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm"
        />
        <input
          placeholder="Email (optional)"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm"
        />
        <input
          placeholder="Phone (optional)"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm"
        />
        {error && <p className="text-sm text-red-400">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-neutral-400">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-black disabled:opacity-50"
          >
            {submitting ? "Creating..." : "Create customer"}
          </button>
        </div>
      </form>
    </div>
  );
}
