"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Invoice,
  InvoiceLineItem,
  approveInvoice,
  createCreditNoteRequest,
  createInvoiceCheckout,
  createWriteOffRequest,
  getInvoice,
  recordTestPayment,
  rejectInvoice,
  requestInvoiceApproval,
  sendInvoice,
  syncInvoiceToQuickBooks,
  voidInvoice,
} from "@/lib/api";

export default function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { token, user, loading: authLoading } = useAuth();
  const [invoice, setInvoice] = useState<(Invoice & { line_items: InvoiceLineItem[] }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showWriteOffForm, setShowWriteOffForm] = useState(false);
  const [writeOffAmount, setWriteOffAmount] = useState("");
  const [writeOffReason, setWriteOffReason] = useState("");
  const [showCreditNoteForm, setShowCreditNoteForm] = useState(false);
  const [creditNoteAmount, setCreditNoteAmount] = useState("");
  const [creditNoteReason, setCreditNoteReason] = useState("");
  const [checkoutLink, setCheckoutLink] = useState<string | null>(null);
  const [checkoutBusy, setCheckoutBusy] = useState(false);
  const [qbSyncBusy, setQbSyncBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token || !id) return;
    setLoading(true);
    setError(null);
    try {
      setInvoice(await getInvoice(token, id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load invoice.");
    } finally {
      setLoading(false);
    }
  }, [token, id]);

  useEffect(() => {
    load();
  }, [load]);

  async function runAction(fn: () => Promise<unknown>, successMsg: string) {
    if (!token) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await fn();
      if (result && typeof result === "object" && "status" in result && (result as any).status === "pending_approval") {
        setNotice("This action requires approval — an ApprovalRequest has been created.");
      } else {
        setNotice(successMsg);
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  }

  if (authLoading || loading) {
    return (
      <AppShell user={user}>
        <div className="px-8 py-8 text-sm text-muted">Loading...</div>
      </AppShell>
    );
  }

  if (error && !invoice) {
    return (
      <AppShell user={user}>
        <div className="px-8 py-8">
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">{error}</div>
        </div>
      </AppShell>
    );
  }

  if (!invoice) return null;

  async function handleGenerateCheckoutLink() {
    if (!token || !invoice) return;
    setCheckoutBusy(true);
    setError(null);
    try {
      const origin = typeof window !== "undefined" ? window.location.origin : "";
      const result = await createInvoiceCheckout(token, invoice.id, {
        success_url: `${origin}/finance/invoices/${invoice.id}`,
        cancel_url: `${origin}/finance/invoices/${invoice.id}`,
      });
      setCheckoutLink(result.checkout_url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to generate a payment link.");
    } finally {
      setCheckoutBusy(false);
    }
  }

  async function handleSyncToQuickBooks() {
    if (!token || !invoice) return;
    setQbSyncBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await syncInvoiceToQuickBooks(token, invoice.id);
      setNotice(
        result.already_synced
          ? "Already synced to QuickBooks."
          : `Synced to QuickBooks (invoice ${result.quickbooks_invoice_id}).`
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to sync to QuickBooks.");
    } finally {
      setQbSyncBusy(false);
    }
  }

  async function submitWriteOff(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !invoice || !writeOffAmount || !writeOffReason.trim()) return;
    await runAction(
      () => createWriteOffRequest(token, { invoice_id: invoice.id, amount: writeOffAmount, reason: writeOffReason.trim() }),
      "Write-off requested — pending approval."
    );
    setShowWriteOffForm(false);
    setWriteOffAmount("");
    setWriteOffReason("");
  }

  async function submitCreditNote(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !invoice || !creditNoteAmount || !creditNoteReason.trim()) return;
    await runAction(
      () =>
        createCreditNoteRequest(token, {
          invoice_id: invoice.id,
          reason: creditNoteReason.trim(),
          line_items: [{ description: creditNoteReason.trim(), amount: creditNoteAmount }],
        }),
      "Credit note requested — pending approval."
    );
    setShowCreditNoteForm(false);
    setCreditNoteAmount("");
    setCreditNoteReason("");
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Invoice {invoice.invoice_number}</h1>
          <span className="rounded-full border border-border-strong px-3 py-1 text-xs">{invoice.status}</span>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50/30 p-3 text-sm text-red-700">{error}</div>
        )}
        {notice && (
          <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50/30 p-3 text-sm text-emerald-700">
            {notice}
          </div>
        )}

        <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <div className="text-xs text-muted">Total</div>
            <div className="mt-1 text-lg font-semibold">${invoice.total}</div>
          </div>
          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <div className="text-xs text-muted">Amount paid</div>
            <div className="mt-1 text-lg font-semibold">${invoice.amount_paid}</div>
          </div>
          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <div className="text-xs text-muted">Amount due</div>
            <div className="mt-1 text-lg font-semibold">${invoice.amount_due}</div>
          </div>
          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <div className="text-xs text-muted">Due date</div>
            <div className="mt-1 text-lg font-semibold">{invoice.due_date}</div>
          </div>
        </div>

        {invoice.notes && (
          <div className="mb-6 rounded-md border border-border bg-surface p-3 text-sm text-muted">
            {invoice.notes}
          </div>
        )}

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
              {invoice.line_items.map((li) => (
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

        <div className="flex flex-wrap gap-2">
          {invoice.status === "DRAFT" && (
            <button
              disabled={busy}
              onClick={() => runAction(() => requestInvoiceApproval(token!, invoice.id), "Approval requested.")}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              Request approval
            </button>
          )}
          {invoice.status === "PENDING_APPROVAL" && (
            <>
              <button
                disabled={busy}
                onClick={() => runAction(() => approveInvoice(token!, invoice.id), "Invoice approved.")}
                className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
              >
                Approve
              </button>
              <button
                disabled={busy}
                onClick={() => runAction(() => rejectInvoice(token!, invoice.id), "Invoice rejected — returned to draft.")}
                className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50/30 disabled:opacity-50"
              >
                Reject
              </button>
            </>
          )}
          {invoice.status === "APPROVED" && (
            <button
              disabled={busy}
              onClick={() => runAction(() => sendInvoice(token!, invoice.id), "Invoice sent.")}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              Send
            </button>
          )}
          {(invoice.status === "SENT" || invoice.status === "PARTIALLY_PAID") && (
            <button
              disabled={busy}
              onClick={() =>
                runAction(
                  () =>
                    recordTestPayment(token!, {
                      customer_id: invoice.customer_id,
                      amount: invoice.amount_due,
                      allocations: [{ invoice_id: invoice.id, amount: invoice.amount_due }],
                    }),
                  "Test payment recorded."
                )
              }
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              Record test payment (${invoice.amount_due})
            </button>
          )}
          {(invoice.status === "SENT" || invoice.status === "PARTIALLY_PAID") && (
            <button
              disabled={checkoutBusy}
              onClick={handleGenerateCheckoutLink}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {checkoutBusy ? "Generating..." : "Generate payment link"}
            </button>
          )}
          {!["DRAFT", "VOID", "CANCELLED"].includes(invoice.status) && (
            <button
              disabled={qbSyncBusy}
              onClick={handleSyncToQuickBooks}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {qbSyncBusy ? "Syncing..." : "Sync to QuickBooks"}
            </button>
          )}
          {!["PAID", "VOID", "CANCELLED"].includes(invoice.status) && (
            <button
              disabled={busy}
              onClick={() => runAction(() => voidInvoice(token!, invoice.id, "Voided from invoice detail page"), "Invoice voided.")}
              className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50/30 disabled:opacity-50"
            >
              Void
            </button>
          )}
          {!["DRAFT", "VOID", "CANCELLED"].includes(invoice.status) && (
            <>
              <button
                disabled={busy}
                onClick={() => setShowWriteOffForm((v) => !v)}
                className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
              >
                Request write-off
              </button>
              <button
                disabled={busy}
                onClick={() => setShowCreditNoteForm((v) => !v)}
                className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
              >
                Request credit note
              </button>
            </>
          )}
        </div>

        {checkoutLink && (
          <div className="mt-2 text-xs text-muted">
            Payment link (share or read out to the customer): <code className="break-all text-muted">{checkoutLink}</code>
          </div>
        )}

        {showWriteOffForm && (
          <form onSubmit={submitWriteOff} className="mt-4 flex flex-wrap items-end gap-2 rounded-lg border border-border bg-surface p-4">
            <div>
              <label className="block text-xs text-muted">Amount</label>
              <input
                value={writeOffAmount}
                onChange={(e) => setWriteOffAmount(e.target.value)}
                placeholder={invoice.amount_due}
                required
                className="w-28 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
            </div>
            <div className="flex-1">
              <label className="block text-xs text-muted">Reason</label>
              <input
                value={writeOffReason}
                onChange={(e) => setWriteOffReason(e.target.value)}
                required
                className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
            </div>
            <button
              type="submit"
              disabled={busy}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              Submit
            </button>
          </form>
        )}

        {showCreditNoteForm && (
          <form onSubmit={submitCreditNote} className="mt-4 flex flex-wrap items-end gap-2 rounded-lg border border-border bg-surface p-4">
            <div>
              <label className="block text-xs text-muted">Amount</label>
              <input
                value={creditNoteAmount}
                onChange={(e) => setCreditNoteAmount(e.target.value)}
                placeholder={invoice.total}
                required
                className="w-28 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
            </div>
            <div className="flex-1">
              <label className="block text-xs text-muted">Reason</label>
              <input
                value={creditNoteReason}
                onChange={(e) => setCreditNoteReason(e.target.value)}
                required
                className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
            </div>
            <button
              type="submit"
              disabled={busy}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              Submit
            </button>
          </form>
        )}
      </div>
    </AppShell>
  );
}
