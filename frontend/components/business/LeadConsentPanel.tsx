"use client";

import { useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { ApiError, LeadConsentState, attestLeadConsent, eraseLeadPersonalData, getLeadConsent } from "@/lib/api";
import { usePilotMode } from "@/lib/usePilotMode";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { when } from "@/lib/opsLabels";

const SCOPES: { key: string; label: string; help: string }[] = [
  { key: "contact", label: "May be contacted", help: "About this enquiry." },
  { key: "store_personal_data", label: "May keep personal details", help: "Name, phone number and the enquiry." },
  { key: "store_medical_information", label: "May keep health information", help: "Only if the patient was asked and said yes." },
];

const METHOD: Record<string, string> = {
  voice_ai_verbal: "Said to the AI receptionist",
  operator_attested: "Recorded by a team member",
  web_form: "Ticked on the website form",
};

/**
 * What this patient has agreed to RIGHT NOW (their newest decision), who recorded it and when, a way for a team member to record what the patient
 * told them (a complete new decision: an empty selection is a withdrawal), and the erase action once personal-data consent is no longer given.
 * Shown only for a consent-gated Medical Tourism organisation. Nothing here is inferred: the figures are the backend's, and an AI agent cannot
 * change them.
 */
export function LeadConsentPanel({ token, leadId, onChanged }: { token: string | null; leadId: string; onChanged?: () => void }) {
  const pilot = usePilotMode(token);
  const [state, setState] = useState<LeadConsentState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmErase, setConfirmErase] = useState(false);
  const [erasing, setErasing] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const s = await getLeadConsent(token, leadId);
      setState(s);
      setSelected(s.granted_scopes);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load consent.");
    }
  }, [token, leadId]);

  useEffect(() => {
    if (pilot) load();
  }, [pilot, load]);

  if (!pilot) return null;

  const toggle = (key: string) => setSelected((cur) => (cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]));
  const unchanged = state ? [...selected].sort().join() === [...state.granted_scopes].sort().join() : true;

  async function save() {
    if (!token) return;
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      const s = await attestLeadConsent(token, leadId, selected);
      setState(s);
      setSelected(s.granted_scopes);
      setMessage(selected.length === 0 ? "Recorded: the patient has withdrawn consent." : "Recorded.");
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to record this decision.");
    } finally {
      setSaving(false);
    }
  }

  async function erase() {
    if (!token) return;
    setErasing(true);
    setError(null);
    try {
      await eraseLeadPersonalData(token, leadId);
      setConfirmErase(false);
      setMessage("Personal details have been erased.");
      await load();
      onChanged?.();
    } catch (err) {
      setConfirmErase(false);
      setError(err instanceof ApiError ? err.message : "Unable to erase personal details.");
    } finally {
      setErasing(false);
    }
  }

  return (
    <section aria-labelledby="lead-consent-h" className="rounded-3xl bg-surface-muted p-5 sm:p-6">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
          <ShieldCheck className="h-4 w-4" strokeWidth={2} />
        </span>
        <div className="min-w-0">
          <h2 id="lead-consent-h" className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Consent</h2>
          <p className="mt-0.5 text-sm text-muted">What this patient has agreed to now. Their newest answer is the one that counts.</p>
        </div>
      </div>

      {error && <Alert variant="danger" className="mt-4">{error}</Alert>}
      {message && !error && <Alert variant="success" className="mt-4">{message}</Alert>}

      {!state ? (
        !error && <p className="mt-4 text-sm text-muted">Loading…</p>
      ) : (
        <>
          {!state.evidence_recorded && (
            <Alert variant="warning" className="mt-4">
              No consent has been recorded for this patient, so nothing is treated as agreed.
            </Alert>
          )}
          <dl className="mt-4 divide-y divide-border rounded-xl border border-border">
            {SCOPES.map((s) => {
              const yes = s.key === "contact" ? state.contact : s.key === "store_personal_data" ? state.store_personal_data : state.store_medical_information;
              return (
                <div key={s.key} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                  <div>
                    <dt className="text-foreground">{s.label}</dt>
                    <dd className="text-xs text-muted-foreground">{s.help}</dd>
                  </div>
                  <span className={yes ? "font-medium text-success" : "text-muted"} aria-label={`${s.label}: ${yes ? "yes" : "no"}`}>
                    {yes ? "Yes" : "No"}
                  </span>
                </div>
              );
            })}
          </dl>
          {state.evidence_recorded && (
            <p className="mt-3 text-xs text-muted-foreground">
              Last decision {state.as_of ? when(state.as_of) : "—"}
              {state.method ? ` · ${METHOD[state.method] ?? state.method}` : ""}
              {state.wording_version ? ` · wording ${state.wording_version}` : ""}
              {` · ${state.history_count} ${state.history_count === 1 ? "decision" : "decisions"} on record (history is kept)`}
            </p>
          )}

          <fieldset className="mt-5">
            <legend className="text-sm font-medium text-foreground">Record what the patient told you</legend>
            <p className="mt-0.5 text-xs text-muted-foreground">Tick everything they agree to. This replaces the current decision; ticking nothing records that they withdrew.</p>
            <div className="mt-2 space-y-1.5">
              {SCOPES.map((s) => (
                <label key={s.key} className="flex items-center gap-2 text-sm text-foreground">
                  <input type="checkbox" checked={selected.includes(s.key)} onChange={() => toggle(s.key)} />
                  {s.label}
                </label>
              ))}
            </div>
            <div className="mt-3">
              <Button size="sm" onClick={save} disabled={saving || unchanged}>
                {saving ? "Saving…" : selected.length === 0 ? "Record withdrawal" : "Record decision"}
              </Button>
            </div>
          </fieldset>

          {!state.store_personal_data && (
            <div className="mt-5 border-t border-border pt-4">
              <h3 className="text-sm font-medium text-foreground">Erase personal details</h3>
              <p className="mt-0.5 text-xs text-muted-foreground">
                The patient has not agreed to us keeping their details. Erasing removes their name, contact details and free-text notes from this lead. It cannot be undone; the
                consent history stays.
              </p>
              <div className="mt-2">
                <Button size="sm" variant="secondary" onClick={() => setConfirmErase(true)}>
                  Erase personal details…
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      {confirmErase && (
        <Modal title="Erase this patient's personal details?" onClose={() => (erasing ? undefined : setConfirmErase(false))}>
          <p className="text-sm text-muted">
            Their name, contact details and free-text notes are removed from this lead, its appointments and the AI receptionist's call records. This cannot be undone.
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Button size="sm" variant="secondary" onClick={() => setConfirmErase(false)} disabled={erasing}>
              Cancel
            </Button>
            <Button size="sm" variant="danger" onClick={erase} disabled={erasing}>
              {erasing ? "Erasing…" : "Erase"}
            </Button>
          </div>
        </Modal>
      )}
    </section>
  );
}
