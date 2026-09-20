"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Users } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Customer,
  CustomerImportRow,
  bulkImportCustomers,
  createCustomer,
  searchCustomers,
} from "@/lib/api";
import { parseCsv } from "@/lib/csv";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";

export default function CustomersPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [showImport, setShowImport] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await searchCustomers(token, { q: q || undefined, limit: 50 });
      setCustomers(result.customers);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load customers.");
    } finally {
      setLoading(false);
    }
  }, [token, q]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <header className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Customers ({total})</h1>
          <div className="flex gap-2">
            <button
              onClick={() => setShowImport(true)}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
            >
              Import CSV
            </button>
            <button
              onClick={() => setShowCreate(true)}
              className="klaros-btn-primary"
            >
              New customer
            </button>
          </div>
        </header>

        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name or email..."
          className="mb-4 w-72 rounded-md border border-border-strong bg-surface-muted px-3 py-1.5 text-sm"
        />

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : customers.length === 0 ? (
          <EmptyState
            icon={Users}
            title="No customers yet."
            action={
              <div className="flex gap-2">
                <button
                  onClick={() => setShowImport(true)}
                  className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
                >
                  Import CSV
                </button>
                <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
                  New customer
                </button>
              </div>
            }
          />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Name</th>
                  <th className="px-4 py-2">Email</th>
                  <th className="px-4 py-2">Phone</th>
                  <th className="px-4 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {customers.map((c) => (
                  <tr key={c.id} className="border-t border-border hover:bg-surface">
                    <td className="px-4 py-2">
                      <Link href={`/customers/${c.id}`} className="hover:underline">
                        {c.name}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-muted">{c.email ?? "—"}</td>
                    <td className="px-4 py-2 text-muted">{c.phone ?? "—"}</td>
                    <td className="px-4 py-2">
                      <Badge status={c.status}>{c.status}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate && token && (
        <CreateCustomerModal token={token} onClose={() => setShowCreate(false)} onCreated={load} />
      )}
      {showImport && token && (
        <ImportCustomersModal token={token} onClose={() => setShowImport(false)} onImported={load} />
      )}
    </AppShell>
  );
}

