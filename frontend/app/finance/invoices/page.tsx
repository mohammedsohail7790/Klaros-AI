"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Receipt } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Invoice,
  InvoiceImportResult,
  InvoiceImportRow,
  bulkImportInvoices,
  listInvoices,
} from "@/lib/api";
import { parseCsv } from "@/lib/csv";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
const STATUS_TABS = ["ALL", "DRAFT", "PENDING_APPROVAL", "APPROVED", "SENT", "PARTIALLY_PAID", "PAID", "OVERDUE", "VOID"];

export default function InvoicesPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState("ALL");
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listInvoices(token, status === "ALL" ? {} : { status });
      setInvoices(result.invoices);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load invoices.");
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
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Invoices</h1>
          <button
            onClick={() => setShowImport(true)}
            className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
          >
            Import CSV
          </button>
        </div>

        <div className="mb-4 flex flex-wrap gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`rounded-full border px-3 py-1 text-xs ${
                status === s ? "border-foreground bg-surface text-foreground" : "border-border-strong text-muted"
              }`}
            >
              {s}
            </button>
          ))}
        </div>

        {authLoading || loading ? (
          <p className="text-sm text-muted">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : invoices.length === 0 ? (
          <EmptyState
            icon={Receipt}
            title="No invoices."
            action={
              <button
                onClick={() => setShowImport(true)}
                className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
              >
                Import CSV
              </button>
            }
          />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Number</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Due date</th>
                  <th className="px-4 py-2">Total</th>
                  <th className="px-4 py-2">Amount due</th>
                </tr>
              </thead>
              <tbody>
                {invoices.map((inv) => (
                  <tr key={inv.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <Link href={`/finance/invoices/${inv.id}`} className="underline hover:text-foreground">
                        {inv.invoice_number}
                      </Link>
                    </td>
                    <td className="px-4 py-2">
                      <Badge status={inv.status}>{inv.status}</Badge>
                    </td>
                    <td className="px-4 py-2 text-muted">{inv.due_date}</td>
                    <td className="px-4 py-2">${inv.total}</td>
                    <td className="px-4 py-2 text-muted">${inv.amount_due}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showImport && token && (
        <ImportInvoicesModal token={token} onClose={() => setShowImport(false)} onImported={load} />
      )}
    </AppShell>
  );
}

const IMPORT_FIELDS: { key: keyof InvoiceImportRow; label: string; required?: boolean; aliases: string[] }[] = [
  { key: "customer_name", label: "Customer name", required: true, aliases: ["customer", "customer name", "name", "client"] },
  { key: "customer_email", label: "Customer email", aliases: ["email", "customer email"] },
  { key: "invoice_number", label: "Invoice #", aliases: ["invoice number", "invoice #", "invoice", "number"] },
  { key: "issue_date", label: "Issue date", required: true, aliases: ["issue date", "date", "invoice date"] },
  { key: "due_date", label: "Due date", required: true, aliases: ["due date", "due"] },
  { key: "amount", label: "Amount", required: true, aliases: ["amount", "total", "balance"] },
  { key: "amount_paid", label: "Amount paid", aliases: ["amount paid", "paid"] },
  { key: "description", label: "Description", aliases: ["description", "memo", "notes"] },
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

function toIsoDate(value: string): string | null {
  const trimmed = value.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) return trimmed;
  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toISOString().slice(0, 10);
}

function ImportInvoicesModal({
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
  const [result, setResult] = useState<InvoiceImportResult | null>(null);

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

  function buildRows(): { rows: InvoiceImportRow[]; invalidCount: number } {
    if (!headers) return { rows: [], invalidCount: 0 };
    const colIndex = (header: string) => headers.indexOf(header);
    const get = (row: string[], key: string) => {
      const mapped = mapping[key];
      if (!mapped || mapped === UNMAPPED) return "";
      return row[colIndex(mapped)]?.trim() ?? "";
    };

    const rows: InvoiceImportRow[] = [];
    let invalidCount = 0;
    for (const row of dataRows) {
      const customer_name = get(row, "customer_name");
      const amountRaw = get(row, "amount");
      const issueRaw = get(row, "issue_date");
      const dueRaw = get(row, "due_date");
      if (!customer_name || !amountRaw || !issueRaw || !dueRaw) {
        invalidCount += 1;
        continue;
      }
      const issue_date = toIsoDate(issueRaw);
      const due_date = toIsoDate(dueRaw);
      const amount = amountRaw.replace(/[$,]/g, "");
      if (!issue_date || !due_date || Number.isNaN(Number(amount))) {
        invalidCount += 1;
        continue;
      }
      const record: InvoiceImportRow = { customer_name, issue_date, due_date, amount };
      const email = get(row, "customer_email");
      const invoiceNumber = get(row, "invoice_number");
      const amountPaid = get(row, "amount_paid");
      const description = get(row, "description");
      if (email) record.customer_email = email;
      if (invoiceNumber) record.invoice_number = invoiceNumber;
      if (amountPaid) record.amount_paid = amountPaid.replace(/[$,]/g, "");
      if (description) record.description = description;
      rows.push(record);
    }
    return { rows, invalidCount };
  }

  const preview = headers ? buildRows() : { rows: [], invalidCount: 0 };

  async function handleImport() {
    setImporting(true);
    setImportError(null);
    try {
      const { rows } = buildRows();
      const res = await bulkImportInvoices(token, rows);
      setResult(res);
      onImported();
    } catch (err) {
      setImportError(err instanceof ApiError ? err.message : "Import failed.");
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-background/60 px-4 py-8 backdrop-blur-sm">
      <div className="w-full max-w-xl space-y-4 rounded-lg border border-border bg-surface p-6">
        <h2 className="font-display text-xl text-foreground">Import open invoices from CSV</h2>

        {result ? (
          <>
            <div className="rounded-md border border-emerald-200 bg-emerald-50/30 p-3 text-sm text-emerald-700">
              Imported {result.created_count} invoice(s)
              {result.customers_created_count > 0 && `, creating ${result.customers_created_count} new customer(s)`}.
              {result.skipped_count > 0 && ` ${result.skipped_count} row(s) skipped — see below.`}
            </div>
            {result.results.some((r) => r.status === "skipped") && (
              <div className="max-h-32 space-y-1 overflow-y-auto text-xs text-muted">
                {result.results
                  .filter((r) => r.status === "skipped")
                  .map((r, i) => (
                    <div key={i}>
                      {r.customer_name}: {r.reason}
                    </div>
                  ))}
              </div>
            )}
            <div className="flex justify-end">
              <button onClick={onClose} className="klaros-btn-primary">
                Done
              </button>
            </div>
          </>
        ) : !headers ? (
          <>
            <p className="text-sm text-muted">
              Bring your currently-owed invoices in from a spreadsheet or accounting export. Each row lands as
              a real invoice — already SENT (or PAID/PARTIALLY_PAID if you map an amount paid) — feeding AR
              aging and collections from day one, never replayed through the normal draft/approval flow.
              Customers are matched by email, then by exact name, or created if neither matches.
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,text/csv"
              onChange={handleFile}
              className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
            />
            {parseError && <p className="text-sm text-red-600">{parseError}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-muted">
                Cancel
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="text-xs text-muted">
              {fileName} — {dataRows.length} row(s) found. Map your columns below (Customer name, Amount, Issue
              date, and Due date are required). Dates like "8/1/2026" and amounts like "$1,250.00" are parsed
              automatically.
            </p>
            <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
              {IMPORT_FIELDS.map((field) => (
                <div key={field.key} className="flex items-center gap-2">
                  <label className="w-32 shrink-0 text-xs text-muted">
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
              {preview.rows.length} row(s) will be imported.
              {preview.invalidCount > 0 &&
                ` ${preview.invalidCount} row(s) skipped — missing or unparseable Customer name/Amount/Issue date/Due date.`}
            </div>

            {importError && <p className="text-sm text-red-600">{importError}</p>}

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
                disabled={importing || preview.rows.length === 0}
                className="klaros-btn-primary disabled:opacity-50"
              >
                {importing ? "Importing..." : `Import ${preview.rows.length} invoice(s)`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
