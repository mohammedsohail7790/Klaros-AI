"use client";

import { useCallback, useEffect, useState } from "react";
import { MessageSquare, Phone } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  VoiceCallRow,
  VoiceSettings,
  getVoiceSettings,
  listVoiceCalls,
  updateVoiceSettings,
} from "@/lib/api";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

function outcomeLabel(outcome: string | null): string {
  if (!outcome) return "In progress";
  return outcome
    .toLowerCase()
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

export default function VoiceReceptionistPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [settings, setSettings] = useState<VoiceSettings | null>(null);
  const [calls, setCalls] = useState<VoiceCallRow[] | null>(null);
  const [draftGreeting, setDraftGreeting] = useState("");
  const [draftHours, setDraftHours] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedCall, setSelectedCall] = useState<VoiceCallRow | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [s, c] = await Promise.all([getVoiceSettings(token), listVoiceCalls(token)]);
      setSettings(s);
      setDraftGreeting(s.greeting);
      setDraftHours(s.business_hours_note ?? "");
      setCalls(c.calls);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load voice receptionist data.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleToggle() {
    if (!token || !settings) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateVoiceSettings(token, { enabled: !settings.enabled });
      setSettings(updated);
      toast.success(updated.enabled ? "AI Voice Receptionist enabled." : "AI Voice Receptionist disabled.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update settings.");
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveText() {
    if (!token) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateVoiceSettings(token, {
        greeting: draftGreeting, business_hours_note: draftHours || null,
      });
      setSettings(updated);
      toast.success("Saved.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="font-display text-2xl text-foreground mb-2">AI Voice Receptionist</h1>
        <p className="mb-6 max-w-2xl text-sm text-muted">
          A real, governed AI phone receptionist — every action it takes (creating a lead, answering a
          knowledge question, escalating to a human) goes through the same permission/policy/audit
          pipeline as every other Klaros action. Speech-to-text and text-to-speech require real provider
          credentials (Deepgram / ElevenLabs) to be configured server-side; without them, calls honestly
          end in a handoff rather than a fabricated resolution.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">
            {error}
          </div>
        )}

        {authLoading || loading || !settings ? (
          <Skeleton />
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="rounded-lg border border-border bg-surface p-5">
              <div className="mb-2 flex items-center justify-between">
                <div>
                  <h2 className="font-medium">Status</h2>
                  <p className="text-xs text-muted">
                    {settings.enabled ? "Enabled — inbound calls are handled by the AI receptionist." : "Disabled — inbound calls use the standard capture-and-acknowledge flow."}
                  </p>
                </div>
                <button
                  onClick={handleToggle}
                  disabled={saving}
                  className={`rounded-md border px-3 py-1.5 text-sm disabled:opacity-50 ${
                    settings.enabled
                      ? "border-danger/25 bg-danger/[0.06] text-danger hover:bg-danger/10"
                      : "border-success/20 bg-success/[0.06] text-success hover:bg-success/10"
                  }`}
                >
                  {settings.enabled ? "Disable" : "Enable"}
                </button>
              </div>

              <div className="mb-4 flex gap-4 text-xs">
                <span className={settings.stt_provider === "NOT_CONFIGURED" ? "text-warning" : "text-success"}>
                  Speech-to-text: {settings.stt_provider}
                </span>
                <span className={settings.tts_provider === "NOT_CONFIGURED" ? "text-warning" : "text-success"}>
                  Text-to-speech: {settings.tts_provider}
                </span>
              </div>

              <label className="mb-1 block text-xs text-muted">Greeting</label>
              <textarea
                value={draftGreeting}
                onChange={(e) => setDraftGreeting(e.target.value)}
                rows={2}
                className="mb-3 w-full rounded-md border border-border-strong bg-background p-2 text-sm text-foreground"
              />

              <label className="mb-1 block text-xs text-muted">Business hours note (optional)</label>
              <textarea
                value={draftHours}
                onChange={(e) => setDraftHours(e.target.value)}
                rows={2}
                placeholder="e.g. Mon-Fri 8am-6pm, emergency service available 24/7"
                className="mb-3 w-full rounded-md border border-border-strong bg-background p-2 text-sm text-foreground"
              />

              <button
                onClick={handleSaveText}
                disabled={saving}
                className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
              >
                Save
              </button>
            </div>

            <div>
              <h2 className="mb-3 font-medium">Recent calls</h2>
              {!calls || calls.length === 0 ? (
                <EmptyState icon={Phone} title="No calls yet." />
              ) : (
                <div className="space-y-2">
                  {calls.map((c) => (
                    <button
                      key={c.id}
                      onClick={() => setSelectedCall(c)}
                      className="block w-full rounded-md border border-border px-3 py-2 text-left text-sm hover:bg-surface-muted"
                    >
                      <div className="flex items-center justify-between">
                        <span>{c.caller_number ?? "Unknown number"}</span>
                        <span className="text-xs text-muted">{new Date(c.started_at).toLocaleString()}</span>
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-xs text-muted">
                        <span>{c.status}</span>
                        <span>&middot;</span>
                        <span>{outcomeLabel(c.outcome)}</span>
                        {c.handoff_requested && <span className="text-warning">&middot; Handoff requested</span>}
                        {c.appointment_id && <span className="text-success">&middot; Appointment booked</span>}
                      </div>
                    </button>
                  ))}
                </div>
              )}

              {selectedCall && (
                <div className="mt-4 rounded-lg border border-border bg-surface p-4">
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="text-sm font-medium">Call detail</h3>
                    <button onClick={() => setSelectedCall(null)} className="text-xs text-muted hover:text-foreground">
                      close
                    </button>
                  </div>

                  {selectedCall.booking && (
                    <div className="mb-3 rounded-md border border-border bg-background p-3 text-xs">
                      <div className="mb-1 text-muted">Booking state: <span className="text-foreground">{selectedCall.booking.state}</span></div>
                      {selectedCall.booking.service_summary && (
                        <div className="mb-1 text-muted">
                          Service: <span className="text-foreground">{selectedCall.booking.service_summary}</span>
                          {selectedCall.booking.service_type && ` (${selectedCall.booking.service_type.replace(/_/g, " ")})`}
                        </div>
                      )}
                      {selectedCall.booking.selected_slot && (
                        <div className="text-muted">
                          Selected time: <span className="text-foreground">{selectedCall.booking.selected_slot.label}</span>
                        </div>
                      )}
                      {selectedCall.appointment_id && (
                        <div className="mt-1 text-success">Appointment ID: {selectedCall.appointment_id}</div>
                      )}
                      {selectedCall.handoff_requested && (
                        <div className="mt-1 text-warning">Handoff reason: {selectedCall.handoff_reason ?? "unspecified"}</div>
                      )}
                    </div>
                  )}

                  {selectedCall.latency_ms.length > 0 && (
                    <div className="mb-3 rounded-md border border-border bg-background p-3 text-xs">
                      <div className="mb-1 text-muted">Latency (most recent turn)</div>
                      {(() => {
                        const last = selectedCall.latency_ms[selectedCall.latency_ms.length - 1];
                        return (
                          <div className="flex gap-4 text-foreground">
                            <span>Conversation: {last.conversation_ms ?? "—"}ms</span>
                            <span>TTS: {last.tts_ms ?? "—"}ms</span>
                            <span>Total: {last.total_ms ?? "—"}ms</span>
                          </div>
                        );
                      })()}
                    </div>
                  )}

                  <h4 className="mb-2 text-xs font-medium text-muted">Transcript</h4>
                  {selectedCall.transcript.length === 0 ? (
                    <EmptyState icon={MessageSquare} title="No transcript captured for this call." compact />
                  ) : (
                    <div className="space-y-2 text-xs">
                      {selectedCall.transcript.map((t, i) => (
                        <div key={i} className={t.role === "agent" ? "text-success" : "text-muted"}>
                          <span className="text-muted">{t.role === "agent" ? "Agent: " : "Caller: "}</span>
                          {t.text}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
