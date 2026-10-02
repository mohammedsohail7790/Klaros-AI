"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Users } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Lead,
  MedicalTourismPatientLead,
  MedicalTourismProcedure,
  createPatientLead,
  listMedicalTourismProcedures,
  listPatientLeads,
  searchLeads,
} from "@/lib/api";

import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";

export default function MedicalTourismPatientLeadsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [patientLeads, setPatientLeads] = useState<MedicalTourismPatientLead[]>([]);
  const [total, setTotal] = useState(0);
  const [leadNames, setLeadNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listPatientLeads(token, { limit: 50 });
      setPatientLeads(result.patient_leads);
      setTotal(result.total);
      // Names come from the CRM leads these extend; the page still works (and never shows a raw id) without them.
      try {
        const crm = await searchLeads(token, { limit: 100 });
        setLeadNames(Object.fromEntries(crm.leads.map((l) => [l.id, l.name])));
      } catch {
        setLeadNames({});
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load patient leads.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <PageHeader
          title={`Patient Leads (${total})`}
          description="Medical Tourism intake extensions on top of the tenant's CRM Leads — sourced from the authenticated Leads screen or the public website's lead-intake form."
          icon={Users}
          actions={
            <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
              Extend a lead
            </button>
          }
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
        ) : patientLeads.length === 0 ? (
          <EmptyState
            icon={Users}
            title="No Medical Tourism patient leads yet."
            action={
              <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
                Extend a lead
              </button>
            }
          />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Patient</th>
                  <th className="px-4 py-2">Destination</th>
                  <th className="px-4 py-2">Travel window</th>
                  <th className="px-4 py-2">Insurance</th>
                  <th className="px-4 py-2">Created</th>
                </tr>
              </thead>
              <tbody>
                {patientLeads.map((pl) => (
                  <tr key={pl.id} className="border-t border-border hover:bg-surface">
                    <td className="px-4 py-2 text-foreground">
                      <Link href={`/leads/${pl.lead_id}`} className="font-medium underline-offset-2 hover:underline">
                        {leadNames[pl.lead_id] ?? "Patient enquiry"}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-muted">{pl.preferred_destination_country || "—"}</td>
                    <td className="px-4 py-2 text-muted">
                      {pl.travel_start_date ? new Date(pl.travel_start_date).toLocaleDateString() : "—"}
                      {pl.travel_end_date ? ` – ${new Date(pl.travel_end_date).toLocaleDateString()}` : ""}
                    </td>
                    <td className="px-4 py-2 text-muted">
                      {pl.has_insurance === null ? "—" : pl.has_insurance ? "Yes" : "No"}
                    </td>
                    <td className="px-4 py-2 text-muted">{new Date(pl.created_at).toLocaleDateString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate && token && (
        <CreatePatientLeadModal token={token} onClose={() => setShowCreate(false)} onCreated={load} />
      )}
    </AppShell>
  );
}

function CreatePatientLeadModal({
  token,
  onClose,
  onCreated,
}: {
  token: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [leadQuery, setLeadQuery] = useState("");
  const [leadResults, setLeadResults] = useState<Lead[]>([]);
  const [leadId, setLeadId] = useState("");
  const [procedures, setProcedures] = useState<MedicalTourismProcedure[]>([]);
  const [procedureId, setProcedureId] = useState("");
  const [destinationCountry, setDestinationCountry] = useState("");
  const [travelStart, setTravelStart] = useState("");
  const [travelEnd, setTravelEnd] = useState("");
  const [hasInsurance, setHasInsurance] = useState<"" | "yes" | "no">("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listMedicalTourismProcedures(token, { limit: 100 })
      .then((r) => setProcedures(r.procedures))
      .catch(() => setProcedures([]));
  }, [token]);

  useEffect(() => {
    if (!leadQuery) {
      setLeadResults([]);
      return;
    }
    const handle = setTimeout(() => {
      searchLeads(token, { q: leadQuery, limit: 5 })
        .then((r) => setLeadResults(r.leads))
        .catch(() => setLeadResults([]));
    }, 300);
    return () => clearTimeout(handle);
  }, [leadQuery, token]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!leadId) {
      setError("Search for and select a lead first.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await createPatientLead(token, {
        lead_id: leadId,
        procedure_id: procedureId || undefined,
        preferred_destination_country: destinationCountry || undefined,
        travel_start_date: travelStart || undefined,
        travel_end_date: travelEnd || undefined,
        has_insurance: hasInsurance === "" ? undefined : hasInsurance === "yes",
      });
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not extend lead.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title="Extend a lead with Medical Tourism intake" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <input
            required={!leadId}
            placeholder="Search lead by name/email/phone"
            value={leadQuery}
            onChange={(e) => {
              setLeadQuery(e.target.value);
              setLeadId("");
            }}
            className="klaros-input"
          />
          {leadResults.length > 0 && (
            <ul className="mt-1 overflow-hidden rounded-lg border border-border bg-surface text-sm shadow-raised">
              {leadResults.map((l) => (
                <li
                  key={l.id}
                  onClick={() => {
                    setLeadId(l.id);
                    setLeadQuery(l.name);
                    setLeadResults([]);
                  }}
                  className={`cursor-pointer px-3 py-2 transition-colors hover:bg-accent-soft/40 ${leadId === l.id ? "bg-accent-soft/40" : ""}`}
                >
                  {l.name} {l.email && `(${l.email})`} {l.phone && `· ${l.phone}`}
                </li>
              ))}
            </ul>
          )}
          {leadId && <p className="mt-1 text-xs text-muted">Selected lead: {leadQuery}</p>}
        </div>
        <select value={procedureId} onChange={(e) => setProcedureId(e.target.value)} className="klaros-input">
          <option value="">Procedure (optional)...</option>
          {procedures.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <input
          placeholder="Preferred destination country (ISO-2)"
          value={destinationCountry}
          maxLength={2}
          onChange={(e) => setDestinationCountry(e.target.value.toUpperCase())}
          className="klaros-input"
        />
        <div className="grid grid-cols-2 gap-2">
          <input type="date" value={travelStart} onChange={(e) => setTravelStart(e.target.value)} className="klaros-input" />
          <input type="date" value={travelEnd} onChange={(e) => setTravelEnd(e.target.value)} className="klaros-input" />
        </div>
        <select value={hasInsurance} onChange={(e) => setHasInsurance(e.target.value as "" | "yes" | "no")} className="klaros-input">
          <option value="">Insurance status unknown</option>
          <option value="yes">Has insurance</option>
          <option value="no">No insurance</option>
        </select>
        {error && <p className="text-sm text-danger">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="klaros-btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="klaros-btn-primary">
            {submitting ? "Saving..." : "Save"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
