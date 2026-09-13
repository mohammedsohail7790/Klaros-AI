"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Contract,
  Quote,
  QuoteLineItem,
  QuoteLineItemInput,
  createStaffQuoteDepositCheckout,
  getQuote,
  listContracts,
  sendQuote,
  updateQuoteDraft,
} from "@/lib/api";

function toDraftLineItems(items: QuoteLineItem[]): QuoteLineItemInput[] {
  return items.map((li) => ({
    description: li.description,
    quantity: li.quantity,
    unit_price: li.unit_price,
    discount: li.discount,
    tax_rate: li.tax_rate,
  }));
}

export default function QuoteDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { token, user, loading: authLoading } = useAuth();
  const [quote, setQuote] = useState<(Quote & { line_items: QuoteLineItem[] }) | null>(null);
  const [contract, setContract] = useState<Contract | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [viewUrlPath, setViewUrlPath] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draftItems, setDraftItems] = useState<QuoteLineItemInput[]>([]);
  const [draftNotes, setDraftNotes] = useState("");
  const [draftTerms, setDraftTerms] = useState("");
  const [saving, setSaving] = useState(false);
  const [depositLink, setDepositLink] = useState<string | null>(null);
  const [depositBusy, setDepositBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token || !id) return;
    setLoading(true);
    setError(null);
    try {
      const loadedQuote = await getQuote(token, id);
      setQuote(loadedQuote);
      // The Contract auto-creates only once the quote is ACCEPTED or later
      // (see the QUOTE_ACCEPTED subscriber in app/events/finance_handlers.py)
      // -- a quote still DRAFT/SENT/VIEWED genuinely has no contract yet;
      // that's not an error, just "not created yet".
      const contractsResult = await listContracts(token, { quote_id: id });
      setContract(contractsResult.contracts[0] ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load quote.");
    } finally {
      setLoading(false);
    }
  }, [token, id]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSend() {
    if (!token || !id) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await sendQuote(token, id);
      setViewUrlPath(result.view_url_path);
      setNotice("Quote sent — the customer link below is real and ready to share.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to send quote.");
    } finally {
      setBusy(false);
    }
  }

  async function handleGenerateDepositLink() {
    if (!token || !id) return;
    setDepositBusy(true);
    setError(null);
    try {
      const origin = typeof window !== "undefined" ? window.location.origin : "";
      const result = await createStaffQuoteDepositCheckout(token, id, {
        success_url: `${origin}/quotes/${id}`,
        cancel_url: `${origin}/quotes/${id}`,
      });
      setDepositLink(result.checkout_url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to generate a deposit payment link.");
    } finally {
      setDepositBusy(false);
    }
  }

  function startEditing() {
    if (!quote) return;
    setDraftItems(toDraftLineItems(quote.line_items));
    setDraftNotes(quote.notes ?? "");
    setDraftTerms(quote.terms ?? "");
    setEditing(true);
  }

  function updateDraftItem(index: number, field: keyof QuoteLineItemInput, value: string) {
    setDraftItems((prev) => prev.map((li, i) => (i === index ? { ...li, [field]: value } : li)));
  }

  function addDraftItem() {
    setDraftItems((prev) => [...prev, { description: "", quantity: "1", unit_price: "0" }]);
  }

  function removeDraftItem(index: number) {
    setDraftItems((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleSaveDraft() {
    if (!token || !id) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      await updateQuoteDraft(token, id, {
        line_items: draftItems,
        notes: draftNotes || undefined,
        terms: draftTerms || undefined,
      });
      setNotice("Quote draft updated.");
      setEditing(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save quote draft.");
    } finally {
      setSaving(false);
    }
  }

  if (authLoading || loading) {
    return (
      <AppShell user={user}>
        <div className="px-8 py-8 text-sm text-muted">Loading...</div>
      </AppShell>
    );
  }

  if (error && !quote) {
    return (
      <AppShell user={user}>
        <div className="px-8 py-8">
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">{error}</div>
        </div>
      </AppShell>
    );
  }

  if (!quote) return null;

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Quote {quote.quote_number}</h1>
          <span className="rounded-full border border-border-strong px-3 py-1 text-xs">{quote.status}</span>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50/30 p-3 text-sm text-red-700">{error}</div>
        )}
        {notice && (
          <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50/30 p-3 text-sm text-emerald-700">
            {notice}
          </div>
        )}
        {viewUrlPath && (
          <div className="mb-4 rounded-md border border-border bg-surface p-3 text-sm text-muted">
            Customer view link:{" "}
            <code className="break-all text-muted">
              {(typeof window !== "undefined" ? window.location.origin : "") + viewUrlPath}
            </code>
          </div>
        )}

        <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <div className="text-xs text-muted">Total</div>
            <div className="mt-1 text-lg font-semibold">${quote.total}</div>
          </div>
          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <div className="text-xs text-muted">Valid until</div>
            <div className="mt-1 text-lg font-semibold">{quote.valid_until ?? "—"}</div>
          </div>
          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <div className="text-xs text-muted">Sent</div>
            <div className="mt-1 text-lg font-semibold">{quote.sent_at ? "Yes" : "No"}</div>
          </div>
          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <div className="text-xs text-muted">Decided</div>
            <div className="mt-1 text-lg font-semibold">{quote.decided_at ? "Yes" : "No"}</div>
          </div>
        </div>

        {(quote.status === "ACCEPTED" ||
          quote.status === "DEPOSIT_PENDING" ||
          quote.status === "DEPOSIT_PAID" ||
          quote.status === "CONVERTED") && (
          <div className="mb-6 rounded-md border border-border bg-surface p-4 text-sm">
            {contract ? (
              <>
                <span className="text-muted">
                  Contract {contract.contract_number}:{" "}
                  {contract.status === "SIGNED" ? (
                    <span className="text-emerald-600">Signed by {contract.signer_name}</span>
                  ) : contract.status === "DECLINED" ? (
                    <span className="text-red-600">Declined</span>
                  ) : contract.status === "DRAFT" ? (
                    <span className="text-muted">Draft — not yet sent</span>
                  ) : (
                    <span className="text-amber-700">Awaiting signature ({contract.status.toLowerCase()})</span>
                  )}
                </span>{" "}
                <Link href={`/contracts/${contract.id}`} className="underline hover:text-foreground">
                  Review contract
                </Link>
              </>
            ) : (
              <span className="text-muted">Contract pending creation.</span>
            )}
          </div>
        )}

        {quote.deposit_type && (quote.status === "DEPOSIT_PENDING" || quote.status === "DEPOSIT_PAID") && (
          <div className="mb-6 rounded-md border border-border bg-surface p-4 text-sm">
            <div className="mb-2 text-muted">
              Deposit: <span className="text-foreground">${quote.deposit_amount ?? "—"}</span>{" "}
              {quote.status === "DEPOSIT_PAID" ? (
                <span className="text-emerald-600">Paid</span>
              ) : (
                <span className="text-amber-700">Pending</span>
              )}
            </div>
            {quote.status === "DEPOSIT_PENDING" && (
              <>
                <button
                  disabled={depositBusy}
                  onClick={handleGenerateDepositLink}
                  className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                >
                  {depositBusy ? "Generating..." : "Generate deposit payment link"}
                </button>
                {depositLink && (
                  <div className="mt-2 text-xs text-muted">
                    Payment link (share or read out to the customer):{" "}
                    <code className="break-all text-muted">{depositLink}</code>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {quote.decline_reason && (
          <div className="mb-6 rounded-md border border-border bg-surface p-3 text-sm text-muted">
            Decline reason: {quote.decline_reason}
          </div>
        )}

        {editing ? (
          <div className="mb-6 space-y-3">
            <div className="klaros-table-wrap">
              <table className="klaros-table">
                <thead className="bg-surface text-muted">
                  <tr>
                    <th className="px-4 py-2">Description</th>
                    <th className="px-4 py-2">Qty</th>
                    <th className="px-4 py-2">Unit price</th>
                    <th className="px-4 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {draftItems.map((li, i) => (
                    <tr key={i} className="border-t border-border">
                      <td className="px-4 py-2">
                        <input
                          value={li.description}
                          onChange={(e) => updateDraftItem(i, "description", e.target.value)}
                          className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                        />
                      </td>
                      <td className="px-4 py-2">
                        <input
                          value={li.quantity}
                          onChange={(e) => updateDraftItem(i, "quantity", e.target.value)}
                          className="w-20 rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                        />
                      </td>
                      <td className="px-4 py-2">
                        <input
                          value={li.unit_price}
                          onChange={(e) => updateDraftItem(i, "unit_price", e.target.value)}
                          className="w-24 rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                        />
                      </td>
                      <td className="px-4 py-2">
                        <button
                          onClick={() => removeDraftItem(i)}
                          className="text-xs text-red-600 hover:underline"
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button
              onClick={addDraftItem}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
            >
              Add line item
            </button>

            <div>
              <label className="block text-xs text-muted">Notes</label>
              <textarea
                value={draftNotes}
                onChange={(e) => setDraftNotes(e.target.value)}
                rows={2}
                className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-muted">Terms</label>
              <textarea
                value={draftTerms}
                onChange={(e) => setDraftTerms(e.target.value)}
                rows={2}
                className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
              />
            </div>

            <div className="flex gap-2">
              <button
                onClick={handleSaveDraft}
                disabled={saving || draftItems.length === 0}
                className="klaros-btn-primary disabled:opacity-50"
              >
                {saving ? "Saving..." : "Save draft"}
              </button>
              <button
                onClick={() => setEditing(false)}
                className="rounded-md px-3 py-1.5 text-sm text-muted hover:underline"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="mb-6 klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Description</th>
                  <th className="px-4 py-2">Qty</th>
                  <th className="px-4 py-2">Unit price</th>
                  <th className="px-4 py-2">Line total</th>
                </tr>
              </thead>
              <tbody>
                {quote.line_items.map((li) => (
                  <tr key={li.id} className="border-t border-border">
                    <td className="px-4 py-2">{li.description}</td>
                    <td className="px-4 py-2">{li.quantity}</td>
                    <td className="px-4 py-2">${li.unit_price}</td>
                    <td className="px-4 py-2">${li.line_total}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          {quote.status === "DRAFT" && !editing && (
            <>
              <button
                onClick={startEditing}
                className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
              >
                Edit line items
              </button>
              <button
                disabled={busy}
                onClick={handleSend}
                className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
              >
                Send to customer
              </button>
            </>
          )}
        </div>
      </div>
    </AppShell>
  );
}
