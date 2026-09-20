"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Calendar, CalendarX } from "lucide-react";
import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Appointment,
  Customer,
  GoogleCalendarEntry,
  TimeSlot,
  cancelAppointment,
  checkAvailability,
  createAppointment,
  listAppointments,
  listGoogleCalendars,
  rescheduleAppointment,
  searchCustomers,
  syncAppointmentToGoogle,
} from "@/lib/api";

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function CalendarPage() {
  return (
    <Suspense fallback={<Skeleton />}>
      <CalendarPageInner />
    </Suspense>
  );
}

function CalendarPageInner() {
  const { token, user, loading: authLoading } = useAuth();
  const searchParams = useSearchParams();
  const prefilledLeadId = searchParams.get("lead_id") ?? undefined;
  const prefilledCustomerId = searchParams.get("customer_id") ?? undefined;

  const [date, setDate] = useState(todayIso());
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [slots, setSlots] = useState<TimeSlot[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bookingSlot, setBookingSlot] = useState<TimeSlot | null>(null);
  const [reschedulingId, setReschedulingId] = useState<string | null>(null);
  const [rescheduleDraft, setRescheduleDraft] = useState("");
  const [reschedulingBusy, setReschedulingBusy] = useState(false);
  const [googleCalendars, setGoogleCalendars] = useState<GoogleCalendarEntry[]>([]);
  const [syncCalendarId, setSyncCalendarId] = useState("primary");

  const dayRange = useMemo(() => {
    const from = `${date}T00:00:00+00:00`;
    const to = `${date}T23:59:59+00:00`;
    return { from, to };
  }, [date]);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [apptResult, availResult] = await Promise.all([
        listAppointments(token, { date_from: dayRange.from, date_to: dayRange.to }),
        checkAvailability(token, { date_from: dayRange.from, date_to: dayRange.to, duration_minutes: 60 }),
      ]);
      setAppointments(apptResult.appointments);
      setSlots(availResult.slots);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Calendar unavailable.");
    } finally {
      setLoading(false);
    }
  }, [token, dayRange]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!token) return;
    // Best-effort: only tenants with Google Calendar connected have more
    // than one real calendar to pick from — silently fall back to the
    // default "primary" target when this fails or returns nothing extra.
    listGoogleCalendars(token)
      .then((r) => {
        setGoogleCalendars(r.calendars);
        const primary = r.calendars.find((c) => c.primary);
        if (primary) setSyncCalendarId(primary.id);
      })
      .catch(() => setGoogleCalendars([]));
  }, [token]);

  async function handleCancel(appointmentId: string) {
    if (!token) return;
    try {
      await cancelAppointment(token, appointmentId);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Appointment could not be cancelled.");
    }
  }

  function toUtcInputValue(iso: string): string {
    return iso.slice(0, 16);
  }

  function openReschedule(appointment: Appointment) {
    setReschedulingId(appointment.id);
    setRescheduleDraft(toUtcInputValue(appointment.start_time));
    setError(null);
  }

  async function handleReschedule(appointment: Appointment) {
    if (!token || !rescheduleDraft) return;
    const durationMs = new Date(appointment.end_time).getTime() - new Date(appointment.start_time).getTime();
    const newStart = new Date(`${rescheduleDraft}:00Z`);
    const newEnd = new Date(newStart.getTime() + durationMs);
    setReschedulingBusy(true);
    setError(null);
    try {
      await rescheduleAppointment(token, appointment.id, newStart.toISOString(), newEnd.toISOString());
      setReschedulingId(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Appointment could not be rescheduled.");
    } finally {
      setReschedulingBusy(false);
    }
  }

  const [syncingId, setSyncingId] = useState<string | null>(null);

  async function handleSyncToGoogle(appointmentId: string) {
    if (!token) return;
    setSyncingId(appointmentId);
    setError(null);
    try {
      await syncAppointmentToGoogle(token, appointmentId, syncCalendarId);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sync to Google Calendar.");
    } finally {
      setSyncingId(null);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <header className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Calendar</h1>
          <div className="flex items-center gap-2">
            {googleCalendars.length > 1 && (
              <select
                value={syncCalendarId}
                onChange={(e) => setSyncCalendarId(e.target.value)}
                title="Google Calendar to sync appointments to"
                className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              >
                {googleCalendars.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.summary ?? c.id}
                    {c.primary ? " (primary)" : ""}
                  </option>
                ))}
              </select>
            )}
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="rounded-md border border-border-strong bg-surface-muted px-3 py-1.5 text-sm"
            />
          </div>
        </header>

        <p className="mb-4 text-xs text-muted">
          Internal test calendar (no external provider connected). Business hours 09:00-17:00 UTC.
        </p>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <section>
              <h2 className="mb-3 text-sm font-medium text-muted">
                Appointments ({appointments.length})
              </h2>
              {appointments.length === 0 ? (
                <EmptyState icon={Calendar} title="Nothing booked for this day." compact />
              ) : (
                <ul className="space-y-2">
                  {appointments.map((a) => (
                    <li
                      key={a.id}
                      className="rounded-md border border-border bg-surface px-4 py-3 text-sm"
                    >
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="font-medium">{a.title}</p>
                          <p className="text-xs text-muted">
                            {new Date(a.start_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} –{" "}
                            {new Date(a.end_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} UTC ·{" "}
                            {a.status}
                            {a.external_provider === "google_calendar" && (
                              <span className="ml-2 text-success">· synced to Google</span>
                            )}
                          </p>
                        </div>
                        <div className="flex items-center gap-3">
                          {a.status !== "CANCELLED" && (
                            <button
                              onClick={() => handleSyncToGoogle(a.id)}
                              disabled={syncingId === a.id}
                              className="text-xs text-muted hover:underline disabled:opacity-50"
                            >
                              {syncingId === a.id
                                ? "Syncing..."
                                : a.external_provider === "google_calendar"
                                  ? "Re-sync"
                                  : "Sync to Google"}
                            </button>
                          )}
                          {a.status !== "CANCELLED" && (
                            <button
                              onClick={() => openReschedule(a)}
                              className="text-xs text-muted hover:underline"
                            >
                              Reschedule
                            </button>
                          )}
                          {a.status !== "CANCELLED" && (
                            <button
                              onClick={() => handleCancel(a.id)}
                              className="text-xs text-danger hover:underline"
                            >
                              Cancel
                            </button>
                          )}
                        </div>
                      </div>
                      {reschedulingId === a.id && (
                        <div className="mt-3 flex items-center gap-2 border-t border-border pt-3">
                          <input
                            type="datetime-local"
                            value={rescheduleDraft}
                            onChange={(e) => setRescheduleDraft(e.target.value)}
                            className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                          />
                          <span className="text-xs text-muted">UTC</span>
                          <button
                            onClick={() => handleReschedule(a)}
                            disabled={reschedulingBusy}
                            className="rounded-md border border-border-strong px-3 py-1.5 text-xs hover:bg-surface-muted disabled:opacity-50"
                          >
                            {reschedulingBusy ? "Saving..." : "Save"}
                          </button>
                          <button
                            onClick={() => setReschedulingId(null)}
                            className="text-xs text-muted hover:underline"
                          >
                            Cancel
                          </button>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section>
              <h2 className="mb-3 text-sm font-medium text-muted">Open slots ({slots.length})</h2>
              {slots.length === 0 ? (
                <EmptyState icon={CalendarX} title="No open slots this day." compact />
              ) : (
                <div className="grid grid-cols-3 gap-2">
                  {slots.map((s) => (
                    <button
                      key={s.start_time}
                      onClick={() => setBookingSlot(s)}
                      className="rounded-md border border-border-strong px-2 py-2 text-xs hover:bg-surface-muted"
                    >
                      {new Date(s.start_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: "UTC" })}
                    </button>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
      </div>

      {bookingSlot && token && (
        <BookSlotModal
          token={token}
          slot={bookingSlot}
          leadId={prefilledLeadId}
          initialCustomerId={prefilledCustomerId}
          onClose={() => setBookingSlot(null)}
          onBooked={load}
        />
      )}
    </AppShell>
  );
}

function BookSlotModal({
  token,
  slot,
  leadId,
  initialCustomerId,
  onClose,
  onBooked,
}: {
  token: string;
  slot: TimeSlot;
  leadId?: string;
  initialCustomerId?: string;
  onClose: () => void;
  onBooked: () => void;
}) {
  const [title, setTitle] = useState("");
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerResults, setCustomerResults] = useState<Customer[]>([]);
  const [customerId, setCustomerId] = useState(initialCustomerId ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!customerQuery) {
      setCustomerResults([]);
      return;
    }
    const handle = setTimeout(() => {
      searchCustomers(token, { q: customerQuery, limit: 5 })
        .then((r) => setCustomerResults(r.customers))
        .catch(() => setCustomerResults([]));
    }, 300);
    return () => clearTimeout(handle);
  }, [customerQuery, token]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!customerId) {
      setError("Select a customer first.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await createAppointment(token, {
        customer_id: customerId,
        title,
        start_time: slot.start_time,
        end_time: slot.end_time,
        lead_id: leadId,
      });
      onBooked();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Appointment could not be created.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Book ${new Date(slot.start_time).toLocaleString([], { timeZone: "UTC" })} UTC`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <input
          required
          placeholder="Title (e.g. AC repair)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="klaros-input"
        />

        {!initialCustomerId && (
          <div>
            <input
              placeholder="Search customer by name/email"
              value={customerQuery}
              onChange={(e) => setCustomerQuery(e.target.value)}
              className="klaros-input"
            />
            {customerResults.length > 0 && (
              <ul className="mt-1 overflow-hidden rounded-lg border border-border bg-surface text-sm shadow-raised">
                {customerResults.map((c) => (
                  <li
                    key={c.id}
                    onClick={() => {
                      setCustomerId(c.id);
                      setCustomerQuery(c.name);
                      setCustomerResults([]);
                    }}
                    className={`cursor-pointer px-3 py-2 transition-colors hover:bg-accent-soft/40 ${
                      customerId === c.id ? "bg-accent-soft/40" : ""
                    }`}
                  >
                    {c.name} {c.email && `(${c.email})`}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {error && <p className="text-sm text-danger">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="klaros-btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="klaros-btn-primary">
            {submitting ? "Booking..." : "Book appointment"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
