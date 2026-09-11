"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Appointment,
  Customer,
  TimeSlot,
  cancelAppointment,
  checkAvailability,
  createAppointment,
  listAppointments,
  searchCustomers,
  syncAppointmentToGoogle,
} from "@/lib/api";

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function CalendarPage() {
  return (
    <Suspense fallback={<p className="p-8 text-sm text-muted">Loading calendar...</p>}>
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

  async function handleCancel(appointmentId: string) {
    if (!token) return;
    try {
      await cancelAppointment(token, appointmentId);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Appointment could not be cancelled.");
    }
  }

  const [syncingId, setSyncingId] = useState<string | null>(null);

  async function handleSyncToGoogle(appointmentId: string) {
    if (!token) return;
    setSyncingId(appointmentId);
    setError(null);
    try {
      await syncAppointmentToGoogle(token, appointmentId);
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
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="rounded-md border border-border-strong bg-surface-muted px-3 py-1.5 text-sm"
          />
        </header>

        <p className="mb-4 text-xs text-muted">
          Internal test calendar (no external provider connected). Business hours 09:00-17:00 UTC.
        </p>

        {authLoading || loading ? (
          <p className="text-sm text-muted">Loading calendar...</p>
        ) : error ? (
          <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
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
                <p className="text-sm text-muted">Nothing booked for this day.</p>
              ) : (
                <ul className="space-y-2">
                  {appointments.map((a) => (
                    <li
                      key={a.id}
                      className="flex items-center justify-between rounded-md border border-border bg-surface px-4 py-3 text-sm"
                    >
                      <div>
                        <p className="font-medium">{a.title}</p>
                        <p className="text-xs text-muted">
                          {new Date(a.start_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} –{" "}
                          {new Date(a.end_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} ·{" "}
                          {a.status}
                          {a.external_provider === "google_calendar" && (
                            <span className="ml-2 text-emerald-600">· synced to Google</span>
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
                            onClick={() => handleCancel(a.id)}
                            className="text-xs text-red-600 hover:underline"
                          >
                            Cancel
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section>
              <h2 className="mb-3 text-sm font-medium text-muted">Open slots ({slots.length})</h2>
              {slots.length === 0 ? (
                <p className="text-sm text-muted">No open slots this day.</p>
              ) : (
                <div className="grid grid-cols-3 gap-2">
                  {slots.map((s) => (
                    <button
                      key={s.start_time}
                      onClick={() => setBookingSlot(s)}
                      className="rounded-md border border-border-strong px-2 py-2 text-xs hover:bg-surface-muted"
                    >
                      {new Date(s.start_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
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
    <div className="fixed inset-0 flex items-center justify-center bg-background/60 px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-md space-y-3 rounded-lg border border-border bg-surface p-6"
      >
        <h2 className="font-display text-xl text-foreground">
          Book {new Date(slot.start_time).toLocaleString()}
        </h2>
        <input
          required
          placeholder="Title (e.g. AC repair)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
        />

        {!initialCustomerId && (
          <div>
            <input
              placeholder="Search customer by name/email"
              value={customerQuery}
              onChange={(e) => setCustomerQuery(e.target.value)}
              className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
            />
            {customerResults.length > 0 && (
              <ul className="mt-1 rounded-md border border-border bg-surface-muted text-sm">
                {customerResults.map((c) => (
                  <li
                    key={c.id}
                    onClick={() => {
                      setCustomerId(c.id);
                      setCustomerQuery(c.name);
                      setCustomerResults([]);
                    }}
                    className={`cursor-pointer px-3 py-2 hover:bg-surface-muted ${
                      customerId === c.id ? "bg-surface-muted" : ""
                    }`}
                  >
                    {c.name} {c.email && `(${c.email})`}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {error && <p className="text-sm text-red-600">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-muted">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-surface px-3 py-1.5 text-sm font-medium text-foreground disabled:opacity-50"
          >
            {submitting ? "Booking..." : "Book appointment"}
          </button>
        </div>
      </form>
    </div>
  );
}
