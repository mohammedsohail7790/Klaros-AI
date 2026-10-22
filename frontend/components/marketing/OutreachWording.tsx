"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, OutreachBlocks, approveOutreachBlocks, getOutreachBlocks, saveOutreachBlocks } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";

const PLACEHOLDERS = ["clinic_name", "business_name", "sender_name", "sender_title", "postal_address", "opt_out_line", "commission", "payment_days"];
const SAMPLE: Record<string, string> = {
  clinic_name: "Example Clinic",
  business_name: "Your Business",
  sender_name: "Sam",
  sender_title: "Partnerships",
  postal_address: "1 Main Street",
  opt_out_line: "",
  commission: "the agreed referral fee",
  payment_days: "the agreed number of",
};
const LABELS: Record<string, string> = {
  subject_intro: "Introduction: subject",
  intro: "Introduction: message",
  subject_followup_1: "First follow-up: subject",
  followup_1: "First follow-up: message",
  subject_followup_2: "Last follow-up: subject",
  followup_2: "Last follow-up: message",
  opt_out_line: "Opt-out line",
  how_referrals_work: "How referrals work",
  faq_what_we_do: "What we do",
  faq_what_we_need: "What we need from a clinic",
  faq_commission: "About referral fees",
};

function sample(text: string, optOut: string): string {
  return text.replace(/\{([a-z_]+)\}/g, (m, k: string) => (k === "opt_out_line" ? optOut : SAMPLE[k] ?? m));
}

/** The owner edits and approves the fixed wording the outreach engine builds every clinic email from. Nothing is used until it is approved. */
export function OutreachWording({ token }: { token: string | null }) {
  const [data, setData] = useState<OutreachBlocks | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const r = await getOutreachBlocks(token);
      setData(r);
      setDraft(r.blocks);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't load the wording just now.");
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function save() {
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    setErrors({});
    try {
      const r = await saveOutreachBlocks(token, draft);
      setData(r);
      setDraft(r.blocks);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422 && err.details) setErrors(err.details as Record<string, string[]>);
      else setError(err instanceof ApiError ? err.message : "That didn't save just now.");
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    try {
      setData(await approveOutreachBlocks(token));
      setConfirming(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work just now.");
    } finally {
      setBusy(false);
    }
  }

  if (!data) return error ? <p role="alert" className="text-sm text-danger">{error}</p> : <p className="text-sm text-muted">Loading...</p>;

  const dirty = JSON.stringify(draft) !== JSON.stringify(data.blocks);
  const approved = data.status === "APPROVED";
  const optOut = draft.opt_out_line ?? "";

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted">Drafts written by the assistant. Nothing is used until you approve.</p>
      <div className="flex flex-wrap items-center gap-2">
        {approved ? (
          <Badge status="ACTIVE">Approved{data.approved_at ? ` on ${new Date(data.approved_at).toLocaleDateString()}` : ""}</Badge>
        ) : (
          <Badge>Draft</Badge>
        )}
        <span className="text-sm text-muted">Version {data.version}</span>
      </div>
      <p className="rounded-xl border border-border bg-surface p-3 text-sm text-muted">
        Use only these placeholders: {PLACEHOLDERS.map((p) => `{${p}}`).join(", ")}. Do not type any number: amounts and days come from the campaign&apos;s terms through{" "}
        {"{commission}"} and {"{payment_days}"}.
      </p>
      {error && <p role="alert" className="rounded-xl border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</p>}

      {Object.keys(draft).map((key) => (
        <div key={key} className="space-y-2">
          <label htmlFor={`block-${key}`} className="block text-sm font-medium">{LABELS[key] ?? key}</label>
          <textarea
            id={`block-${key}`}
            value={draft[key]}
            rows={key.startsWith("subject") || key === "opt_out_line" ? 2 : 8}
            onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
            className="w-full rounded-xl border border-border-strong bg-surface px-3 py-2 text-sm"
          />
          {(errors[key] ?? []).map((p) => (
            <p key={p} className="text-xs text-danger">{p}</p>
          ))}
          <p className="whitespace-pre-wrap rounded-lg bg-surface/60 p-3 text-xs text-muted">Preview: {sample(draft[key], optOut)}</p>
        </div>
      ))}

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" disabled={busy || !dirty} onClick={save}>Save</Button>
        {!confirming ? (
          <Button disabled={busy || dirty || approved} onClick={() => setConfirming(true)}>Approve wording</Button>
        ) : (
          <>
            <span className="text-sm">Approve this wording for every outreach campaign?</span>
            <Button disabled={busy} onClick={approve}>Yes, approve</Button>
            <Button variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>Cancel</Button>
          </>
        )}
        {dirty && <span className="text-xs text-muted">Save your changes before approving.</span>}
      </div>
    </div>
  );
}
