"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { HandCoins } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, CommissionStatus, RevenueSummary, getMedicalTourismRevenue } from "@/lib/api";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";

const ROWS: { key: CommissionStatus; label: string; note: string; earned?: boolean }[] = [
  { key: "PENDING", label: "Potential", note: "A referral exists. Nothing has been earned." },
  { key: "CONFIRMED", label: "Earned", note: "The clinic reported the agreed outcome, with evidence.", earned: true },
  { key: "INVOICED", label: "Invoiced", note: "An invoice was issued to the clinic.", earned: true },
  { key: "PAID", label: "Paid", note: "Payment received.", earned: true },
  { key: "DISPUTED", label: "Disputed", note: "Under dispute until settled." },
  { key: "CANCELLED", label: "Cancelled", note: "Will not be earned." },
];

/** What the business is owed, by what stage it has genuinely reached. Potential value is never shown as revenue, and currencies are never added together. */
export default function MedicalTourismRevenuePage() {
  const { token, user, loading: authLoading } = useAuth();
  const [data, setData] = useState<RevenueSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setData(await getMedicalTourismRevenue(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load revenue.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const currencies = Object.keys(data?.by_currency ?? {}).sort();

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <PageHeader
          title="Revenue"
          description="Commission by the stage it has actually reached. Potential is not revenue."
          icon={HandCoins}
          actions={<Link href="/medical-tourism/referrals" className="klaros-btn-secondary">All commissions</Link>}
        />
        {authLoading || (loading && !data) ? (
          <Skeleton />
        ) : error ? (
          <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>{error}</Alert>
        ) : currencies.length === 0 ? (
          <EmptyState icon={HandCoins} title="No commissions recorded yet." />
        ) : (
          <div className="space-y-8">
            {currencies.map((cur) => (
              <section key={cur} aria-label={`Revenue in ${cur}`}>
                <h2 className="text-lg font-semibold tracking-tight text-foreground">{cur}</h2>
                <div className="klaros-table-wrap mt-2">
                  <table className="klaros-table">
                    <thead className="bg-surface text-muted">
                      <tr><th className="px-4 py-2">Stage</th><th className="px-4 py-2">Count</th><th className="px-4 py-2">Amount</th><th className="px-4 py-2">Meaning</th></tr>
                    </thead>
                    <tbody>
                      {ROWS.map((r) => {
                        const slot = data!.by_currency[cur][r.key] ?? { count: 0, amount: "0.00" };
                        return (
                          <tr key={r.key} className="border-t border-border">
                            <td className="px-4 py-2 font-medium text-foreground">{r.label}</td>
                            <td className="px-4 py-2">{slot.count}</td>
                            <td className={`px-4 py-2 ${r.earned && slot.count > 0 ? "font-medium text-success" : "text-muted"}`}>{slot.amount} {cur}</td>
                            <td className="px-4 py-2 text-muted">{r.note}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </section>
            ))}
          </div>
        )}
        {data && <p className="mt-6 max-w-3xl text-xs text-muted-foreground">{data.definitions}</p>}
      </div>
    </AppShell>
  );
}
