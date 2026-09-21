"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, Contract, Quote, getContract, getQuote, sendContract } from "@/lib/api";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatCard } from "@/components/ui/StatCard";
import { useToast } from "@/components/ui/Toast";

export default function ContractDetailPage() {
  const toast = useToast();
  const { id } = useParams<{ id: string }>();
  const { token, user, loading: authLoading } = useAuth();
  const [contract, setContract] = useState<Contract | null>(null);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [viewUrlPath, setViewUrlPath] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token || !id) return;
    setLoading(true);
    setError(null);
    try {
      const loadedContract = await getContract(token, id);
      setContract(loadedContract);
      // The quote's own deposit/payment state is authoritative and
      // unaffected by contract signing (they are separate, parallel
      // tracks by design -- deposit collection is already available as
      // soon as the quote is accepted, not gated on the contract). Shown
      // here purely so staff can see "what's next" from one screen.
      try {
        setQuote(await getQuote(token, loadedContract.quote_id));
      } catch {
        // A quote lookup failure here shouldn't block viewing the contract itself.
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load contract.");
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
      const result = await sendContract(token, id);
      setViewUrlPath(result.view_url_path);
      toast.success("Contract sent — the customer link below is real and ready to share.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to send contract.");
    } finally {
      setBusy(false);
    }
  }

  if (authLoading || loading) {
    return (
      <AppShell user={user}>
        <Skeleton />
      </AppShell>
    );
  }

  if (error && !contract) {
    return (
      <AppShell user={user}>
        <div className="px-8 py-8">
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">{error}</div>
        </div>
      </AppShell>
    );
  }

  if (!contract) return null;

  // Real backend state only -- no timestamp is fabricated when absent.
  const timeline: { label: string; at: string | null }[] = [
    { label: "Created", at: contract.created_at },
    { label: "Sent", at: contract.sent_at },
    { label: "Viewed", at: contract.viewed_at },
    { label: "Decided", at: contract.decided_at },
  ];

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-2 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Contract {contract.contract_number}</h1>
          <span className="rounded-full border border-border-strong px-3 py-1 text-xs">{contract.status}</span>
        </div>
        <p className="mb-6 text-sm text-muted">
          <Link href={`/quotes/${contract.quote_id}`} className="underline hover:text-muted">
            View originating quote
          </Link>
          {" · "}
          <Link href={`/customers/${contract.customer_id}`} className="underline hover:text-muted">
            View customer
          </Link>
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
        )}
        {viewUrlPath && (
          <div className="mb-4 rounded-md border border-border bg-surface p-3 text-sm text-muted">
            Customer signing link:{" "}
            <code className="break-all text-muted">
              {(typeof window !== "undefined" ? window.location.origin : "") + viewUrlPath}
            </code>
          </div>
        )}

        {contract.status === "SIGNED" && (
          <div className="mb-6 rounded-md border border-success/20 bg-success/[0.06] p-4 text-sm text-success">
            <div>Signed by {contract.signer_name} — internal attestation recorded, not a third-party e-signature.</div>
            {quote && (
              <div className="mt-2 border-t border-success/30 pt-2 text-success">
                {quote.status === "DEPOSIT_PENDING" ? (
                  <>
                    Deposit of ${quote.deposit_amount ?? "—"} is outstanding.{" "}
                    <Link href={`/quotes/${quote.id}`} className="underline hover:text-foreground">
                      View quote / collect deposit
                    </Link>
                  </>
                ) : quote.status === "DEPOSIT_PAID" || quote.status === "CONVERTED" ? (
                  <>
                    Deposit received.{" "}
                    <Link href={`/quotes/${quote.id}`} className="underline hover:text-foreground">
                      View quote
                    </Link>
                  </>
                ) : quote.status === "ACCEPTED" ? (
                  <>
                    No deposit required — quote already accepted.{" "}
                    <Link href={`/quotes/${quote.id}`} className="underline hover:text-foreground">
                      View quote
                    </Link>
                  </>
                ) : (
                  <Link href={`/quotes/${quote.id}`} className="underline hover:text-foreground">
                    View quote
                  </Link>
                )}
              </div>
            )}
          </div>
        )}
        {contract.status === "DECLINED" && (
          <div className="mb-6 rounded-md border border-border bg-surface p-4 text-sm text-muted">
            Declined by the customer{contract.decline_reason ? `: ${contract.decline_reason}` : "."}
          </div>
        )}

        <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
          {timeline.map((step) => (
            <StatCard
              key={step.label}
              label={step.label}
              value={step.at ? new Date(step.at).toLocaleString() : "—"}
              tone={step.at ? "success" : "neutral"}
              compact
            />
          ))}
        </div>

        <div className="mb-6 whitespace-pre-wrap rounded-lg border border-border bg-surface p-4 text-sm text-muted">
          {contract.content}
        </div>

        <div className="flex flex-wrap gap-2">
          {contract.status === "DRAFT" && (
            <button
              disabled={busy}
              onClick={handleSend}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {busy ? "Sending..." : "Send to customer"}
            </button>
          )}
        </div>
      </div>
    </AppShell>
  );
}
