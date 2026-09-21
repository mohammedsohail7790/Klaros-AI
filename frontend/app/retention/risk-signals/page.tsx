"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Star } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  AdvocateCandidateRow,
  RiskSignalRow,
  detectAdvocates,
  detectAtRisk,
  detectPaymentRisk,
  listAdvocateCandidates,
  listRiskSignals,
} from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

export default function RiskSignalsPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [signals, setSignals] = useState<RiskSignalRow[]>([]);
  const [candidates, setCandidates] = useState<AdvocateCandidateRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [signalsResult, candidatesResult] = await Promise.all([
        listRiskSignals(token),
        listAdvocateCandidates(token),
      ]);
      setSignals(signalsResult.risk_signals);
      setCandidates(candidatesResult.advocate_candidates);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load risk signals.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function runDetection(name: string, fn: (t: string) => Promise<{ [key: string]: string[] }>) {
    if (!token) return;
    setBusy(name);
    setError(null);
    try {
      const result = await fn(token);
      const count = Object.values(result)[0]?.length ?? 0;
      toast.success(`${name}: ${count} customer(s) flagged.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Unable to run ${name}.`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <header className="mb-1 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Risk & Advocacy</h1>
          <div className="flex flex-wrap gap-2">
            <button
              disabled={busy !== null}
              onClick={() => runDetection("At-risk/inactive sweep", detectAtRisk)}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {busy === "At-risk/inactive sweep" ? "Running..." : "Run at-risk sweep"}
            </button>
            <button
              disabled={busy !== null}
              onClick={() => runDetection("Payment risk sweep", detectPaymentRisk)}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {busy === "Payment risk sweep" ? "Running..." : "Run payment risk sweep"}
            </button>
            <button
              disabled={busy !== null}
              onClick={() => runDetection("Advocate sweep", detectAdvocates)}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {busy === "Advocate sweep" ? "Running..." : "Run advocate sweep"}
            </button>
          </div>
        </header>
        <p className="mb-6 text-sm text-muted">
          Deterministic, on-demand sweeps — no scheduler yet, so run them here. Flags customers with overdue
          payments as a risk, and identifies repeat customers with no complaints as advocate candidates.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
        )}

        {authLoading || loading ? (
          <Skeleton />
        ) : (
          <>
            <h2 className="mb-3 font-medium">Open risk signals</h2>
            {signals.length === 0 ? (
              <EmptyState icon={AlertTriangle} title="No open risk signals." />
            ) : (
              <div className="mb-8 klaros-table-wrap">
                <table className="klaros-table">
                  <thead className="bg-surface text-muted">
                    <tr>
                      <th className="px-4 py-2">Type</th>
                      <th className="px-4 py-2">Severity</th>
                      <th className="px-4 py-2">Description</th>
                      <th className="px-4 py-2">Detected</th>
                    </tr>
                  </thead>
                  <tbody>
                    {signals.map((s) => (
                      <tr key={s.id} className="border-t border-border">
                        <td className="px-4 py-2">{s.signal_type.replaceAll("_", " ")}</td>
                        <td className="px-4 py-2">
                          <Badge status={s.severity}>{s.severity}</Badge>
                        </td>
                        <td className="px-4 py-2 text-muted">{s.description}</td>
                        <td className="px-4 py-2 text-muted">{new Date(s.detected_at).toLocaleDateString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <h2 className="mb-3 font-medium">Advocate candidates</h2>
            {candidates.length === 0 ? (
              <EmptyState icon={Star} title="No advocate candidates yet." />
            ) : (
              <div className="klaros-table-wrap">
                <table className="klaros-table">
                  <thead className="bg-surface text-muted">
                    <tr>
                      <th className="px-4 py-2">Reason</th>
                      <th className="px-4 py-2">Signals</th>
                      <th className="px-4 py-2">Priority</th>
                      <th className="px-4 py-2">Identified</th>
                    </tr>
                  </thead>
                  <tbody>
                    {candidates.map((c) => (
                      <tr key={c.id} className="border-t border-border">
                        <td className="px-4 py-2 text-muted">{c.reason}</td>
                        <td className="px-4 py-2 text-muted">{c.signals.join(", ")}</td>
                        <td className="px-4 py-2">
                          <Badge status={c.priority}>{c.priority}</Badge>
                        </td>
                        <td className="px-4 py-2 text-muted">{new Date(c.identified_at).toLocaleDateString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
