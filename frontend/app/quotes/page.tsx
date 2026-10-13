"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FileText } from "lucide-react";
import AppShell from "@/components/AppShell";
import { StatusSummary } from "@/components/ui/layout";
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
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
const STATUS_TABS = ["ALL", "DRAFT", "SENT", "VIEWED", "ACCEPTED", "DECLINED", "EXPIRED", "CONVERTED"];

export default function QuotesPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState("ALL");
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [detecting, setDetecting] = useState(false);

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
    setError(null);
    try {
      const result = await detectExpiredQuotes(token);
      toast.success(
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
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <header className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-[2rem] font-semibold leading-[1.05] tracking-[-0.03em] text-foreground sm:text-[2.75rem]">Quotes</h1>
          <div className="flex gap-2">
            <button
              disabled={detecting}
              onClick={handleDetectExpired}
              className="klaros-btn-secondary !px-3 !py-1.5 disabled:opacity-50"
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

        <StatusSummary rows={quotes} noun="quotes" />

        <div className="mb-4 flex flex-wrap gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`klaros-filter-tab ${status === s ? "klaros-filter-tab-active" : "klaros-filter-tab-inactive"}`}
            >
              {s === "ALL" ? "All" : s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase())}
            </button>
          ))}
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-xl border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : quotes.length === 0 ? (
          <EmptyState icon={FileText} title="No quotes." />
        ) : (
          <ul className="divide-y divide-border/70 overflow-hidden rounded-3xl bg-surface-muted" aria-label="Quotes">
            {quotes.map((q) => (
              <li key={q.id} className="group relative flex flex-wrap items-center justify-between gap-4 px-6 py-5 transition-colors duration-300 hover:bg-black/[0.03]">
                <div className="min-w-0">
                  <Link href={`/quotes/${q.id}`} className="font-display text-xl font-semibold tracking-tight text-foreground after:absolute after:inset-0 after:content-['']">
                    {q.quote_number}
                  </Link>
                  <p className="mt-0.5 text-[13px] text-muted">Valid until {q.valid_until ?? "—"}</p>
                </div>
                <div className="flex items-center gap-6">
                  <p className="font-display text-xl font-semibold tabular-nums tracking-tight text-foreground">${q.total}</p>
                  <Badge status={q.status}>{q.status.replace(/_/g, " ")}</Badge>
                </div>
              </li>
            ))}
          </ul>
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
    <Modal title="New quote" onClose={onClose} size="lg">
      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <input
            placeholder="Search customer by name/email"
            value={customerLabel || customerQuery}
            onChange={(e) => {
              setCustomerLabel("");
              setCustomerQuery(e.target.value);
              setCustomerId("");
            }}
            className="w-full klaros-input"
          />
          {customerResults.length > 0 && (
            <ul className="mt-1 rounded-2xl bg-surface-muted-muted text-sm">
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
                className="flex-1 klaros-input"
              />
              <input
                placeholder="Qty"
                value={item.quantity}
                onChange={(e) => updateLineItem(i, { quantity: e.target.value })}
                className="w-16 klaros-input"
              />
              <input
                placeholder="Unit price"
                value={item.unit_price}
                onChange={(e) => updateLineItem(i, { unit_price: e.target.value })}
                className="w-24 klaros-input"
              />
              {lineItems.length > 1 && (
                <button
                  type="button"
                  onClick={() => removeLineItem(i)}
                  className="px-2 text-sm text-danger hover:text-danger"
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
          className="w-full klaros-input"
        />
        <textarea
          placeholder="Terms (optional)"
          value={terms}
          onChange={(e) => setTerms(e.target.value)}
          rows={2}
          className="w-full klaros-input"
        />

        <div>
          <label className="mb-1 block text-xs text-muted">Deposit (optional)</label>
          <div className="flex gap-2">
            <select
              value={depositType}
              onChange={(e) => setDepositType(e.target.value as "" | "FIXED" | "PERCENTAGE")}
              className="klaros-input"
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
                className="w-28 klaros-input"
              />
            )}
          </div>
        </div>

        {error && <p className="text-sm text-danger">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="klaros-btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="klaros-btn-primary">
            {submitting ? "Creating..." : "Create quote"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
