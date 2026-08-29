"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, Invoice, listInvoices } from "@/lib/api";

const STATUS_TABS = ["ALL", "DRAFT", "PENDING_APPROVAL", "APPROVED", "SENT", "PARTIALLY_PAID", "PAID", "OVERDUE", "VOID"];

export default function InvoicesPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState("ALL");
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listInvoices(token, status === "ALL" ? {} : { status });
      setInvoices(result.invoices);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load invoices.");
    } finally {
      setLoading(false);
    }
  }, [token, status]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="mb-6 text-xl font-semibold">Invoices</h1>

        <div className="mb-4 flex flex-wrap gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`rounded-full border px-3 py-1 text-xs ${
                status === s ? "border-white bg-white text-black" : "border-neutral-700 text-neutral-400"
              }`}
            >
              {s}
            </button>
          ))}
        </div>

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : invoices.length === 0 ? (
          <p className="text-sm text-neutral-500">No invoices.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-neutral-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-neutral-950 text-neutral-500">
                <tr>
                  <th className="px-4 py-2">Number</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Due date</th>
                  <th className="px-4 py-2">Total</th>
                  <th className="px-4 py-2">Amount due</th>
                </tr>
              </thead>
              <tbody>
                {invoices.map((inv) => (
                  <tr key={inv.id} className="border-t border-neutral-900">
                    <td className="px-4 py-2">
                      <Link href={`/finance/invoices/${inv.id}`} className="underline hover:text-white">
                        {inv.invoice_number}
                      </Link>
                    </td>
                    <td className="px-4 py-2">
                      <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">{inv.status}</span>
                    </td>
                    <td className="px-4 py-2 text-neutral-500">{inv.due_date}</td>
                    <td className="px-4 py-2">${inv.total}</td>
                    <td className="px-4 py-2 text-neutral-400">${inv.amount_due}</td>
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
