"use client";

import { useEffect, useState } from "react";
import { Bot, UserRound } from "lucide-react";
import { LeadNote, listLeadNotes } from "@/lib/api";

/** What people and the AI crew have written about this enquiry, newest first. Stays hidden until there is a note. */
export function LeadNotes({ token, leadId, refreshKey }: { token: string | null; leadId: string; refreshKey?: string }) {
  const [notes, setNotes] = useState<LeadNote[]>([]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await listLeadNotes(token, leadId);
        if (!cancelled) setNotes(r.notes);
      } catch {
        if (!cancelled) setNotes([]); // notes are a convenience: never block the page on them
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, leadId, refreshKey]);

  if (notes.length === 0) return null;
  return (
    <section aria-labelledby="lead-notes-h" className="rounded-3xl bg-surface-muted p-5 sm:p-6">
      <h2 id="lead-notes-h" className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Notes</h2>
      <ul className="mt-3 space-y-3" aria-label="Lead notes">
        {notes.map((n) => (
          <li key={n.id} className="flex items-start gap-3">
            <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface text-muted-foreground">
              {n.author_type.toUpperCase() === "AI" ? <Bot className="h-3.5 w-3.5" aria-hidden="true" /> : <UserRound className="h-3.5 w-3.5" aria-hidden="true" />}
            </span>
            <div className="min-w-0">
              <p className="whitespace-pre-wrap text-[15px] leading-snug text-foreground">{n.body}</p>
              <p className="mt-0.5 text-[12px] text-muted-foreground">
                {n.author_type.toUpperCase() === "AI" ? "AI crew" : "Team"} · {new Date(n.created_at).toLocaleString()}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
