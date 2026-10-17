"use client";

import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { ProblemAlert } from "@/components/dropshipping/parts";
import {
  Availability, DsSupplier, ImportReport, commitImport, createProduct, createSupplier, describeError, listSuppliers, previewImport,
} from "@/lib/dropshippingApi";

type Err = { message: string; problems: string[] } | null;

/** One product with one item. More items, or many products at once, go in through the CSV import. */
export function AddProductDialog({ token, onDone, onClose }: { token: string; onDone: () => void; onClose: () => void }) {
  const [suppliers, setSuppliers] = useState<DsSupplier[] | null>(null);
  const [newSupplier, setNewSupplier] = useState("");
  const [f, setF] = useState({ name: "", category: "", description: "", sku_code: "", price: "", supplier: "", supplier_sku: "", supplier_cost: "", availability: "AVAILABLE" as Availability, perishable: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Err>(null);
  const set = (k: keyof typeof f, v: string | boolean) => setF((p) => ({ ...p, [k]: v }));

  useEffect(() => {
    listSuppliers(token).then((r) => setSuppliers(r.suppliers)).catch((e) => { setSuppliers([]); setError(describeError(e, "Unable to load suppliers.")); });
  }, [token]);

  async function addSupplier() {
    setBusy(true);
    setError(null);
    try {
      const s = (await createSupplier(token, newSupplier.trim())).supplier;
      setSuppliers((p) => [...(p ?? []).filter((x) => x.id !== s.id), s]);
      set("supplier", s.name);
      setNewSupplier("");
    } catch (e) {
      setError(describeError(e, "Unable to add the supplier."));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await createProduct(token, {
        name: f.name.trim(), category: f.category.trim() || undefined, description: f.description.trim() || undefined, is_non_perishable: !f.perishable,
        skus: [{ sku_code: f.sku_code.trim(), price: f.price.trim(), supplier: f.supplier, supplier_sku: f.supplier_sku.trim() || undefined, supplier_cost: f.supplier_cost.trim() || undefined, availability: f.availability }],
      });
      onDone();
      onClose();
    } catch (e) {
      setError(describeError(e, "The product was not saved."));
    } finally {
      setBusy(false);
    }
  }

  const ready = f.name.trim() && f.sku_code.trim() && f.price.trim() && f.supplier;
  return (
    <Modal title="Add a product" onClose={onClose} size="lg">
      <div className="space-y-4">
        <p className="text-sm text-muted">The product is saved as a draft. It goes on sale only when you publish it.</p>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name"><Input value={f.name} onChange={(e) => set("name", e.target.value)} autoFocus /></Field>
          <Field label="Category (optional)"><Input value={f.category} onChange={(e) => set("category", e.target.value)} /></Field>
          <Field label="Item code (yours)"><Input value={f.sku_code} onChange={(e) => set("sku_code", e.target.value)} /></Field>
          <Field label="Price to shoppers (USD)"><Input inputMode="decimal" value={f.price} onChange={(e) => set("price", e.target.value)} /></Field>
          <Field label="Supplier">
            <select className="h-10 w-full rounded-xl bg-surface-muted px-3 text-sm" value={f.supplier} onChange={(e) => set("supplier", e.target.value)}>
              <option value="">Choose a supplier</option>
              {(suppliers ?? []).map((s) => <option key={s.id} value={s.name}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="Supplier's code for it (optional)"><Input value={f.supplier_sku} onChange={(e) => set("supplier_sku", e.target.value)} /></Field>
          <Field label="Your cost (optional)"><Input inputMode="decimal" value={f.supplier_cost} onChange={(e) => set("supplier_cost", e.target.value)} /></Field>
          <Field label="Stock">
            <select className="h-10 w-full rounded-xl bg-surface-muted px-3 text-sm" value={f.availability} onChange={(e) => set("availability", e.target.value)}>
              <option value="AVAILABLE">In stock</option><option value="OUT_OF_STOCK">Out of stock</option><option value="UNKNOWN">Unknown</option>
            </select>
          </Field>
        </div>
        <Field label="Description (optional)"><Input value={f.description} onChange={(e) => set("description", e.target.value)} /></Field>
        <label className="flex items-start gap-3 text-[15px]"><input type="checkbox" className="mt-1" checked={f.perishable} onChange={(e) => set("perishable", e.target.checked)} /><span>This is perishable (chilled, frozen or short-dated). The store cannot sell perishable items.</span></label>
        <div className="flex items-end gap-2"><div className="flex-1"><Field label="Supplier not listed? Add one"><Input value={newSupplier} onChange={(e) => setNewSupplier(e.target.value)} placeholder="Supplier name" /></Field></div><Button variant="secondary" onClick={addSupplier} disabled={busy || newSupplier.trim() === ""}>Add supplier</Button></div>
        {error && <ProblemAlert message={error.message} problems={error.problems} />}
        <div className="flex justify-end gap-2"><Button variant="secondary" onClick={onClose} disabled={busy}>Not now</Button><Button onClick={save} disabled={busy || !ready}>{busy ? "Saving…" : "Save as draft"}</Button></div>
      </div>
    </Modal>
  );
}

/** Preview first, then commit. Nothing is saved until the whole file passes; the file you commit is exactly the one previewed. */
export function ImportDialog({ token, onDone, onClose }: { token: string; onDone: () => void; onClose: () => void }) {
  const [csv, setCsv] = useState("");
  const [report, setReport] = useState<ImportReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Err>(null);
  const [done, setDone] = useState<ImportReport | null>(null);

  async function readFile(file: File | undefined) {
    if (!file) return;
    setCsv(await file.text());
    setReport(null);
    setError(null);
  }

  async function preview() {
    setBusy(true);
    setError(null);
    try {
      setReport(await previewImport(token, csv));
    } catch (e) {
      setError(describeError(e, "The file could not be checked."));
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    if (!report?.token) return;
    setBusy(true);
    setError(null);
    try {
      setDone(await commitImport(token, csv, report.token));
      onDone();
    } catch (e) {
      setError(describeError(e, "The import was refused. Nothing was saved."));
    } finally {
      setBusy(false);
    }
  }

  const bad = report?.rows.filter((r) => r.problems.length > 0) ?? [];
  return (
    <Modal title="Import a catalog (CSV)" onClose={onClose} size="lg">
      <div className="space-y-4">
        {done ? (
          <>
            <Alert variant="success">Imported. {done.summary.products_created} new products, {done.summary.skus_created} new items, {done.summary.skus_updated} updated. They are drafts until you publish them.</Alert>
            <div className="flex justify-end"><Button onClick={onClose}>Close</Button></div>
          </>
        ) : (
          <>
            <p className="text-sm text-muted">Choose a CSV file or paste it below. We check the whole file first and save nothing until you confirm. If any row has a problem, nothing is saved.</p>
            <input type="file" accept=".csv,text/csv" aria-label="CSV file" onChange={(e) => readFile(e.target.files?.[0])} />
            <textarea aria-label="CSV text" className="h-40 w-full rounded-xl bg-surface-muted p-3 font-mono text-xs" value={csv} onChange={(e) => { setCsv(e.target.value); setReport(null); }} placeholder="sku_code,product_name,price,supplier,…" />
            {error && <ProblemAlert message={error.message} problems={error.problems} />}
            {report && (
              <div className="space-y-3" aria-label="Import check">
                {report.file_problems.length > 0 && <ProblemAlert message="The file cannot be imported." problems={report.file_problems} />}
                {report.file_problems.length === 0 && (
                  <Alert variant={report.ok ? "success" : "danger"}>
                    {report.ok
                      ? `Ready: ${report.summary.rows} rows. ${report.summary.products_created} new products, ${report.summary.skus_created} new items, ${report.summary.skus_updated} updated, ${report.summary.skus_unchanged} unchanged.`
                      : `${report.summary.rejected} of ${report.summary.rows} rows have problems. Fix them in the file and check again. Nothing was saved.`}
                  </Alert>
                )}
                {bad.length > 0 && (
                  <ul className="max-h-48 space-y-1 overflow-y-auto text-sm">{bad.map((r) => <li key={r.row}><strong>Row {r.row}{r.sku_code ? ` (${r.sku_code})` : ""}:</strong> {r.problems.join("; ")}</li>)}</ul>
                )}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
              <Button variant="secondary" onClick={preview} disabled={busy || csv.trim() === ""}>{busy && !report ? "Checking…" : "Check the file"}</Button>
              <Button onClick={commit} disabled={busy || !report?.ok || !report.token}>Import</Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
