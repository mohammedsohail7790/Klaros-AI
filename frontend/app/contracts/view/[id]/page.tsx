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

  if (!contract) return null;

  const decidable = contract.status === "SENT" || contract.status === "VIEWED";

  return (
    <div className="mx-auto max-w-2xl px-6 py-16">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="font-display text-2xl text-foreground">Contract {contract.contract_number}</h1>
        <Badge status={contract.status}>{contract.status.replace(/_/g, " ")}</Badge>
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
      )}

      {contract.status === "SIGNED" && (
        <div className="mb-6 rounded-md border border-success/20 bg-success/[0.06] p-4 text-sm text-success">
          Signed by {contract.signer_name} — thank you. This is an internal record of your agreement, not a
          third-party verified e-signature.
        </div>
      )}
      {contract.status === "DECLINED" && (
        <div className="mb-6 rounded-md border border-border bg-surface p-4 text-sm text-muted">
          You declined this contract.
        </div>
      )}
      {contract.status === "EXPIRED" && (
        <div className="mb-6 rounded-md border border-border bg-surface p-4 text-sm text-muted">
          This contract has expired. Please contact us for an updated agreement.
        </div>
      )}
      {contract.status === "CANCELLED" && (
        <div className="mb-6 rounded-md border border-border bg-surface p-4 text-sm text-muted">
          This contract has been cancelled.
        </div>
      )}

      <div className="mb-6 whitespace-pre-wrap rounded-lg border border-border bg-surface p-4 text-sm text-muted">
        {contract.content}
      </div>

      {decidable && !confirmingSign && (
        <div className="flex flex-col gap-3">
          <button
            disabled={busy}
            onClick={() => setConfirmingSign(true)}
            className="rounded-md border border-success/20 bg-success/[0.06] px-4 py-2 text-sm text-success hover:bg-success/10 disabled:opacity-50"
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

      {decidable && confirmingSign && (
        <div className="rounded-md border border-border bg-surface p-4">
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
            className="mb-2 w-full rounded border border-border-strong bg-surface px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
          />
          <input
            type="email"
            placeholder="Email (optional)"
            value={signerEmail}
            onChange={(e) => setSignerEmail(e.target.value)}
            disabled={busy}
            className="mb-3 w-full rounded border border-border-strong bg-surface px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
          />
          <div className="flex gap-2">
            <button
              disabled={busy || !signerName.trim()}
              onClick={handleSign}
              className="rounded-md border border-success/20 bg-success/[0.06] px-4 py-2 text-sm text-success hover:bg-success/10 disabled:opacity-50"
            >
              {busy ? "Signing..." : "Confirm signature"}
            </button>
            <button
              disabled={busy}
              onClick={() => setConfirmingSign(false)}
              className="rounded-md border border-border-strong px-4 py-2 text-sm text-muted hover:bg-surface-muted disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
