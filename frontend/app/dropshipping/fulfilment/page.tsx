"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Truck } from "lucide-react";
import AppShell from "@/components/AppShell";
import { ConfirmAction, ProblemAlert, ToneBadge } from "@/components/dropshipping/parts";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  SupplierOrderStatus, WorkQueueEntry, addShipment, canCoordinate, confirmSupplierOrder, describeError, downloadSupplierOrdersCsv, failSupplierOrder, listSupplierOrders, markShipment, placeSupplierOrder,
} from "@/lib/dropshippingApi";
import { addressBlock, SUPPLIER_STATUS } from "@/lib/dropshippingLabels";
import { useAuth } from "@/lib/useAuth";

const TABS: { value: SupplierOrderStatus; label: string }[] = [
  { value: "TO_PLACE", label: "To order" }, { value: "PLACED", label: "Placed" }, { value: "CONFIRMED", label: "Confirmed" }, { value: "FAILED", label: "Failed" },
];

type Pending =
  | { kind: "place" | "fail" | "confirm" | "track"; entry: WorkQueueEntry }
  | { kind: "delivered" | "problem"; entry: WorkQueueEntry; shipmentId: string }
  | null;

function TrackingDialog({ onSave, onClose }: { onSave: (b: { carrier?: string; tracking_number: string; tracking_url?: string }) => Promise<void>; onClose: () => void }) {
  const [carrier, setCarrier] = useState("");
  const [number, setNumber] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; problems: string[] } | null>(null);
  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onSave({ carrier: carrier.trim() || undefined, tracking_number: number.trim(), tracking_url: url.trim() || undefined });
      onClose();
    } catch (err) {
      setError(err as { message: string; problems: string[] });
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="Add tracking" onClose={onClose}>
      <div className="space-y-4">
        <Field label="Carrier (optional)"><Input value={carrier} onChange={(e) => setCarrier(e.target.value)} placeholder="UPS, USPS, FedEx…" /></Field>
        <Field label="Tracking number"><Input value={number} onChange={(e) => setNumber(e.target.value)} autoFocus /></Field>
        <Field label="Tracking link (optional, https)"><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" /></Field>
        {error && <ProblemAlert message={error.message} problems={error.problems} />}
        <div className="flex justify-end gap-2"><Button variant="secondary" onClick={onClose} disabled={busy}>Not now</Button><Button onClick={save} disabled={busy || number.trim() === ""}>{busy ? "Saving…" : "Save tracking"}</Button></div>
      </div>
    </Modal>
  );
}

function CopyAddress({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button size="sm" variant="secondary" onClick={async () => { try { await navigator.clipboard.writeText(text); setCopied(true); } catch { setCopied(false); } }}>
      {copied ? "Copied" : "Copy address"}
    </Button>
  );
}

