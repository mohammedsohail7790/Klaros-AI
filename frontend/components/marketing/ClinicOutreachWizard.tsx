"use client";

import { useEffect, useState } from "react";
import { ApiError, EligibleOutreachClinic, OutreachConfig, createOutreachCampaign, listEligibleOutreachClinics } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";

const STEPS = ["Basics", "Clinics", "Term sheet", "Sender", "Limits", "Review"] as const;
const FIELD = "mt-1 w-full rounded-xl border border-border-strong bg-surface px-3 py-2 text-sm";

interface Form {
  name: string;
  countries: string;
  basis: "PERCENTAGE" | "FLAT";
  minValue: string;
  maxValue: string;
  paymentDays: string;
  currency: string;
  outcome: string;
  displayName: string;
  signature: string;
  companyName: string;
  postalAddress: string;
  dailyCap: string;
  rampStart: string;
  perClinic: string;
  windowStart: string;
  windowEnd: string;
  weekdaysOnly: boolean;
}

// The same defaults the backend uses for limits (20 a day ramping from 5, at most 3 emails per clinic, 09:00 to 17:00 on weekdays).
const INITIAL: Form = {
  name: "", countries: "", basis: "PERCENTAGE", minValue: "", maxValue: "", paymentDays: "30", currency: "USD", outcome: "",
  displayName: "", signature: "", companyName: "", postalAddress: "",
  dailyCap: "20", rampStart: "5", perClinic: "3", windowStart: "9", windowEnd: "17", weekdaysOnly: true,
};

function parseCountries(raw: string): string[] {
  return raw.split(",").map((c) => c.trim().toUpperCase()).filter(Boolean);
}

const isWhole = (v: string, min: number, max: number) => /^\d+$/.test(v.trim()) && Number(v) >= min && Number(v) <= max;

/** The first thing wrong with a step, in plain words (mirrors the backend's rules), or null when the step is fine. */
function stepError(step: number, f: Form, chosen: number): string | null {
  if (step === 0) {
    if (!f.name.trim()) return "Give the campaign a name.";
    const codes = parseCountries(f.countries);
    if (codes.length === 0) return "Enter at least one country code, for example TR.";
    if (codes.some((c) => !/^[A-Z]{2}$/.test(c))) return "Country codes are two letters each, separated by commas.";
  }
  if (step === 1 && chosen === 0) return "Tick at least one clinic.";
  if (step === 2) {
    const min = Number(f.minValue);
    const max = Number(f.maxValue);
    if (f.minValue.trim() === "" || !(min > 0)) return "Enter a minimum above 0.";
    if (f.maxValue.trim() === "" || !(max > 0)) return "Enter a maximum above 0.";
    if (min > max) return "The minimum must not be above the maximum.";
    if (f.basis === "PERCENTAGE" && max > 100) return "A percentage cannot be above 100.";
    if (!isWhole(f.paymentDays, 1, 120)) return "Payment days must be a whole number from 1 to 120.";
    if (!/^[A-Za-z]{3}$/.test(f.currency.trim())) return "The currency is a three-letter code, for example USD.";
    if (!f.outcome.trim()) return "Say what outcome qualifies for the commission.";
  }
  if (step === 3 && (!f.displayName.trim() || !f.signature.trim() || !f.companyName.trim() || !f.postalAddress.trim())) {
    return "Fill in the sender name, signature, company name and postal address.";
  }
  if (step === 4) {
    if (!isWhole(f.dailyCap, 1, 100000)) return "The daily cap must be a whole number of at least 1.";
    if (!isWhole(f.rampStart, 1, 100000)) return "The starting amount must be a whole number of at least 1.";
    if (Number(f.rampStart) > Number(f.dailyCap)) return "The starting amount cannot be above the daily cap.";
    if (!isWhole(f.perClinic, 1, 3)) return "Send each clinic between 1 and 3 emails.";
    if (!isWhole(f.windowStart, 0, 23) || !isWhole(f.windowEnd, 1, 24)) return "Send hours run from 0 to 24.";
    if (Number(f.windowStart) >= Number(f.windowEnd)) return "The send window must start before it ends.";
  }
  return null;
}

function toConfig(f: Form): OutreachConfig {
  return {
    term_sheet: { basis: f.basis, min_value: f.minValue.trim(), max_value: f.maxValue.trim(), payment_days: Number(f.paymentDays), currency: f.currency.trim().toUpperCase(), qualifying_outcome: f.outcome.trim() },
    sender: { display_name: f.displayName.trim(), signature: f.signature.trim(), company_name: f.companyName.trim(), postal_address: f.postalAddress.trim() },
    limits: { daily_cap: Number(f.dailyCap), ramp_start: Number(f.rampStart), max_emails_per_clinic: Number(f.perClinic), send_window_start: Number(f.windowStart), send_window_end: Number(f.windowEnd), weekdays_only: f.weekdaysOnly },
    countries: parseCountries(f.countries),
  };
}

