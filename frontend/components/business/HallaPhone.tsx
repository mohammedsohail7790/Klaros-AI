"use client";

import { useCallback, useEffect, useState } from "react";
import { Phone, PhoneForwarded, Search } from "lucide-react";
import {
  ApiError,
  HallaPhoneOption,
  HallaPhoneStatus,
  buyHallaPhone,
  getHallaPhone,
  refreshHallaReceptionist,
  searchHallaPhone,
  setHallaBusinessNumber,
} from "@/lib/api";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";

const fail = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

/** A calendar date for something in the future ("26 Oct 2026"); the relative "x ago" helper reads a future time as "just now". Empty if unreadable. */
export function shortDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

/** "+19193715609" -> "+1 (919) 371-5609"; anything else is shown as it is. */
export function formatPhone(n: string | null | undefined): string {
  const m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(n ?? "");
  return m ? `+1 (${m[1]}) ${m[2]}-${m[3]}` : n ?? "";
}

/**
 * The business's phone line for its AI receptionist. Two separate things, both optional:
 *  - the AI number: one number per business, bought through Halla. It costs money, so the price is shown and has to be accepted before anything is bought.
 *  - the business's own existing number: where hard cases are handed to a person, and the line to forward to the AI number if every call should reach the AI.
 * Nothing here ever shows a credential, and nothing is bought without the owner ticking the price.
 */
