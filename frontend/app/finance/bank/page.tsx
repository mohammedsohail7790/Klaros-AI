"use client";

import { useCallback, useEffect, useState } from "react";
import { Landmark } from "lucide-react";
import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  BankFeedAccounts,
  BankTransactionRow,
  getBankFeedAccounts,
  ignoreBankTransaction,
  listBankTransactions,
  matchBankTransaction,
  syncBankFeed,
} from "@/lib/api";

export default function BankFeedPage() {
  const { token, user } = useAuth();
  const [feed, setFeed] = useState<BankFeedAccounts | null>(null);
  const [rows, setRows] = useState<BankTransactionRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const accounts = await getBankFeedAccounts(token);
      setFeed(accounts);
      setRows(accounts.connected ? await listBankTransactions(token) : []);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load the bank feed.");
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function act(fn: () => Promise<unknown>, fallback: string) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : fallback);
    } finally {
      setBusy(false);
    }
  }

  const needsReview = rows.filter((r) => r.direction === "IN" && (r.match_status === "SUGGESTED" || r.match_status === "UNMATCHED"));

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <PageHeader title="Bank feed" description="Deposits from your bank, matched to your invoices. Klaros only records the match; it never moves money or edits an invoice." />
        {error && <div className="mb-4 rounded border border-danger/25 bg-danger/[0.06] px-4 py-3 text-sm text-danger">{error}</div>}
        {feed && !feed.connected && (
          <EmptyState icon={Landmark} title="No bank connected" hint="Connect your bank in Settings > Integrations, under Connect your accounts." />
        )}
        {feed?.connected && (
          <>
            <div className="mb-4 flex flex-wrap items-center gap-3 text-sm">
              <button
                className="klaros-btn-secondary !px-3 !py-1.5 disabled:opacity-50"
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    const r = await syncBankFeed(token as string);
                    setNote(r.status === "ok" ? `${r.added} new, ${r.modified} changed, ${r.removed} removed.` : r.message ?? r.status);
                  }, "Sync failed.")
                }
              >
                {busy ? "Working..." : "Sync now"}
              </button>
              {feed.last_sync_at && <span className="text-muted-foreground">Last synced {new Date(feed.last_sync_at).toLocaleString()}</span>}
              {note && <span className="text-muted-foreground">{note}</span>}
              {feed.last_sync_status && feed.last_sync_status !== "ok" && <span className="text-danger">{feed.last_sync_error ?? feed.last_sync_status}</span>}
            </div>
            <div className="mb-6 grid gap-2 sm:grid-cols-2">
              {feed.accounts.map((a) => (
                <div key={a.id} className="rounded border border-border bg-surface p-3 text-sm">
                  <div className="font-medium">{a.name}{a.mask ? ` ••${a.mask}` : ""}</div>
                  <div className="text-muted-foreground">{a.current_balance ? `${a.current_balance} ${a.currency}` : "Balance not available"}</div>
                </div>
              ))}
            </div>
            <h2 className="mb-2 text-base font-semibold">Deposits to review ({needsReview.length})</h2>
            {needsReview.length === 0 && <p className="text-sm text-muted-foreground">Nothing to review.</p>}
            <div className="grid gap-2">
              {needsReview.map((t) => (
                <div key={t.id} className="rounded border border-border bg-surface p-3 text-sm" data-testid={`tx-${t.id}`}>
                  <div className="flex flex-wrap justify-between gap-2">
                    <span>{t.posted_date} · {t.merchant ?? t.description ?? "Deposit"}</span>
                    <span className="font-medium">{t.amount} {t.currency}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {(t.match_candidates ?? []).map((c) => (
                      <button
                        key={c.invoice_id}
                        disabled={busy}
                        className="klaros-btn-secondary !px-3 !py-1 text-xs disabled:opacity-50"
                        title={c.reason}
                        onClick={() => act(() => matchBankTransaction(token as string, t.id, c.invoice_id), "Unable to match.")}
                      >
                        Match to invoice {c.invoice_number}
                      </button>
                    ))}
                    {(t.match_candidates ?? []).length === 0 && <span className="text-xs text-muted-foreground">No matching invoice found.</span>}
                    <button disabled={busy} className="text-xs text-muted-foreground underline disabled:opacity-50" onClick={() => act(() => ignoreBankTransaction(token as string, t.id), "Unable to ignore.")}>
                      Ignore
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
