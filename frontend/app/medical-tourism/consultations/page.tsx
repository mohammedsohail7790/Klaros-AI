"use client";

import Link from "next/link";
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
import { Hero, StatusSummary } from "@/components/ui/layout";

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
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <Hero
          title={`Consultations (${total})`}
          lede="Consultations booked between a patient and a clinic, and how each one went."
          actions={
            <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
              New consultation
            </button>
          }
        />

        <StatusSummary rows={consultations} noun="consultations" />

        <div className="mb-6 flex flex-wrap items-center gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`klaros-filter-tab ${status === s ? "klaros-filter-tab-active" : "klaros-filter-tab-inactive"}`}
            >
              {s}
            </button>
          ))}
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-xl border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
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
          <ul className="divide-y divide-border/70 overflow-hidden rounded-3xl bg-surface-muted" aria-label="Consultations">
            {consultations.map((c) => (
              <li key={c.id} className="flex flex-col gap-4 px-6 py-5 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-3">
                    <p className="text-[17px] font-semibold tracking-tight text-foreground">{providerName(c.provider_id)}</p>
                    <Badge status={c.status}>{c.status}</Badge>
                  </div>
                  <p className="mt-1 text-[14px] leading-snug text-muted">{c.notes || "No notes"}</p>
                </div>
                <div className="flex shrink-0 flex-wrap gap-2">
                  {(NEXT_STATUS[c.status] || []).map((next) => (
                    <button
                      key={next}
                      disabled={busyId === c.id}
                      onClick={() => handleStatusChange(c.id, next)}
                      className="rounded-full bg-surface px-4 py-1.5 text-[13px] font-medium text-foreground transition-colors duration-300 hover:bg-border disabled:opacity-50"
                    >
                      {next.replace("_", " ").toLowerCase()}
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ul>
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
  const [loadedAppointments, setLoadedAppointments] = useState(false);
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
      })
      .finally(() => setLoadedAppointments(true));
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
        {loadedAppointments && appointments.length === 0 && (
          <div className="rounded-xl border border-warning/30 bg-warning/[0.06] p-3 text-sm text-foreground">
            <p className="font-medium">Book the appointment first.</p>
            <p className="mt-1 text-muted">
              A consultation is attached to a booked appointment, and none was found in the last or next 90 days. Add the patient under{" "}
              <Link href="/customers" className="underline">Customers</Link>, book the time in{" "}
              <Link href="/calendar" className="underline">Calendar</Link>, then come back here.
            </p>
          </div>
        )}
        <select required value={appointmentId} onChange={(e) => setAppointmentId(e.target.value)} className="klaros-input">
          <option value="">Select appointment (last or next 90 days)…</option>
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
          <button type="submit" disabled={submitting || appointments.length === 0} className="klaros-btn-primary">
            {submitting ? "Creating..." : "Create consultation"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