function CreateCustomerModal({
  token,
  onClose,
  onCreated,
}: {
  token: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await createCustomer(token, { name, email: email || undefined, phone: phone || undefined });
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create customer.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title="New customer" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <input
          required
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="klaros-input"
        />
        <input
          placeholder="Email (optional)"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="klaros-input"
        />
        <input
          placeholder="Phone (optional)"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          className="klaros-input"
        />
        {error && <p className="text-sm text-danger">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="klaros-btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="klaros-btn-primary">
            {submitting ? "Creating..." : "Create customer"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

const IMPORT_FIELDS: { key: keyof CustomerImportRow; label: string; required?: boolean; aliases: string[] }[] = [
  { key: "name", label: "Name", required: true, aliases: ["name", "full name", "customer name", "contact"] },
  { key: "email", label: "Email", aliases: ["email", "email address"] },
  { key: "phone", label: "Phone", aliases: ["phone", "phone number", "mobile", "cell"] },
  { key: "company_name", label: "Company", aliases: ["company", "company name", "business"] },
  { key: "address", label: "Address", aliases: ["address", "street", "street address"] },
  { key: "city", label: "City", aliases: ["city"] },
  { key: "state", label: "State", aliases: ["state", "province"] },
  { key: "postal_code", label: "Postal code", aliases: ["zip", "zip code", "postal code", "postcode"] },
];

const UNMAPPED = "__none__";

function guessMapping(headers: string[]): Record<string, string> {
  const mapping: Record<string, string> = {};
  for (const field of IMPORT_FIELDS) {
    const match = headers.find((h) => field.aliases.includes(h.trim().toLowerCase()));
    mapping[field.key] = match ?? UNMAPPED;
  }
  return mapping;
}

function ImportCustomersModal({
  token,
  onClose,
  onImported,
}: {
  token: string;
  onClose: () => void;
  onImported: () => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [headers, setHeaders] = useState<string[] | null>(null);
  const [dataRows, setDataRows] = useState<string[][]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [fileName, setFileName] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [result, setResult] = useState<{ created_count: number; skipped_duplicate_count: number } | null>(null);

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setParseError(null);
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? "");
      const rows = parseCsv(text);
      if (rows.length < 1) {
        setParseError("That file doesn't look like a CSV — no rows found.");
        return;
      }
      const [headerRow, ...rest] = rows;
      setHeaders(headerRow);
      setDataRows(rest);
      setMapping(guessMapping(headerRow));
    };
    reader.onerror = () => setParseError("Unable to read that file.");
    reader.readAsText(file);
  }

  function buildRows(): CustomerImportRow[] {
    if (!headers) return [];
    const nameCol = mapping.name;
    if (!nameCol || nameCol === UNMAPPED) return [];
    const colIndex = (header: string) => headers.indexOf(header);

    const rows: CustomerImportRow[] = [];
    for (const row of dataRows) {
      const name = row[colIndex(nameCol)]?.trim();
      if (!name) continue;
      const record: CustomerImportRow = { name };
      for (const field of IMPORT_FIELDS) {
        if (field.key === "name") continue;
        const mapped = mapping[field.key];
        if (mapped && mapped !== UNMAPPED) {
          const value = row[colIndex(mapped)]?.trim();
          if (value) (record as unknown as Record<string, string>)[field.key] = value;
        }
      }
      rows.push(record);
    }
    return rows;
  }

  const previewRows = headers ? buildRows() : [];
  const skippedForNoName = headers ? dataRows.length - previewRows.length : 0;

  async function handleImport() {
    setImporting(true);
    setImportError(null);
    try {
      const rows = buildRows();
      const res = await bulkImportCustomers(token, rows);
      setResult(res);
      onImported();
    } catch (err) {
      setImportError(err instanceof ApiError ? err.message : "Import failed.");
    } finally {
      setImporting(false);
    }
  }

  return (
    <Modal title="Import customers from CSV" onClose={onClose} size="lg">
      <div className="space-y-4">
        {result ? (
          <>
            <div className="rounded-md border border-success/20 bg-success/[0.06] p-3 text-sm text-success">
              Imported {result.created_count} customer(s).
              {result.skipped_duplicate_count > 0 &&
                ` Skipped ${result.skipped_duplicate_count} row(s) matching an existing customer's email.`}
            </div>
            <div className="flex justify-end">
              <button onClick={onClose} className="klaros-btn-primary">
                Done
              </button>
            </div>
          </>
        ) : !headers ? (
          <>
            <p className="text-sm text-muted">
              Bring your existing customer list in from a spreadsheet export — pick a CSV file with a header
              row (Name, Email, Phone, etc.).
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,text/csv"
              onChange={handleFile}
              className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
            />
            {parseError && <p className="text-sm text-danger">{parseError}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-muted">
                Cancel
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="text-xs text-muted">
              {fileName} — {dataRows.length} row(s) found. Map your columns below (Name is required).
            </p>
            <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
              {IMPORT_FIELDS.map((field) => (
                <div key={field.key} className="flex items-center gap-2">
                  <label className="w-28 shrink-0 text-xs text-muted">
                    {field.label}
                    {field.required && " *"}
                  </label>
                  <select
                    value={mapping[field.key] ?? UNMAPPED}
                    onChange={(e) => setMapping((prev) => ({ ...prev, [field.key]: e.target.value }))}
                    className="flex-1 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                  >
                    <option value={UNMAPPED}>— Don&apos;t import —</option>
                    {headers.map((h) => (
                      <option key={h} value={h}>
                        {h}
                      </option>
                    ))}
                  </select>
                </div>
              ))}
            </div>

            <div className="rounded-md border border-border bg-surface-muted p-3 text-xs text-muted">
              {previewRows.length} row(s) will be imported.
              {skippedForNoName > 0 && ` ${skippedForNoName} row(s) skipped — no value in the mapped Name column.`}
            </div>

            {importError && <p className="text-sm text-danger">{importError}</p>}

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => {
                  setHeaders(null);
                  setDataRows([]);
                  if (fileInputRef.current) fileInputRef.current.value = "";
                }}
                className="rounded-md px-3 py-1.5 text-sm text-muted"
              >
                Choose a different file
              </button>
              <button
                onClick={handleImport}
                disabled={importing || previewRows.length === 0}
                className="klaros-btn-primary disabled:opacity-50"
              >
                {importing ? "Importing..." : `Import ${previewRows.length} customer(s)`}
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
