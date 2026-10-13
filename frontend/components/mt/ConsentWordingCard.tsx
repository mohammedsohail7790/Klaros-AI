"use client";

import { useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { ApiError, ConsentWordingList, approveConsentWording, listConsentWordings } from "@/lib/api";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";

const fail = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);
const date = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "");

/**
 * The exact words a visitor agrees to on your website's enquiry form. You write them (a plain-language draft is offered as a starting point), read them,
 * and approve them. Approval creates a numbered version that is never edited: if you want different words, you approve a new version, so every enquiry
 * stays tied to the exact text its sender saw. Until a wording is approved the enquiry form stays closed.
 */
export function ConsentWordingCard({ token, onChanged }: { token: string | null; onChanged?: () => void }) {
  const [data, setData] = useState<ConsentWordingList | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [language, setLanguage] = useState<"en" | "ar">("en");
  const [read, setRead] = useState(false);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "success" | "danger"; text: string } | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoadError(null);
    try {
      const d = await listConsentWordings(token);
      setData(d);
      setText((cur) => (cur === "" ? d.wordings[0]?.text ?? d.suggested_text : cur));
    } catch (err) {
      setLoadError(fail(err, "We couldn't load your consent wording just now."));
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  if (loadError) return <Alert variant="danger">{loadError}</Alert>;
  if (!data) return <p className="text-sm text-muted">Loading…</p>;

  const current = data.wordings[0] ?? null;
  const showEditor = current === null || editing;
  const length = text.trim().length;
  const lengthOk = length >= data.min_length && length <= data.max_length;
  const changed = current === null || text.trim() !== current.text || language !== current.language;

  async function approve() {
    if (!token || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const v = await approveConsentWording(token, { text: text.trim(), language });
      setNotice({ kind: "success", text: `${v.version} is approved. Your website's enquiry form can now take enquiries, and it shows exactly these words.` });
      setEditing(false);
      setRead(false);
      await load();
      onChanged?.();
    } catch (err) {
      setNotice({ kind: "danger", text: fail(err, "We couldn't approve that wording. Nothing was changed.") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section id="consent-wording" aria-labelledby="consent-wording-title" className="klaros-card space-y-4 p-5 sm:p-6">
      <div className="flex items-start gap-2">
        <ShieldCheck className="mt-0.5 h-5 w-5 text-success" aria-hidden="true" />
        <div>
          <h2 id="consent-wording-title" className="text-lg font-semibold tracking-tight text-foreground">Consent wording for your enquiry form</h2>
          <p className="mt-0.5 text-sm text-muted">The exact words a visitor agrees to before sending their details. Until you approve a wording, the enquiry form on your website stays closed.</p>
        </div>
      </div>

      {notice && <Alert variant={notice.kind}>{notice.text}</Alert>}

      {current && !showEditor && (
        <div className="space-y-2">
          <p className="text-sm text-foreground">
            <span className="font-medium">{current.version}</span> · approved {date(current.approved_at)}
          </p>
          <blockquote dir={current.language === "ar" ? "rtl" : "ltr"} className="whitespace-pre-line rounded-xl border border-border bg-surface-muted/70 p-3 text-sm text-foreground">{current.text}</blockquote>
          <Button variant="secondary" size="sm" onClick={() => { setEditing(true); setRead(false); }}>Write a new version</Button>
          {data.wordings.length > 1 && <p className="text-xs text-muted">Earlier versions stay on record: {data.wordings.slice(1).map((w) => w.version).join(", ")}. Each enquiry is tied to the version its sender saw.</p>}
        </div>
      )}

      {showEditor && (
        <div className="space-y-3">
          <p className="text-xs text-muted">{current ? "Changing the words creates a new version. The old one is kept." : "This is a draft to start from, not an approved wording. Change it to fit your business, then approve it."}</p>
          <label className="block text-sm">
            <span className="klaros-label">Wording</span>
            <textarea className="klaros-input mt-1 min-h-[9rem] w-full" dir={language === "ar" ? "rtl" : "ltr"} value={text} onChange={(e) => { setText(e.target.value); setRead(false); }} maxLength={data.max_length} />
          </label>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
            <label className="flex items-center gap-2">
              <span>Language</span>
              <select className="klaros-input !w-auto" value={language} onChange={(e) => setLanguage(e.target.value as "en" | "ar")}>
                <option value="en">English</option>
                <option value="ar">Arabic</option>
              </select>
            </label>
            <span>{length} / {data.max_length}{!lengthOk && length > 0 ? ` · at least ${data.min_length}` : ""}</span>
          </div>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={read} onChange={(e) => setRead(e.target.checked)} />
            <span>I have read these words and they are what I want visitors to agree to. I understand I can&apos;t edit them once approved, only replace them with a new version.</span>
          </label>
          <div className="flex flex-wrap gap-2">
            <Button onClick={approve} disabled={busy || !read || !lengthOk || !changed}>{busy ? "Approving…" : "Approve this wording"}</Button>
            {current && <Button variant="ghost" onClick={() => { setEditing(false); setText(current.text); setLanguage(current.language === "ar" ? "ar" : "en"); }}>Cancel</Button>}
          </div>
        </div>
      )}
    </section>
  );
}
