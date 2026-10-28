"use client";

import { useState } from "react";
import { Card, Button } from "@/components/store/ui";
import { StoreApiError, reportOrderProblem } from "@/lib/storeApi";

const MIN = 10;
const MAX = 1000;

/** "Something wrong with this order?" on the shopper's own order page. Offered only once the order has shipped; one report at a time; staff decide and reply by e-mail. */
export default function ProblemReport({ token, canReport, status }: { token: string; canReport: boolean; status: "PENDING" | "APPROVED" | "REJECTED" | null }) {
  const [text, setText] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "done">("idle");
  const [error, setError] = useState<string | null>(null);

  if (state === "done" || status === "PENDING") {
    return <Card className="p-6 sm:p-7" role="status"><h2 className="text-[24px]">We have your report</h2><p className="mt-2 text-[15px] text-muted">Thank you. We will look at it and reply by e-mail.</p></Card>;
  }
  if (status === "APPROVED") {
    return <Card className="p-6 sm:p-7" role="status"><h2 className="text-[24px]">Your report was approved</h2><p className="mt-2 text-[15px] text-muted">We are arranging your refund and will e-mail you when it is done.</p></Card>;
  }
  if (!canReport) return null;

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const description = text.trim();
    if (description.length < MIN) return setError(`Please tell us a little more (at least ${MIN} characters).`);
    setError(null);
    setState("sending");
    try {
      await reportOrderProblem(token, description);
      setState("done");
    } catch (err) {
      setState("idle");
      setError(err instanceof StoreApiError ? [err.message, ...err.problems].join(" ") : "Something went wrong. Please try again.");
    }
  }

  return (
    <Card className="p-6 sm:p-7">
      <h2 className="text-[24px]">Something wrong with this order?</h2>
      <p className="mt-2 text-[15px] text-muted">If an item arrived damaged, wrong or missing, tell us what happened and we will look into it.{status === "REJECTED" && " Your last report was not approved; you can write to us again with more detail."}</p>
      <form onSubmit={send} noValidate aria-label="Report a problem with this order" className="mt-4 space-y-3">
        <label htmlFor="problem-text" className="block text-sm font-medium text-foreground">What happened?</label>
        <textarea id="problem-text" value={text} onChange={(e) => setText(e.target.value)} rows={4} maxLength={MAX} aria-invalid={error ? true : undefined} aria-describedby={error ? "problem-err" : undefined}
          className="w-full rounded-2xl bg-background px-4 py-3 text-[17px] text-foreground placeholder:text-muted focus-visible:ring-2 focus-visible:ring-[rgb(var(--color-brand))]" />
        {error && <p id="problem-err" role="alert" className="text-sm font-medium text-red-800">{error}</p>}
        <Button type="submit" variant="brand" disabled={state === "sending"}>{state === "sending" ? "Sending…" : "Send report"}</Button>
      </form>
    </Card>
  );
}
