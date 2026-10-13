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
        <div className="rounded-xl border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
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
    <div className="mx-auto w-full max-w-2xl px-5 py-12 sm:py-20">
      <div className="mb-10 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-[15px] font-medium text-muted-foreground">Quote</p>
          <h1 className="font-display text-[2.25rem] font-semibold leading-[1.05] tracking-[-0.03em] text-foreground sm:text-[3rem]">{quote.quote_number}</h1>
        </div>
        <Badge status={quote.status}>{quote.status.replace(/_/g, " ")}</Badge>
      </div>

      {error && (
        <div className="mb-6 rounded-3xl bg-danger/10 px-6 py-5 text-[15px] text-danger">{error}</div>
      )}

      {depositReturn === "cancelled" && depositPending && (
        <div className="mb-6 rounded-3xl bg-warning/10 px-6 py-5 text-[15px] text-warning">
          Checkout was cancelled — no payment was made. You can try again below whenever you&apos;re ready.
        </div>
      )}

      {confirming && depositPending && (
        <div className="mb-6 rounded-3xl bg-accent-soft px-6 py-5 text-[15px] text-accent-hover">
          Confirming your payment with Stripe — this usually takes just a few seconds. This page will update
          automatically once it&apos;s confirmed.
        </div>
      )}

      {!confirming && depositReturn === "success" && depositPending && (
        <div className="mb-6 rounded-3xl bg-surface-muted px-6 py-5 text-[15px] text-muted">
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
        <div className="mb-6 rounded-3xl bg-success/10 px-6 py-5 text-[15px] text-success">
          You accepted this quote.{jobCreated ? " Work has been scheduled." : ""}
          {quote.status === "CONVERTED" && quote.deposit_required && (
            <div className="mt-1">Your deposit has been received — thank you.</div>
          )}
        </div>
      )}

      {quote.status === "DEPOSIT_PAID" && (
        <div className="mb-6 rounded-3xl bg-success/10 px-6 py-5 text-[15px] text-success">
          Your deposit has been received and this quote is accepted. We&apos;re finalizing your job now.
        </div>
      )}

      {quote.status === "DECLINED" && (
        <div className="mb-6 rounded-3xl bg-surface-muted px-6 py-5 text-[15px] text-muted">
          You declined this quote.
        </div>
      )}

      {quote.status === "EXPIRED" && (
        <div className="mb-6 rounded-3xl bg-surface-muted px-6 py-5 text-[15px] text-muted">
          This quote has expired. Please contact us for an updated quote.
        </div>
      )}

      <section aria-label="Line items" className="mb-8 rounded-3xl bg-surface-muted p-6 sm:p-8">
        <ul className="divide-y divide-border/70">
          {quote.line_items.map((li, i) => (
            <li key={i} className="flex items-baseline justify-between gap-4 py-3.5">
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
          <p className="font-display text-[2rem] font-semibold tabular-nums tracking-[-0.03em] text-foreground">${quote.total}</p>
        </div>
      </section>

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
        <div className="mb-6 whitespace-pre-wrap rounded-2xl bg-surface-muted p-3 text-sm text-muted">
          {quote.terms}
        </div>
      )}

      {decidable && (
        <div className="flex flex-col gap-3">
          {quote.deposit_required && (
            <div className="rounded-2xl bg-surface-muted p-3 text-sm text-muted">
              This quote requires a deposit to begin work. The exact amount will be shown after you accept, and
              you&apos;ll be able to pay it securely via Stripe.
            </div>
          )}
          <button
            disabled={busy}
            onClick={handleAccept}
            className="rounded-full bg-success px-6 py-3 text-[15px] font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
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
              className="klaros-input flex-1"
            />
            <button
              disabled={busy}
              onClick={handleDecline}
              className="rounded-full bg-danger/10 px-5 py-3 text-[15px] font-medium text-danger transition-colors hover:bg-danger/20 disabled:opacity-50"
            >
              Decline
            </button>
          </div>
        </div>
      )}

      {depositPending && !confirming && (
        <div className="flex flex-col gap-3">
          <div className="rounded-2xl bg-surface-muted p-4 text-sm text-muted">
            <div className="mb-1 font-medium text-foreground">Deposit required: ${quote.deposit_amount ?? "—"}</div>
            Payment is handled securely by Stripe — Klaros never sees or stores your card details.
          </div>
          <button
            disabled={busy}
            onClick={handlePayDeposit}
            className="rounded-full bg-success px-6 py-3 text-[15px] font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "Starting checkout..." : "Pay deposit securely with Stripe"}
          </button>
        </div>
      )}
    </div>
  );
}
