"use client";

import { useRef, useState } from "react";
import { Button, Input } from "@/components/store/ui";
import { StoreApiError, sendEnquiry } from "@/lib/storeApi";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** "Write to us": name, email and a message. It reaches the shop owner as a lead. The hidden `website` box is for bots; a person never sees it. */
export default function EnquiryForm() {
  const [state, setState] = useState<"idle" | "sending" | "done">("idle");
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<{ name?: string; email?: string; message?: string }>({});
  const key = useRef(`enq-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`);   // one try = one key, so a double click cannot send it twice

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const name = String(f.get("name") ?? "").trim();
    const email = String(f.get("email") ?? "").trim();
    const message = String(f.get("message") ?? "").trim();
    const order = String(f.get("order_number") ?? "").trim();
    const found: typeof fieldErrors = {};
    if (!name) found.name = "Please tell us your name.";
    if (!EMAIL.test(email)) found.email = "Please enter a valid email address.";
    if (!message) found.message = "Please write a message.";
    setFieldErrors(found);
    setError(null);
    if (Object.keys(found).length) return;
    setState("sending");
    try {
      await sendEnquiry({ name, email, message, order_number: order || undefined, website: String(f.get("website") ?? ""), idempotency_key: key.current });
      setState("done");
    } catch (err) {
      setState("idle");
      setError(err instanceof StoreApiError ? err.message : "Something went wrong. Please try again.");
    }
  }

  if (state === "done") {
    return (
      <div role="status" className="mx-auto max-w-[520px] rounded-[24px] bg-background p-8 text-center text-foreground">
        <h3 className="text-[24px] font-semibold tracking-[-0.02em]">Thank you. We have your message.</h3>
        <p className="mt-2 text-[17px] text-muted">We will reply by email.</p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} noValidate aria-label="Write to us" className="mx-auto max-w-[520px] space-y-5 rounded-[24px] bg-background p-6 text-left text-foreground sm:p-8">
      <Input label="Your name" name="name" autoComplete="name" required maxLength={255} error={fieldErrors.name} />
      <Input label="Email" name="email" type="email" autoComplete="email" required maxLength={255} error={fieldErrors.email} />
      <Input label="Order number (if it is about an order)" name="order_number" maxLength={40} />
      <div>
        <label htmlFor="enq-message" className="mb-2 block text-sm font-medium text-foreground">Message</label>
        <textarea id="enq-message" name="message" required maxLength={1800} rows={4} aria-invalid={fieldErrors.message ? true : undefined} aria-describedby={fieldErrors.message ? "enq-message-err" : undefined}
          className="w-full rounded-2xl bg-surface-muted px-4 py-3 text-[17px] text-foreground placeholder:text-muted focus-visible:bg-surface focus-visible:ring-2 focus-visible:ring-[rgb(var(--color-brand))]" />
        {fieldErrors.message && <p id="enq-message-err" className="mt-2 text-sm text-red-800">{fieldErrors.message}</p>}
      </div>
      <div aria-hidden className="absolute left-[-9999px] h-0 w-0 overflow-hidden"><label>Leave this empty<input name="website" tabIndex={-1} autoComplete="off" /></label></div>
      {error && <p role="alert" className="text-[15px] font-medium text-red-800">{error}</p>}
      <Button type="submit" variant="brand" size="lg" className="w-full" disabled={state === "sending"}>{state === "sending" ? "Sending…" : "Send message"}</Button>
    </form>
  );
}
