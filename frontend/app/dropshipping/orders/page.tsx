"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Package } from "lucide-react";
import AppShell from "@/components/AppShell";
import { ToneBadge } from "@/components/dropshipping/parts";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { DsOrder, describeError, listOrders } from "@/lib/dropshippingApi";
import { money, ORDER_STATUS, shortDate, STATUS_FILTERS } from "@/lib/dropshippingLabels";
import { useAuth } from "@/lib/useAuth";

/** Every order, newest first. Only the shopper's name and city appear here; the full address is on the order itself. */
export default function DropshippingOrdersPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [orders, setOrders] = useState<DsOrder[] | null>(null);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      setOrders((await listOrders(token, { status: status || undefined, q: query || undefined })).orders);
    } catch (err) {
      setError(describeError(err, "Unable to load orders.").message);
    }
  }, [token, status, query]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <PageHeader title="Orders" description="Every store order and where it stands." icon={Package} />
        <form className="mb-6 flex flex-wrap items-center gap-3" onSubmit={(e) => { e.preventDefault(); setQuery(search.trim()); }}>
          <select aria-label="Filter by status" className="h-10 rounded-full bg-surface-muted px-4 text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
          <Input aria-label="Order number" className="max-w-xs" placeholder="Order number, for example DS-7K3M" value={search} onChange={(e) => setSearch(e.target.value)} />
          <Button type="submit" variant="secondary">Search</Button>
        </form>
        {authLoading || (!orders && !error) ? (
          <Skeleton />
        ) : error ? (
          <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>{error}</Alert>
        ) : orders && orders.length === 0 ? (
          <EmptyState icon={Package} title="No orders match." />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead><tr><th className="px-5 py-4">Order</th><th className="px-5 py-4">Placed</th><th className="px-5 py-4">Shopper</th><th className="px-5 py-4">Status</th><th className="px-5 py-4">Total</th></tr></thead>
              <tbody>
                {orders!.map((o) => (
                  <tr key={o.id} className="border-t border-border">
                    <td className="px-5 py-4 font-medium text-foreground"><Link href={`/dropshipping/orders/${o.id}`}>{o.order_number}</Link></td>
                    <td className="px-5 py-4 text-muted">{shortDate(o.placed_at)}</td>
                    <td className="px-5 py-4">{o.personal_data_erased ? <span className="text-muted">Erased</span> : o.ship_to ? `${o.ship_to.name}, ${o.ship_to.city}` : "—"}</td>
                    <td className="px-5 py-4"><ToneBadge tone={ORDER_STATUS[o.status].tone}>{ORDER_STATUS[o.status].label}</ToneBadge></td>
                    <td className="px-5 py-4">{money(o.total, o.currency)}</td>
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
