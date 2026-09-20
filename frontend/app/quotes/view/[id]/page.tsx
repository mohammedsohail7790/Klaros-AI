"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  ApiError,
  PublicQuote,
  acceptPublicQuote,
  createPublicQuoteDepositCheckout,
  declinePublicQuote,
  getPublicQuote,
} from "@/lib/api";

// Klaros' first customer-facing page with no login — no AppShell, no
// useAuth. The signed `token` query param is the only credential; the
// backend (app/api/v1/public_quotes.py) is the real trust boundary, this
// page just renders whatever it honestly returns. Phase 16: extended with
// the deposit-collection UX on top of the Phase 15 backend — this page
// never computes a price or a deposit amount itself, and never treats the
// `deposit=success` return query param as proof of payment; only a
// re-fetched quote status (ultimately driven by the real Stripe webhook)
// counts as confirmation.
export default function PublicQuoteViewPage() {
  return (
    <Suspense fallback={<Skeleton />}>
      <PublicQuoteViewInner />
    </Suspense>
  );
}

const POLL_ATTEMPTS = 5;
const POLL_INTERVAL_MS = 2500;

function PublicQuoteViewInner() {
  const { id } = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const depositReturn = searchParams.get("deposit"); // "success" | "cancelled" | null — a UI hint only, never trusted as payment proof

  const [quote, setQuote] = useState<PublicQuote | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [declineReason, setDeclineReason] = useState("");
  const [jobCreated, setJobCreated] = useState(false);
  const [confirming, setConfirming] = useState(depositReturn === "success");
  const pollAttemptsRef = useRef(0);

  const load = useCallback(async () => {
    if (!id || !token) {
      setError("This link is missing required information.");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setQuote(await getPublicQuote(id, token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load this quote.");
    } finally {
      setLoading(false);
    }
  }, [id, token]);

  useEffect(() => {
    load();
  }, [load]);

  // After returning from Stripe with ?deposit=success, the webhook that
  // actually confirms payment may not have landed yet by the time the
  // browser redirect completes — poll the real backend a few times rather
  // than ever showing "paid" based on the query param alone. Stops as
  // soon as the server-side status genuinely advances past DEPOSIT_PENDING,
  // or after a bounded number of attempts (never an infinite poll).
  useEffect(() => {
    if (!confirming || !quote || !id || !token) return;
    if (quote.status !== "DEPOSIT_PENDING") {
      setConfirming(false);
      return;
    }
    if (pollAttemptsRef.current >= POLL_ATTEMPTS) {
      setConfirming(false);
      return;
    }
    const timer = setTimeout(async () => {
      pollAttemptsRef.current += 1;
      try {
        setQuote(await getPublicQuote(id, token));
      } catch {
        // A transient poll failure isn't worth surfacing as a page error —
        // the next attempt (or a manual refresh) will retry the real state.
      }
    }, POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [confirming, quote, id, token]);

  async function handleAccept() {
    if (!id || !token || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await acceptPublicQuote(id, token);
      setQuote(result.quote);
      setJobCreated(result.job_created);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to accept this quote.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDecline() {
    if (!id || !token || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await declinePublicQuote(id, token, declineReason || undefined);
      setQuote(result.quote);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to decline this quote.");
    } finally {
      setBusy(false);
    }
  }

  async function handlePayDeposit() {
    if (!id || !token || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { checkout_url } = await createPublicQuoteDepositCheckout(id, token);
      // Full-page navigation to Stripe's own hosted Checkout page — this
      // app never collects card details itself.
      window.location.href = checkout_url;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to start payment. Please try again.");
      setBusy(false);
    }
  }

  if (loading) {
    return <Skeleton />;
  }

  if (error && !quote) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-16">
        <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
          {error}
          <div className="mt-2 text-xs text-danger">
            If you followed a link from an email or text message, it may have expired — please contact us for a
            fresh link.
          </div>
        </div>
      </div>
    );
  }

  if (!quote) return null;

  const decidable = quote.status === "SENT" || quote.status === "VIEWED";
  const depositPending = quote.status === "DEPOSIT_PENDING";
  const depositSettled = quote.status === "DEPOSIT_PAID" || quote.status === "CONVERTED";
  const remainingBalance =
    depositSettled && quote.deposit_amount !== null
      ? (Number(quote.total) - Number(quote.deposit_amount)).toFixed(2)
      : null;

  return (
    <div className="mx-auto max-w-2xl px-6 py-16">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="font-display text-2xl text-foreground">Quote {quote.quote_number}</h1>
        <Badge status={quote.status}>{quote.status.replace(/_/g, " ")}</Badge>
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
      )}

      {depositReturn === "cancelled" && depositPending && (
        <div className="mb-4 rounded-md border border-warning/25 bg-warning/[0.07] p-3 text-sm text-warning">
          Checkout was cancelled — no payment was made. You can try again below whenever you&apos;re ready.
        </div>
      )}

      {confirming && depositPending && (
        <div className="mb-6 rounded-md border border-blue-200 bg-blue-50/30 p-4 text-sm text-blue-700">
          Confirming your payment with Stripe — this usually takes just a few seconds. This page will update
          automatically once it&apos;s confirmed.
        </div>
      )}

      {!confirming && depositReturn === "success" && depositPending && (
        <div className="mb-6 rounded-md border border-border bg-surface p-4 text-sm text-muted">
          We haven&apos;t received confirmation of your payment yet. If you completed checkout, this can take a
          moment — refresh this page shortly, or contact us if this persists.
          <button
            onClick={() => {
              pollAttemptsRef.current = 0;
              setConfirming(true);
            }}
            className="ml-2 underline hover:text-foreground"
          >
            Check again
          </button>
        </div>
      )}

      {(quote.status === "ACCEPTED" || quote.status === "CONVERTED") && !depositPending && (
        <div className="mb-6 rounded-md border border-success/20 bg-success/[0.06] p-4 text-sm text-success">
          You accepted this quote.{jobCreated ? " Work has been scheduled." : ""}
          {quote.status === "CONVERTED" && quote.deposit_required && (
            <div className="mt-1">Your deposit has been received — thank you.</div>
          )}
        </div>
      )}

      {quote.status === "DEPOSIT_PAID" && (
        <div className="mb-6 rounded-md border border-success/20 bg-success/[0.06] p-4 text-sm text-success">
          Your deposit has been received and this quote is accepted. We&apos;re finalizing your job now.
        </div>
      )}

      {quote.status === "DECLINED" && (
        <div className="mb-6 rounded-md border border-border bg-surface p-4 text-sm text-muted">
          You declined this quote.
        </div>
      )}

      {quote.status === "EXPIRED" && (
        <div className="mb-6 rounded-md border border-border bg-surface p-4 text-sm text-muted">
          This quote has expired. Please contact us for an updated quote.
        </div>
      )}

      <div className="mb-6 klaros-table-wrap">
        <table className="klaros-table">
          <thead className="bg-surface text-muted">
            <tr>
              <th className="px-4 py-2">Description</th>
              <th className="px-4 py-2">Qty</th>
              <th className="px-4 py-2">Unit price</th>
              <th className="px-4 py-2">Total</th>
            </tr>
          </thead>
          <tbody>
            {quote.line_items.map((li, i) => (
              <tr key={i} className="border-t border-border">
                <td className="px-4 py-2">{li.description}</td>
                <td className="px-4 py-2">{li.quantity}</td>
                <td className="px-4 py-2">${li.unit_price}</td>
                <td className="px-4 py-2">${li.line_total}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mb-6 space-y-1 text-right text-sm text-muted">
        <div>Subtotal: ${quote.subtotal}</div>
        {Number(quote.discount) > 0 && <div>Discount: -${quote.discount}</div>}
        {Number(quote.tax) > 0 && <div>Tax: ${quote.tax}</div>}
        <div className="text-lg font-semibold text-foreground">Total: ${quote.total}</div>
        {quote.deposit_required && quote.deposit_amount !== null && !depositSettled && (
          <div className="text-muted">Deposit required: ${quote.deposit_amount}</div>
        )}
        {depositSettled && quote.deposit_amount !== null && (
          <>
            <div className="text-success">Deposit paid: ${quote.deposit_amount}</div>
            {remainingBalance !== null && <div className="text-muted">Remaining balance: ${remainingBalance}</div>}
          </>
        )}
      </div>

      {quote.terms && (
        <div className="mb-6 whitespace-pre-wrap rounded-md border border-border bg-surface p-3 text-sm text-muted">
          {quote.terms}
        </div>
      )}

      {decidable && (
        <div className="flex flex-col gap-3">
          {quote.deposit_required && (
            <div className="rounded-md border border-border bg-surface p-3 text-sm text-muted">
              This quote requires a deposit to begin work. The exact amount will be shown after you accept, and
              you&apos;ll be able to pay it securely via Stripe.
            </div>
          )}
          <button
            disabled={busy}
            onClick={handleAccept}
            className="rounded-md border border-success/20 bg-success/[0.06] px-4 py-2 text-sm text-success hover:bg-success/10 disabled:opacity-50"
          >
            {busy ? "Accepting..." : "Accept quote"}
          </button>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="Reason (optional)"
              value={declineReason}
              onChange={(e) => setDeclineReason(e.target.value)}
              disabled={busy}
              className="flex-1 rounded border border-border-strong bg-surface px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
            />
            <button
              disabled={busy}
              onClick={handleDecline}
              className="rounded-md border border-danger/25 px-4 py-2 text-sm text-danger hover:bg-danger/[0.06] disabled:opacity-50"
            >
              Decline
            </button>
          </div>
        </div>
      )}

      {depositPending && !confirming && (
        <div className="flex flex-col gap-3">
          <div className="rounded-md border border-border bg-surface p-4 text-sm text-muted">
            <div className="mb-1 font-medium text-foreground">Deposit required: ${quote.deposit_amount ?? "—"}</div>
            Payment is handled securely by Stripe — Klaros never sees or stores your card details.
          </div>
          <button
            disabled={busy}
            onClick={handlePayDeposit}
            className="rounded-md border border-success/20 bg-success/[0.06] px-4 py-2 text-sm text-success hover:bg-success/10 disabled:opacity-50"
          >
            {busy ? "Starting checkout..." : "Pay deposit securely with Stripe"}
          </button>
        </div>
      )}
    </div>
  );
}