/**
 * The short wizard that creates a clinic outreach campaign: basics, which clinics, the term sheet the AI may offer within, the sender, limits and a plain-words review.
 * Next stays off, with the reason shown, until the step is valid. The campaign starts in shadow mode and nothing is sent until the owner takes it live.
 */
export function ClinicOutreachWizard({ token, onCreated }: { token: string | null; onCreated: (campaignId: string) => void }) {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<Form>(INITIAL);
  const [clinics, setClinics] = useState<EligibleOutreachClinic[]>([]);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [loadingClinics, setLoadingClinics] = useState(false);
  const [busy, setBusy] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));
  const countryKey = parseCountries(form.countries).join(",");

  // Entering the Clinics step loads who is eligible in those countries, every one ticked to start with.
  useEffect(() => {
    if (step !== 1 || !token || loadedFor === countryKey) return;
    let cancelled = false;
    setLoadingClinics(true);
    setApiError(null);
    listEligibleOutreachClinics(token, parseCountries(form.countries))
      .then((r) => {
        if (cancelled) return;
        setClinics(r.clinics);
        setTicked(new Set(r.clinics.map((c) => c.id)));
        setLoadedFor(countryKey);
      })
      .catch((err) => {
        if (!cancelled) setApiError(err instanceof ApiError ? err.message : "We couldn't load the clinics just now.");
      })
      .finally(() => setLoadingClinics(false));
    return () => {
      cancelled = true;
    };
  }, [step, token, countryKey, loadedFor, form.countries]);

  const error = stepError(step, form, ticked.size);
  const last = step === STEPS.length - 1;

  function toggle(id: string) {
    setTicked((prev) => {
      const nextSet = new Set(prev);
      if (nextSet.has(id)) nextSet.delete(id);
      else nextSet.add(id);
      return nextSet;
    });
  }

  async function create() {
    if (!token || busy) return;
    setBusy(true);
    setApiError(null);
    try {
      const r = await createOutreachCampaign(token, { name: form.name.trim(), config: toConfig(form), provider_ids: clinics.filter((c) => ticked.has(c.id)).map((c) => c.id) });
      onCreated(r.campaign_id);
    } catch (err) {
      setApiError(err instanceof ApiError ? err.message : "We couldn't create the campaign just now. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  const shown = error ?? apiError;

  return (
    <div className="rounded-xl border border-border p-5">
      <ol className="mb-5 flex flex-wrap gap-2 text-xs">
        {STEPS.map((label, i) => (
          <li key={label} className={`rounded-full px-3 py-1 ${i === step ? "bg-accent text-accent-foreground" : "bg-surface-muted text-muted"}`}>
            {i + 1}. {label}
          </li>
        ))}
      </ol>

      {step === 0 && (
        <div className="space-y-3">
          <label className="block text-sm">
            Campaign name
            <input value={form.name} onChange={(e) => set("name", e.target.value)} className={FIELD} />
          </label>
          <label className="block text-sm">
            Countries (two-letter codes, separated by commas)
            <input value={form.countries} onChange={(e) => set("countries", e.target.value)} placeholder="TR, ES" className={FIELD} />
          </label>
        </div>
      )}

      {step === 1 &&
        (loadingClinics ? (
          <p className="text-sm text-muted">Finding clinics...</p>
        ) : clinics.length === 0 ? (
          <EmptyState compact title="No eligible clinics in those countries." hint="Clinics already in another open outreach campaign, or with agreed terms, are left out." />
        ) : (
          <ul className="space-y-2">
            {clinics.map((c) => (
              <li key={c.id}>
                <label className="flex items-center gap-3 text-sm">
                  <input type="checkbox" checked={ticked.has(c.id)} onChange={() => toggle(c.id)} />
                  <span>{c.name}</span>
                  <span className="text-muted">{[c.city, c.country].filter(Boolean).join(", ")}</span>
                  {!c.has_email && <Badge>No email on file</Badge>}
                </label>
              </li>
            ))}
          </ul>
        ))}

      {step === 2 && (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            Commission basis
            <select value={form.basis} onChange={(e) => set("basis", e.target.value as Form["basis"])} className={FIELD}>
              <option value="PERCENTAGE">Percentage of treatment price</option>
              <option value="FLAT">Flat fee</option>
            </select>
          </label>
          <label className="block text-sm">
            Currency
            <input value={form.currency} onChange={(e) => set("currency", e.target.value)} maxLength={3} className={FIELD} />
          </label>
          <label className="block text-sm">
            Minimum
            <input inputMode="decimal" value={form.minValue} onChange={(e) => set("minValue", e.target.value)} className={FIELD} />
          </label>
          <label className="block text-sm">
            Maximum
            <input inputMode="decimal" value={form.maxValue} onChange={(e) => set("maxValue", e.target.value)} className={FIELD} />
          </label>
          <label className="block text-sm">
            Payment days
            <input inputMode="numeric" value={form.paymentDays} onChange={(e) => set("paymentDays", e.target.value)} className={FIELD} />
          </label>
          <label className="block text-sm">
            Qualifying outcome
            <input value={form.outcome} onChange={(e) => set("outcome", e.target.value)} className={FIELD} />
          </label>
          <p className="text-xs text-muted sm:col-span-2">The AI may only offer terms inside this range.</p>
        </div>
      )}

      {step === 3 && (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            Sender name
            <input value={form.displayName} onChange={(e) => set("displayName", e.target.value)} className={FIELD} />
          </label>
          <label className="block text-sm">
            Signature
            <input value={form.signature} onChange={(e) => set("signature", e.target.value)} className={FIELD} />
          </label>
          <label className="block text-sm">
            Company name
            <input value={form.companyName} onChange={(e) => set("companyName", e.target.value)} className={FIELD} />
          </label>
          <label className="block text-sm">
            Postal address
            <input value={form.postalAddress} onChange={(e) => set("postalAddress", e.target.value)} className={FIELD} />
          </label>
        </div>
      )}

      {step === 4 && (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            Daily send cap
            <input inputMode="numeric" value={form.dailyCap} onChange={(e) => set("dailyCap", e.target.value)} className={FIELD} />
          </label>
          <label className="block text-sm">
            Starting amount per day
            <input inputMode="numeric" value={form.rampStart} onChange={(e) => set("rampStart", e.target.value)} className={FIELD} />
          </label>
          <label className="block text-sm">
            Emails per clinic (1 to 3)
            <input inputMode="numeric" value={form.perClinic} onChange={(e) => set("perClinic", e.target.value)} className={FIELD} />
          </label>
          <label className="flex items-center gap-2 self-end text-sm">
            <input type="checkbox" checked={form.weekdaysOnly} onChange={(e) => set("weekdaysOnly", e.target.checked)} />
            Weekdays only
          </label>
          <label className="block text-sm">
            Send window starts (hour)
            <input inputMode="numeric" value={form.windowStart} onChange={(e) => set("windowStart", e.target.value)} className={FIELD} />
          </label>
          <label className="block text-sm">
            Send window ends (hour)
            <input inputMode="numeric" value={form.windowEnd} onChange={(e) => set("windowEnd", e.target.value)} className={FIELD} />
          </label>
        </div>
      )}

      {step === 5 && (
        <div className="space-y-2 text-sm">
          <p className="font-medium text-foreground">{form.name.trim()}</p>
          <ul className="list-disc space-y-1 pl-5 text-muted">
            <li>Clinics: {ticked.size} in {countryKey}.</li>
            <li>
              Commission: {form.basis === "PERCENTAGE" ? `${form.minValue}% to ${form.maxValue}%` : `${form.currency.toUpperCase()} ${form.minValue} to ${form.maxValue}`} for &ldquo;{form.outcome.trim()}&rdquo;, paid within {form.paymentDays} days. The AI may not offer anything outside this range.
            </li>
            <li>Sent as {form.displayName.trim()} of {form.companyName.trim()}.</li>
            <li>
              At most {form.dailyCap} emails a day, starting at {form.rampStart}, and {form.perClinic} per clinic, between {form.windowStart}:00 and {form.windowEnd}:00{form.weekdaysOnly ? " on weekdays" : ""}.
            </li>
          </ul>
          <p className="font-medium text-foreground">The campaign starts in shadow mode: nothing is sent until you take it live.</p>
        </div>
      )}

      {shown && (
        <p role="alert" className="mt-4 rounded-xl border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">
          {shown}
        </p>
      )}

      <div className="mt-5 flex justify-between">
        <Button variant="ghost" disabled={step === 0 || busy} onClick={() => setStep((s) => s - 1)}>
          Back
        </Button>
        {last ? (
          <Button disabled={busy || !token} onClick={create}>
            {busy ? "Creating..." : "Create campaign"}
          </Button>
        ) : (
          <Button disabled={error !== null || loadingClinics} onClick={() => setStep((s) => s + 1)}>
            Next
          </Button>
        )}
      </div>
    </div>
  );
}
