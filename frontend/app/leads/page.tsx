"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronRight, Users } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, LeadBoardRow, LeadImportRow, bulkImportLeads, createLead, getLeadBoard } from "@/lib/api";
import { parseCsv } from "@/lib/csv";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { AiStatePill } from "@/components/business/AiState";
import { LEAD_STATUS_ORDER, leadStatusLabel, sourceLabel, titleCase, when } from "@/lib/opsLabels";

const STATUS_TABS = ["ALL", "NEW", "CONTACTED", "QUALIFIED", "BOOKED", "LOST"];
const SOURCES = ["PHONE", "WEB", "CHAT", "TEXT", "DM", "MARKETPLACE", "REFERRAL", "WALK_IN"];
const PRIORITY: Record<string, { label: string; tone: string }> = {
  LOW: { label: "Low", tone: "text-muted" },
  MEDIUM: { label: "Medium", tone: "text-foreground" },
  HIGH: { label: "High", tone: "text-warning font-medium" },
  EMERGENCY: { label: "Emergency", tone: "text-danger font-medium" },
};

const priority = (p: string) => PRIORITY[p] ?? { label: titleCase(p.toLowerCase()), tone: "text-muted" };

export default function LeadsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [leads, setLeads] = useState<LeadBoardRow[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("ALL");
  const [source, setSource] = useState("");
  // Deep links from the operating console ("/leads?status=NEW"). Read once on the client.
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("status");
    if (wanted && LEAD_STATUS_ORDER.includes(wanted)) setStatus(wanted);
  }, []);
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const limit = 20;

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getLeadBoard(token, {
        status: status === "ALL" ? undefined : status,
        source: source || undefined,
        q: q || undefined,
        limit,
        offset,
      });
      setLeads(result.leads);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load leads.");
    } finally {
      setLoading(false);
    }
  }, [token, status, source, q, offset]);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = status !== "ALL" || source !== "" || q !== "";

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
        <PageHeader
          title="Leads"
          icon={Users}
          description="Everyone who has enquired — with what each one needs next."
          actions={
            <>
              <button onClick={() => setShowImport(true)} className="klaros-btn-secondary text-sm">Import CSV</button>
              <button onClick={() => setShowCreate(true)} className="klaros-btn-primary text-sm">New lead</button>
            </>
          }
        />

        <div className="mb-4 flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Filter by status" className="flex flex-wrap gap-2">
            {STATUS_TABS.map((s) => (
              <button
                key={s}
                aria-pressed={status === s}
                onClick={() => {
                  setStatus(s);
                  setOffset(0);
                }}
                className={`klaros-filter-tab ${status === s ? "border-foreground bg-surface text-foreground" : "border-border-strong text-muted hover:text-foreground"}`}
              >
                {s === "ALL" ? "All" : leadStatusLabel(s)}
              </button>
            ))}
          </div>
          <select
            aria-label="Filter by source"
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              setOffset(0);
            }}
            className="klaros-input !w-auto !py-1 text-xs"
          >
            <option value="">All sources</option>
            {SOURCES.map((s) => (
              <option key={s} value={s}>{sourceLabel(s)}</option>
            ))}
          </select>
          <input
            value={q}
            aria-label="Search leads"
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
            placeholder="Search name, email, service…"
            className="klaros-input ml-auto !w-full sm:!w-64"
          />
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div role="alert" className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : leads.length === 0 ? (
          <EmptyState
            icon={Users}
            title={filtered ? "No leads match these filters." : "No leads yet — they appear here when someone submits your website form."}
            action={
              filtered ? (
                <button onClick={() => { setStatus("ALL"); setSource(""); setQ(""); setOffset(0); }} className="klaros-btn-secondary text-sm">Clear filters</button>
              ) : (
                <div className="flex gap-2">
                  <button onClick={() => setShowImport(true)} className="klaros-btn-secondary text-sm">Import CSV</button>
                  <button onClick={() => setShowCreate(true)} className="klaros-btn-primary text-sm">New lead</button>
                </div>
              )
            }
          />
        ) : (
          <>
            {/* Wide screens: one row per lead */}
            <div className="klaros-table-wrap hidden lg:block">
              <table className="klaros-table">
                <thead>
                  <tr>
                    <th scope="col">Lead</th>
                    <th scope="col">Status</th>
                    <th scope="col">Priority</th>
                    <th scope="col">Country</th>
                    <th scope="col">Next action</th>
                    <th scope="col">AI interaction</th>
                    <th scope="col">Assigned</th>
                    <th scope="col">Received</th>
                  </tr>
                </thead>
                <tbody>
                  {leads.map((l) => (
                    <tr key={l.id}>
                      <td>
                        <Link href={`/leads/${l.id}`} className="font-medium text-foreground hover:underline">{l.name}</Link>
                        <p className="text-xs text-muted">{[l.service, sourceLabel(l.source)].filter(Boolean).join(" · ")}</p>
                      </td>
                      <td><Badge status={l.status}>{leadStatusLabel(l.status)}</Badge></td>
                      <td className={priority(l.priority).tone}>{priority(l.priority).label}</td>
                      <td className="text-muted">{l.country ?? "—"}</td>
                      <td className="max-w-[16rem]">
                        {l.next_action ? <Link href={l.next_action.route} className="text-foreground hover:underline">{l.next_action.text}</Link> : <span className="text-muted">Nothing to do</span>}
                      </td>
                      <td><AiStatePill state={l.ai_state} /></td>
                      <td className="text-muted">{l.assigned_to ?? "Unassigned"}</td>
                      <td className="whitespace-nowrap text-muted">{when(l.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Narrow screens: one card per lead — no sideways scrolling */}
            <ul className="space-y-3 lg:hidden" aria-label="Leads">
              {leads.map((l) => (
                <li key={l.id}>
                  <Link href={`/leads/${l.id}`} className="klaros-card klaros-card-interactive block p-4">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate font-medium text-foreground">{l.name}</p>
                        <p className="text-xs text-muted">{[l.service, sourceLabel(l.source), l.country].filter(Boolean).join(" · ")}</p>
                      </div>
                      <Badge status={l.status}>{leadStatusLabel(l.status)}</Badge>
                    </div>
                    <p className="mt-2 text-sm text-foreground">
                      <span className="text-muted">Next: </span>
                      {l.next_action ? l.next_action.text : "Nothing to do"}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
                      <AiStatePill state={l.ai_state} />
                      <span className={priority(l.priority).tone}>{priority(l.priority).label} priority</span>
                      <span>{l.assigned_to ?? "Unassigned"}</span>
                      <span>{when(l.created_at)}</span>
                      <ChevronRight className="ml-auto h-4 w-4" aria-hidden="true" />
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          </>
        )}

        {!loading && !error && total > limit && (
          <div className="mt-4 flex items-center gap-3 text-sm text-muted">
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))} className="klaros-btn-secondary !px-3 !py-1 text-xs">Previous</button>
            <span>{offset + 1}–{Math.min(offset + limit, total)} of {total}</span>
            <button disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)} className="klaros-btn-secondary !px-3 !py-1 text-xs">Next</button>
          </div>
        )}
      </div>

      {showCreate && token && <CreateLeadModal token={token} onClose={() => setShowCreate(false)} onCreated={load} />}
      {showImport && token && <ImportLeadsModal token={token} onClose={() => setShowImport(false)} onImported={load} />}
    </AppShell>
  );
}

