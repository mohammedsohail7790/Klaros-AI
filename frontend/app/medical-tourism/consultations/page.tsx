"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CalendarCheck } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Appointment,
  MedicalTourismConsultation,
  MedicalTourismProcedure,
  MedicalTourismProvider,
  createConsultation,
  listAppointments,
  listConsultations,
  listMedicalTourismProcedures,
  listMedicalTourismProviders,
  updateConsultation,
} from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";

const STATUS_TABS = ["ALL", "SCHEDULED", "COMPLETED", "CANCELLED", "NO_SHOW"];
const NEXT_STATUS: Record<string, string[]> = {
  SCHEDULED: ["COMPLETED", "CANCELLED", "NO_SHOW"],
  COMPLETED: [],
  CANCELLED: [],
  NO_SHOW: [],
};

export default function MedicalTourismConsultationsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [consultations, setConsultations] = useState<MedicalTourismConsultation[]>([]);
  const [providers, setProviders] = useState<MedicalTourismProvider[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("ALL");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const providerName = useMemo(() => {
    const map = new Map(providers.map((p) => [p.id, p.name]));
    return (id: string) => map.get(id) || id;
  }, [providers]);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [result, providerResult] = await Promise.all([
        listConsultations(token, { status: status === "ALL" ? undefined : status, limit: 50 }),
        listMedicalTourismProviders(token, { limit: 100 }),
      ]);
      setConsultations(result.consultations);
      setTotal(result.total);
      setProviders(providerResult.providers);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load consultations.");
    } finally {
      setLoading(false);
    }
  }, [token, status]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleStatusChange(consultationId: string, newStatus: string) {
    if (!token) return;
    setBusyId(consultationId);
    try {
      await updateConsultation(token, consultationId, { status: newStatus });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update consultation.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <PageHeader
          title={`Consultations (${total})`}
          description="Medical Tourism provider consultations — a Medical Tourism extension of the tenant's Appointments."
          icon={CalendarCheck}
          actions={
            <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
              New consultation
            </button>
          }
        />

        <div className="mb-4 flex flex-wrap items-center gap-2">
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
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : consultations.length === 0 ? (
          <EmptyState
            icon={CalendarCheck}
            title="No consultations yet."
            action={
              <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
                New consultation
              </button>
            }
          />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Provider</th>
                  <th className="px-4 py-2">Appointment</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Notes</th>
                  <th className="px-4 py-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {consultations.map((c) => (
                  <tr key={c.id} className="border-t border-border hover:bg-surface">
                    <td className="px-4 py-2 font-medium text-foreground">{providerName(c.provider_id)}</td>
                    <td className="px-4 py-2 font-mono text-xs text-muted">{c.appointment_id}</td>
                    <td className="px-4 py-2">
                      <Badge status={c.status}>{c.status}</Badge>
                    </td>
                    <td className="px-4 py-2 text-muted">{c.notes || "—"}</td>
                    <td className="px-4 py-2">
                      <div className="flex flex-wrap gap-1">
                        {(NEXT_STATUS[c.status] || []).map((next) => (
                          <button
                            key={next}
                            disabled={busyId === c.id}
                            onClick={() => handleStatusChange(c.id, next)}
                            className="rounded-full border border-border-strong px-2 py-0.5 text-xs text-muted hover:border-foreground hover:text-foreground disabled:opacity-50"
                          >
                            {next.replace("_", " ")}
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate && token && (
        <CreateConsultationModal token={token} providers={providers} onClose={() => setShowCreate(false)} onCreated={load} />
      )}
    </AppShell>
  );
}

function CreateConsultationModal({
  token,
  providers,
  onClose,
  onCreated,
}: {
  token: string;
  providers: MedicalTourismProvider[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [procedures, setProcedures] = useState<MedicalTourismProcedure[]>([]);
  const [appointmentId, setAppointmentId] = useState("");
  const [providerId, setProviderId] = useState("");
  const [procedureId, setProcedureId] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Appointments from 90 days ago through 90 days ahead -- a full
    // search-as-you-type picker would need a dedicated appointments search
    // endpoint, which doesn't exist in lib/api.ts today; listAppointments'
    // date-range list is the closest existing primitive, so use a
    // wide-enough window for a dropdown, same tradeoff as this page's
    // provider dropdown. Deliberately starts 90 days in the PAST, not
    // "now" -- a consultation is frequently created for an appointment
    // booked earlier the same day (or in the past, for a backdated
    // record), and a `from = new Date()` window silently excluded any
    // same-day-but-earlier appointment (caught live in this round's
    // browser smoke test).
    const from = new Date();
    from.setDate(from.getDate() - 90);
    const to = new Date();
    to.setDate(to.getDate() + 90);
    Promise.all([
      listAppointments(token, { date_from: from.toISOString(), date_to: to.toISOString() }),
      listMedicalTourismProcedures(token, { limit: 100 }),
    ])
      .then(([apptResult, procResult]) => {
        setAppointments(apptResult.appointments);
        setProcedures(procResult.procedures);
      })
      .catch(() => {
        setAppointments([]);
        setProcedures([]);
      });
  }, [token]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!appointmentId || !providerId) {
      setError("Select an appointment and a provider.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await createConsultation(token, {
        appointment_id: appointmentId,
        provider_id: providerId,
        procedure_id: procedureId || undefined,
        notes: notes || undefined,
      });
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create consultation.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title="New consultation" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <select required value={appointmentId} onChange={(e) => setAppointmentId(e.target.value)} className="klaros-input">
          <option value="">Select appointment (next 90 days)...</option>
          {appointments.map((a) => (
            <option key={a.id} value={a.id}>
              {a.title} — {new Date(a.start_time).toLocaleString()}
            </option>
          ))}
        </select>
        <select required value={providerId} onChange={(e) => setProviderId(e.target.value)} className="klaros-input">
          <option value="">Select provider...</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <select value={procedureId} onChange={(e) => setProcedureId(e.target.value)} className="klaros-input">
          <option value="">Procedure (optional)...</option>
          {procedures.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <textarea placeholder="Notes" value={notes} onChange={(e) => setNotes(e.target.value)} className="klaros-input" rows={3} />
        {error && <p className="text-sm text-danger">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="klaros-btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="klaros-btn-primary">
            {submitting ? "Creating..." : "Create consultation"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
