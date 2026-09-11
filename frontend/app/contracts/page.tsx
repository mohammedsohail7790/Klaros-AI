"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, Contract, listContracts } from "@/lib/api";

const STATUS_TABS = ["ALL", "DRAFT", "SENT", "VIEWED", "SIGNED", "DECLINED", "EXPIRED", "CANCELLED"];

export default function ContractsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState("ALL");
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="mb-1 text-xl font-semibold">Contracts</h1>
        <p className="mb-6 text-sm text-neutral-500">
          The agreement a customer signs after accepting a quote — an internal attestation, not a third-party
          e-signature.
        </p>

        <div className="mb-4 flex flex-wrap gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`rounded-full border px-3 py-1 text-xs ${
                status === s ? "border-white bg-white text-black" : "border-neutral-700 text-neutral-400"
              }`}
            >
              {s}
            </button>
          ))}
        </div>

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : contracts.length === 0 ? (
          <p className="text-sm text-neutral-500">No contracts.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-neutral-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-neutral-950 text-neutral-500">
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
                  <tr key={c.id} className="border-t border-neutral-900">
                    <td className="px-4 py-2">
                      <Link href={`/contracts/${c.id}`} className="underline hover:text-white">
                        {c.contract_number}
                      </Link>
                    </td>
                    <td className="px-4 py-2">
                      <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">{c.status}</span>
                    </td>
                    <td className="px-4 py-2 text-neutral-500">{c.sent_at ? "Yes" : "—"}</td>
                    <td className="px-4 py-2 text-neutral-500">{c.viewed_at ? "Yes" : "—"}</td>
                    <td className="px-4 py-2 text-neutral-500">{c.signer_name ?? "—"}</td>
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
