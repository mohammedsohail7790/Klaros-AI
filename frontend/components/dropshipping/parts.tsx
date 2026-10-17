"use client";

import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { cn } from "@/lib/cn";
import type { Tone } from "@/lib/dropshippingLabels";

const TONE: Record<Tone, string> = {
  neutral: "bg-black/[0.06] text-muted",
  warning: "bg-amber-50 text-amber-700",
  success: "bg-emerald-50 text-emerald-700",
  danger: "bg-red-50 text-red-700",
  info: "bg-sky-50 text-sky-700",
};

export function ToneBadge({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return <Badge className={cn(TONE[tone])}>{children}</Badge>;
}

/** A refusal: the message, and the server's list of reasons underneath when it sent one. */
export function ProblemAlert({ message, problems }: { message: string; problems?: string[] }) {
  return (
    <Alert variant="danger">
      <div>{message}</div>
      {problems && problems.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5">
          {problems.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      )}
    </Alert>
  );
}

/**
 * Asks before a consequential action. `fieldLabel` adds a required text box (a reason, a reference, a note): the confirm button stays disabled until it is filled.
 * `onConfirm` receives the text; if it throws the dialog stays open and shows why.
 */
export function ConfirmAction({
  title, body, confirmLabel, fieldLabel, fieldHint, danger, onConfirm, onClose,
}: {
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  fieldLabel?: string;
  fieldHint?: string;
  danger?: boolean;
  onConfirm: (text: string) => Promise<void>;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; problems: string[] } | null>(null);
  const missing = fieldLabel !== undefined && text.trim() === "";

  async function go() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm(text.trim());
      onClose();
    } catch (err) {
      const e = err as { message?: string; problems?: string[] };
      setError({ message: e.message || "That did not work.", problems: e.problems ?? [] });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={title} onClose={onClose}>
      <div className="space-y-4 text-[15px] text-muted">
        <div>{body}</div>
        {fieldLabel !== undefined && (
          <Field label={fieldLabel}>
            <Input value={text} onChange={(e) => setText(e.target.value)} placeholder={fieldHint} autoFocus />
          </Field>
        )}
        {error && <ProblemAlert message={error.message} problems={error.problems} />}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={busy}>Not now</Button>
          <Button variant={danger ? "danger" : "primary"} onClick={go} disabled={busy || missing}>{busy ? "Working…" : confirmLabel}</Button>
        </div>
      </div>
    </Modal>
  );
}
