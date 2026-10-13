"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { FileSignature } from "lucide-react";
import AppShell from "@/components/AppShell";
import { StatusSummary } from "@/components/ui/layout";
import { useAuth } from "@/lib/useAuth";
import { ApiError, Contract, detectPendingContracts, listContracts } from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
const STATUS_TABS = ["ALL", "DRAFT", "SENT", "VIEWED", "SIGNED", "DECLINED", "EXPIRED", "CANCELLED"];

export default function ContractsPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState("ALL");
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detecting, setDetecting] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listContracts(token, status === "ALL" ? {} : { status_filter: status });
      setContracts(result.contracts);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load contracts.");
    } finally {
      setLoading(false);
    }
  }, [token, status]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDetectPending() {
    if (!token) return;
    setDetecting(true);
    setError(null);
    try {
      const result = await detectPendingContracts(token);
      toast.success(
        result.expired_contract_ids.length === 0
          ? "No newly overdue contracts found."
          : `${result.expired_contract_ids.length} contract(s) marked EXPIRED.`
      );
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to detect pending contracts.");
    } finally {
      setDetecting(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <header className="mb-1 flex items-center justify-between">
          <h1 className="font-display text-[2rem] font-semibold leading-[1.05] tracking-[-0.03em] text-foreground sm:text-[2.75rem]">Contracts</h1>
          <button
            disabled={detecting}
            onClick={handleDetectPending}
            className="klaros-btn-secondary !px-3 !py-1.5 disabled:opacity-50"
          >
            {detecting ? "Checking..." : "Detect pending contracts"}
          </button>
        </header>
        <p className="mb-6 text-sm text-muted">
          The agreement a customer signs after accepting a quote — an internal attestation, not a third-party
          e-signature.
        </p>

        <StatusSummary rows={contracts} noun="contracts" />

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
        ) : contracts.length === 0 ? (
          <EmptyState icon={FileSignature} title="No contracts." />
        ) : (
          <ul className="divide-y divide-border/70 overflow-hidden rounded-3xl bg-surface-muted" aria-label="Contracts">
            {contracts.map((c) => (
              <li key={c.id} className="group relative flex flex-wrap items-center justify-between gap-4 px-6 py-5 transition-colors duration-300 hover:bg-black/[0.03]">
                <div className="min-w-0">
                  <Link href={`/contracts/${c.id}`} className="font-display text-xl font-semibold tracking-tight text-foreground after:absolute after:inset-0 after:content-['']">
                    {c.contract_number}
                  </Link>
                  <p className="mt-0.5 text-[13px] text-muted">
                    {c.signer_name ? `Signed by ${c.signer_name}` : c.viewed_at ? "Viewed, not signed" : c.sent_at ? "Sent, not viewed" : "Not sent"}
                  </p>
                </div>
                <Badge status={c.status}>{c.status.replace(/_/g, " ")}</Badge>
              </li>
            ))}
          </ul>
        )}
      </div>
    </AppShell>
  );
}
