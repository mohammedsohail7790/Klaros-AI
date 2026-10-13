"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, CircleAlert, Loader2, Send, Square, XCircle } from "lucide-react";
import { BUILDER_ACTIVE_STATES, BuilderMessage, BuilderRun } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { EDIT_SUGGESTIONS, STATE_LABEL } from "./labels";

export function RunProgress({ run, onCancel }: { run: BuilderRun; onCancel: () => void }) {
  const active = BUILDER_ACTIVE_STATES.includes(run.state);
  const events = run.progress.slice(-8);
  return (
    <div className="rounded-xl border border-border bg-surface-muted/60 p-3" role="status" aria-live="polite">
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-2 text-sm font-medium text-foreground">
          {active ? <Loader2 className="h-4 w-4 animate-spin text-accent" aria-hidden="true" /> : run.state === "ready" ? <CheckCircle2 className="h-4 w-4 text-success" aria-hidden="true" /> : run.state === "cancelled" ? <Square className="h-4 w-4 text-muted" aria-hidden="true" /> : <XCircle className="h-4 w-4 text-danger" aria-hidden="true" />}
          {STATE_LABEL[run.state]}
          {run.repair_attempts > 0 && <span className="text-xs font-normal text-muted">· {run.repair_attempts} repair{run.repair_attempts > 1 ? "s" : ""}</span>}
        </p>
        {active && (
          <Button variant="secondary" size="sm" onClick={onCancel} disabled={run.cancel_requested}>
            {run.cancel_requested ? "Stopping…" : "Cancel"}
          </Button>
        )}
      </div>
      <ol className="mt-2 space-y-1">
        {events.map((e, i) => (
          <li key={`${e.at}-${i}`} className={cn("text-xs leading-snug", i === events.length - 1 ? "text-foreground" : "text-muted")}>
            {e.message}
          </li>
        ))}
      </ol>
      {!active && run.source && run.state === "ready" && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {run.source === "ai" ? `Planned by AI${run.usage.models?.length ? ` (${run.usage.models.join(", ")})` : ""} · ${run.usage.calls ?? 0} model call(s)` : "Planned from your requirements — no AI model was used."}
        </p>
      )}
      {run.state === "failed" && run.error && run.error !== "timeout" && <p className="mt-2 flex gap-1.5 text-xs text-danger"><CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />{run.error.slice(0, 280)}</p>}
    </div>
  );
}

export default function ChatPanel({ messages, run, onSend, onCancel, sending }: { messages: BuilderMessage[]; run: BuilderRun | null; onSend: (m: string) => Promise<void>; onCancel: () => void; sending: boolean }) {
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const active = !!run && BUILDER_ACTIVE_STATES.includes(run.state);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [messages.length, run?.progress.length]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const m = text.trim();
    if (!m || active || sending) return;
    setText("");
    await onSend(m);
  }

  return (
    <section aria-label="Chat" className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && !run && <p className="text-sm text-muted">Describe what to change and Klaros will edit the project, check it, and update the preview.</p>}
        {messages.map((m) => (
          <div key={m.id} className={cn("max-w-[92%] whitespace-pre-wrap rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed", m.role === "user" ? "ml-auto bg-accent text-accent-foreground" : "bg-surface-muted text-foreground")}>
            {m.content}
          </div>
        ))}
        {run && <RunProgress run={run} onCancel={onCancel} />}
        <div ref={end} />
      </div>
      <div className="border-t border-border p-3">
        {!active && (
          <div className="mb-2 flex flex-wrap gap-1.5" aria-label="Suggestions">
            {EDIT_SUGGESTIONS.map((s) => (
              <button key={s} type="button" onClick={() => setText(s)} className="rounded-full border border-border bg-surface px-2.5 py-1 text-xs text-muted hover:border-border-strong hover:text-foreground">{s}</button>
            ))}
          </div>
        )}
        <form onSubmit={submit} className="flex items-end gap-2">
          <label htmlFor="builder-chat" className="sr-only">Ask for a change</label>
          <textarea
            id="builder-chat"
            rows={2}
            value={text}
            disabled={active}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit(e as unknown as React.FormEvent);
              }
            }}
            placeholder={active ? "Working on it…" : "Ask for a change, e.g. “add a pricing page”"}
            className="klaros-input min-h-[2.75rem] resize-none disabled:opacity-60"
          />
          <Button type="submit" disabled={active || sending || !text.trim()} aria-label="Send">
            <Send className="h-4 w-4" aria-hidden="true" />
          </Button>
        </form>
      </div>
    </section>
  );
}
