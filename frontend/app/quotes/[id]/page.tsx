"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatCard } from "@/components/ui/StatCard";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { useToast } from "@/components/ui/Toast";
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
  const toast = useToast();
  const { id } = useParams<{ id: string }>();
  const { token, user, loading: authLoading } = useAuth();
  const [quote, setQuote] = useState<(Quote & { line_items: QuoteLineItem[] }) | null>(null);
  const [contract, setContract] = useState<Contract | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [viewUrlPath, setViewUrlPath] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draftItems, setDraftItems] = useState<QuoteLineItemInput[]>([]);
  const [draftNotes, setDraftNotes] = useState("");
  const [draftTerms, setDraftTerms] = useState("");
  const [draftDepositType, setDraftDepositType] = useState<"" | "FIXED" | "PERCENTAGE">("");
  const [draftDepositValue, setDraftDepositValue] = useState("");
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
    try {
      const result = await sendQuote(token, id);
      setViewUrlPath(result.view_url_path);
      toast.success("Quote sent — the customer link below is real and ready to share.");
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
    setDraftDepositType((quote.deposit_type as "FIXED" | "PERCENTAGE" | null) ?? "");
    setDraftDepositValue(quote.deposit_value ?? "");
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
    try {
      const hadDeposit = quote?.deposit_type != null;
      await updateQuoteDraft(token, id, {
        line_items: draftItems,
        notes: draftNotes || undefined,
        terms: draftTerms || undefined,
        deposit_type: draftDepositType || undefined,
        deposit_value: draftDepositType ? draftDepositValue.trim() : undefined,
        clear_deposit: hadDeposit && !draftDepositType,
      });
      toast.success("Quote draft updated.");
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
        <Skeleton />
      </AppShell>
    );
  }

  if (error && !quote) {
    return (
      <AppShell user={user}>
        <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
          <Alert variant="danger">{error}</Alert>
        </div>
      </AppShell>
    );
  }

  if (!quote) return null;

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <div className="mb-10 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="mb-2 text-[15px] font-medium text-muted-foreground">Quote</p>
            <h1 className="font-display text-[2.25rem] font-semibold leading-[1.05] tracking-[-0.03em] text-foreground sm:text-[3rem]">{quote.quote_number}</h1>
          </div>
          <Badge status={quote.status}>{quote.status.replace(/_/g, " ")}</Badge>
        </div>

        {error && <Alert variant="danger" className="mb-4">{error}</Alert>}
        {viewUrlPath && (
          <div className="mb-4 rounded-2xl bg-surface-muted p-3 text-sm text-muted">
            Customer view link:{" "}
            <code className="break-all text-muted">
              {(typeof window !== "undefined" ? window.location.origin : "") + viewUrlPath}
            </code>
          </div>
        )}

        <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
          <StatCard label="Total" value={`$${quote.total}`} tone="accent" />
          <StatCard label="Valid until" value={quote.valid_until ?? "—"} compact />
          <StatCard label="Sent" value={quote.sent_at ? "Yes" : "No"} tone={quote.sent_at ? "success" : "neutral"} />
          <StatCard label="Decided" value={quote.decided_at ? "Yes" : "No"} tone={quote.decided_at ? "success" : "neutral"} />
        </div>

        {(quote.status === "ACCEPTED" ||
          quote.status === "DEPOSIT_PENDING" ||
          quote.status === "DEPOSIT_PAID" ||
          quote.status === "CONVERTED") && (
          <div className="mb-6 rounded-2xl bg-surface-muted p-4 text-sm">
            {contract ? (
              <>
                <span className="text-muted">
                  Contract {contract.contract_number}:{" "}
                  {contract.status === "SIGNED" ? (
                    <span className="text-success">Signed by {contract.signer_name}</span>
                  ) : contract.status === "DECLINED" ? (
                    <span className="text-danger">Declined</span>
                  ) : contract.status === "DRAFT" ? (
                    <span className="text-muted">Draft — not yet sent</span>
                  ) : (
                    <span className="text-warning">Awaiting signature ({contract.status.toLowerCase()})</span>
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
          <div className="mb-6 rounded-2xl bg-surface-muted p-4 text-sm">
            <div className="mb-2 text-muted">
              Deposit: <span className="text-foreground">${quote.deposit_amount ?? "—"}</span>{" "}
              {quote.status === "DEPOSIT_PAID" ? (
                <span className="text-success">Paid</span>
              ) : (
                <span className="text-warning">Pending</span>
              )}
            </div>
            {quote.status === "DEPOSIT_PENDING" && (
              <>
                <button
                  disabled={depositBusy}
                  onClick={handleGenerateDepositLink}
                  className="klaros-btn-secondary !px-3 !py-1.5 disabled:opacity-50"
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
          <div className="mb-6 rounded-2xl bg-surface-muted p-3 text-sm text-muted">
            Decline reason: {quote.decline_reason}
          </div>
        )}

        {editing ? (
          <div className="mb-6 space-y-3">
            <div className="klaros-table-wrap">
              <table className="klaros-table">
                <thead>
                  <tr>
                    <th className="px-5 py-4">Description</th>
                    <th className="px-5 py-4">Qty</th>
                    <th className="px-5 py-4">Unit price</th>
                    <th className="px-5 py-4"></th>
                  </tr>
                </thead>
                <tbody>
                  {draftItems.map((li, i) => (
                    <tr key={i} className="border-t border-border">
                      <td className="px-5 py-4">
                        <input
                          value={li.description}
                          onChange={(e) => updateDraftItem(i, "description", e.target.value)}
                          className="w-full rounded-xl border border-border-strong bg-surface px-3 py-1.5 text-sm"
                        />
                      </td>
                      <td className="px-5 py-4">
                        <input
                          value={li.quantity}
                          onChange={(e) => updateDraftItem(i, "quantity", e.target.value)}
                          className="w-20 rounded-xl border border-border-strong bg-surface px-3 py-1.5 text-sm"
                        />
                      </td>
                      <td className="px-5 py-4">
                        <input
                          value={li.unit_price}
                          onChange={(e) => updateDraftItem(i, "unit_price", e.target.value)}
                          className="w-24 rounded-xl border border-border-strong bg-surface px-3 py-1.5 text-sm"
                        />
                      </td>
                      <td className="px-5 py-4">
                        <button
                          onClick={() => removeDraftItem(i)}
                          className="text-xs text-danger hover:underline"
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
              className="klaros-btn-secondary !px-3 !py-1.5"
            >
              Add line item
            </button>

            <div>
              <label className="block text-xs text-muted">Notes</label>
              <textarea
                value={draftNotes}
                onChange={(e) => setDraftNotes(e.target.value)}
                rows={2}
                className="w-full rounded-xl border border-border-strong bg-surface px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-muted">Terms</label>
              <textarea
                value={draftTerms}
                onChange={(e) => setDraftTerms(e.target.value)}
                rows={2}
                className="w-full rounded-xl border border-border-strong bg-surface px-3 py-2 text-sm"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs text-muted">Deposit</label>
              <div className="flex gap-2">
                <select
                  value={draftDepositType}
                  onChange={(e) => setDraftDepositType(e.target.value as "" | "FIXED" | "PERCENTAGE")}
                  className="rounded-xl border border-border-strong bg-surface px-3 py-2 text-sm"
                >
                  <option value="">No deposit required</option>
                  <option value="FIXED">Fixed amount</option>
                  <option value="PERCENTAGE">Percentage of total</option>
                </select>
                {draftDepositType && (
                  <input
                    placeholder={draftDepositType === "PERCENTAGE" ? "e.g. 25" : "e.g. 200"}
                    value={draftDepositValue}
                    onChange={(e) => setDraftDepositValue(e.target.value)}
                    className="w-28 rounded-xl border border-border-strong bg-surface px-3 py-2 text-sm"
                  />
                )}
              </div>
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
                className="rounded-xl px-3 py-1.5 text-sm text-muted hover:underline"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <section aria-label="Line items" className="mb-8 rounded-3xl bg-surface-muted p-6 sm:p-8">
            <h2 className="mb-4 font-display text-[1.35rem] font-semibold tracking-[-0.02em] text-foreground">Line items</h2>
            <ul className="divide-y divide-border/70">
              {quote.line_items.map((li) => (
                <li key={li.id} className="flex items-baseline justify-between gap-4 py-3.5">
                  <div className="min-w-0">
                    <p className="text-[16px] font-medium text-foreground">{li.description}</p>
                    <p className="text-[13px] text-muted">{li.quantity} × ${li.unit_price}</p>
                  </div>
                  <p className="shrink-0 font-display text-lg font-semibold tabular-nums text-foreground">${li.line_total}</p>
                </li>
              ))}
            </ul>
            <div className="mt-4 flex items-baseline justify-between border-t border-border pt-4">
              <p className="text-[15px] font-medium text-muted">Total</p>
              <p className="font-display text-2xl font-semibold tabular-nums tracking-tight text-foreground">${quote.total}</p>
            </div>
          </section>
        )}

        <div className="flex flex-wrap gap-2">
          {quote.status === "DRAFT" && !editing && (
            <>
              <button
                onClick={startEditing}
                className="klaros-btn-secondary !px-3 !py-1.5"
              >
                Edit line items
              </button>
              <button
                disabled={busy}
                onClick={handleSend}
                className="klaros-btn-secondary !px-3 !py-1.5 disabled:opacity-50"
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
