"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FileText } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Customer,
  Quote,
  QuoteLineItemInput,
  createQuoteDraft,
  detectExpiredQuotes,
  listQuotes,
  searchCustomers,
} from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
const STATUS_TABS = ["ALL", "DRAFT", "SENT", "VIEWED", "ACCEPTED", "DECLINED", "EXPIRED", "CONVERTED"];

export default function QuotesPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState("ALL");
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

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

  async function handleDetectExpired() {
    if (!token) return;
    setDetecting(true);
    setNotice(null);
    setError(null);
    try {
      const result = await detectExpiredQuotes(token);
      setNotice(
        result.expired_quote_ids.length === 0
          ? "No newly expired quotes found."
          : `${result.expired_quote_ids.length} quote(s) marked EXPIRED.`
      );
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to detect expired quotes.");
    } finally {
      setDetecting(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <header className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Quotes</h1>
          <div className="flex gap-2">
            <button
              disabled={detecting}
              onClick={handleDetectExpired}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {detecting ? "Checking..." : "Detect expired quotes"}
            </button>
            <button
              onClick={() => setShowCreate(true)}
              className="klaros-btn-primary"
            >
              New quote
            </button>
          </div>
        </header>

        {notice && (
          <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50/30 p-3 text-sm text-emerald-700">
            {notice}
          </div>
        )}

        <div className="mb-4 flex flex-wrap gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`rounded-full border px-3 py-1 text-xs ${
                status === s ? "border-foreground bg-surface text-foreground" : "border-border-strong text-muted"
              }`}
            >
              {s}
            </button>
          ))}
        </div>

        {authLoading || loading ? (
          <p className="text-sm text-muted">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : quotes.length === 0 ? (
          <EmptyState icon={FileText} title="No quotes." />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Number</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Valid until</th>
                  <th className="px-4 py-2">Total</th>
                </tr>
              </thead>
              <tbody>
                {quotes.map((q) => (
                  <tr key={q.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <Link href={`/quotes/${q.id}`} className="underline hover:text-foreground">
                        {q.quote_number}
                      </Link>
                    </td>
                    <td className="px-4 py-2">
                      <Badge status={q.status}>{q.status}</Badge>
                    </td>
                    <td className="px-4 py-2 text-muted">{q.valid_until ?? "—"}</td>
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
  const [depositType, setDepositType] = useState<"" | "FIXED" | "PERCENTAGE">("");
  const [depositValue, setDepositValue] = useState("");
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
    if (depositType && !depositValue.trim()) {
      setError("Enter a deposit value or leave the deposit type unset.");
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
        deposit_type: depositType || undefined,
        deposit_value: depositType ? depositValue.trim() : undefined,
      });
      router.push(`/quotes/${quote.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create quote.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 flex items-center justify-center overflow-y-auto bg-background/60 px-4 py-8">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-lg space-y-3 rounded-lg border border-border bg-surface p-6"
      >
        <h2 className="font-display text-xl text-foreground">New quote</h2>

        <div>
          <input
            placeholder="Search customer by name/email"
            value={customerLabel || customerQuery}
            onChange={(e) => {
              setCustomerLabel("");
              setCustomerQuery(e.target.value);
              setCustomerId("");
            }}
            className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
          />
          {customerResults.length > 0 && (
            <ul className="mt-1 rounded-md border border-border bg-surface-muted text-sm">
              {customerResults.map((c) => (
                <li
                  key={c.id}
                  onClick={() => {
                    setCustomerId(c.id);
                    setCustomerLabel(c.name);
                    setCustomerQuery("");
                    setCustomerResults([]);
                  }}
                  className={`cursor-pointer px-3 py-2 hover:bg-surface-muted ${
                    customerId === c.id ? "bg-surface-muted" : ""
                  }`}
                >
                  {c.name} {c.email && `(${c.email})`}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="space-y-2">
          <p className="text-xs text-muted">Line items</p>
          {lineItems.map((item, i) => (
            <div key={i} className="flex gap-2">
              <input
                placeholder="Description"
                value={item.description}
                onChange={(e) => updateLineItem(i, { description: e.target.value })}
                className="flex-1 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
              <input
                placeholder="Qty"
                value={item.quantity}
                onChange={(e) => updateLineItem(i, { quantity: e.target.value })}
                className="w-16 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
              <input
                placeholder="Unit price"
                value={item.unit_price}
                onChange={(e) => updateLineItem(i, { unit_price: e.target.value })}
                className="w-24 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
              {lineItems.length > 1 && (
                <button
                  type="button"
                  onClick={() => removeLineItem(i)}
                  className="px-2 text-sm text-red-600 hover:text-red-700"
                >
                  ✕
                </button>
              )}
            </div>
          ))}
          <button
            type="button"
            onClick={() => setLineItems((items) => [...items, emptyLineItem()])}
            className="text-xs text-muted underline hover:text-foreground"
          >
            + Add line item
          </button>
        </div>

        <textarea
          placeholder="Notes (optional)"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
        />
        <textarea
          placeholder="Terms (optional)"
          value={terms}
          onChange={(e) => setTerms(e.target.value)}
          rows={2}
          className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
        />

        <div>
          <label className="mb-1 block text-xs text-muted">Deposit (optional)</label>
          <div className="flex gap-2">
            <select
              value={depositType}
              onChange={(e) => setDepositType(e.target.value as "" | "FIXED" | "PERCENTAGE")}
              className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
            >
              <option value="">No deposit required</option>
              <option value="FIXED">Fixed amount</option>
              <option value="PERCENTAGE">Percentage of total</option>
            </select>
            {depositType && (
              <input
                placeholder={depositType === "PERCENTAGE" ? "e.g. 25" : "e.g. 200"}
                value={depositValue}
                onChange={(e) => setDepositValue(e.target.value)}
                className="w-28 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
            )}
          </div>
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-muted">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-surface px-3 py-1.5 text-sm font-medium text-foreground disabled:opacity-50"
          >
            {submitting ? "Creating..." : "Create quote"}
          </button>
        </div>
      </form>
    </div>
  );
}
