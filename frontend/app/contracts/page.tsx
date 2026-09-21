"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { FileSignature } from "lucide-react";
import AppShell from "@/components/AppShell";
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
      <div className="px-8 py-8">
        <header className="mb-1 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Contracts</h1>
          <button
            disabled={detecting}
            onClick={handleDetectPending}
            className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
          >
            {detecting ? "Checking..." : "Detect pending contracts"}
          </button>
        </header>
        <p className="mb-6 text-sm text-muted">
          The agreement a customer signs after accepting a quote — an internal attestation, not a third-party
          e-signature.
        </p>

        <div className="mb-4 flex flex-wrap gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`rounded-full border px-3 py-1 text-xs ${
                status === s ? "border-foreground bg-surface text-foreground" : "border-border-strong text-muted"
              }`}
            >
              {s}
            </button>
          ))}
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : contracts.length === 0 ? (
          <EmptyState icon={FileSignature} title="No contracts." />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Number</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Sent</th>
                  <th className="px-4 py-2">Viewed</th>
                  <th className="px-4 py-2">Signed</th>
                </tr>
              </thead>
              <tbody>
                {contracts.map((c) => (
                  <tr key={c.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <Link href={`/contracts/${c.id}`} className="underline hover:text-foreground">
                        {c.contract_number}
                      </Link>
                    </td>
                    <td className="px-4 py-2">
                      <Badge status={c.status}>{c.status}</Badge>
                    </td>
                    <td className="px-4 py-2 text-muted">{c.sent_at ? "Yes" : "—"}</td>
                    <td className="px-4 py-2 text-muted">{c.viewed_at ? "Yes" : "—"}</td>
                    <td className="px-4 py-2 text-muted">{c.signer_name ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
