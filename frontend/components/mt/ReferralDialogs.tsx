"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  MedicalTourismProvider,
  PipelineLead,
  ProviderReferralDetail,
  createProviderReferral,
  getProviderReferral,
  recordClinicResponse,
  recordReferralApproval,
  recordReferralOutcome,
  sendProviderReferral,
} from "@/lib/api";
import { FIELD_LABEL, OUTCOME_LABEL, SHAREABLE_FIELDS, gateReason } from "@/lib/mtLabels";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";

const CHANNELS = ["phone", "email", "in_person", "other"];

/** The referral dialogs, shared by the Referrals page and each patient's case file. The backend, not these dialogs, decides whether anything may be sent. */
export function NewReferral({ token, patients, providers, initialLeadId, onClose, onCreated }: { token: string; patients: PipelineLead[]; providers: MedicalTourismProvider[]; initialLeadId?: string; onClose: () => void; onCreated: (id: string) => void }) {
  const [lead, setLead] = useState(initialLeadId ?? "");
  const [provider, setProvider] = useState("");
  const [error, setError] = useState<string | null>(null);
  const verified = providers.filter((p) => p.status === "ACTIVE" && p.verification_status === "VERIFIED");

  async function create() {
    setError(null);
    try {
      onCreated((await createProviderReferral(token, { lead_id: lead, provider_id: provider })).referral.id);
    } catch (err) {
      setError(err instanceof ApiError ? (err.code ? gateReason(err.code) || err.message : err.message) : "Unable to create the referral.");
    }
  }

  return (
    <Modal title="New referral" onClose={onClose}>
      <p className="mb-3 text-sm text-muted">Only active, verified clinics can be chosen. Nothing is sent until the patient's approval is recorded.</p>
      {error && <Alert variant="danger" className="mb-3">{error}</Alert>}
      <label className="block text-sm">Patient
        <select aria-label="Patient" value={lead} onChange={(e) => setLead(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5">
          <option value="">Choose…</option>
          {patients.map((p) => <option key={p.lead_id} value={p.lead_id}>{p.name ?? "Name withheld"} · {p.stage.replace("_", " ").toLowerCase()}</option>)}
        </select>
      </label>
      <label className="mt-3 block text-sm">Clinic
        <select aria-label="Clinic" value={provider} onChange={(e) => setProvider(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5">
          <option value="">Choose…</option>
          {verified.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.country})</option>)}
        </select>
      </label>
      {verified.length === 0 && <p className="mt-2 text-xs text-warning">No clinic is verified yet. Verify one on the Providers page first.</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button size="sm" variant="secondary" onClick={onClose}>Cancel</Button>
        <Button size="sm" disabled={!lead || !provider} onClick={create}>Create</Button>
      </div>
    </Modal>
  );
}

export function ReferralDetail({ token, id, providerName, onClose }: { token: string; id: string; providerName: Record<string, string>; onClose: () => void }) {
  const [d, setD] = useState<ProviderReferralDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [fields, setFields] = useState<string[]>(["name", "phone"]);
  const [informed, setInformed] = useState(false);
  const [wording, setWording] = useState("");
  const [channel, setChannel] = useState("phone");
  const [outcome, setOutcome] = useState("TREATMENT_COMPLETED");
  const [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setD(await getProviderReferral(token, id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load this referral.");
    }
  }, [token, id]);

  useEffect(() => {
    load();
  }, [load]);

  async function run(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await fn();
      setMessage(done);
    } catch (err) {
      setError(err instanceof ApiError ? (err.code && gateReason(err.code)) || err.message : "That did not work.");
    } finally {
      setBusy(false);
      await load();
    }
  }

  const r = d?.referral;
  const toggle = (f: string) => setFields((cur) => (cur.includes(f) ? cur.filter((x) => x !== f) : [...cur, f]));
  const canApprove = informed && fields.length > 0 && wording.trim().length > 0;

  return (
    <Modal title={r ? `Referral to ${providerName[r.provider_id] ?? "clinic"}` : "Referral"} onClose={onClose} size="lg">
      {error && <Alert variant="danger" className="mb-3">{error}</Alert>}
      {message && !error && <Alert variant="success" className="mb-3">{message}</Alert>}
      {!d || !r ? (
        !error && <p className="text-sm text-muted">Loading…</p>
      ) : (
        <div className="space-y-5 text-sm">
          <div className="flex items-center gap-2"><Badge status={r.status}>{r.status.replace("_", " ")}</Badge>
            {r.sent_at && <span className="text-xs text-muted-foreground">sent {new Date(r.sent_at).toLocaleString()}</span>}</div>

          <section aria-label="Can this be sent">
            <h3 className="font-medium text-foreground">Can this be sent?</h3>
            {d.gate.allowed ? (
              <p className="mt-1 text-success">Yes. Only these will go: {d.gate.fields.map((f) => FIELD_LABEL[f] ?? f).join(", ")}.</p>
            ) : (
              <p className="mt-1 text-muted" data-testid="gate-reason">No. {gateReason(d.gate.code)}</p>
            )}
          </section>

          {["DRAFT", "APPROVED"].includes(r.status) && (
            <section aria-label="Patient decision" className="rounded-md border border-border p-4">
              <h3 className="font-medium text-foreground">Record the patient's decision</h3>
              <p className="mt-1 text-xs text-muted-foreground">Record what the patient told you. Tell them which clinic receives which information first; do not agree on their behalf.</p>
              <fieldset className="mt-3">
                <legend className="text-xs text-muted">Information the patient agrees to share</legend>
                <div className="mt-1 grid grid-cols-1 gap-1 sm:grid-cols-2">
                  {SHAREABLE_FIELDS.map((f) => (
                    <label key={f} className="flex items-center gap-2"><input type="checkbox" checked={fields.includes(f)} onChange={() => toggle(f)} />{FIELD_LABEL[f]}</label>
                  ))}
                </div>
              </fieldset>
              <label className="mt-3 flex items-center gap-2"><input type="checkbox" checked={informed} onChange={(e) => setInformed(e.target.checked)} />
                The patient was told which clinic receives this information</label>
              <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="block text-xs">Wording shown to the patient (label)
                  <input aria-label="Wording version" value={wording} onChange={(e) => setWording(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5 text-sm" placeholder="e.g. SHARE-v1" /></label>
                <label className="block text-xs">How they told you
                  <select aria-label="Channel" value={channel} onChange={(e) => setChannel(e.target.value)} className="mt-1 w-full rounded-md border border-border-strong bg-surface px-2 py-1.5 text-sm">
                    {CHANNELS.map((c) => <option key={c} value={c}>{c.replace("_", " ")}</option>)}</select></label>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" disabled={busy || !canApprove} onClick={() => run(() => recordReferralApproval(token, id, { decision: "GRANTED", permitted_fields: fields, patient_informed: informed, wording_version: wording.trim(), channel }), "The patient's approval was recorded.")}>Record approval</Button>
                <Button size="sm" variant="secondary" disabled={busy || wording.trim().length === 0} onClick={() => run(() => recordReferralApproval(token, id, { decision: "DECLINED", permitted_fields: [], patient_informed: false, wording_version: wording.trim(), channel }), "Recorded: the patient declined.")}>Record that they declined</Button>
              </div>
            </section>
          )}

          {r.status === "APPROVED" && (
            <Button size="sm" disabled={busy || !d.gate.allowed} onClick={() => run(() => sendProviderReferral(token, id), "Sent (sandbox: recorded, no clinic contacted).")}>Send to clinic</Button>
          )}

          {r.status === "SENT" && (
            <div className="flex gap-2">
              <Button size="sm" disabled={busy} onClick={() => run(() => recordClinicResponse(token, id, true), "Recorded: the clinic accepted.")}>Clinic accepted</Button>
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => recordClinicResponse(token, id, false), "Recorded: the clinic declined.")}>Clinic declined</Button>
            </div>
          )}

          {r.status === "ACCEPTED" && (
            <section aria-label="Outcome" className="rounded-md border border-border p-4">
              <h3 className="font-medium text-foreground">Record the outcome the clinic reported</h3>
              <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <select aria-label="Outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)} className="rounded-md border border-border-strong bg-surface px-2 py-1.5">
                  {Object.entries(OUTCOME_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                <input aria-label="Where the clinic reported it" value={evidence} onChange={(e) => setEvidence(e.target.value)} placeholder="Where it was reported (reference)" className="rounded-md border border-border-strong bg-surface px-2 py-1.5" />
              </div>
              <div className="mt-3"><Button size="sm" disabled={busy || !evidence.trim()} onClick={() => run(() => recordReferralOutcome(token, id, { outcome, evidence: evidence.trim() }), "Outcome recorded.")}>Record outcome</Button></div>
            </section>
          )}

          <section aria-label="History">
            <h3 className="font-medium text-foreground">Patient decisions on record</h3>
            {d.approvals.length === 0 ? <p className="mt-1 text-muted">None yet.</p> : (
              <ul className="mt-1 space-y-1">
                {d.approvals.map((a) => (
                  <li key={a.id} className="text-muted">{a.decision.toLowerCase()} · {new Date(a.recorded_at).toLocaleString()} · wording {a.wording_version}{a.channel ? ` · by ${a.channel.replace("_", " ")}` : ""}{a.fields.length ? ` · ${a.fields.map((f) => FIELD_LABEL[f] ?? f).join(", ")}` : ""}</li>
                ))}
              </ul>
            )}
            {d.deliveries.length > 0 && <p className="mt-2 text-muted">Sent {d.deliveries.length === 1 ? "once" : `${d.deliveries.length} times`} ({d.deliveries[0].transport}): {d.deliveries[0].fields_sent.map((f) => FIELD_LABEL[f] ?? f).join(", ")}.</p>}
          </section>
        </div>
      )}
    </Modal>
  );
}
