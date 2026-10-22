"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  OutreachMessageRow,
  OutreachSequenceMessage,
  OutreachShadowProgress,
  getOutreachMessage,
  getOutreachShadowProgress,
  listOutreachMessages,
  previewOutreachSequence,
  OutreachReplyTry,
  tryOutreachReply,
  reviewOutreachMessage,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";

const FILTERS = [
  { key: "ALL", label: "All" },
  { key: "UNREVIEWED", label: "Unreviewed" },
  { key: "FLAGGED", label: "Flagged" },
  { key: "BLOCKED", label: "Blocked" },
] as const;

const kindLabel = (k: string) => ({ intro: "Introduction", followup_1: "First follow-up", followup_2: "Last follow-up" })[k] ?? k;

/** The shadow week: the drafts the engine prepared, where the owner marks each one good or flags it. Nothing is sent while a campaign is in shadow mode. */
export function ShadowReview({ token, campaignId }: { token: string | null; campaignId: string }) {
  const [progress, setProgress] = useState<OutreachShadowProgress | null>(null);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("ALL");
  const [items, setItems] = useState<OutreachMessageRow[]>([]);
  const [open, setOpen] = useState<OutreachMessageRow | null>(null);
  const [flagging, setFlagging] = useState(false);
  const [reason, setReason] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [preview, setPreview] = useState<OutreachSequenceMessage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [replyText, setReplyText] = useState("");
  const [tried, setTried] = useState<OutreachReplyTry | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const params = filter === "BLOCKED" ? { status: "BLOCKED" } : filter === "FLAGGED" ? { review: "FLAGGED" } : filter === "UNREVIEWED" ? { review: "UNREVIEWED" } : {};
      const [p, m] = await Promise.all([getOutreachShadowProgress(token, campaignId), listOutreachMessages(token, campaignId, { ...params, limit: 100 })]);
      setProgress(p);
      setItems(m.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't load the shadow review just now.");
    }
  }, [token, campaignId, filter]);

  useEffect(() => {
    load();
  }, [load]);

  async function openMessage(m: OutreachMessageRow) {
    if (!token) return;
    setFlagging(false);
    setReason("");
    setFormError(null);
    setPreview(null);
    try {
      setOpen(await getOutreachMessage(token, campaignId, m.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't open that message.");
    }
  }

  async function mark(m: "GOOD" | "FLAGGED" | null) {
    if (!token || !open || busy) return;
    if (m === "FLAGGED" && !reason.trim()) {
      setFormError("Say what is wrong with this message before flagging it.");
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      await reviewOutreachMessage(token, campaignId, open.id, m, m === "FLAGGED" ? reason.trim() : undefined);
      setOpen(await getOutreachMessage(token, campaignId, open.id));
      setFlagging(false);
      setReason("");
      await load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "That didn't save just now.");
    } finally {
      setBusy(false);
    }
  }

  async function tryReply() {
    if (!token || busy || !replyText.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setTried(await tryOutreachReply(token, campaignId, replyText.trim()));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't try that reply just now.");
    } finally {
      setBusy(false);
    }
  }

  async function previewFor(providerId: string) {
    if (!token) return;
    try {
      setPreview((await previewOutreachSequence(token, campaignId, providerId)).messages);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't build that preview.");
    }
  }

  if (!progress) return error ? <p role="alert" className="text-sm text-danger">{error}</p> : <p className="text-sm text-muted">Loading...</p>;

  const counters: [string, number][] = [
    ["Days in shadow", progress.days_in_shadow],
    ["Drafts", progress.shadow_messages],
    ["Reviewed", progress.reviewed],
    ["Flagged", progress.flagged],
    ["Blocked", progress.blocked],
  ];

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted">Nothing is sent while a campaign is in shadow mode.</p>
      {error && <p role="alert" className="rounded-xl border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</p>}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {counters.map(([name, n]) => (
          <div key={name} className="rounded-xl border border-border bg-surface p-3">
            <div className="text-xs text-muted">{name}</div>
            <div className="text-xl font-semibold">{n}</div>
          </div>
        ))}
      </div>

      {progress.golive_checks && (
        <section aria-label="Before going live">
          <h2 className="mb-2 font-medium">Before going live</h2>
          <ul className="space-y-1 text-sm">
            <li>{progress.golive_checks.shadow_days.ok ? "Done" : "Not yet"}: at least {progress.golive_checks.shadow_days.required} days in shadow ({progress.golive_checks.shadow_days.actual} so far)</li>
            <li>{progress.golive_checks.reviewed.ok ? "Done" : "Not yet"}: at least {progress.golive_checks.reviewed.required} drafts reviewed with under 10 percent flagged ({progress.golive_checks.reviewed.actual} reviewed)</li>
            <li>{progress.golive_checks.reply_tests.ok ? "Done" : "Not yet"}: try a reply of each kind: {progress.golive_checks.reply_tests.required_kinds.map((k) => k.replace("_", "-")).join(", ")} ({progress.golive_checks.reply_tests.covered.length} of {progress.golive_checks.reply_tests.required_kinds.length} covered)</li>
            <li>{progress.golive_checks.settings.ok ? "Done" : "Not yet"}: terms and sender details complete</li>
          </ul>
        </section>
      )}

      <section aria-label="Try a reply" className="space-y-2">
        <h2 className="font-medium">Try a reply</h2>
        <p className="text-sm text-muted">Paste a sample reply from a clinic to see how it would be read and answered. Nothing is stored about any clinic and nothing is sent.</p>
        <label htmlFor="try-reply-text" className="text-sm">Sample clinic reply</label>
        <textarea id="try-reply-text" rows={4} value={replyText} onChange={(e) => setReplyText(e.target.value)} className="w-full rounded-xl border border-border-strong bg-surface px-3 py-2 text-sm" />
        <Button size="sm" variant="secondary" disabled={busy || !replyText.trim()} onClick={tryReply}>Try this reply</Button>
        {tried && (
          <div className="space-y-2 rounded-xl border border-border p-3 text-sm" aria-label="Reply result">
            <p><span className="font-medium">Read as:</span> {tried.label.replace(/_/g, " ").toLowerCase()} ({Math.round(tried.confidence * 100)}% sure)</p>
            <p><span className="font-medium">Next step:</span> {tried.handoff_reason ? `A person takes over. ${tried.handoff_reason}.` : tried.next_stage ? tried.next_stage.replace(/_/g, " ").toLowerCase() : "no change"}</p>
            {tried.draft ? (
              <div>
                <p className="font-medium">{tried.draft.subject}</p>
                <p className="whitespace-pre-wrap text-muted">{tried.draft.body}</p>
              </div>
            ) : (
              <p className="text-muted">No answer is drafted for this one.</p>
            )}
          </div>
        )}
      </section>

      {progress.needs_you.length > 0 && (
        <section>
          <h2 className="mb-2 font-medium">Needs you</h2>
          <ul className="space-y-1 text-sm">
            {progress.needs_you.map((n) => (
              <li key={n.provider_id}>{n.clinic}: {n.reason}</li>
            ))}
          </ul>
        </section>
      )}

      <div className="flex flex-wrap gap-2" role="group" aria-label="Filter messages">
        {FILTERS.map((f) => (
          <Button key={f.key} size="sm" variant={filter === f.key ? "primary" : "secondary"} onClick={() => setFilter(f.key)}>
            {f.label}
          </Button>
        ))}
      </div>

      {items.length === 0 ? (
        <EmptyState compact title="No drafts here yet." />
      ) : (
        <ul className="divide-y divide-border rounded-xl border border-border">
          {items.map((m) => (
            <li key={m.id} className="flex flex-wrap items-center gap-3 p-3">
              <button type="button" className="text-left text-sm font-medium underline-offset-2 hover:underline" onClick={() => openMessage(m)}>
                {m.clinic}: {kindLabel(m.kind)}
              </button>
              <span className="text-sm text-muted">{m.subject}</span>
              {m.status === "BLOCKED" && <Badge>Blocked</Badge>}
              {m.review_mark && <Badge>{m.review_mark === "GOOD" ? "Good" : "Flagged"}</Badge>}
              <Button size="sm" variant="ghost" onClick={() => previewFor(m.provider_id)}>Preview full sequence</Button>
            </li>
          ))}
        </ul>
      )}

      {preview && (
        <section aria-label="Full sequence preview" className="space-y-3 rounded-xl border border-border p-4">
          <h2 className="font-medium">Full sequence</h2>
          {preview.map((p) => (
            <article key={p.kind} className="space-y-1">
              <h3 className="text-sm font-medium">{kindLabel(p.kind)}: {p.subject}</h3>
              <p className="whitespace-pre-wrap text-sm text-muted">{p.body}</p>
              {p.problems.map((x) => <p key={x} className="text-xs text-danger">{x}</p>)}
            </article>
          ))}
          <Button size="sm" variant="ghost" onClick={() => setPreview(null)}>Close preview</Button>
        </section>
      )}

      {open && (
        <aside aria-label="Message" className="space-y-3 rounded-xl border border-border-strong p-4">
          <h2 className="font-medium">{open.clinic}: {kindLabel(open.kind)}</h2>
          <p className="text-sm font-medium">{open.subject}</p>
          <p className="whitespace-pre-wrap text-sm">{open.body}</p>
          {open.blocked_reason && <p className="text-sm text-danger">Blocked: {open.blocked_reason}</p>}
          {open.review_reason && <p className="text-sm text-muted">Flagged because: {open.review_reason}</p>}
          {flagging && (
            <div>
              <label htmlFor="flag-reason" className="text-sm">Why are you flagging it?</label>
              <input id="flag-reason" value={reason} onChange={(e) => setReason(e.target.value)} className="mt-1 w-full rounded-xl border border-border-strong bg-surface px-3 py-2 text-sm" />
            </div>
          )}
          {formError && <p role="alert" className="text-sm text-danger">{formError}</p>}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy} onClick={() => mark("GOOD")}>Good</Button>
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => (flagging ? mark("FLAGGED") : setFlagging(true))}>{flagging ? "Save flag" : "Flag"}</Button>
            {open.review_mark && <Button size="sm" variant="ghost" disabled={busy} onClick={() => mark(null)}>Clear mark</Button>}
            <Button size="sm" variant="ghost" onClick={() => setOpen(null)}>Close</Button>
          </div>
        </aside>
      )}
    </div>
  );
}
