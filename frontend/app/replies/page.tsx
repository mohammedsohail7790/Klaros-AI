"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Mail } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, InboxReply, getInboxReplies } from "@/lib/api";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";

const when = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

/** Email replies from patients and clinics, brought in by Klaros. A reply from a patient is also on that enquiry's notes. What a sender wrote is shown as plain text and is never acted on. */
export default function RepliesPage() {
  const { token, user, loading } = useAuth();
  const [replies, setReplies] = useState<InboxReply[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setReplies((await getInboxReplies(token)).replies);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load replies.");
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-4xl px-5 py-8 sm:px-8 sm:py-12">
        <PageHeader title="Replies" description="Email replies from patients and clinics, in one place." icon={Mail} />
        {error ? (
          <p role="alert" className="text-danger">{error}</p>
        ) : loading || replies === null ? (
          <Skeleton />
        ) : replies.length === 0 ? (
          <EmptyState title="No replies yet. When a patient or a clinic replies to an email you sent, it appears here and on their record." />
        ) : (
          <ul className="space-y-3" aria-label="Replies">
            {replies.map((r) => (
              <li key={r.id} className="rounded-3xl bg-surface-muted px-6 py-5">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-[16px] font-semibold text-foreground">
                    {r.entity_type === "lead" ? <Link href={`/leads/${r.entity_id}`} className="hover:underline">{r.about}</Link> : r.about}
                    <span className="ml-2 text-[13px] font-normal text-muted">{r.entity_type === "lead" ? "patient enquiry" : "clinic"}</span>
                  </p>
                  <p className="text-[13px] text-muted">{when(r.received_at)}</p>
                </div>
                <p className="mt-1 text-[14px] text-foreground">{r.subject || "(no subject)"}</p>
                <p className="text-[13px] text-muted">From {r.from_name ? `${r.from_name} <${r.from_address}>` : r.from_address}{r.attachment_count > 0 ? ` · ${r.attachment_count} attachment${r.attachment_count === 1 ? "" : "s"} not kept` : ""}</p>
                {!r.sender_matches && <p className="mt-1 text-[13px] text-danger">Sent from a different address than the one we wrote to. Check who it is before acting on it.</p>}
                {r.body_stored ? (
                  <p className="mt-3 whitespace-pre-wrap text-[14px] leading-snug text-foreground">{r.body_text}</p>
                ) : (
                  <p className="mt-3 text-[14px] text-muted">The text was not kept, because this patient's consent does not yet cover keeping medical information. Ask them to confirm consent.</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </AppShell>
  );
}
