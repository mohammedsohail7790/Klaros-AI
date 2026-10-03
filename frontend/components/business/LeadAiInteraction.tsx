"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, Bot, PhoneOutgoing, Send } from "lucide-react";
import { ApiError, LeadInteraction, askHallaToCall, getLeadInteraction, syncLeadToHalla } from "@/lib/api";
import { when } from "@/lib/opsLabels";
import { AiStatePill } from "./AiState";

/**
 * "AI interaction" for one lead: the state Klaros derived from what the AI workforce has
 * actually reported — and, when it has reported nothing, the plain truth about the workforce
 * (e.g. "Halla not connected"). It never shows a conversation that did not happen.
 */
export function LeadAiInteraction({
  token,
  leadId,
  refreshKey,
  onLoaded,
}: {
  token: string | null;
  leadId: string;
  refreshKey?: unknown;
  onLoaded?: (i: LeadInteraction) => void;
}) {
  const [data, setData] = useState<LeadInteraction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"sync" | "call" | null>(null);
  const [confirmCall, setConfirmCall] = useState(false);
  const [actionNote, setActionNote] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setError(null);
    (async () => {
      try {
        const i = await getLeadInteraction(token, leadId);
        if (!cancelled) {
          setData(i);
          onLoaded?.(i);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError && err.status === 403 ? "You don't have permission to view AI interactions." : "We couldn't load the AI interaction just now.");
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, leadId, refreshKey]);

  if (error) return <div role="alert" className="rounded-lg border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">{error}</div>;
  if (!data) return null;

  const simulated = data.workforce.mode === "development";
  // These actions exist only when the backend reports a REAL, currently-connected Halla.
  const canAct = data.workforce.mode === "live" && data.workforce.status === "CONNECTED";

  async function act(kind: "sync" | "call") {
    if (!token || busy) return;
    setBusy(kind);
    setActionNote(null);
    try {
      if (kind === "sync") {
        const r = await syncLeadToHalla(token, leadId);
        setActionNote({ ok: true, text: r.created ? "Sent to Halla." : "Halla's copy of this lead was updated." });
      } else {
        await askHallaToCall(token, leadId, { reason: "follow_up" });
        setActionNote({ ok: true, text: "Halla has been asked to call this customer." });
      }
    } catch (err) {
      setActionNote({ ok: false, text: err instanceof ApiError ? err.message : "That didn't go through. Nothing was changed." });
    } finally {
      setBusy(null);
      setConfirmCall(false);
    }
  }
  const noConversation = data.events.length === 0;
  return (
    <section aria-labelledby="lead-ai-h" className="rounded-lg border border-border bg-surface p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="lead-ai-h" className="flex items-center gap-2 font-display text-xl text-foreground">
          <Bot className="h-5 w-5 text-accent" aria-hidden="true" /> AI interaction
        </h2>
        <AiStatePill state={data.state} />
      </div>
      {noConversation ? (
        <p className="mt-2 text-sm text-muted">
          {data.workforce.status === "CONNECTED"
            ? "Halla hasn't spoken with this customer yet."
            : "No AI conversation has taken place with this customer — your team handles this lead."}{" "}
          <Link href="/workforce" className="underline underline-offset-2">AI workforce</Link>
        </p>
      ) : (
        <>
          {data.summary && (
            <div className="mt-3 rounded-lg bg-surface-muted/70 p-3">
              <p className="klaros-label mb-1">Conversation summary</p>
              <p className="text-sm text-foreground">{data.summary}</p>
            </div>
          )}
          <ol className="mt-3 space-y-1.5 border-l border-border pl-4" aria-label="AI interaction events">
            {data.events.map((e, i) => (
              <li key={`${e.at}-${i}`} className="text-sm">
                <span className="text-foreground">{e.text}</span>
                {e.channel && <span className="text-muted"> via {e.channel}</span>}
                {e.simulated && <span className="ml-1.5 rounded-full border border-dashed border-border-strong px-1.5 py-0.5 text-[10px] text-muted">simulated</span>}
                <span className="text-xs text-muted-foreground"> · {when(e.at)}</span>
              </li>
            ))}
          </ol>
        </>
      )}
      {canAct && (
        <div className="mt-4 border-t border-border pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="klaros-btn-secondary !px-3 !py-1.5 text-sm" onClick={() => act("sync")} disabled={busy !== null}>
              <Send className="h-3.5 w-3.5" aria-hidden="true" /> {busy === "sync" ? "Sending…" : "Send to Halla"}
            </button>
            {confirmCall ? (
              <span role="alertdialog" aria-label="Confirm call" className="inline-flex flex-wrap items-center gap-2 text-sm">
                Halla will call this customer now.
                <button type="button" className="klaros-btn-primary !px-3 !py-1 text-xs" onClick={() => act("call")} disabled={busy !== null}>{busy === "call" ? "Requesting…" : "Yes, call"}</button>
                <button type="button" className="klaros-btn-secondary !px-3 !py-1 text-xs" onClick={() => setConfirmCall(false)}>Cancel</button>
              </span>
            ) : (
              <button type="button" className="klaros-btn-secondary !px-3 !py-1.5 text-sm" onClick={() => setConfirmCall(true)} disabled={busy !== null}>
                <PhoneOutgoing className="h-3.5 w-3.5" aria-hidden="true" /> Ask Halla to call
              </button>
            )}
          </div>
          {actionNote && <p role={actionNote.ok ? "status" : "alert"} className={`mt-2 text-xs ${actionNote.ok ? "text-success" : "text-danger"}`}>{actionNote.text}</p>}
        </div>
      )}
      {simulated && <p className="mt-3 text-xs text-muted">Development simulator — these events are simulated. This is not a live Halla connection.</p>}
    </section>
  );
}

export function GenericNextAction({ action }: { action: LeadInteraction["next_action"] }) {
  return (
    <section aria-label="Next action" className="rounded-lg border border-accent/30 bg-accent-soft/60 p-4">
      <p className="klaros-label mb-1">Next action</p>
      {action ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-foreground">{action.text}</p>
          <Link href={action.route} className="klaros-btn-secondary text-sm">Open <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" /></Link>
        </div>
      ) : (
        <p className="text-sm text-muted">Nothing to do — this lead is closed.</p>
      )}
    </section>
  );
}
