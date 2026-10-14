"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, getAIProcessing, updateAIProcessing } from "@/lib/api";

/**
 * Which external AI providers may process this business's own data. The default is none for a consent-gated business. Only the owner can change it,
 * and only by confirming they have a lawful basis. Phone numbers and e-mail addresses are still removed from what is sent.
 */
export function AIProcessingCard({ token }: { token: string | null }) {
  const [providers, setProviders] = useState<string[] | null>(null);
  const [draft, setDraft] = useState("");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setProviders((await getAIProcessing(token)).external_providers);
    } catch {
      setProviders(null);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function save(next: string[]) {
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await updateAIProcessing(token, { external_providers: next, acknowledged: ack || next.length === 0 });
      setProviders(r.external_providers);
      setDraft("");
      setAck(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't save that just now. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  if (providers === null) return null;
  return (
    <section aria-label="AI processing" className="mt-10 rounded-3xl bg-surface-muted px-6 py-6">
      <h2 className="font-display text-xl font-semibold tracking-tight text-foreground">AI providers for your data</h2>
      <p className="mt-1 text-[15px] leading-snug text-muted">
        {providers.length === 0 ? "No external AI provider is allowed to process your business's data." : `Allowed for your business's data: ${providers.join(", ")}.`}
      </p>
      {providers.length > 0 && (
        <button type="button" disabled={busy} onClick={() => save([])} className="mt-3 text-[14px] text-accent hover:underline disabled:opacity-50">
          Stop allowing all providers
        </button>
      )}
      <div className="mt-5 flex flex-wrap items-center gap-2">
        <input
          aria-label="AI provider name"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Provider, e.g. openai"
          className="rounded-xl border border-border bg-surface px-3 py-2 text-[14px] text-foreground placeholder:text-muted-foreground focus:border-accent focus:outline-none"
        />
        <button type="button" disabled={busy || !draft.trim() || !ack} onClick={() => save([...providers, draft.trim().toLowerCase()])} className="klaros-btn-secondary !py-2 disabled:opacity-50">
          Allow provider
        </button>
      </div>
      <label className="mt-3 flex items-start gap-2 text-[13px] leading-snug text-muted">
        <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5" />
        <span>I have a lawful basis (for example a data-processing agreement) to let this provider process my business&apos;s data. Only the owner can change this.</span>
      </label>
      {error && <p className="mt-3 text-[14px] text-danger">{error}</p>}
    </section>
  );
}