function CreateLeadModal({
  token,
  onClose,
  onCreated,
}: {
  token: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [source, setSource] = useState("WEB");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [serviceRequested, setServiceRequested] = useState("");
  const [urgency, setUrgency] = useState("MEDIUM");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await createLead(token, {
        name,
        source,
        email: email || undefined,
        phone: phone || undefined,
        service_requested: serviceRequested || undefined,
        urgency,
      });
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create lead.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title="New lead" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <input
          required
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="klaros-input"
        />
        <select value={source} onChange={(e) => setSource(e.target.value)} className="klaros-input">
          {["PHONE", "WEB", "CHAT", "TEXT", "DM", "MARKETPLACE", "REFERRAL", "WALK_IN"].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
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
        <input
          placeholder="Service requested"
          value={serviceRequested}
          onChange={(e) => setServiceRequested(e.target.value)}
          className="klaros-input"
        />
        <select value={urgency} onChange={(e) => setUrgency(e.target.value)} className="klaros-input">
          {["LOW", "MEDIUM", "HIGH", "EMERGENCY"].map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>

        {error && <p className="text-sm text-danger">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="klaros-btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="klaros-btn-primary">
            {submitting ? "Creating..." : "Create lead"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

const IMPORT_FIELDS: { key: keyof LeadImportRow; label: string; required?: boolean; aliases: string[] }[] = [
  { key: "name", label: "Name", required: true, aliases: ["name", "full name", "contact"] },
  { key: "email", label: "Email", aliases: ["email", "email address"] },
  { key: "phone", label: "Phone", aliases: ["phone", "phone number", "mobile", "cell"] },
  { key: "service_requested", label: "Service requested", aliases: ["service", "service requested"] },
  { key: "location", label: "Location", aliases: ["location", "address", "city"] },
  { key: "description", label: "Notes", aliases: ["notes", "description", "comment", "comments"] },
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

function ImportLeadsModal({
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
  const [result, setResult] = useState<{ created_count: number; matched_existing_customer_count: number } | null>(
    null
  );

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

  function buildRows(): LeadImportRow[] {
    if (!headers) return [];
    const nameCol = mapping.name;
    if (!nameCol || nameCol === UNMAPPED) return [];
    const colIndex = (header: string) => headers.indexOf(header);

    const rows: LeadImportRow[] = [];
    for (const row of dataRows) {
      const name = row[colIndex(nameCol)]?.trim();
      if (!name) continue;
      const record: LeadImportRow = { name };
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
      const res = await bulkImportLeads(token, rows);
      setResult(res);
      onImported();
    } catch (err) {
      setImportError(err instanceof ApiError ? err.message : "Import failed.");
    } finally {
      setImporting(false);
    }
  }

  return (
    <Modal title="Import leads from CSV" onClose={onClose} size="lg">
      <div className="space-y-4">
        {result ? (
          <>
            <div className="rounded-md border border-success/20 bg-success/[0.06] p-3 text-sm text-success">
              Imported {result.created_count} lead(s).
              {result.matched_existing_customer_count > 0 &&
                ` ${result.matched_existing_customer_count} matched an existing customer by email/phone.`}
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
              Bring an existing prospect list in from a spreadsheet export — pick a CSV file with a header
              row (Name, Email, Phone, etc.). Imported leads are tagged source &quot;OTHER&quot; and matched
              to an existing customer by email/phone, same as any lead.
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
                {importing ? "Importing..." : `Import ${previewRows.length} lead(s)`}
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
