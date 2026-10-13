"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { ApiError, PublicContract, declinePublicContract, getPublicContract, signPublicContract } from "@/lib/api";

// Klaros' public, unauthenticated contract page — no AppShell, no useAuth,
// mirrors app/quotes/view/[id]/page.tsx exactly. The signed `token` query
// param is the only credential; the backend
// (app/api/v1/public_contracts.py) is the real trust boundary. Signing
// here is an INTERNAL ATTESTATION only -- there is no third-party
// e-signature provider integrated, and this page never implies one.
export default function PublicContractViewPage() {
  return (
    <Suspense fallback={<Skeleton />}>
      <PublicContractViewInner />
    </Suspense>
  );
}

function PublicContractViewInner() {
  const { id } = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  const [contract, setContract] = useState<PublicContract | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [signerName, setSignerName] = useState("");
  const [signerEmail, setSignerEmail] = useState("");
  const [declineReason, setDeclineReason] = useState("");
  const [confirmingSign, setConfirmingSign] = useState(false);

  const load = useCallback(async () => {
    if (!id || !token) {
      setError("This link is missing required information.");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setContract(await getPublicContract(id, token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load this contract.");
    } finally {
      setLoading(false);
    }
  }, [id, token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSign() {
    if (!id || !token || busy || !signerName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setContract(await signPublicContract(id, token, signerName.trim(), signerEmail.trim() || undefined));
      setConfirmingSign(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to sign this contract.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDecline() {
    if (!id || !token || busy) return;
    setBusy(true);
    setError(null);
    try {
      setContract(await declinePublicContract(id, token, declineReason || undefined));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to decline this contract.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <Skeleton />;
  }

  if (error && !contract) {
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

  if (!contract) return null;

  const decidable = contract.status === "SENT" || contract.status === "VIEWED";

  return (
    <div className="mx-auto w-full max-w-2xl px-5 py-12 sm:py-20">
      <div className="mb-10 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-[15px] font-medium text-muted-foreground">Agreement</p>
          <h1 className="font-display text-[2.25rem] font-semibold leading-[1.05] tracking-[-0.03em] text-foreground sm:text-[3rem]">{contract.contract_number}</h1>
        </div>
        <Badge status={contract.status}>{contract.status.replace(/_/g, " ")}</Badge>
      </div>

      {error && (
        <div className="mb-6 rounded-3xl bg-danger/10 px-6 py-5 text-[15px] text-danger">{error}</div>
      )}

      {contract.status === "SIGNED" && (
        <div className="mb-6 rounded-3xl bg-success/10 px-6 py-5 text-[15px] text-success">
          Signed by {contract.signer_name} — thank you. This is an internal record of your agreement, not a
          third-party verified e-signature.
        </div>
      )}
      {contract.status === "DECLINED" && (
        <div className="mb-6 rounded-3xl bg-surface-muted px-6 py-5 text-[15px] text-muted">
          You declined this contract.
        </div>
      )}
      {contract.status === "EXPIRED" && (
        <div className="mb-6 rounded-3xl bg-surface-muted px-6 py-5 text-[15px] text-muted">
          This contract has expired. Please contact us for an updated agreement.
        </div>
      )}
      {contract.status === "CANCELLED" && (
        <div className="mb-6 rounded-3xl bg-surface-muted px-6 py-5 text-[15px] text-muted">
          This contract has been cancelled.
        </div>
      )}

      <div className="mb-8 whitespace-pre-wrap rounded-3xl bg-surface-muted p-6 text-[15px] leading-relaxed text-foreground sm:p-8">
        {contract.content}
      </div>

      {decidable && !confirmingSign && (
        <div className="flex flex-col gap-3">
          <button
            disabled={busy}
            onClick={() => setConfirmingSign(true)}
            className="rounded-full bg-success px-6 py-3 text-[15px] font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            Sign this agreement
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

      {decidable && confirmingSign && (
        <div className="rounded-3xl bg-surface-muted p-6">
          <p className="mb-3 text-sm text-muted">
            Typing your name below records your agreement to the terms above. This is an internal record kept by
            the business, not a third-party verified electronic signature.
          </p>
          <input
            type="text"
            placeholder="Your full name"
            value={signerName}
            onChange={(e) => setSignerName(e.target.value)}
            disabled={busy}
            className="klaros-input mb-2"
          />
          <input
            type="email"
            placeholder="Email (optional)"
            value={signerEmail}
            onChange={(e) => setSignerEmail(e.target.value)}
            disabled={busy}
            className="klaros-input mb-3"
          />
          <div className="flex gap-2">
            <button
              disabled={busy || !signerName.trim()}
              onClick={handleSign}
              className="rounded-full bg-success px-6 py-3 text-[15px] font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Signing..." : "Confirm signature"}
            </button>
            <button
              disabled={busy}
              onClick={() => setConfirmingSign(false)}
              className="rounded-xl border border-border-strong px-4 py-2 text-sm text-muted hover:bg-surface-muted disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