/** The supplier work queue: exactly what to order from whom and where it ships, with the steps to record that it was ordered, shipped and delivered. */
export default function DropshippingFulfilmentPage() {
  const { token, user, loading: authLoading } = useAuth();
  const coordinate = canCoordinate(user?.role);
  const [tab, setTab] = useState<SupplierOrderStatus>("TO_PLACE");
  const [entries, setEntries] = useState<WorkQueueEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  async function exportCsv() {
    setExportError(null);
    try {
      await downloadSupplierOrdersCsv(token!, tab);
    } catch (err) {
      setExportError(describeError(err, "Could not make the file.").message);
    }
  }

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      setEntries((await listSupplierOrders(token, tab)).supplier_orders);
    } catch (err) {
      setError(describeError(err, "Unable to load the work queue.").message);
    }
  }, [token, tab]);

  useEffect(() => {
    setEntries(null);
    load();
  }, [load]);

  // Runs an action; a refusal is rethrown as {message, problems} for the dialog, and the queue reloads after a success.
  const act = (fn: () => Promise<unknown>) => async () => {
    try {
      await fn();
      await load();
    } catch (err) {
      throw describeError(err, "That did not work.");
    }
  };

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <PageHeader title="Fulfilment" description="What to order from each supplier, and where it ships." icon={Truck} />
        <div role="tablist" aria-label="Supplier order status" className="mb-6 flex flex-wrap gap-2">
          {TABS.map((t) => (
            <button key={t.value} role="tab" aria-selected={tab === t.value} onClick={() => setTab(t.value)} className={`rounded-full px-4 py-2 text-sm font-medium ${tab === t.value ? "bg-accent text-accent-foreground" : "bg-surface-muted text-foreground"}`}>{t.label}</button>
          ))}
        </div>
        {coordinate && entries && entries.length > 0 && (
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <Button size="sm" variant="secondary" onClick={exportCsv}>Download for the supplier (CSV)</Button>
            <span className="text-sm text-muted">One row per item, with the delivery address. Staff only.</span>
          </div>
        )}
        {exportError && <div className="mb-4"><Alert variant="danger">{exportError}</Alert></div>}
        {!coordinate && <div className="mb-4"><Alert variant="info">You can look at the queue. Only staff, managers and owners can record what was ordered.</Alert></div>}
        {authLoading || (!entries && !error) ? (
          <Skeleton />
        ) : error ? (
          <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>{error}</Alert>
        ) : entries && entries.length === 0 ? (
          <EmptyState icon={Truck} title={tab === "TO_PLACE" ? "Nothing to order from suppliers right now." : "Nothing here."} />
        ) : (
          <ul className="space-y-4">
            {entries!.map((e) => (
              <li key={e.id} aria-label={`Supplier order for ${e.order_number}`} className="rounded-2xl border border-border p-5">
                <div className="flex flex-wrap items-center gap-3">
                  <Link href={`/dropshipping/orders/${e.order_id}`} className="font-medium text-foreground">{e.order_number}</Link>
                  <span className="text-muted">{e.supplier ?? "Supplier"}</span>
                  <ToneBadge tone={SUPPLIER_STATUS[e.status].tone}>{SUPPLIER_STATUS[e.status].label}</ToneBadge>
                  {e.supplier_reference && <span className="text-sm text-muted">Their order {e.supplier_reference}</span>}
                </div>
                {e.note && <p className="mt-2 text-sm text-muted">{e.note}</p>}
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  <div>
                    <h3 className="mb-1 text-sm font-medium text-muted">Order these</h3>
                    <ul className="space-y-1 text-[15px]">{e.items.map((i) => <li key={i.sku_code}>{i.quantity} × {i.name} <span className="text-muted">(supplier code {i.supplier_sku ?? "none recorded"})</span></li>)}</ul>
                  </div>
                  <div>
                    <h3 className="mb-1 text-sm font-medium text-muted">Ship to</h3>
                    {e.ship_to ? (<><pre className="whitespace-pre-wrap font-sans text-[15px]">{addressBlock(e.ship_to)}</pre><div className="mt-2"><CopyAddress text={addressBlock(e.ship_to)} /></div></>) : <p className="text-muted">Address erased.</p>}
                  </div>
                </div>
                {e.shipments.length > 0 && (
                  <ul className="mt-4 space-y-2">
                    {e.shipments.map((sh) => (
                      <li key={sh.id} className="flex flex-wrap items-center gap-3 text-sm"><span>{sh.carrier ?? "Carrier"} {sh.tracking_number}</span><ToneBadge tone={sh.status === "DELIVERED" ? "success" : sh.status === "EXCEPTION" ? "danger" : "info"}>{sh.status.toLowerCase()}</ToneBadge>
                        {coordinate && sh.status === "SHIPPED" && (<><Button size="sm" variant="secondary" onClick={() => setPending({ kind: "delivered", entry: e, shipmentId: sh.id })}>Mark delivered</Button><Button size="sm" variant="ghost" onClick={() => setPending({ kind: "problem", entry: e, shipmentId: sh.id })}>Report a problem</Button></>)}
                      </li>
                    ))}
                  </ul>
                )}
                {coordinate && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {(e.status === "TO_PLACE" || e.status === "FAILED") && <Button size="sm" onClick={() => setPending({ kind: "place", entry: e })}>Mark as ordered</Button>}
                    {(e.status === "TO_PLACE" || e.status === "PLACED") && <Button size="sm" variant="secondary" onClick={() => setPending({ kind: "fail", entry: e })}>Mark as failed</Button>}
                    {e.status === "PLACED" && <Button size="sm" variant="secondary" onClick={() => setPending({ kind: "confirm", entry: e })}>Supplier confirmed</Button>}
                    {(e.status === "PLACED" || e.status === "CONFIRMED") && <Button size="sm" variant="secondary" onClick={() => setPending({ kind: "track", entry: e })}>Add tracking</Button>}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {pending?.kind === "place" && <ConfirmAction title={`Ordered from ${pending.entry.supplier ?? "the supplier"}`} body="Enter the supplier's own order number once you have placed the order with them." fieldLabel="Supplier order number" confirmLabel="Mark as ordered" onConfirm={(text) => act(() => placeSupplierOrder(token!, pending.entry.id, text))()} onClose={() => setPending(null)} />}
      {pending?.kind === "fail" && <ConfirmAction danger title="Mark as failed?" body="Use this if the supplier could not fill the order. It stays in the failed list so it can be fixed." fieldLabel="What went wrong" confirmLabel="Mark as failed" onConfirm={(text) => act(() => failSupplierOrder(token!, pending.entry.id, text))()} onClose={() => setPending(null)} />}
      {pending?.kind === "confirm" && <ConfirmAction title="Supplier confirmed?" body="Record that the supplier has confirmed they will ship this order." confirmLabel="Yes, confirmed" onConfirm={() => act(() => confirmSupplierOrder(token!, pending.entry.id))()} onClose={() => setPending(null)} />}
      {pending?.kind === "track" && <TrackingDialog onSave={(b) => act(() => addShipment(token!, pending.entry.id, b))()} onClose={() => setPending(null)} />}
      {pending?.kind === "delivered" && <ConfirmAction title="Mark as delivered?" body="Record that this parcel reached the shopper. When every parcel is delivered the order is delivered." confirmLabel="Mark delivered" onConfirm={() => act(() => markShipment(token!, pending.shipmentId, "delivered"))()} onClose={() => setPending(null)} />}
      {pending?.kind === "problem" && <ConfirmAction danger title="Report a problem" body="Use this if the parcel is lost, damaged or stuck. Say what happened." fieldLabel="What happened" confirmLabel="Report" onConfirm={(text) => act(() => markShipment(token!, pending.shipmentId, "exception", text))()} onClose={() => setPending(null)} />}
    </AppShell>
  );
}
