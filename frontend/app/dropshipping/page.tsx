"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ShoppingBag } from "lucide-react";
import AppShell from "@/components/AppShell";
import { ToneBadge } from "@/components/dropshipping/parts";
import ProblemReports from "@/components/dropshipping/ProblemReports";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatCard } from "@/components/ui/StatCard";
import { DsOrder, StoreView, WorkQueueEntry, canCoordinate, describeError, getStoreSettings, listOrders, listSupplierOrders } from "@/lib/dropshippingApi";
import { money, ORDER_STATUS } from "@/lib/dropshippingLabels";
import { useAuth } from "@/lib/useAuth";

interface Today {
  toPlace: WorkQueueEntry[];
  refunds: DsOrder[];
  unpaid: DsOrder[];
  store: StoreView | null;
}

/** What needs a person right now. Each list is loaded on its own, so one failing never blanks the page. */
export default function DropshippingTodayPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [data, setData] = useState<Today | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    const [work, refunds, unpaid, store] = await Promise.allSettled([
      listSupplierOrders(token, "TO_PLACE"), listOrders(token, { status: "REFUND_REQUESTED" }), listOrders(token, { status: "PENDING_PAYMENT" }), getStoreSettings(token),
    ]);
    const failed = [work, refunds, unpaid, store].find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
    if (failed && [work, refunds, unpaid, store].every((r) => r.status === "rejected")) setError(describeError(failed.reason, "Unable to load the store.").message);
    setData({
      toPlace: work.status === "fulfilled" ? work.value.supplier_orders : [],
      refunds: refunds.status === "fulfilled" ? refunds.value.orders : [],
      unpaid: unpaid.status === "fulfilled" ? unpaid.value.orders : [],
      store: store.status === "fulfilled" ? store.value : null,
    });
    setLoading(false);
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const noted = data?.unpaid.filter((o) => o.status_note) ?? [];
  const nothingToDo = data && data.toPlace.length === 0 && data.refunds.length === 0 && noted.length === 0;

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <PageHeader title="Store today" description="What needs a person right now." icon={ShoppingBag} actions={<Link href="/dropshipping/orders" className="klaros-btn-secondary">All orders</Link>} />
        {authLoading || (loading && !data) ? (
          <Skeleton />
        ) : error ? (
          <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>{error}</Alert>
        ) : data ? (
          <div className="space-y-8">
            {data.store && !data.store.open_to_shoppers && (
              <Alert variant="warning" action={<Link href="/dropshipping/store" className="klaros-btn-secondary">Store settings</Link>}>
                The store is closed to shoppers.{data.store.blockers.length > 0 && ` Still needed: ${data.store.blockers.join(" ")}`}
              </Alert>
            )}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <StatCard label="To order from suppliers" value={data.toPlace.length} tone={data.toPlace.length ? "warning" : "neutral"} />
              <StatCard label="Refunds needed" value={data.refunds.length} tone={data.refunds.length ? "danger" : "neutral"} />
              <StatCard label="Waiting for payment" value={data.unpaid.length} />
              <StatCard label="Store" value={data.store ? (data.store.open_to_shoppers ? "Open" : "Closed") : "Unknown"} tone={data.store?.open_to_shoppers ? "success" : "neutral"} compact />
            </div>

            {token && <ProblemReports token={token} canDecide={canCoordinate(user?.role)} />}

            {nothingToDo && <EmptyState icon={ShoppingBag} title="Nothing needs you right now." />}

            {data.toPlace.length > 0 && (
              <section aria-label="To order from suppliers">
                <div className="mb-2 flex items-center justify-between"><h2 className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">To order from suppliers</h2><Link href="/dropshipping/fulfilment" className="klaros-btn-secondary">Open fulfilment</Link></div>
                <div className="klaros-table-wrap"><table className="klaros-table"><tbody>
                  {data.toPlace.slice(0, 5).map((w) => (
                    <tr key={w.id} className="border-t border-border"><td className="px-5 py-4 font-medium"><Link href={`/dropshipping/orders/${w.order_id}`}>{w.order_number}</Link></td><td className="px-5 py-4">{w.supplier ?? "Supplier"}</td><td className="px-5 py-4 text-muted">{w.items.map((i) => `${i.quantity} × ${i.name}`).join(", ")}</td></tr>
                  ))}
                </tbody></table></div>
              </section>
            )}

            {data.refunds.length > 0 && (
              <section aria-label="Refunds needed">
                <h2 className="mb-2 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Refunds needed</h2>
                <p className="mb-2 text-sm text-muted">Klaros does not move money. Refund these in Stripe.</p>
                <div className="klaros-table-wrap"><table className="klaros-table"><tbody>
                  {data.refunds.map((o) => (
                    <tr key={o.id} className="border-t border-border"><td className="px-5 py-4 font-medium"><Link href={`/dropshipping/orders/${o.id}`}>{o.order_number}</Link></td><td className="px-5 py-4">{money(o.total, o.currency)}</td><td className="px-5 py-4 text-muted">{o.status_note ?? ORDER_STATUS[o.status].hint}</td></tr>
                  ))}
                </tbody></table></div>
              </section>
            )}

            {noted.length > 0 && (
              <section aria-label="Payment notes">
                <h2 className="mb-2 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Payments to check</h2>
                <div className="klaros-table-wrap"><table className="klaros-table"><tbody>
                  {noted.map((o) => (
                    <tr key={o.id} className="border-t border-border"><td className="px-5 py-4 font-medium"><Link href={`/dropshipping/orders/${o.id}`}>{o.order_number}</Link></td><td className="px-5 py-4"><ToneBadge tone="warning">{ORDER_STATUS[o.status].label}</ToneBadge></td><td className="px-5 py-4 text-muted">{o.status_note}</td></tr>
                  ))}
                </tbody></table></div>
              </section>
            )}
          </div>
        ) : null}
      </div>
    </AppShell>
  );
}