export function HallaPhonePanel({ token, connected }: { token: string | null; connected: boolean }) {
  const [status, setStatus] = useState<HallaPhoneStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "success" | "danger" | "info"; text: string } | null>(null);
  const [area, setArea] = useState("");
  const [options, setOptions] = useState<HallaPhoneOption[] | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [accepted, setAccepted] = useState(false);
  const [biz, setBiz] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setLoadError(null);
    try {
      const s = await getHallaPhone(token);
      setStatus(s);
      setBiz((cur) => (cur === "" ? s.business_number ?? "" : cur));
    } catch (err) {
      setStatus(null);
      setLoadError(fail(err, "We couldn't load your phone details just now."));
    }
  }, [token]);

  useEffect(() => {
    if (connected) load();
  }, [connected, load]);

  if (!connected) return null;

  async function run(label: string, fn: () => Promise<void>) {
    if (busy) return;
    setBusy(label);
    setNotice(null);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  }

  const update = () =>
    run("update", async () => {
      try {
        const r = await refreshHallaReceptionist(token as string);
        setNotice({
          kind: "success",
          text: !r.updated
            ? "Your AI receptionist is already up to date."
            : r.configured
              ? "Your AI receptionist is updated and your business details were sent to it again."
              : "Your AI receptionist is updated, but your business details did not reach it. Use “Send business context to Halla” above.",
        });
      } catch (err) {
        setNotice({ kind: "danger", text: fail(err, "We couldn't update the receptionist. Nothing was changed.") });
      }
      await load();
    });

  const search = () =>
    run("search", async () => {
      try {
        const r = await searchHallaPhone(token as string, area.trim());
        setOptions(r.numbers);
        setPicked("");
        setAccepted(false);
        if (r.numbers.length === 0) setNotice({ kind: "info", text: "No numbers are free in that area code. Try a nearby one." });
      } catch (err) {
        setOptions(null);
        setNotice({ kind: "danger", text: fail(err, "We couldn't search for numbers just now.") });
      }
    });

  const buy = () =>
    run("buy", async () => {
      try {
        const r = await buyHallaPhone(token as string, { phone_number: picked, accepted_monthly_cost_usd: status?.monthly_cost_usd ?? 0 });
        setNotice({ kind: "success", text: `${formatPhone(r.phone_number)} is now your AI number. Call it from another phone to try it.` });
        setOptions(null);
        setPicked("");
        setAccepted(false);
      } catch (err) {
        setNotice({ kind: "danger", text: fail(err, "We couldn't buy that number. Nothing was charged.") });
      }
      await load();
    });

  const saveBiz = (value: string) =>
    run("biz", async () => {
      try {
        const r = await setHallaBusinessNumber(token as string, value.trim());
        setNotice({ kind: "success", text: r.business_number ? "Saved. Hard cases will be handed to this number." : "Removed. Hard cases are recorded for your team instead of rung through." });
        if (!r.business_number) setBiz("");
      } catch (err) {
        setNotice({ kind: "danger", text: fail(err, "We couldn't save that number.") });
      }
      await load();
    });

  const aiNumber = status?.numbers[0]?.phone_number ?? null;
  const cost = status?.monthly_cost_usd ?? null;

  return (
    <section aria-labelledby="halla-phone" className="mb-8">
      <h2 id="halla-phone" className="klaros-label mb-2">Phone number</h2>
      <div className="klaros-card space-y-5 p-5">
        {notice && <Alert variant={notice.kind}>{notice.text}</Alert>}
        {loadError && <Alert variant="danger">{loadError}</Alert>}
        {!status && !loadError && <p className="text-sm text-muted">Loading…</p>}

        {status?.template_outdated && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-surface-muted/70 p-3">
            <p className="text-sm text-foreground">A newer version of your AI receptionist is ready. It includes improvements to what it says about medical questions.</p>
            <Button variant="secondary" size="sm" onClick={update} disabled={busy !== null}>{busy === "update" ? "Updating…" : "Update receptionist"}</Button>
          </div>
        )}

        {status && (
          <>
            {aiNumber ? (
              <div>
                <p className="klaros-label mb-1">Your AI number</p>
                <p className="flex items-center gap-2 text-2xl text-foreground"><Phone className="h-5 w-5" aria-hidden="true" /> {formatPhone(aiNumber)}</p>
                <p className="mt-1 text-sm text-muted">Answered by {status.numbers[0]?.agent ?? "your AI receptionist"}. It says it is an AI and asks permission before it takes any detail.</p>
                {status.plan?.status === "trialing" && (
                  <p className="mt-2 text-xs text-muted">Trial plan{status.plan.trial_expires_at ? ` until ${shortDate(status.plan.trial_expires_at)}` : ""}. It is capped while you test.</p>
                )}
                <p className="mt-2 text-sm text-foreground">To try it, call this number from another phone.</p>
              </div>
            ) : status.can_buy ? (
              <div className="space-y-3">
                <div>
                  <p className="text-sm text-foreground">Get a phone number for your AI receptionist.</p>
                  <p className="text-xs text-muted">One number per business. Pick an area code, then choose from what is free.</p>
                </div>
                <div className="flex flex-wrap items-end gap-2">
                  <label className="text-sm">
                    <span className="klaros-label">Area code</span>
                    <input className="klaros-input mt-1 w-28" inputMode="numeric" maxLength={5} value={area} onChange={(e) => setArea(e.target.value)} placeholder="415" autoComplete="off" />
                  </label>
                  <Button variant="secondary" onClick={search} disabled={busy !== null || area.replace(/\D/g, "").length !== 3}>
                    <Search className="h-4 w-4" aria-hidden="true" /> {busy === "search" ? "Searching…" : "Search numbers"}
                  </Button>
                </div>

                {options && options.length > 0 && (
                  <fieldset className="space-y-2">
                    <legend className="klaros-label mb-1">Choose a number</legend>
                    {options.map((o) => (
                      <label key={o.phone_number} className="flex cursor-pointer items-center gap-2 rounded-xl border border-border px-3 py-2 text-sm">
                        <input type="radio" name="halla-number" value={o.phone_number} checked={picked === o.phone_number} onChange={() => { setPicked(o.phone_number); setAccepted(false); }} />
                        <span className="text-foreground">{formatPhone(o.phone_number)}</span>
                      </label>
                    ))}
                    <label className="flex items-start gap-2 text-sm">
                      <input type="checkbox" className="mt-1" checked={accepted} disabled={!picked} onChange={(e) => setAccepted(e.target.checked)} />
                      <span>I understand this number costs about ${cost?.toFixed(2)} a month, plus call usage, and will be billed to my account.</span>
                    </label>
                    <Button onClick={buy} disabled={busy !== null || !picked || !accepted}>
                      <Phone className="h-4 w-4" aria-hidden="true" /> {busy === "buy" ? "Buying…" : "Buy this number"}
                    </Button>
                  </fieldset>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted">A phone number can&apos;t be added right now. Please try again later or contact support.</p>
            )}

            <div className="border-t border-border pt-4">
              <p className="klaros-label mb-1">Your business number (optional)</p>
              <p className="mb-2 text-xs text-muted">Where the AI hands a caller to a person for urgent or hard cases. You can also forward this number to your AI number, so every call is answered by the AI first.</p>
              <div className="flex flex-wrap items-end gap-2">
                <label className="text-sm">
                  <span className="sr-only">Business phone number</span>
                  <input className="klaros-input w-56" inputMode="tel" value={biz} onChange={(e) => setBiz(e.target.value)} placeholder="+1 415 555 0123" autoComplete="off" aria-label="Business phone number" />
                </label>
                <Button variant="secondary" onClick={() => saveBiz(biz)} disabled={busy !== null || biz.trim() === "" || biz.trim() === (status.business_number ?? "")}>
                  {busy === "biz" ? "Saving…" : "Save"}
                </Button>
                {status.business_number && (
                  <Button variant="ghost" onClick={() => saveBiz("")} disabled={busy !== null}>Remove</Button>
                )}
              </div>
              {status.business_number && aiNumber && (
                <details className="mt-3 text-sm">
                  <summary className="flex cursor-pointer items-center gap-1.5 text-muted"><PhoneForwarded className="h-4 w-4" aria-hidden="true" /> How to forward {formatPhone(status.business_number)} to your AI number</summary>
                  <div className="mt-2 space-y-1 text-muted">
                    <p>Ask your phone provider, or open your phone or provider app, and turn on call forwarding from <span className="text-foreground">{formatPhone(status.business_number)}</span> to <span className="text-foreground">{formatPhone(aiNumber)}</span>. Forward <em>all calls</em> to have the AI answer first, or only <em>calls you don&apos;t answer</em> to have it as a back-up.</p>
                    <p>On many US lines, dialling <code>*72</code> and then the AI number turns forwarding on, and <code>*73</code> turns it off.</p>
                  </div>
                </details>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
