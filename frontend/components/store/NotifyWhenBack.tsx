"use client";

import { useState } from "react";
import { Button, Input } from "@/components/store/ui";
import { StoreApiError, askToBeTold } from "@/lib/storeApi";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** For a sold-out item: leave an e-mail address and get one plain message when it is back. The address is removed once we have told you. The hidden `website` box is for bots. */
export default function NotifyWhenBack({ sku }: { sku: string }) {
  const [state, setState] = useState<"idle" | "sending" | "done">("idle");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const email = String(f.get("email") ?? "").trim();
    if (!EMAIL.test(email)) return setError("Please enter a valid email address.");
    setError(null);
    setState("sending");
    try {
      await askToBeTold(sku, email, String(f.get("website") ?? ""));
      setState("done");
    } catch (err) {
      setState("idle");
      setError(err instanceof StoreApiError ? (err.status === 409 ? "This item may be in stock now. Please refresh the page." : err.message) : "Something went wrong. Please try again.");
    }
  }

  if (state === "done") {
    return <p role="status" className="rounded-2xl bg-surface-muted px-5 py-4 text-foreground">Thank you. We will send you one e-mail when it is back, and then remove your address.</p>;
  }
  return (
    <form onSubmit={submit} noValidate aria-label="Tell me when it is back" className="space-y-3">
      <Input label="Tell me when it is back" name="email" type="email" autoComplete="email" maxLength={255} error={error ?? undefined} hint="One e-mail, then we forget your address." />
      <div aria-hidden className="absolute left-[-9999px] h-0 w-0 overflow-hidden"><label>Leave this empty<input name="website" tabIndex={-1} autoComplete="off" /></label></div>
      <Button type="submit" variant="secondary" disabled={state === "sending"}>{state === "sending" ? "Sending…" : "Tell me"}</Button>
    </form>
  );
}
