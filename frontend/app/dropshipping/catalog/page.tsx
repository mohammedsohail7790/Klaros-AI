"use client";

import { useCallback, useEffect, useState } from "react";
import { Boxes } from "lucide-react";
import AppShell from "@/components/AppShell";
import { AddProductDialog, ImportDialog } from "@/components/dropshipping/CatalogDialogs";
import { ConfirmAction, ProblemAlert, ToneBadge } from "@/components/dropshipping/parts";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { Availability, DsProduct, archiveProduct, canManage, describeError, listProducts, publishProduct, setAvailability } from "@/lib/dropshippingApi";
import { AVAILABILITY } from "@/lib/dropshippingLabels";
import { useAuth } from "@/lib/useAuth";

const STATUS_TONE = { DRAFT: "warning", ACTIVE: "success", ARCHIVED: "neutral" } as const;
const STATUS_LABEL = { DRAFT: "Draft", ACTIVE: "On sale", ARCHIVED: "Archived" } as const;

/** The catalog: what is for sale, at what price, from which supplier. Prices and costs are shown as stored; only managers change anything. */
export default function DropshippingCatalogPage() {
  const { token, user, loading: authLoading } = useAuth();
  const manage = canManage(user?.role);
  const [products, setProducts] = useState<DsProduct[] | null>(null);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<{ message: string; problems: string[] } | null>(null);
  const [dialog, setDialog] = useState<"add" | "import" | null>(null);
  const [archiving, setArchiving] = useState<DsProduct | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      setProducts((await listProducts(token, { status: status || undefined, q: query || undefined })).products);
    } catch (err) {
      setError(describeError(err, "Unable to load the catalog.").message);
    }
  }, [token, status, query]);

  useEffect(() => {
    load();
  }, [load]);

  async function attempt(fn: () => Promise<unknown>, fallback: string) {
    setRefusal(null);
    try {
      await fn();
      await load();
    } catch (err) {
      setRefusal(describeError(err, fallback));
    }
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <PageHeader
          title="Catalog" description="Products, prices and stock." icon={Boxes}
          actions={manage ? <div className="flex gap-2"><Button variant="secondary" onClick={() => setDialog("import")}>Import CSV</Button><Button onClick={() => setDialog("add")}>Add a product</Button></div> : undefined}
        />
        <form className="mb-6 flex flex-wrap items-center gap-3" onSubmit={(e) => { e.preventDefault(); setQuery(search.trim()); }}>
          <select aria-label="Filter by status" className="h-10 rounded-full bg-surface-muted px-4 text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All products</option><option value="ACTIVE">On sale</option><option value="DRAFT">Drafts</option><option value="ARCHIVED">Archived</option>
          </select>
          <Input aria-label="Search products" className="max-w-xs" placeholder="Search by name" value={search} onChange={(e) => setSearch(e.target.value)} />
          <Button type="submit" variant="secondary">Search</Button>
        </form>
        {refusal && <div className="mb-4"><ProblemAlert message={refusal.message} problems={refusal.problems} /></div>}
        {authLoading || (!products && !error) ? (
          <Skeleton />
        ) : error ? (
          <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>{error}</Alert>
        ) : products && products.length === 0 ? (
          <EmptyState icon={Boxes} title="No products yet." hint={manage ? "Add one, or import a CSV file." : undefined} />
        ) : (
          <ul className="space-y-4">
            {products!.map((p) => (
              <li key={p.id} aria-label={p.name} className="rounded-2xl border border-border p-5">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-medium text-foreground">{p.name}</span>
                  <ToneBadge tone={STATUS_TONE[p.status]}>{STATUS_LABEL[p.status]}</ToneBadge>
                  {p.category && <span className="text-sm text-muted">{p.category}</span>}
                  {!p.is_non_perishable && <ToneBadge tone="danger">Perishable</ToneBadge>}
                </div>
                <div className="klaros-table-wrap mt-3"><table className="klaros-table">
                  <thead><tr><th className="px-4 py-3">Item</th><th className="px-4 py-3">Supplier</th><th className="px-4 py-3">Price</th><th className="px-4 py-3">Cost</th><th className="px-4 py-3">Margin</th><th className="px-4 py-3">Stock</th></tr></thead>
                  <tbody>
                    {p.skus.map((k) => (
                      <tr key={k.id} className="border-t border-border">
                        <td className="px-4 py-3">{k.sku_code}</td><td className="px-4 py-3">{k.supplier ?? "—"}</td><td className="px-4 py-3">{k.price} {k.currency}</td>
                        <td className="px-4 py-3 text-muted">{k.supplier_cost ?? "—"}</td><td className="px-4 py-3 text-muted">{k.margin ?? "—"}</td>
                        <td className="px-4 py-3">
                          {manage ? (
                            <select aria-label={`Stock for ${k.sku_code}`} className="h-8 rounded-full bg-surface-muted px-3 text-xs" value={k.availability} onChange={(e) => attempt(() => setAvailability(token!, k.id, e.target.value as Availability), "Stock was not changed.")}>
                              {(Object.keys(AVAILABILITY) as Availability[]).map((a) => <option key={a} value={a}>{AVAILABILITY[a]}</option>)}
                            </select>
                          ) : AVAILABILITY[k.availability]}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table></div>
                {p.status === "DRAFT" && p.publish_problems.length > 0 && <p className="mt-3 text-sm text-muted">Not ready to publish: {p.publish_problems.join("; ")}</p>}
                {manage && p.status !== "ARCHIVED" && (
                  <div className="mt-3 flex gap-2">
                    {p.status === "DRAFT" && <Button size="sm" onClick={() => attempt(() => publishProduct(token!, p.id), "The product was not published.")}>Publish</Button>}
                    <Button size="sm" variant="secondary" onClick={() => setArchiving(p)}>Archive</Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {dialog === "add" && token && <AddProductDialog token={token} onDone={load} onClose={() => setDialog(null)} />}
      {dialog === "import" && token && <ImportDialog token={token} onDone={load} onClose={() => setDialog(null)} />}
      {archiving && <ConfirmAction danger title={`Archive ${archiving.name}?`} body="It stops being sold. Existing orders are not affected." confirmLabel="Archive" onConfirm={async () => { try { await archiveProduct(token!, archiving.id); await load(); } catch (e) { throw describeError(e, "Not archived."); } }} onClose={() => setArchiving(null)} />}
    </AppShell>
  );
}
