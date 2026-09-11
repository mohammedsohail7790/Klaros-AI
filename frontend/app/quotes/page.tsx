"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, Customer, Quote, QuoteLineItemInput, createQuoteDraft, listQuotes, searchCustomers } from "@/lib/api";

const STATUS_TABS = ["ALL", "DRAFT", "SENT", "VIEWED", "ACCEPTED", "DECLINED", "EXPIRED", "CONVERTED"];

export default function QuotesPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState("ALL");
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listQuotes(token, status === "ALL" ? {} : { status_filter: status });
      setQuotes(result.quotes);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load quotes.");
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
        <header className="mb-6 flex items-center justify-between">
          <h1 className="text-xl font-semibold">Quotes</h1>
          <button
            onClick={() => setShowCreate(true)}
            className="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-black hover:bg-neutral-200"
          >
            New quote
          </button>
        </header>

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
        ) : quotes.length === 0 ? (
          <p className="text-sm text-neutral-500">No quotes.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-neutral-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-neutral-950 text-neutral-500">
                <tr>
                  <th className="px-4 py-2">Number</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Valid until</th>
                  <th className="px-4 py-2">Total</th>
                </tr>
              </thead>
              <tbody>
                {quotes.map((q) => (
                  <tr key={q.id} className="border-t border-neutral-900">
                    <td className="px-4 py-2">
                      <Link href={`/quotes/${q.id}`} className="underline hover:text-white">
                        {q.quote_number}
                      </Link>
                    </td>
                    <td className="px-4 py-2">
                      <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">{q.status}</span>
                    </td>
                    <td className="px-4 py-2 text-neutral-500">{q.valid_until ?? "—"}</td>
                    <td className="px-4 py-2">${q.total}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate && token && (
        <CreateQuoteModal token={token} onClose={() => setShowCreate(false)} />
      )}
    </AppShell>
  );
}

function emptyLineItem(): QuoteLineItemInput {
  return { description: "", quantity: "1", unit_price: "0" };
}

function CreateQuoteModal({ token, onClose }: { token: string; onClose: () => void }) {
  const router = useRouter();
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerResults, setCustomerResults] = useState<Customer[]>([]);
  const [customerId, setCustomerId] = useState("");
  const [customerLabel, setCustomerLabel] = useState("");
  const [lineItems, setLineItems] = useState<QuoteLineItemInput[]>([emptyLineItem()]);
  const [notes, setNotes] = useState("");
  const [terms, setTerms] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!customerQuery) {
      setCustomerResults([]);
      return;
    }
    const handle = setTimeout(() => {
      searchCustomers(token, { q: customerQuery, limit: 5 })
        .then((r) => setCustomerResults(r.customers))
        .catch(() => setCustomerResults([]));
    }, 300);
    return () => clearTimeout(handle);
  }, [customerQuery, token]);

  function updateLineItem(index: number, patch: Partial<QuoteLineItemInput>) {
    setLineItems((items) => items.map((it, i) => (i === index ? { ...it, ...patch } : it)));
  }

  function removeLineItem(index: number) {
    setLineItems((items) => items.filter((_, i) => i !== index));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!customerId) {
      setError("Select a customer first.");
      return;
    }
    const items = lineItems.filter((it) => it.description.trim());
    if (items.length === 0) {
      setError("Add at least one line item.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const { quote } = await createQuoteDraft(token, {
        customer_id: customerId,
        line_items: items,
        notes: notes || undefined,
        terms: terms || undefined,
      });
      router.push(`/quotes/${quote.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create quote.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 flex items-center justify-center overflow-y-auto bg-black/60 px-4 py-8">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-lg space-y-3 rounded-lg border border-neutral-800 bg-neutral-950 p-6"
      >
        <h2 className="text-lg font-semibold">New quote</h2>

        <div>
          <input
            placeholder="Search customer by name/email"
            value={customerLabel || customerQuery}
            onChange={(e) => {
              setCustomerLabel("");
              setCustomerQuery(e.target.value);
              setCustomerId("");
            }}
            className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm"
          />
          {customerResults.length > 0 && (
            <ul className="mt-1 rounded-md border border-neutral-800 bg-neutral-900 text-sm">
              {customerResults.map((c) => (
                <li
                  key={c.id}
                  onClick={() => {
                    setCustomerId(c.id);
                    setCustomerLabel(c.name);
                    setCustomerQuery("");
                    setCustomerResults([]);
                  }}
                  className={`cursor-pointer px-3 py-2 hover:bg-neutral-800 ${
                    customerId === c.id ? "bg-neutral-800" : ""
                  }`}
                >
                  {c.name} {c.email && `(${c.email})`}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="space-y-2">
          <p className="text-xs text-neutral-500">Line items</p>
          {lineItems.map((item, i) => (
            <div key={i} className="flex gap-2">
              <input
                placeholder="Description"
                value={item.description}
                onChange={(e) => updateLineItem(i, { description: e.target.value })}
                className="flex-1 rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm"
              />
              <input
                placeholder="Qty"
                value={item.quantity}
                onChange={(e) => updateLineItem(i, { quantity: e.target.value })}
                className="w-16 rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm"
              />
              <input
                placeholder="Unit price"
                value={item.unit_price}
                onChange={(e) => updateLineItem(i, { unit_price: e.target.value })}
                className="w-24 rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm"
              />
              {lineItems.length > 1 && (
                <button
                  type="button"
                  onClick={() => removeLineItem(i)}
                  className="px-2 text-sm text-red-400 hover:text-red-300"
                >
                  ✕
                </button>
              )}
            </div>
          ))}
          <button
            type="button"
            onClick={() => setLineItems((items) => [...items, emptyLineItem()])}
            className="text-xs text-neutral-400 underline hover:text-white"
          >
            + Add line item
          </button>
        </div>

        <textarea
          placeholder="Notes (optional)"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm"
        />
        <textarea
          placeholder="Terms (optional)"
          value={terms}
          onChange={(e) => setTerms(e.target.value)}
          rows={2}
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
            {submitting ? "Creating..." : "Create quote"}
          </button>
        </div>
      </form>
    </div>
  );
}
